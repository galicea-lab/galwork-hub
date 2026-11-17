# coding: utf-8
# 2026-11-16: wydruki PDF z formularzy obiegów.
"""
Wydruki PDF z danych formularza instancji procesu.

Szablony:
  * szablon własny - plik `*.html.j2` w folderze obiegu (w wersji obiegu jako
    `ProcessFileType.OTHER`), wskazany kluczem `x-print` w `*-schema.json`,
  * szablon standardowy (`wf/templates/print/domyslny.html.j2`) - gdy obieg
    nie ma szablonu dla kroku albo użytkownik wybierze wydruk standardowy:
    nagłówek, tabela "etykieta pola: wartość" w kolejności `ui:order`, stopka.

Klucz `x-print` w głównym obiekcie schematu - obiekt albo lista obiektów:

    "x-print": [
      {"template": "postepowanie_Task_03_ZBB.html.j2",
       "label": "Zawiadomienie o wszczęciu postępowania (ZBB)",
       "tasks": ["Task_03"],
       "when": {"Systemowe/Formularz zawiadomienia - akronim": "ZBB"}}
    ]

  * `template` (wymagane) - nazwa pliku szablonu w folderze obiegu,
  * `label` - nazwa wydruku na przycisku (domyślnie nazwa pliku),
  * `title` - tytuł dokumentu (zwykły tekst, domyślnie `label`); tytuł
    i nazwa pliku PDF dostają na końcu numer sprawy (`business_key`).
    Bez Jinja: hub renderuje schemat zawierający `{{` przed pokazaniem
    formularza, a w tym miejscu nie ma kontekstu wydruku,
  * `tasks` - id zadań BPMN, w których wydruk jest dostępny (brak = wszędzie),
  * `when` - warunki na danych: ścieżka `Sekcja/Pole` -> wartość albo lista
    wartości. Warunek odrzuca wydruk tylko wtedy, gdy pole ma wartość INNĄ
    niż oczekiwana; pole puste nie odrzuca (wariant nieustalony = użytkownik
    wybiera z listy).

Szablony druków dziedziczą wspólną bibliotekę (`_base/*.j2`, `_wzory/*.html.j2`).
Biblioteka leży w katalogu obiegów (`[print] library_dir`, domyślnie
`wspolne/druki`); synchronizacja z dysku dołącza ją do każdej wersji obiegu,
która ma szablony, więc dokument drukuje się szablonem z tej wersji, w której
powstał. Wersja bez biblioteki (np. starsza) czyta ją z dysku.

Bezpieczeństwo:
  * Jinja w `SandboxedEnvironment` z `autoescape=True` (dane formularza nie
    mogą wstrzyknąć HTML),
  * WeasyPrint dostaje fetcher, który przepuszcza tylko adresy `data:` - szablon
    nie odpyta sieci wewnętrznej (SSRF) ani nie przeczyta plików serwera.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Mapping

from jinja2 import BaseLoader, TemplateError, TemplateNotFound
from jinja2.sandbox import SandboxedEnvironment

from config.config import section
from wf.schemas.wf_schemas import ProcessFileType

logger = logging.getLogger(__name__)

#: rozszerzenie plików szablonów wydruku (pliki druków: `*.html.j2`, makra: `*.j2`)
TEMPLATE_SUFFIX = '.j2'
PRINT_SUFFIX = '.html.j2'
DEFAULT_KEY = '__default__'
DEFAULT_LABEL = 'Wydruk standardowy'
DEFAULT_LIBRARY_DIR = 'wspolne/druki'

_HUB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BUILTIN_DIR = os.path.join(_HUB_ROOT, 'wf', 'templates', 'print')
_DEFAULT_TEMPLATE = 'domyslny.html.j2'

_MONTHS = ('stycznia', 'lutego', 'marca', 'kwietnia', 'maja', 'czerwca', 'lipca',
           'sierpnia', 'września', 'października', 'listopada', 'grudnia')
_ISO_DATE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')
_ISO_DATETIME = re.compile(r'^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})')


class PrintError(ValueError):
    """Błąd wydruku; `status` to kod HTTP dla routera."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# --- konfiguracja ---------------------------------------------------------

