# scripts/

Skrypty uruchamiane poza procesem aplikacji (FastAPI/`main.py`) — głównie
zadania cykliczne odpalane z crona, jako alternatywa dla mechanizmów
wymagających wejściowego żądania HTTP (np. callbacki, które z różnych
przyczyn bywają niedostępne lub niewiarygodne — zob. `edr.py` niżej).

Zakłada się system **Linux** (crontab, `bash`, aktywacja virtualenv przez
`source venv/bin/activate`) — skrypty nie są testowane/wspierane na Windows.

## Konwencja

Każde zadanie to para plików w tym katalogu:
- skrypt `*.py` z właściwą logiką, wołany z argumentem (`argparse`) określającym
  operację,
- wrapper `cron_*.sh`, który aktywuje virtualenv, ustawia katalog roboczy na
  korzeń repozytorium i woła skrypt `*.py` z odpowiednimi argumentami,
  przekierowując wynik do pliku logu.

Skrypty `*.py` muszą leżeć bezpośrednio w `scripts/` (jeden poziom pod
korzeniem repozytorium) — doklejają katalog nadrzędny (`sys.path.insert`) do
ścieżki importów, żeby zadziałały `import connector...`, `import core...`
itd. tak samo jak w `main.py`. Przeniesienie skryptu głębiej (np.
`scripts/edr/edr.py`) wymagałoby poprawienia tej ścieżki. Reguła dotyczy
tylko plików `.py` — podkatalogi z danymi (np. `process_definitions/` z
plikami BPMN/JSON Schema, czytanymi przez skrypt, nie importowanymi) są OK.

## Wykaz skryptów

### `edr.py` + `cron_edr.sh` — eDoręczenia

Obsługa skrzynki eDoręczeń (`connector/edoreczenia/edr_webcon.py`) metodą
odpytywania (polling) zamiast callbacków HTTP wywoływanych przez usługę
e-Delivery (`wf/routers/edr_api.py::*Callback`) — callbacki bywają problem
jest z ich dostarczeniem/konfiguracją, więc `edr.py` daje niezależną od tego
ścieżkę pobierania danych.

Operacje (`python3 scripts/edr.py <cmd>`):
- `inbox` — pobiera nowe wiadomości ze skrzynki i przekazuje je do Webcon
  (`inbox_to_webcon`),
- `send` — wysyła wiadomości zlecone z Webcon (`send_from_webcon`),
- `evidences` — pobiera potwierdzenia/dowody doręczenia (`fetch_evidences`),
- `test` — zapytania diagnostyczne do rejestru eDoręczeń (`SzukajInstytucji`),
  bez wysyłki/zapisu.

`cron_edr.sh` woła po kolei `inbox`, `send`, `evidences` i dopisuje wynik do
`$PROJECT_HOME/cron_edr.log`.

> **Alternatywa bez WEBCON (Paperless-ngx):** `edr.py`/`cron_edr.sh` piszą
> do WEBCON. Odpowiednik dla wdrożeń bez WEBCON (korespondencja w
> `wf.Document`/Paperless-ngx, proces BPMN w `wf`, kancelaria w
> `galwork-dashboard`) to `connector/edoreczenia/edr_paperless.py`
> (`sync_inbox`/`submit_staged_documents`/`poll_submitted_documents`/
> `fetch_evidences`) + router akcji `wf/routers/edr_paperless_api.py`
> (`/v1/wf/edr/*`, montowany od razu, pod tokenem Keycloak) - zob.
> `analiza_edr.md`. Osobny skrypt crona łączący te funkcje z WEBCON jak
> `cron_edr.sh` nie jest jeszcze napisany (`sync-inbox`/`refresh-evidences`
> da się już wywołać ręcznie przez API; `submit_staged_documents`/
> `poll_submitted_documents` nie są wystawione jako endpoint - zob.
> docstring `edr_paperless.py` - i czekają na wpis crona).

### `seed_edr_process.py` — model procesu „edr-korespondencja” (Paperless-ngx)

