# wf/services/dictionary_service.py
# coding: utf-8
"""
Słowniki (pola `AsyncSelectWidget`/`TreeSelectWidget`) dla formularzy
obiegów migrowanych z Webcona do RJSF.

Kontekst: migracja opisana w `rjsf.md`/`x-webcon.md` (katalog
`spiffworkflow-core`) zamieniła macierz atrybutów Webcona na
`x-permissions`, a pola słownikowe (`x-webcon.type` = "Pole wyboru"/"Osoba
lub grupa"/"Spółka"/"Drzewo wyboru") bez zaszytego `oneOf` dostały w
`*-uischema.json` widget `AsyncSelectWidget`/`TreeSelectWidget` z
`ui:options.endpoint` wskazującym na backend (`/v1/wf/dictionaries/<slug>`
albo `/v1/wf/users`). Lista tych słowników i ich źródeł jest spisana w
`spiffworkflow-core/zaleznosci.md` (ten plik jest "wykonaniem" tamtej
analizy po stronie huba).

Rejestr w tym module odwzorowuje każdy `slug` na jedno z pięciu źródeł
danych:

  1. **Tabela** (`TableDictionary`) - słownik płaski (kod/etykieta) w
     tabeli `dictionary_entry` (wf/models/wf.py::DictionaryEntry,
     wf/services/dictionary_entry_service.py) - (2026-10-18).
     DOCELOWY wariant dla słowników bez bogatej struktury; nowe słowniki
     płaskie powinny lądować TU, nie w `INTERNAL_DICTIONARIES`.
  2. **Kontrahenci i adresy** (`ContactAddressDictionary`) - słownik =
     adresy (`ContactAddress` JOIN `Contact`, wf/models/wf.py) - zob.
     (2026-10-19). DOCELOWY wariant dla słowników o strukturze
     "tożsamość + adres(y)" (dziś: "adres-e-doreczen"; "Kontrahent"/
     "[Umowy] Karta kontrahenta" z `INTERNAL_DICTIONARIES` mogą dostać tę
     samą migrację później, jeśli ich struktura się do tego nadaje).
  3. **Obieg wewnętrzny** (`InternalDictionary`) - słownik to zwykły,
     opublikowany `ProcessModel` w tym hubie (proces "Krok słownikowy" albo
     inny samodzielny obieg, np. "Lista terminów", "Kontrahent"). Jeden
     rekord słownika = jedna `ProcessInstance`; jako (value, label) używamy
     uniwersalnie `ProcessInstance.id`/`ProcessInstance.title` - te dwie
     kolumny istnieją dla KAŻDEGO procesu w hubie, więc nie trzeba znać
     (niespójnych między obiegami) nazw pól formularza danego słownika -
     zob. przegląd pól w `spiffworkflow-core/zaleznosci.md` ("Nazwa" /
     "Nazwa firmy" / "Firma lub skrót nazwy" / "Krótki opis sprawy" -
     każdy obieg nazywał to inaczej, `title` jest wspólnym mianownikiem).
     PRZESTARZAŁE dla słowników płaskich (zob. 1.) i dla "tożsamość+adres"
     (zob. 2.) - zostaje dla pozostałych słowników o bogatszej strukturze,
     do czasu własnej migracji każdego z nich.
  4. **Frappe Education** (`FrappeDictionary`) - słownik odpowiada Doctype
     w systemie dziekanatowym (connector/frappe_education), np. "Studenci".
  5. **Niepodłączone** (`NOT_CONNECTED`) - `source-name` wskazuje na system,
     dla którego w tym repozytorium nie ma jeszcze konektora (ERP Optima,
     EZD, "Spółki" uczelni) - zwracamy jawny błąd 501 z wyjaśnieniem,
     zamiast fałszywie pustej listy (żeby nie wyglądało to na "słownik jest
     pusty", gdy w rzeczywistości nikt go jeszcze nie podłączył).

`process_model_key` jest tu umowny - taki klucz powinien dostać
`ProcessModel` w chwili zaimportowania danego obiegu-słownika do huba
(`POST /process-models` + `.../publish`, zob.
wf/routers/process_model_router.py, albo skrypt wzorowany na
scripts/seed_edr_process.py). Dopóki dany słownik nie zostanie
zaimportowany, endpoint zwraca 503 - ten sam wzorzec co już istniejące
konektory (connector/frappe_education, connector/signius) dla "sekcja w
config.ini nieskonfigurowana": "jeszcze nie podłączone", nie "zepsute".
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from connector.base import ConnectorConfigError, ConnectorError
from connector.frappe_education.connector import get_connector as get_frappe_connector
from wf.models import Contact, ContactAddress, ProcessInstance, ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import OptionRead
from wf.services.dictionary_entry_service import list_entries as list_dictionary_entries


class DictionaryError(Exception):
    """Błąd domenowy słownika - router tłumaczy go na kod HTTP."""


class DictionaryNotFoundError(DictionaryError):
    """Nieznany `slug` (nie ma go w żadnym rejestrze poniżej)."""


class DictionaryNotSeededError(DictionaryError):
    """Słownik wewnętrzny jeszcze nie zaimportowany do huba (brak
    opublikowanego `ProcessModel` o umówionym kluczu), albo zewnętrzny
    konektor (Frappe Education) nieskonfigurowany."""


class DictionaryNotConnectedError(DictionaryError):
    """Słownik, dla którego w tym repozytorium nie ma (jeszcze) żadnego
    konektora - zob. `NOT_CONNECTED` poniżej."""


@dataclass(frozen=True)
class TableDictionary:
    """Słownik płaski = wiersze `dictionary_entry` o danym `dictionary_key`
    (zob. wf/models/wf.py::DictionaryEntry). `dictionary_key` NIE musi być
    tym samym stringiem co `slug` w rejestrze poniżej (choć w praktyce
    będzie - trzymane rozdzielnie, żeby nie zakładać tej równości)."""

    dictionary_key: str
    source_name: str


@dataclass(frozen=True)
class ContactAddressDictionary:
    """Słownik = adresy kontrahentów (`ContactAddress` JOIN `Contact`, zob.
    wf/models/wf.py). Jeden wiersz `ContactAddress` = jedna opcja; `value`
    jest `ContactAddress.id` (nie `Contact.id`), bo dictionary opisuje
    KONKRETNY adres, a jeden kontrahent może mieć kilka."""

    source_name: str


@dataclass(frozen=True)
class InternalDictionary:
    """Słownik = opublikowany `ProcessModel` w tym hubie."""

    process_model_key: str
    source_name: str  # x-webcon.source-name oryginalnego pola (dokumentacyjnie)


@dataclass(frozen=True)
class FrappeDictionary:
    """Słownik = Doctype w systemie dziekanatowym Frappe Education."""

    doctype: str
    label_field: str
    source_name: str


# ---------------------------------------------------------------------
# 1. Słowniki płaskie w tabeli `dictionary_entry` (decyzja 2026-10-18) -
#    DOCELOWE miejsce dla nowych słowników kod/etykieta. "dzialy" i
#    "kategorie-spraw" przeniesione tu jako pilot z `INTERNAL_DICTIONARIES`
#    (zob. 2. poniżej) - reszta słowników płaskich z tamtej listy czeka na
#    tę samą migrację (ręczne przeniesienie wierszy + zmiana rejestracji
#    tutaj, jeden słownik na raz, żeby nie migrować wszystkiego naraz bez
#    sprawdzenia na pilocie).
# ---------------------------------------------------------------------
TABLE_DICTIONARIES: dict[str, TableDictionary] = {
    "dzialy": TableDictionary("dzialy", "Działy"),
    "kategorie-spraw": TableDictionary("kategorie-spraw", "Kategorie spraw"),
    #  2026-10-20: zakupy.md ("Reguły akceptacji") - "rodzaj-wydatku" ma
    # ustalone z góry dwie wartości (koszt operacyjny/inwestycja, zob.
    # scripts/seed_zakupy.py), "projekt-mpk" jest administrowany ręcznie
    # (ekran "Słowniki" w galwork-arena) - referencja do projektów/MPK z ERP,
    # nie ich systemem rekordu (zob. zakupy.md, "Co zostaje poza Nodavią").
    "rodzaj-wydatku": TableDictionary("rodzaj-wydatku", "Rodzaj wydatku"),
    "projekt-mpk": TableDictionary("projekt-mpk", "Projekt / MPK"),
    # 2026-11-02: słowniki ŚCIŚLE płaskie z galwork-models/slowniki i
    # spiffworkflow/slowniki - formularz miał tylko ID + jedno pole nazwy
    # (Nazwa/Kod/...) + opcjonalnie Opis + Aktywny (Opis trafia do
    # `extra["opis"]`). Klucz = nazwa folderu obiegu-słownika. Pierwsze dwa
    # PRZENIESIONE z `INTERNAL_DICTIONARIES` (0 rekordów w hubie - skrypt
    # scripts/seed_flat_dictionaries.py przenosi ewentualne instancje).
    "dostawcy-uslug-podpisu-elektronicznego": TableDictionary("dostawcy-uslug-podpisu-elektronicznego", "Dostawcy usług podpisu elektronicznego"),
    "slownik-tematow": TableDictionary("slownik-tematow", "Słownik tematów"),
    "grupa-formularzy": TableDictionary("grupa-formularzy", "Grupa formularzy"),
    "kraj": TableDictionary("kraj", "Kraj"),
    "oplaty": TableDictionary("oplaty", "Opłaty"),
    "polon-academicsemesters": TableDictionary("polon-academicsemesters", "POLON – academicSemesters"),
    "polon-documenttype": TableDictionary("polon-documenttype", "POLON – documentType"),
    "polon-fosforms": TableDictionary("polon-fosforms", "POLON – fosForms"),
    "polon-foslevels": TableDictionary("polon-foslevels", "POLON – fosLevels"),
    "polon-fosprofiles": TableDictionary("polon-fosprofiles", "POLON – fosProfileS"),
    "polon-institutions": TableDictionary("polon-institutions", "POLON – Institutions"),
    "polon-professionaltitles": TableDictionary("polon-professionaltitles", "POLON – professionalTitles"),
    "poziom-studiow": TableDictionary("poziom-studiow", "Poziom studiów"),
    "roznice-programowe": TableDictionary("roznice-programowe", "Różnice programowe"),
    "semestry": TableDictionary("semestry", "Semestry"),
    "typ-studiow": TableDictionary("typ-studiow", "Typ studiów"),
    "typ-studiow-kierunku": TableDictionary("typ-studiow-kierunku", "Typ studiów (kierunek)"),
    "zwolnienie-z-oplat": TableDictionary("zwolnienie-z-oplat", "Zwolnienie z opłat"),
    "jednostki-miary": TableDictionary("jednostki-miary", "Jednostki miary"),
    "kategoria-pilnosci": TableDictionary("kategoria-pilnosci", "Kategoria pilności"),
    "kategorie-kosztow-materialnych-i-uslug": TableDictionary("kategorie-kosztow-materialnych-i-uslug", "Kategorie – koszty materialne i usługi"),
    "rodzaj-br": TableDictionary("rodzaj-br", "Rodzaj B/R"),
    "termin-ab": TableDictionary("termin-ab", "Termin A/B"),
    "v": TableDictionary("v", "V"),
    "lista-grup-odpadow": TableDictionary("lista-grup-odpadow", "Lista grup odpadów"),
    "lista-podgrup-odpadow": TableDictionary("lista-podgrup-odpadow", "Lista podgrup odpadów"),
    "lista-pojazdow": TableDictionary("lista-pojazdow", "Lista pojazdów"),
    "okres-przegladu": TableDictionary("okres-przegladu", "Okres przeglądu"),
    "rodzaj-zamierzenia-budowlanego": TableDictionary("rodzaj-zamierzenia-budowlanego", "Rodzaj zamierzenia budowlanego"),
    "slownik-wynikow-przegladu-spp": TableDictionary("slownik-wynikow-przegladu-spp", "Słownik wyników przeglądu SPP"),
    "wykaz-osob-wykonujacych-przeglad": TableDictionary("wykaz-osob-wykonujacych-przeglad", "Wykaz osób wykonujących przegląd"),
    "system-operacyjny": TableDictionary("system-operacyjny", "System operacyjny"),
    "forma-zajec": TableDictionary("forma-zajec", "Forma zajęć"),
    "forma-zaliczenia": TableDictionary("forma-zaliczenia", "Forma zaliczenia"),
    "kategoria-efektow-uczenia-sie": TableDictionary("kategoria-efektow-uczenia-sie", "Kategoria efektów uczenia się"),
    "oferta-edukacyjna": TableDictionary("oferta-edukacyjna", "Oferta"),
    "dzien-tygodnia": TableDictionary("dzien-tygodnia", "Dzień tygodnia"),
    "grupy-ad": TableDictionary("grupy-ad", "Grupy AD"),
    "stopien-pokrewienstwa": TableDictionary("stopien-pokrewienstwa", "Stopień pokrewieństwa"),
    "rodzaj-centrum-kosztow": TableDictionary("rodzaj-centrum-kosztow", "Rodzaj centrum kosztów"),
    "nowe-wyjasnienie": TableDictionary("nowe-wyjasnienie", "Nowe wyjaśnienie"),
    "slowa-kluczowe": TableDictionary("slowa-kluczowe", "Słowa kluczowe"),
    "statusy-dokumentow": TableDictionary("statusy-dokumentow", "Statusy dokumentów"),
    "typ-dokumentu": TableDictionary("typ-dokumentu", "Typ dokumentu"),
    "zbiory-rodo": TableDictionary("zbiory-rodo", "Zbiory RODO"),
    "stopien-niepelnosprawnosci": TableDictionary("stopien-niepelnosprawnosci", "Stopień niepełnosprawności"),
    "wyksztalcenie": TableDictionary("wyksztalcenie", "Wykształcenie"),
    "rodzaj-umowy": TableDictionary("rodzaj-umowy", "Rodzaj umowy"),
    "forma-platnosci": TableDictionary("forma-platnosci", "Forma płatności"),
    "rodzaj-wniosku": TableDictionary("rodzaj-wniosku", "Rodzaj wniosku"),
}

# ---------------------------------------------------------------------
# 2. Kontrahenci i adresy w `Contact`/`ContactAddress` (decyzja 2026-10-19).
#    "adres-e-doreczen" przeniesiony tu z `INTERNAL_DICTIONARIES` jako
#    pilot (zob. 3. poniżej) - "Kontrahent"/"[Umowy] Karta kontrahenta"
#    mogą dostać tę samą migrację później, jeśli ich struktura "tożsamość
#    + adres(y)" się do tego nadaje.
# ---------------------------------------------------------------------
CONTACT_ADDRESS_DICTIONARIES: dict[str, ContactAddressDictionary] = {
    "adres-e-doreczen": ContactAddressDictionary("Adres e-Doręczeń"),
}

# ---------------------------------------------------------------------
# 3. Słowniki wewnętrzne - obiegi skopiowane do spiffworkflow-core (zob.
#    zaleznosci.md). "instytuty-uczelni" to alias tego samego obiegu co
#    "instytuty" (x-webcon source-name różnił się między polami, choć
#    wskazywał na ten sam proces "Instytuty"). "dzialy"/"kategorie-spraw"
#    PRZENIESIONE do `TABLE_DICTIONARIES` (zob. 1. powyżej), "adres-e-doreczen"
#    do `CONTACT_ADDRESS_DICTIONARIES` (zob. 2. powyżej) - resztę
#    wymienionych tu słowników czeka migracja do jednego z tych dwóch
#    rejestrów, zależnie od struktury.
# ---------------------------------------------------------------------
INTERNAL_DICTIONARIES: dict[str, InternalDictionary] = {
    "instytuty": InternalDictionary("instytuty", "instytuty"),
    "instytuty-uczelni": InternalDictionary("instytuty", "Instytuty Uczelni"),
    "kierunki": InternalDictionary("kierunki", "Kierunki"),
    "podpisy-elektroniczne": InternalDictionary("podpisy-elektroniczne", "Podpisy Elektroniczne"),
    "trm-terminarze": InternalDictionary("trm-terminarze", "[TRM] Terminarze"),
    "umowy-karta-kontrahenta": InternalDictionary("umowy-karta-kontrahenta", "[Umowy] Karta kontrahenta"),
    "teczki-spraw": InternalDictionary("teczki-spraw", "Teczki Spraw"),
}

# ---------------------------------------------------------------------
# 4. Słowniki z systemu dziekanatowego (connector/frappe_education).
#    `label_field` to standardowe pole Doctype Frappe Education - jeśli w
#    konkretnej instalacji go nie ma, i tak spada na `name` (docname).
# ---------------------------------------------------------------------
FRAPPE_DICTIONARIES: dict[str, FrappeDictionary] = {
    "studenci": FrappeDictionary("Student", "student_name", "Studenci"),
    "rok-akademicki": FrappeDictionary("Academic Term", "term_name", "rok akademicki"),
}

# ---------------------------------------------------------------------
# 5. Źródła zewnętrzne bez konektora w tym repozytorium - zob.
#    spiffworkflow-core/zaleznosci.md, sekcja "odwołania zewnętrzne".
# ---------------------------------------------------------------------
NOT_CONNECTED: dict[str, str] = {
    "companies": (
        "Pole 'Wybór spółki' (x-webcon.type='Spółka') nie wskazuje na żaden "
        "zidentyfikowany obieg-słownik w spiffworkflow-all - prawdopodobnie "
        "lista spółek uczelni żyje w systemie ERP/finansowym. W tym "
        "repozytorium nie ma jeszcze konektora do tego źródła - dodaj go w "
        "connector/, gdy system źródłowy będzie znany."
    ),
    "k-kontrahenci": (
        "source-name='[K] Kontrahenci', source-type='REST Web Service' "
        "(pole 'z systemu ERP (Optima)' w kontrahent-k-uischema.json). W "
        "repozytorium nie ma jeszcze konektora Optima (connector/optima/ "
        "jest tylko zapowiedziane sekcją [optima] w config.ini.example)."
    ),
    "konta-kontrahentow": (
        "source-name='Konta kontrahentów' (pole 'Konto w Optimie' w "
        "kontrahent-k-uischema.json) - jak wyżej, brak konektora Optima."
    ),
    "ezd-jrwa": (
        "source-name='[EZD] JRWA', source-type='Baza MSSQL' - rejestr "
        "Jednolitego Rzeczowego Wykazu Akt zewnętrznego systemu EZD. Nie "
        "znaleziono odpowiadającego obiegu w spiffworkflow-all ani "
        "konektora w connector/ - do potwierdzenia u właściciela integracji "
        "EZD."
    ),
    "ezd-kategoria-archiwizacji": (
        "source-name='[EZD] Kategoria archiwizacji' - jak wyżej (system "
        "EZD, brak konektora)."
    ),
}


def _table_options(db: Session, dictionary: TableDictionary, q: str | None,
                    limit: int) -> list[OptionRead]:
    rows, _total = list_dictionary_entries(
        db, dictionary.dictionary_key, q=q, active_only=True, limit=limit
    )
    return [OptionRead(value=row.code, label=row.label) for row in rows]


def _contact_address_options(db: Session, dictionary: ContactAddressDictionary, q: str | None,
                              limit: int) -> list[OptionRead]:
    stmt = (
        select(ContactAddress)
        .join(Contact, ContactAddress.contact_id == Contact.id)
        .where(Contact.is_active.is_(True))
        .options(selectinload(ContactAddress.contact))
    )
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Contact.company_name.ilike(pattern),
                Contact.last_name.ilike(pattern),
                Contact.first_name.ilike(pattern),
                Contact.nip.ilike(pattern),
                ContactAddress.label.ilike(pattern),
                ContactAddress.ade.ilike(pattern),
            )
        )
    stmt = stmt.order_by(Contact.company_name, Contact.last_name, Contact.first_name).limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [OptionRead(value=str(row.id), label=_contact_address_label(row)) for row in rows]


def _contact_address_label(address: ContactAddress) -> str:
    contact = address.contact
    name = (
        contact.company_name
        or " ".join(filter(None, [contact.first_name, contact.last_name])).strip()
        or str(contact.id)
    )
    return f"{name} — {address.label}"


def _internal_options(db: Session, dictionary: InternalDictionary, q: str | None,
                       limit: int) -> list[OptionRead]:
    model = db.execute(
        select(ProcessModel).where(ProcessModel.key == dictionary.process_model_key)
    ).scalars().first()
    if model is None:
        raise DictionaryNotSeededError(
            f"Słownik '{dictionary.source_name}' (process_model.key="
            f"'{dictionary.process_model_key}') nie został jeszcze "
            f"zaimportowany do huba - brak ProcessModel o tym kluczu. "
            f"Zaimportuj obieg z spiffworkflow-core (POST /process-models + "
            f".../publish, albo skrypt wzorowany na "
            f"scripts/seed_edr_process.py) z process_model.key="
            f"'{dictionary.process_model_key}'."
        )
    stmt = (
        select(ProcessInstance)
        .join(ProcessModelVersion,
              ProcessInstance.process_model_version_id == ProcessModelVersion.id)
        .where(ProcessModelVersion.process_model_id == model.id)
    )
    if q:
        stmt = stmt.where(ProcessInstance.title.ilike(f"%{q}%"))
    stmt = stmt.order_by(ProcessInstance.title).limit(limit)
    instances = db.execute(stmt).scalars().all()
    return [OptionRead(value=str(instance.id), label=instance.title) for instance in instances]


def _frappe_options(dictionary: FrappeDictionary, q: str | None, limit: int) -> list[OptionRead]:
    connector = get_frappe_connector()
    filters = [[dictionary.label_field, "like", f"%{q}%"]] if q else None
    try:
        rows = connector.get_list(
            dictionary.doctype,
            filters=filters,
            fields=["name", dictionary.label_field],
            limit_page_length=limit,
        )
    except ConnectorConfigError as exc:
        raise DictionaryNotSeededError(
            f"Frappe Education nie skonfigurowane ([frappe_education] w "
            f"config/config.ini) - słownik '{dictionary.source_name}': {exc}"
        ) from exc
    except ConnectorError as exc:
        raise DictionaryError(f"Frappe Education ({dictionary.doctype}): {exc}") from exc
    return [
        OptionRead(value=row["name"], label=row.get(dictionary.label_field) or row["name"])
        for row in rows
    ]


def list_options(db: Session, slug: str, q: str | None = None, limit: int = 50) -> list[OptionRead]:
    """Opcje {value, label} dla jednego pola `AsyncSelectWidget`/
    `TreeSelectWidget` (`ui:options.endpoint` = `/v1/wf/dictionaries/{slug}`
    w `*-uischema.json`)."""
    if slug in TABLE_DICTIONARIES:
        return _table_options(db, TABLE_DICTIONARIES[slug], q, limit)
    if slug in CONTACT_ADDRESS_DICTIONARIES:
        return _contact_address_options(db, CONTACT_ADDRESS_DICTIONARIES[slug], q, limit)
    if slug in INTERNAL_DICTIONARIES:
        return _internal_options(db, INTERNAL_DICTIONARIES[slug], q, limit)
    if slug in FRAPPE_DICTIONARIES:
        return _frappe_options(FRAPPE_DICTIONARIES[slug], q, limit)
    if slug in NOT_CONNECTED:
        raise DictionaryNotConnectedError(NOT_CONNECTED[slug])
    raise DictionaryNotFoundError(f"Nieznany słownik '{slug}'")