def _config() -> dict:
    return section('print')


def library_dir(root: str) -> str:
    """Katalog biblioteki szablonów (`_base`, `_wzory`) w katalogu obiegów."""
    rel = (_config().get('library_dir') or DEFAULT_LIBRARY_DIR).strip().strip('/')
    return os.path.realpath(os.path.join(root, rel))


def _logo_uri(value: str) -> str:
    """Logo z konfiguracji: `data:` bez zmian, plik lokalny -> data URI."""
    value = (value or '').strip()
    if not value or value.startswith('data:'):
        return value
    path = value if os.path.isabs(value) else os.path.join(_HUB_ROOT, value)
    try:
        with open(path, 'rb') as f:
            content = f.read()
    except OSError:
        logger.warning('[print] logo: nie można odczytać %s', value)
        return ''
    mime = mimetypes.guess_type(path)[0] or 'image/png'
    return f'data:{mime};base64,{base64.b64encode(content).decode("ascii")}'


def polish_date(value: date | datetime | None = None) -> str:
    """Data do druku: "16 listopada 2026 r."."""
    value = value or date.today()
    return f'{value.day} {_MONTHS[value.month - 1]} {value.year} r.'


def organization_meta() -> dict[str, Any]:
    """Dane organizacji do nagłówka (sekcja `[print]` w config.ini)."""
    cfg = _config()
    city = (cfg.get('city') or '').strip()
    return {
        'uczelnia': (cfg.get('organization') or '').strip(),
        'skrot': (cfg.get('short_name') or '').strip(),
        'miejscowosc': city,
        'miejscowosc_miejscownik': (cfg.get('city_locative') or '').strip() or city,
        'adres_uczelni': (cfg.get('address') or '').strip(),
        'logo': _logo_uri(cfg.get('logo') or ''),
    }


# --- definicje wydruków (x-print) -----------------------------------------

@dataclass(frozen=True)
class PrintEntry:
    template: str
    label: str
    title: str | None = None
    tasks: tuple[str, ...] = ()
    when: tuple[tuple[str, tuple[str, ...]], ...] = ()
    schema_file: str | None = None


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if v is not None)
    return (str(value),)


def parse_x_print(schema: Mapping[str, Any] | None, schema_file: str | None = None) -> list[PrintEntry]:
    """Wpisy `x-print` schematu (obiekt, lista albo `{"templates": [...]}`)."""
    raw = (schema or {}).get('x-print')
    if isinstance(raw, Mapping) and isinstance(raw.get('templates'), list):
        raw = raw['templates']
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    entries = []
    for item in raw:
        if isinstance(item, str):
            item = {'template': item}
        if not isinstance(item, Mapping) or not str(item.get('template') or '').strip():
            continue
        template = str(item['template']).strip()
        when = item.get('when') if isinstance(item.get('when'), Mapping) else {}
        entries.append(PrintEntry(
            template=template,
            label=str(item.get('label') or template.removesuffix(PRINT_SUFFIX)),
            title=str(item['title']) if item.get('title') else None,
            tasks=_as_tuple(item.get('tasks')),
            when=tuple((str(path), _as_tuple(expected)) for path, expected in when.items()),
            schema_file=schema_file,
        ))
    return entries


def get_path(data: Mapping[str, Any] | None, path: str) -> Any:
    """Wartość pola `Sekcja/Pole` (albo `Pole`) z danych formularza."""
    node: Any = data or {}
    for part in path.split('/'):
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


def entry_matches(entry: PrintEntry, bpmn_task_id: str | None, data: Mapping[str, Any] | None) -> bool:
    if entry.tasks and bpmn_task_id not in entry.tasks:
        return False
    for path, expected in entry.when:
        value = get_path(data, path)
        if value in (None, '', [], {}):
            continue  # wariant nieustalony - nie odrzucamy
        if str(value) not in expected:
            return False
    return True