Jednorazowy (idempotentny) skrypt zakładający w bazie model procesu BPMN
wymagany przez `edr_paperless.py::sync_inbox` (bez niego start instancji
zgłasza `ProcessServiceError`). Pliki źródłowe —
`process_definitions/edr_korespondencja.bpmn` (jeden `userTask`
"Przegląd korespondencji", `candidateGroups="kancelaria"`) i jego formularz
(`przeglad.json`/`przeglad.uischema.json`) — są czytane i publikowane jako
nowa wersja `ProcessModel` dokładnie tak, jak robi to
`POST /process-models/.../versions` + `.../publish`
(`wf/routers/process_model_router.py`), tylko bez HTTP/Keycloak.

```bash
python3 scripts/seed_edr_process.py            # zakłada/aktualizuje i publikuje
python3 scripts/seed_edr_process.py --dry-run  # tylko pokazuje, co by zrobił
```

Uruchom go raz po wdrożeniu (i ponownie, jeśli zmienisz pliki w
`process_definitions/` — wykrywa zmianę przez `content_hash` i publikuje
nową wersję, cofając poprzednią).

### `clear_process_data.py` — czyszczenie danych procesów (testowe)

Usuwa z bazy definicje procesów, instancje, zadania, zdarzenia i powiązania;
opcjonalnie dokumenty, powiadomienia i liczniki numerów. Domyślnie **nic nie
usuwa** — pokazuje tylko raport (dry-run).

```bash
python3 scripts/clear_process_data.py                       # dry-run, wszystko
python3 scripts/clear_process_data.py --scope legacy        # tylko stare chosen_path
python3 scripts/clear_process_data.py --execute --yes       # faktyczne usunięcie
python3 scripts/clear_process_data.py --execute --yes --keep-documents
```

- `--scope legacy` wybiera modele, których BPMN zawiera `chosen_path == <liczba>`
  (definicje sprzed normalizacji na `Flow_*`); dokumenty są wtedy zachowywane.
- `--keep-documents` zostawia rejestr dokumentów, tylko odpina je od zadań
  (`document_version.human_task_id = NULL`). Domyślnie usuwane są wpisy
  dokumentów w bazie — **pliki w Paperless-ngx pozostają**.
- `--reset-sequences` czyści `document_number_sequence`,
- `--include-notifications` usuwa wszystkie powiadomienia,
- `--database-url` wskazuje inną bazę (np. `sqlite:////tmp/test.db`).

Kolejność usuwania jest zgodna z kluczami obcymi, a całość idzie w jednej
transakcji. Skrypt nie rusza użytkowników, jednostek organizacyjnych ani
globalnych zastępstw (tych bez `process_model_id`). **Przed `--execute` zrób
kopię bazy** — operacja jest nieodwracalna.

## Konfiguracja w cronie (Linux)

Skrypt wrapper zakłada strukturę katalogów taką jak na serwerze produkcyjnym
(`PROJECT_HOME=/srv/galwork`, repo w `$PROJECT_HOME/galwork-hub`, virtualenv w
`$PROJECT_HOME/venv`) — przy innym rozmieszczeniu popraw te trzy zmienne na
górze `cron_edr.sh`.

1. Upewnij się, że wrapper ma prawa wykonywania:

   ```bash
   chmod +x /srv/galwork/galwork-hub/scripts/cron_edr.sh
   ```

2. Dodaj wpis do crontaba (np. `crontab -e` na użytkowniku, pod którym ma
   działać zadanie — niekoniecznie root):

   ```cron
   */15 * * * * /srv/galwork/galwork-hub/scripts/cron_edr.sh
   ```

   Powyższy przykład odpytuje eDoręczenia co 15 minut; częstotliwość należy
   dopasować do wymagań (np. SLA na odbiór/potwierdzenie wiadomości).

3. Logi zadania trafiają do `$PROJECT_HOME/cron_edr.log` (dopisywane, bez
   rotacji — warto dodać wpis do `logrotate`, jeśli zadanie ma działać
   długoterminowo).

4. Logowanie samej aplikacji (`connector.edoreczenia.*`, poziom z
   `config.ini` [logging]) działa tu tak samo jak w `main.py` — każdy
   skrypt w tym katalogu woła `core.log.log_init()` **sam**, jeśli chce pisać
   do tego samego pliku co aplikacja (`main.py` robi to automatycznie, ale
   skrypt odpalany spoza `main.py` nie dostaje tego za darmo — zob.
   `logging.md`, sekcja 2.1).
