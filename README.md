# galwork-hub


One API for many applications - central hub

**Aplha Version 0.0.4**

Serwis (hub integracyjny) łączący różne systemy przedsiębiorstwa/uczelni w
jedno API REST (FastAPI). Zamiast każdej aplikacji frontendowej integrującej
się bezpośrednio z ERP-em, systemem uczelnianym, systemem obiegu dokumentów
itd., integruje się raz - z tym hubem - a hub rozmawia z systemami
zewnętrznymi przez konektory.

> to repozytorium jest publikowanym, ograniczonym podzbiorem
> huba - zawiera tylko to, co jest aktualnie wdrażane i testowalne (`pm`,
> `wf`, autoryzacja Keycloak). W przygotowaniu aplikacja `edu`.

## Spis treści
- [Architektura](#architektura)
- [Aplikacje](#aplikacje)
- [Konektory](#konektory)
- [Stan implementacji](#stan-implementacji)
- [Co zostało do zrobienia](#co-zostało-do-zrobienia)
- [Jak uruchomić](#jak-uruchomić)
- [Struktura API](#struktura-api)

## Architektura

```
main.py            - tworzy FastAPI app, CORS, middleware logujący, montuje router.py pod /v1
router.py          - montuje sub-aplikacje każdej "aplikacji" (pm, wf, galwork-auth) pod /v1/<app>
core/               - konfiguracja (core/config.py czyta config/config.ini) i logowanie (core/log.py)
config/config.ini   - konfiguracja: [app], [db], [logging], [auth], [keycloak] + sekcje per konektor
db/                 - db/base.py (SQLAlchemy declarative Base), db/session.py (silnik + sesja)
alembic/            - migracje bazy (Alembic); alembic/env.py importuje modele, które mają być migrowane
crud/               - generyczny CRUDBase + konkretne operacje (na razie tylko dla User)
api/                - auth.py (logowanie/rejestracja przez Keycloak), dependencies.py (get_db), security.py
lib/                - lib/keycloak_utils.py: pomocnicze operacje na Keycloak Admin API
connector/          - konektory do systemów zewnętrznych (jeden podkatalog = jeden system)
pm/, wf/            - "aplikacje" domenowe (patrz niżej)
```

Każda "aplikacja" (`pm`, `wf`, ...) to osobna domena biznesowa,
montowana jako osobna pod-aplikacja FastAPI pod swoim prefiksem
(`/v1/pm`, `/v1/wf`) - zob. `router.py`. Dodanie nowej aplikacji
wymaga:

1. założenia katalogu aplikacji z podkatalogami `models/`, `schemas/`,
   `routers/`, ewentualnie `services/`, `calculators/`, `forms/`,
2. dodania potrzebnych konektorów w `connector/` (zob. `connector/base.py`),
3. dodania importu modeli do `alembic/env.py` i uaktualnienia bazy,
4. dodania routerów aplikacji w `router.py` (zamontowania pod-aplikacji FastAPI
   pod `/v1/<app>`).

Testy: `python -m unittest discover -s tests` (SQLite w pamięci, podmiana
`get_db` i `get_current_wf_user` we wszystkich zamontowanych
pod-aplikacjach - `dependency_overrides` działa per aplikacja FastAPI).

## Aplikacje

### `pm` - zarządzanie projektami
Kompletna i wewnętrznie spójna aplikacja, która oferuje:
- **modele** (`pm/models/`): `Project`, `Milestone`/`MilestoneForecast`,
  `Risk`, `WbsItem`/`ScheduleTask`, `BusinessCaseVariant`, `EvmSnapshot`,
  `DpciScore`, `FormInstance` - wszystkie zarejestrowane w `alembic/env.py`.
- **kalkulatory** (`pm/calculators/`): CPM (ścieżka krytyczna), CCPM
  (łańcuch krytyczny), PERT, EVM (Earned Value Management), ROI/NPV,
  DPCI, EMV ryzyka, analiza trendu (MTA).
- **formularze** (`pm/forms/definitions/`): 20 szablonów JSON Schema
  pokrywających cały cykl życia projektu metodycznego - od karty projektu
  SMART po raport końcowy i retrospektywę, plus formularze ITIL
  (incydent/problem/zmiana/SLA).
- **routery** (`pm/routers/`): `projects.py` (CRUD projektów),
  `dashboard.py` (zestawienia EVM/ryzyka/harmonogramu), `forms.py`
  (katalog i wypełnianie formularzy), `calc.py` (udostępnienie
  kalkulatorów przez API).

### `wf` - obieg dokumentów / workflow
Silnik obiegu dokumentów oparty na SpiffWorkflow (BPMN) + integracja z
Paperless-ngx (przechowywanie plików) + Keycloak (autoryzacja).
Modele (`wf/models/wf.py`) i schematy (`wf/schemas/wf_schemas.py`) opisują
pełną domenę: definicje procesów i ich wersje, instancje procesów, zadania
użytkowników (z kandydatami/zastępstwami), dokumenty i ich wersje
(z integracją Paperless), komentarze, zdarzenia audytowe, zadania w tle,
powiadomienia.

API, silnik i usługi (`wf/routers/*.py`, `wf/engine.py`, `wf/services/process_service.py`):
- start instancji parsuje BPMN z `ProcessModelFile` i dociąga `UserTask`,
- `human_task_router.py` umie claim (z `expected_lock_version`; konflikt ->
  409), complete z walidacją JSON Schema, suspend/resume/cancel,
- formularze Spiff-Arena: `GET /tasks/{id}/form` i
  `GET /process-models/{key}/start-form` zwracają JSON Schema + uiSchema dla
  react-jsonschema-form (szablony Jinja renderowane danymi zadania), a
  `complete` waliduje ten sam, wyrenderowany schemat (`tests/test_wf_forms.py`),
- `/tasks/my` filtrowane jest zapytaniem SQL (zadania otwarte, podjęte przez
  siebie, widoczne kandydatom z `candidateGroups`), a nie listą wszystkich
  zadań w systemie,
- autoryzacja: token Keycloak -> `CurrentUser` (`wf/dependencies.py`),
  zapis definicji procesów tylko dla grup z `[keycloak] admin_groups`.

**Modele `wf` są przenośne** między PostgreSQL i SQLite (UUID zamiast
`postgresql.UUID`, JSON z wariantem JSONB, brak `gen_random_uuid()` i
indeksów GIN wymuszających PostgreSQL) - migracja przechodzi round-trip na
obu bazach.

### `galwork` (auth)
`api/auth.py` (`/register`, `/login`, `/me`) - jedyny mechanizm
uwierzytelniania w tym repozytorium jest oparty na **Keycloaku**: token
zwracany przez `/auth/login` jest tokenem Keycloaka, walidowanym tak samo
jak w `api/security.validate_token`. Moduł `wf` korzysta z tego samego
mechanizmu (`api/security.py` -> `get_current_principal` -> `CurrentUser`,
zob. `wf/dependencies.py`) i jednej tabeli użytkowników
(`models.user.User`, kolumna `keycloak_sub`). Alternatywny mechanizm
oparty na własnych tokenach API (`lib/auth.py`: `TokenOwner`/`CheckToken`)
został usunięty razem z aplikacją `edu`, która była jego jedynym aktywnym
użytkownikiem.

## Konektory

Katalog `connector/` zawiera integracje z systemami zewnętrznymi. Od
2026-10-04 obowiązuje wspólny wzorzec w `connector/base.py`
(`BaseConnector`, `ConnectorConfig`) - nowe konektory powinny po nim
dziedziczyć.

> Ten publikowany podzbiór zawiera tylko konektory używane przez `pm`/`wf`.
> Konektory specyficzne dla usuniętej aplikacji `edu` (`u10`,
> `mlegitymacje`) oraz nieużywane jeszcze przez żadną aplikację (`polon`,
> `optima`, `zus`, `redmine`, `erpnext`, `n8n`) zostały usunięte - prace nad
> nimi trwają w pełnej, niepublikowanej wersji repozytorium.

| Konektor | System | Rodzaj integracji | Status |
|---|---|---|---|
| `webcon` | WEBCON BPS | REST + OAuth2 | używany przez `edoreczenia`; baza EDR w osobnej sekcji `[edr_db]` |
| `edoreczenia` | e-Doręczenia (rządowa skrzynka doręczeń) | REST + certyfikat klienta | używany przez `wf/routers/edr_api.py`; `router_edr` nie jest jeszcze montowany; baza z `[edr] test_db=false` (domyślnie produkcyjna, nie testowa) |
| `signius` | Signius (podpis elektroniczny) | REST + PKCS12 | używany przez `wf/routers/signius_api.py` |
| `frappe_education` | Frappe Education | REST, `FrappeEducationConnector(BaseConnector)` | używany przez `wf/routers/signius_api.py` (dokumenty/korespondencja); zawiera tylko `connector.py` - pozostałe moduły (słowniki, oceny, wydruki...) były specyficzne dla `edu` i zostały usunięte |

Konfiguracja konektorów - sekcje w `config/config.ini` (jedna sekcja per
konektor, np. `[signius]`, `[frappe_education]`, `[edr_db]`) - **nie** przez
`core/config.py` (ten plik definiuje sztywny, wymagany `Settings` dla
rzeczy krytycznych do startu huba: `app`, `db`, `logging`, `auth`,
`keycloak`; konektory są opcjonalne, więc czytają swoją sekcję samodzielnie,
przez `BaseConnector.load_ini_section()` albo wprost `configparser`).

## Stan implementacji

| Obszar | Stan |
|---|---|
| Core (config, logowanie, DB, routing) | działa; CORS z `[app]`, URL bazy ze `[db]`/`DATABASE_URL`; baza produkcyjna: PostgreSQL, `[db] name=cm_msqlite.db` to wariant testowy |
| `pm` | kompletny: modele, migracje, kalkulatory, formularze, routery |
| `wf` | modele przenośne PG/SQLite, routery + **silnik BPMN podłączony** (start/claim/complete/suspend/resume/cancel), autoryzacja Keycloak; |
| Konektory | brak importów łączących się z bazą na starcie, hasła poza repo (`[edr_db]`, `[signius]`, `[frappe_education]`) |
| Autoryzacja | tylko Keycloak - `wf` i `galwork` (`api/auth.py`) na jednej tabeli `users` |
| EDR (`wf/routers/edr_api.py`) |  `/ade*` wymaga tokenu, `/webcon_*` i `/evidences/webcon` dodatkowo grupy admina; **router niezamontowany** (decyzja wdrożeniowa) |
| Testy automatyczne | `tests/` - uruchom `python -m unittest discover -s tests`; brak realnego Keycloaka - ta ścieżka jest atrapowana |


## Jak uruchomić

### Wymagania
- Python 3.11+ (projekt rozwijany pod 3.12/3.14)
- zależności z `requirements.txt`: `pip install -r requirements.txt`

### Konfiguracja
Skopiować/uzupełnić `config/config.ini` - sekcje `[app]`, `[db]`,
`[logging]`, `[keycloak]` są **wymagane** (bez nich `core/config.py` nie
wystartuje - `Settings` je waliduje). Sekcje per konektor (`[edr_db]`,
`[edr]`, `[signius]`, `[frappe_education]`, ...) są potrzebne tylko jeśli
dana integracja jest używana.

**Uwaga:** `configparser` (używany przez `core/config.py`) rzuca
`DuplicateSectionError`, jeśli sekcja `[x]` pojawi się dwa razy, a `ConfigObj`
scala duplikaty po cichu - każdą sekcję trzymaj w jednym miejscu pliku.
Hasła w pliku są celowo puste: wgrywaj je ze zmiennych środowiskowych
(`<SEKCJA>_<KLUCZ>`, np. `EDR_DB_PASSWORD`, `SIGNIUS_P12_PASSWORD`), które
mają pierwszeństwo przed plikiem.

### Baza danych
Domyślnie SQLite (plik wskazany w `[db] name=`), docelowo PostgreSQL. Adres
bazy można nadpisać zmienną `DATABASE_URL` - migracja czyta ją pierwszą.
Migracje:
```sh
./alembic_up.sh "opis rewizji"   # alembic revision --autogenerate + upgrade head
```
Od 2026-10-05 `alembic/env.py` rejestruje **także modele `wf`**, a modele są
przenośne między PostgreSQL i SQLite, więc `alembic check` jest czysty na
obu bazach (indeksy GIN i wyrażeniowe `DESC` są ignorowane przy
autogenerowaniu, bo Alembic nie umie ich porównać). Round-trip:
```sh
export DATABASE_URL="postgresql://<uzytkownik>:<haslo>@localhost:5432/<baza>"
alembic downgrade base && alembic upgrade head && alembic check
```

### Uruchomienie serwera
```sh
python main.py        

# uvicorn na 0.0.0.0:8086
```
albo w trybie deweloperskim z automatycznym przeładowaniem:
```sh
uvicorn main:app --reload --port 8086
```
Dokumentacja API (Swagger) pod `/v1/docs` (zob. `v1router` w `router.py`,
`app.mount("/v1", v1router)` w `main.py`), health-check pod `/health`.

## Struktura API

Wszystko pod `/v1/<app>`:

| Prefiks | Aplikacja | Przykładowe ścieżki |
|---|---|---|
| `/v1/galwork` | logowanie (Keycloak) | `/auth/register`, `/auth/login`, `/auth/me` |
| `/v1/pm` | zarządzanie projektami | `/api/projects`, `/api/projects/{id}/dashboard`, `/api/forms` |
| `/v1/wf` | obieg dokumentów | `/process-models`, `/process-instances`, `/tasks/my`, `/notifications`, `/` (kalendarz) |

`wf/routers/edr_api.py` (wyszukiwarka e-Delivery, akcje Webcon) jest
przygotowany, ale **niezamontowany** - patrz "Co zostało do zrobienia".

Pełna, zawsze aktualna lista endpointów - w Swaggerze pod `/v1/docs` po
uruchomieniu serwera.

## Licencja i Prawa Autorskie (Licensing)

Ten projekt jest oprogramowaniem Open Source udostępnianym na warunkach **Mozilla Public License 2.0 (MPL 2.0)**.

### Zasady licencjonowania dla użytkowników i wdrożeniowców:
* **Użycie komercyjne i wdrożenia:** Możesz swobodnie używać, wdrażać i integrować `galwork-hub` w swoich środowiskach uczelnianych, firmowych i komercyjnych.
* **Brak efektu wirusowego dla systemów zewnętrznych (No Viral Copyleft):** Połączenie `galwork-hub` z Twoimi własnymi systemami, bazami danych czy aplikacjami (tworzenie tzw. *Larger Work*) **nie zobowiązuje Cię** do otwierania kodu Twoich prywatnych systemów.
* **Obowiązek przekazywania poprawek (File-Level Copyleft):** Jeśli dokonasz bezpośrednich zmian lub poprawek w istniejącym kodzie źródłowym `galwork-hub`, masz prawny obowiązek udostępnić te modyfikacje społeczności na licencji MPL 2.0.
* **Informacja o autorstwie:** Zachowaj oryginalne nagłówki praw autorskich w plikach źródłowych oraz informację o wykorzystaniu oprogramowania `galwork-hub`.

Pełny tekst licencji znajduje się w pliku [LICENSE](LICENSE).

---

## Wkład w rozwój (Contributing)

Chcesz pomóc w rozwoju `galwork-hub`? Przeczytaj nasze wytyczne w pliku [CONTRIBUTING.md](CONTRIBUTING.md).