def _parse_json(content: Any) -> dict[str, Any] | None:
    if isinstance(content, Mapping):
        return dict(content)
    try:
        parsed = json.loads(content) if isinstance(content, str) else None
    except ValueError:
        return None  # schemat z Jinja (Spiff-Arena) - bez x-print
    return parsed if isinstance(parsed, dict) else None


def version_entries(forms: Mapping[str, Any], form_files: Mapping[str, Any],
                    bpmn_task_id: str | None) -> list[PrintEntry]:
    """Wpisy `x-print` dla zadania: z formularza zadania (rozszerzenie BPMN),
    a gdy zadanie nie wskazuje formularza - ze wszystkich schematów wersji
    (obiegi wariantowe, w których wariant wynika z danych, nie z BPMN)."""
    from wf.services.process_service import form_key

    linked = (form_files.get(bpmn_task_id or '') or {}).get('schema')
    if linked:
        keys = [form_key(str(linked))]
    else:
        keys = sorted(forms)
    entries: list[PrintEntry] = []
    seen: set[str] = set()
    for key in keys:
        for entry in parse_x_print(_parse_json(forms.get(key)), schema_file=key):
            if entry.template not in seen:
                seen.add(entry.template)
                entries.append(entry)
    return entries


# --- formatowanie pól (szablon standardowy i `fields` w szablonach) -------

@dataclass
class PrintField:
    key: str
    label: str
    kind: str = 'value'          # value | section | table
    value: str = ''
    fields: list['PrintField'] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def _resolve_ref(schema: Mapping[str, Any], root: Mapping[str, Any], depth: int = 0) -> Mapping[str, Any]:
    ref = schema.get('$ref') if isinstance(schema, Mapping) else None
    if not ref or not isinstance(ref, str) or not ref.startswith('#/') or depth > 10:
        return schema if isinstance(schema, Mapping) else {}
    node: Any = root
    for part in ref[2:].split('/'):
        node = node.get(part) if isinstance(node, Mapping) else None
    if not isinstance(node, Mapping):
        return {}
    merged = {**node, **{k: v for k, v in schema.items() if k != '$ref'}}
    return _resolve_ref(merged, root, depth + 1)


