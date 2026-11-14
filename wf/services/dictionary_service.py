# wf/services/dictionary_service.py
# coding: utf-8
"""
Słowniki (pola `AsyncSelectWidget`/`TreeSelectWidget`)

TODO: przenieść do plików konfiguracyjnych, by ułatwić wdrażanie oraz
dodawanie nowych słowników bez zmiany kodu (np. w config/dictionaries.json).

Rejestr w tym module odwzorowuje każdy `slug` na jedno ze źródeł
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
     2026-11-13: to książka adresowa korespondencji (nadawcy i adresaci
     e-Doręczeń), nie kontrahenci - pisma przychodzą też od instytucji,
     które nie są kontrahentami. Kontrahenci są w ERPNext (zob. 6.).
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
  6. **ERPNext** (`ErpNextDictionary`, 2026-11-13) - słownik = jeden
     albo kilka Doctype w ERPNext (connector/erpnext), np. "kontrahenci" =
     dostawcy (`Supplier`) i odbiorcy (`Customer`).
  5. **Niepodłączone** (`NOT_CONNECTED`) - `source-name` wskazuje na system,
     dla którego w tym repozytorium nie ma jeszcze konektora (ERP,
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
from connector.erpnext.erpnext import get_connector as get_erpnext_connector
from connector.frappe_education.connector import get_connector as get_frappe_connector
from wf.models import Contact, ContactAddress, HumanTask, ProcessInstance, ProcessModel, ProcessModelVersion
from wf.schemas.wf_schemas import HumanTaskStatus
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
    source_name: str
    # 2026-11-13: tylko instancje z otwartym zadaniem o tej nazwie
    # (np. procedury skreślenia czekające na odwołanie).
    open_task_name: str | None = None


@dataclass(frozen=True)
class FrappeDictionary:
    """Słownik = Doctype w systemie dziekanatowym Frappe Education."""

    doctype: str
    label_field: str
    source_name: str

# TODO: przenieść do plików konfiguracyjnych, by ułatwić wdrażanie oraz
# dodawanie nowych słowników bez zmiany kodu (np. w config/dictionaries.json).

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
    # 2026-11-12: wariant formularza "Zgłoszenie" (dziekanat/prosba_studenta) -
    # kod rodzaju wybiera sekcje warunkowe jednego schematu (allOf/if/then).
    "rodzaj-pisma-studenta": TableDictionary("rodzaj-pisma-studenta", "Rodzaj pisma studenta"),
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
    # BEGIN 2026-11-13: decyzje z TODO/SLOWNIKI_BRAKUJACE.md (galwork-dev-jw)
    "powod-skreslenia": TableDictionary("powod-skreslenia", "Powód skreślenia z listy studentów"),
    "szablony-skreslen": TableDictionary("szablony-skreslen", "Szablony decyzji o skreśleniu"),
    "sale": TableDictionary("sale", "Sale"),
    "budynki": TableDictionary("budynki", "Budynki"),
    "klasa-zdarzen": TableDictionary("klasa-zdarzen", "Klasa zdarzeń IT"),
    "rodzaje-urlopow": TableDictionary("rodzaje-urlopow", "Rodzaje urlopów"),
    "jrwa": TableDictionary("jrwa", "Jednolity rzeczowy wykaz akt"),
    "kategoria-archiwizacji": TableDictionary("kategoria-archiwizacji", "Kategoria archiwalna"),
    "sprawy-formularze": TableDictionary("sprawy-formularze", "Formularze spraw"),
    "rejestr-systemow": TableDictionary("rejestr-systemow", "Rejestr systemów IT"),
    # -END
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
#    zaleznosci.md). "instytuty-uczelni" to alias tego samego obiegu co "instytuty"
# ---------------------------------------------------------------------
INTERNAL_DICTIONARIES: dict[str, InternalDictionary] = {
    "instytuty": InternalDictionary("instytuty", "instytuty"),
    "instytuty-uczelni": InternalDictionary("instytuty", "Instytuty Uczelni"),
    "kierunki": InternalDictionary("kierunki", "Kierunki"),
    "podpisy-elektroniczne": InternalDictionary("podpisy-elektroniczne", "Podpisy Elektroniczne"),
    "trm-terminarze": InternalDictionary("trm-terminarze", "[TRM] Terminarze"),
    "umowy-karta-kontrahenta": InternalDictionary("umowy-karta-kontrahenta", "[Umowy] Karta kontrahenta"),
    "teczki-spraw": InternalDictionary("teczki-spraw", "Teczki Spraw"),
    # BEGIN 2026-11-13
    "podrodzaj-umowy": InternalDictionary("podrodzaj-umowy", "Podrodzaj umowy"),
    "rejestr-serwerow": InternalDictionary("nserwer", "Rejestr serwerów"),
    "elementy-do-przegladu": InternalDictionary("slownik-elementow-do-przegladu", "Elementy do przeglądu"),
    "projekty": InternalDictionary("projekt", "Projekty"),
    "wnioski-zakupowe": InternalDictionary("wniosek-zakupowy", "Wnioski zakupowe"),
    "dokumenty-uczelni": InternalDictionary("dokumenty", "Dokumenty uczelni"),
    "procedury-skreslenia-czeka-na-odwolanie": InternalDictionary(
        "realizacja-procedury-skreslenia", "Procedury skreślenia czekające na odwołanie",
        open_task_name="Czekanie na odwołanie"),
    "szablony-planu-rzeczowo-finansowego": InternalDictionary(
        "szablon-planu-rzeczowo-finansowego-dzialu", "Szablony planu rzeczowo-finansowego"),
    "plany-rzeczowo-finansowe-dzialow": InternalDictionary(
        "plan-rzeczowo-finansowy-dzialu", "Plany rzeczowo-finansowe działów"),
    # -END
}

# ---------------------------------------------------------------------
# 4. Słowniki z systemu dziekanatowego (connector/frappe_education).
#    `label_field` to standardowe pole Doctype Frappe Education - jeśli w
#    konkretnej instalacji go nie ma, i tak spada na `name` (docname).
# ---------------------------------------------------------------------
FRAPPE_DICTIONARIES: dict[str, FrappeDictionary] = {
    "studenci": FrappeDictionary("Student", "student_name", "Studenci"),
    "rok-akademicki": FrappeDictionary("Academic Term", "term_name", "rok akademicki"),
    # 2026-11-13: kadra i przedmioty z Frappe Education, ogłoszenia z Frappe HR.
    "kadra": FrappeDictionary("Instructor", "instructor_name", "Kadra dydaktyczna"),
    "przedmioty": FrappeDictionary("Course", "course_name", "Przedmioty"),
    "ogloszenia-rekrutacji": FrappeDictionary("Job Opening", "job_title", "Ogłoszenia rekrutacji"),
}

# BEGIN 2026-11-13
@dataclass(frozen=True)
class ErpNextSource:
    """Jeden Doctype ERPNext w słowniku: pole etykiety i rola w etykiecie opcji."""

    doctype: str
    label_field: str
    role: str = ""
    filters: tuple[tuple[str, str, object], ...] = ()
    # "tylko aktywne" - zależy od Doctype (Location nie ma `disabled`)
    active: tuple[tuple[str, str, object], ...] = (("disabled", "=", 0),)


@dataclass(frozen=True)
class ErpNextDictionary:
    """Słownik z ERPNext (connector/erpnext). Opcje kilku Doctype są łączone;
    `value` = `name` dokumentu, a ten sam `name` w kilku Doctype to jedna
    opcja (np. firma, która jest i dostawcą, i odbiorcą)."""

    sources: tuple[ErpNextSource, ...]
    source_name: str
    # parametr zapytania -> pole Doctype, porównanie "like" (np. ?department=)
    query_fields: tuple[tuple[str, str], ...] = ()


# Środek trwały w użyciu: zatwierdzony, nie zlikwidowany i nie sprzedany.
_ASSET_ACTIVE = (("docstatus", "=", 1), ("status", "not in", ["Scrapped", "Sold"]))

# 6. Słowniki z ERPNext. Kontrahenci to dostawcy i odbiorcy - nie książka
#    adresowa e-Doręczeń (`adres-e-doreczen`).
ERPNEXT_DICTIONARIES: dict[str, ErpNextDictionary] = {
    "kontrahenci": ErpNextDictionary(
        (ErpNextSource("Supplier", "supplier_name", "dostawca"),
         ErpNextSource("Customer", "customer_name", "odbiorca")),
        "Kontrahenci (ERPNext)",
    ),
    # BEGIN 2026-11-13: struktura organizacyjna i środki trwałe w ERPNext
    # Pracownik = Employee; filtr działu
    # lub stanowiska w adresie pola, np. /pracownicy?designation=Radca%20prawny.
    "pracownicy": ErpNextDictionary(
        (ErpNextSource("Employee", "employee_name", active=(("status", "=", "Active"),)),),
        "Pracownicy (ERPNext)",
        query_fields=(("department", "department"), ("designation", "designation")),
    ),
    "lokalizacje": ErpNextDictionary(
        (ErpNextSource("Location", "location_name", active=()),),
        "Lokalizacje (ERPNext)",
    ),
    "srodki-trwale": ErpNextDictionary(
        (ErpNextSource("Asset", "asset_name", active=_ASSET_ACTIVE),),
        "Środki trwałe (ERPNext)",
        query_fields=(("category", "asset_category"), ("location", "location")),
    ),
    # Sprzęt IT = środki trwałe z kategorii zaczynającej się od "IT"
    # (konwencja nazw Asset Category przy wdrożeniu).
    "sprzet-it": ErpNextDictionary(
        (ErpNextSource("Asset", "asset_name", filters=(("asset_category", "like", "IT%"),),
                       active=_ASSET_ACTIVE),),
        "Sprzęt IT (ERPNext)",
        query_fields=(("category", "asset_category"), ("location", "location")),
    ),
    # -END
    # Konta zespołu 4 (koszty rodzajowe) z planu kont ERPNext, bez kont grupujących.
    "konta-kosztowe": ErpNextDictionary(
        (ErpNextSource("Account", "account_name", "konto",
                       (("account_number", "like", "4%"), ("is_group", "=", 0))),),
        "Konta kosztowe (zespół 4, ERPNext)",
    ),
}
# -END

# ---------------------------------------------------------------------
# 5. Źródła zewnętrzne bez konektora w tym repozytorium - zob.
#    spiffworkflow-core/zaleznosci.md, sekcja "odwołania zewnętrzne".
# ---------------------------------------------------------------------
NOT_CONNECTED: dict[str, str] = {
    "companies": "Pole 'Wybór spółki' nie ma jeszcze konektora do tego źródła - TODO: dodaj go w connector/.",
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
    if dictionary.open_task_name:
        # 2026-11-13: filtr kroku, w którym instancja teraz czeka.
        stmt = stmt.where(ProcessInstance.id.in_(
            select(HumanTask.process_instance_id).where(
                HumanTask.name == dictionary.open_task_name,
                HumanTask.status.in_((HumanTaskStatus.OPEN, HumanTaskStatus.CLAIMED)))))
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


# BEGIN 2026-11-13
def _erpnext_options(dictionary: ErpNextDictionary, q: str | None, limit: int,
                     params: dict[str, str] | None = None) -> list[OptionRead]:
    connector = get_erpnext_connector()
    extra = [[field, "like", f"%{params[name]}%"] for name, field in dictionary.query_fields
             if (params or {}).get(name)]
    labels: dict[str, str] = {}
    roles: dict[str, list[str]] = {}
    for source in dictionary.sources:
        filters: list[list] = ([list(f) for f in source.active] + [list(f) for f in source.filters]
                               + extra)
        if q:
            filters.append([source.label_field, "like", f"%{q}%"])
        try:
            rows = connector.get_list(source.doctype, filters=filters,
                                      fields=["name", source.label_field], limit=limit)
        except ConnectorConfigError as exc:
            raise DictionaryNotSeededError(
                f"ERPNext nie skonfigurowany ([erpnext] w config/config.ini) - "
                f"słownik '{dictionary.source_name}': {exc}") from exc
        except ConnectorError as exc:
            raise DictionaryError(f"ERPNext ({source.doctype}): {exc}") from exc
        for row in rows:
            name = row["name"]
            labels.setdefault(name, row.get(source.label_field) or name)
            roles.setdefault(name, []).append(source.role)
    options = [OptionRead(value=name, label=(f"{label} ({', '.join(r for r in roles[name] if r)})"
                                             if any(roles[name]) else label))
               for name, label in labels.items()]
    return sorted(options, key=lambda option: option.label.lower())[:limit]
# -END


def list_options(db: Session, slug: str, q: str | None = None, limit: int = 50,
                 params: dict[str, str] | None = None) -> list[OptionRead]:
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
    if slug in ERPNEXT_DICTIONARIES:  # 2026-11-13
        return _erpnext_options(ERPNEXT_DICTIONARIES[slug], q, limit, params)
    if slug in NOT_CONNECTED:
        raise DictionaryNotConnectedError(NOT_CONNECTED[slug])
    raise DictionaryNotFoundError(f"Nieznany słownik '{slug}'")