def _if_matches(condition: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    for name, rule in (condition.get('properties') or {}).items():
        if not isinstance(rule, Mapping):
            continue
        value = data.get(name)
        if 'const' in rule and value != rule['const']:
            return False
        if 'enum' in rule and value not in rule['enum']:
            return False
    return all(data.get(name) not in (None, '') for name in condition.get('required') or [])


def effective_properties(schema: Mapping[str, Any], data: Mapping[str, Any],
                         root: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Właściwości schematu z dołączonymi gałęziami `allOf`/`if-then`,
    których warunek spełniają dane (formularze wariantowe)."""
    root = root or schema
    schema = _resolve_ref(schema, root)
    props = {name: _resolve_ref(sub, root) for name, sub in (schema.get('properties') or {}).items()}
    for part in schema.get('allOf') or []:
        if not isinstance(part, Mapping):
            continue
        part = _resolve_ref(part, root)
        if 'if' in part:
            branch = part.get('then') if _if_matches(part['if'], data) else part.get('else')
        else:
            branch = part
        if isinstance(branch, Mapping):
            for name, sub in effective_properties(branch, data, root).items():
                props.setdefault(name, sub)
    return props


def _ordered(names: Iterable[str], ui: Mapping[str, Any]) -> list[str]:
    names = list(names)
    order = ui.get('ui:order') if isinstance(ui, Mapping) else None
    if not isinstance(order, list):
        return names
    head = [n for n in order if n in names]
    rest = [n for n in names if n not in head]
    if '*' in order:
        idx = order.index('*')
        before = [n for n in order[:idx] if n in names]
        after = [n for n in order[idx + 1:] if n in names]
        return before + [n for n in rest if n not in after] + after
    return head + rest


def _hidden(sub_ui: Mapping[str, Any], sub_schema: Mapping[str, Any]) -> bool:
    widget = sub_ui.get('ui:widget') if isinstance(sub_ui, Mapping) else None
    return widget == 'hidden' or bool(sub_schema.get('x-print-hidden'))


def _format_date(text: str) -> str:
    if match := _ISO_DATE.match(text):
        return f'{match[3]}.{match[2]}.{match[1]}'
    if match := _ISO_DATETIME.match(text):
        return f'{match[3]}.{match[2]}.{match[1]} {match[4]}:{match[5]}'
    return text


class _Labels:
    """Etykiety kodów: `oneOf`/`enum` ze schematu i słowniki `AsyncSelectWidget`
    (best effort - niedostępny słownik zostawia surową wartość)."""

    def __init__(self, db=None):
        self.db = db
        self._cache: dict[str, dict[str, str]] = {}

    def dictionary(self, endpoint: str) -> dict[str, str]:
        slug = endpoint.rstrip('/').rsplit('/', 1)[-1]
        if slug in self._cache:
            return self._cache[slug]
        labels: dict[str, str] = {}
        if self.db is not None and slug:
            try:
                from wf.services.dictionary_service import list_options
                labels = {str(o.value): str(o.label) for o in list_options(self.db, slug, limit=1000)}
            except Exception as exc:  # słownik zewnętrzny niedostępny - surowa wartość
                logger.info('Wydruk: słownik %s niedostępny (%s)', slug, exc)
        self._cache[slug] = labels
        return labels

    def value(self, value: Any, schema: Mapping[str, Any], ui: Mapping[str, Any]) -> str:
        if value is None or value == '':
            return ''
        if isinstance(value, bool):
            return 'Tak' if value else 'Nie'
        options = schema.get('oneOf') or schema.get('anyOf')
        if isinstance(options, list):
            for option in options:
                if isinstance(option, Mapping) and option.get('const') == value:
                    return str(option.get('title') or value)
        if isinstance(schema.get('enum'), list):
            names = schema.get('enumNames') or (ui.get('ui:enumNames') if isinstance(ui, Mapping) else None)
            if isinstance(names, list) and value in schema['enum']:
                idx = schema['enum'].index(value)
                if idx < len(names):
                    return str(names[idx])
        ui_options = ui.get('ui:options') if isinstance(ui, Mapping) else None
        if isinstance(ui_options, Mapping) and ui_options.get('endpoint'):
            label = self.dictionary(str(ui_options['endpoint'])).get(str(value))
            if label:
                return label
        if isinstance(value, (int, float)):
            return str(value).replace('.', ',') if isinstance(value, float) else str(value)
        return _format_date(str(value))


def format_fields(schema: Mapping[str, Any] | None, ui_schema: Mapping[str, Any] | None,
                  data: Mapping[str, Any] | None, *, db=None,
                  root: Mapping[str, Any] | None = None, labels: _Labels | None = None) -> list[PrintField]:
    """Pola formularza do druku w kolejności `ui:order`, bez pól ukrytych."""
    schema = schema or {}
    root = root or schema
    ui = ui_schema if isinstance(ui_schema, Mapping) else {}
    data = data if isinstance(data, Mapping) else {}
    labels = labels or _Labels(db)
    props = effective_properties(schema, data, root)
    if not props:
        return _fields_from_data(data, labels)
    result: list[PrintField] = []
    for name in _ordered(props, ui):
        sub = props[name]
        sub_ui = ui.get(name) if isinstance(ui.get(name), Mapping) else {}
        if _hidden(sub_ui, sub) or sub.get('type') == 'null':
            continue
        label = str(sub.get('title') or name)
        value = data.get(name)
        if sub.get('type') == 'object' or (sub.get('properties') and not sub.get('type')):
            nested = format_fields(sub, sub_ui, value if isinstance(value, Mapping) else {},
                                   root=root, labels=labels)
            if nested:
                result.append(PrintField(key=name, label=label, kind='section', fields=nested))
            continue
        if sub.get('type') == 'array':
            items = _resolve_ref(sub.get('items') or {}, root)
            items_ui = sub_ui.get('items') if isinstance(sub_ui.get('items'), Mapping) else {}
            rows = value if isinstance(value, list) else []
            if items.get('type') == 'object' or items.get('properties'):
                columns = [c for c in _ordered(items.get('properties') or {}, items_ui)
                           if not _hidden(items_ui.get(c) or {}, _resolve_ref(items['properties'][c], root))]
                result.append(PrintField(
                    key=name, label=label, kind='table',
                    columns=[str(_resolve_ref(items['properties'][c], root).get('title') or c) for c in columns],
                    rows=[[labels.value(row.get(c) if isinstance(row, Mapping) else None,
                                        _resolve_ref(items['properties'][c], root),
                                        items_ui.get(c) or {}) for c in columns] for row in rows]))
            else:
                text = ', '.join(labels.value(v, items, items_ui) for v in rows if v not in (None, ''))
                result.append(PrintField(key=name, label=label, value=text))
            continue
        result.append(PrintField(key=name, label=label, value=labels.value(value, sub, sub_ui)))
    return result


def _fields_from_data(data: Mapping[str, Any], labels: _Labels) -> list[PrintField]:
    """Zadanie bez formularza (schematu): pola wprost z danych, bez kluczy
    technicznych (`_...`, `chosen_path`)."""
    result: list[PrintField] = []
    for name, value in data.items():
        if not isinstance(name, str) or name.startswith('_') or name == 'chosen_path':
            continue
        if isinstance(value, Mapping):
            nested = _fields_from_data(value, labels)
            if nested:
                result.append(PrintField(key=name, label=name, kind='section', fields=nested))
        elif isinstance(value, list) and value and all(isinstance(v, Mapping) for v in value):
            columns = list(dict.fromkeys(k for row in value for k in row if isinstance(k, str)))
            result.append(PrintField(key=name, label=name, kind='table', columns=columns,
                                     rows=[[labels.value(row.get(c), {}, {}) for c in columns]
                                           for row in value]))
        elif isinstance(value, list):
            result.append(PrintField(key=name, label=name,
                                     value=', '.join(labels.value(v, {}, {}) for v in value)))
        else:
            result.append(PrintField(key=name, label=name, value=labels.value(value, {}, {})))
    return result


# --- środowisko Jinja -----------------------------------------------------

Source = Callable[[str], str | None]


def _safe_name(name: str) -> str | None:
    parts = [p for p in name.replace('\\', '/').split('/') if p not in ('', '.')]
    if not parts or any(p == '..' or p.startswith('.') for p in parts):
        return None
    return '/'.join(parts)


def dir_source(directory: str) -> Source:
    """Pliki `*.j2` z katalogu na dysku (bez wyjścia poza katalog)."""
    directory = os.path.realpath(directory)

    def read(name: str) -> str | None:
        safe = _safe_name(name)
        if not safe or not safe.endswith(TEMPLATE_SUFFIX):
            return None
        path = os.path.realpath(os.path.join(directory, *safe.split('/')))
        if not path.startswith(directory + os.sep) or not os.path.isfile(path):
            return None
        with open(path, encoding='utf-8') as f:
            return f.read()
    return read


def mapping_source(files: Mapping[str, str]) -> Source:
    normalized = {(_safe_name(k) or k): v for k, v in files.items()}
    return lambda name: normalized.get(_safe_name(name) or '')


def version_source(version) -> Source:
    """Szablony z plików wersji obiegu (`ProcessFileType.OTHER`, `*.j2`)."""
    return mapping_source({
        f.file_name: f.content for f in (getattr(version, 'files', None) or [])
        if f.file_type == ProcessFileType.OTHER and f.file_name.endswith(TEMPLATE_SUFFIX)})


class _ChainLoader(BaseLoader):
    def __init__(self, sources: list[Source]):
        self.sources = sources

    def get_source(self, environment, template):
        for source in self.sources:
            content = source(template)
            if content is not None:
                return content, None, lambda: False
        raise TemplateNotFound(template)


def _environment(sources: list[Source]) -> SandboxedEnvironment:
    env = SandboxedEnvironment(loader=_ChainLoader(sources), autoescape=True,
                               trim_blocks=False, lstrip_blocks=False)
    env.filters['data_pl'] = lambda v: polish_date(v) if isinstance(v, (date, datetime)) else _format_date(str(v or ''))
    return env


def template_sources(version, root: str | None) -> list[Source]:
    """Kolejność szukania szablonów: pliki wersji, biblioteka na dysku,
    szablony wbudowane huba (szablon standardowy)."""
    sources = [version_source(version)] if version is not None else []
    if root:
        sources.append(dir_source(library_dir(root)))
    sources.append(dir_source(_BUILTIN_DIR))
    return sources


@dataclass
class RenderedPrint:
    html: str
    title: str
    file_name: str
    template: str | None


def _file_name(title: str) -> str:
    base = re.sub(r'[^\w.-]+', '_', title, flags=re.UNICODE).strip('_.') or 'wydruk'
    return f'{base[:120]}.pdf'


def render_html(*, sources: list[Source], template: str | None, data: Mapping[str, Any] | None,
                meta: Mapping[str, Any], schema: Mapping[str, Any] | None = None,
                ui_schema: Mapping[str, Any] | None = None, title: str | None = None,
                db=None, template_source: str | None = None) -> RenderedPrint:
    """HTML wydruku. `template=None` - szablon standardowy; `template_source` -
    treść szablonu spoza wersji (podgląd z edytora areny)."""
    env = _environment(sources)
    data = dict(data or {})
    fields = format_fields(schema, ui_schema, data, db=db)
    context = {'f': data, 'dane': data, 'data_formularza': data, 'meta': dict(meta),
               'fields': fields, 'schema': schema or {}}
    try:
        if template_source is not None:
            tmpl = env.from_string(template_source)
        else:
            tmpl = env.get_template(template or _DEFAULT_TEMPLATE)
        html = tmpl.render(**context)
    except TemplateNotFound as exc:
        raise PrintError(f'Nie znaleziono szablonu wydruku: {exc.name}', status=404) from exc
    except TemplateError as exc:
        raise PrintError(f'Szablon wydruku nie renderuje się: {exc}') from exc
    doc_title = ' '.join(p for p in (title or str(meta.get('tytul') or 'wydruk'),
                                     str(meta.get('znak') or '')) if p)
    return RenderedPrint(html=html, title=doc_title, file_name=_file_name(doc_title),
                         template=template)


# --- HTML -> PDF ----------------------------------------------------------

def _url_fetcher():
    """Fetcher WeasyPrint przepuszczający tylko `data:`."""
    import weasyprint

    if hasattr(weasyprint, 'URLFetcher'):  # WeasyPrint >= 66
        return weasyprint.URLFetcher(allowed_protocols={'data'})

    def fetch(url, *args, **kwargs):  # starsze API: funkcja zwracająca słownik
        if not str(url).startswith('data:'):
            raise ValueError(f'Wydruk: zasób zewnętrzny zablokowany: {url[:80]}')
        return weasyprint.default_url_fetcher(url, *args, **kwargs)
    return fetch


def html_to_pdf(html: str) -> bytes:
    try:
        import weasyprint
    except ImportError as exc:  # pragma: no cover - obraz bez WeasyPrint
        raise PrintError('Generator PDF (WeasyPrint) nie jest zainstalowany.', status=503) from exc
    return weasyprint.HTML(string=html, url_fetcher=_url_fetcher(), base_url=None).write_pdf()
