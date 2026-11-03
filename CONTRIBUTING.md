# Wytyczne dotyczące wnoszenia wkładu (Contributing Guidelines)

Dziękujemy za zainteresowanie rozwojem projektu **`galwork-hub`**! Praca społeczności jest kluczowa dla utrzymania wysokiej jakości oprogramowania.

---

## 1. Kodeks Postępowania (Code of Conduct)

Oczekujemy, że wszyscy uczestnicy projektu zachowają szacunek, profesjonalizm i otwartość na dyskusję merytoryczną.

---

## 2. Gdzie toczy się rozwój projektu?

* **GitHub (`github.com/galicea-lab/galwork-hub`)** zawiera wyłącznie **wydane wersje produkcyjne** (tagi `v*`). Każdy może swobodnie zrobić fork tego repozytorium albo skorzystać z opublikowanych źródeł na warunkach licencji MPL 2.0 (patrz rozdz. 5).
  Repozytorium na GitHubie jest jednokierunkową kopią wydań - jego zawartość jest zastępowana przy każdym wydaniu, dlatego Pull Requesty zgłoszone na GitHubie nie mogą zostać scalone.
* **Aktywny rozwój** prowadzimy na dwóch platformach dostępnych dla zarejestrowanych deweloperów:
  * **Taiga** ([taiga.galicea.org](https://taiga.galicea.org)) - zgłoszenia, kamienie milowe i zadania,
  * **Forgejo** ([code.galicea.org](https://code.galicea.org)) - repozytorium źródłowe, Pull Requesty, code review i CI.

---

## 3. Jak dołączyć do rozwoju projektu?

1. **Zarejestruj się w Taiga** ([taiga.galicea.org](https://taiga.galicea.org)) - możesz użyć konta GitHub.
2. **Poproś o dostęp do Forgejo** ([code.galicea.org](https://code.galicea.org)). Na razie konta w Forgejo zakładamy ręcznie: po rejestracji w Taiga poproś zespół projektu o dodanie do projektu i założenie konta. Automatyczne nadawanie dostępu jest w przygotowaniu.
3. Po uzyskaniu dostępu pracujesz na gałęziach w repozytorium Forgejo (bez forków) - zgodnie z rozdz. 4.

**Zgłaszanie błędów i propozycji:** zakładaj je jako zadania w Taiga. Przed utworzeniem zgłoszenia sprawdź, czy problem nie został już opisany. W opisie błędu podaj wersję (tag `v*`), środowisko, kroki do odtworzenia oraz logi. Przed rozpoczęciem prac nad dużą funkcjonalnością omów jej architekturę z zespołem utrzymującym.

---

## 4. Proces tworzenia kodu i Pull Request

1. **Zadanie w Taiga:** każda zmiana ma zadanie w Taiga z identyfikatorem `TG-<ref>` (np. `TG-123`).

2. **Gałąź robocza** od aktualnego `main`, według schematu `typ/inicjały/TG-<ref>-opis`:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feature/jw/TG-123-login-validation
   # typy: feature/, fix/, refactor/, docs/, hotfix/
   ```

3. **Commity i tytuł PR** według [Conventional Commits](https://www.conventionalcommits.org/pl/), z identyfikatorem zadania, np.:
   ```text
   fix(auth): poprawiono walidację adresu email w formularzu logowania TG-123
   ```
   PR scalamy metodą *Squash and merge* - tytuł PR staje się komunikatem commita na `main`.

4. **Jakość kodu i testy** - te same kontrole uruchamia CI w Forgejo i blokują one scalenie PR:
   ```bash
   pip install -r requirements-dev.txt
   ruff check .                          # reguły z pyproject.toml
   pytest
   node scripts/check-ai-comments.mjs    # format znaczników AI (rozdz. 4.5)
   ```
   * Przestrzegaj standardów PEP 8 w nowym kodzie. Nie formatuj masowo istniejącego kodu (np. `black .`) w ramach zmian funkcjonalnych - zmiany formatowania zgłaszaj osobnym PR.
   * Każdy nowy plik źródłowy zawiera nagłówek licencyjny MPL 2.0:
     ```python
     # This Source Code Form is subject to the terms of the Mozilla Public
     # License, v. 2.0. If a copy of the MPL was not distributed with this
     # file, You can obtain one at https://mozilla.org/MPL/2.0/.
     ```

5. **Kod generowany przez AI:**
   * komentarze dodane lub zmienione przez asystenta AI zaczynają się od znacznika `AI RRRR-MM-DD:` (np. `AI` `2026-10-29: ...`),
   * PR z takim kodem otrzymuje etykietę `ai-generated` i podlega szczegółowemu code review,
   * przed wydaniem wersji każdy znacznik musi zostać przejrzany: usunięty albo przepisany jako zwykły komentarz - wydanie z nieprzejrzanymi znacznikami jest blokowane (`node scripts/check-ai-comments.mjs --release`).

6. **Pull Request** w Forgejo do gałęzi `main`: opisz zakres zmian, sposób sprawdzenia i podaj link do zadania w Taiga. Recenzentem jest owner projektu.

7. **Pliki publikowane:** na GitHub trafiają tylko pliki wskazane w `.public-paths`. Nowy katalog lub plik w korzeniu repozytorium, który ma być publiczny, dopisz do `.public-paths` w tym samym PR. Nie umieszczaj w repozytorium sekretów, danych klientów ani adresów wewnętrznych - wydanie skanuje eksport pod kątem sekretów.

---

## 5. Zasady Licencyjne (Licensing & Developer Certificate of Origin)

Projekt **`galwork-hub`** objęty jest licencją **Mozilla Public License 2.0 (MPL 2.0)** - pełny tekst w pliku [LICENSE](LICENSE).

Przekazując swój wkład (Pull Request), oświadczasz i zgadzasz się, że:
1. **Zgoda na licencję MPL 2.0:** Całość przekazywanego kodu staje się dostępna na warunkach licencji MPL 2.0.
2. **Prawa autorskie (DCO):** Posiadasz pełne prawa autorskie do przekazywanego kodu lub dysponujesz upoważnieniem do jego udostępnienia na licencji MPL 2.0.
3. **Prawa do poprawek:** Otrzymane poprawki do kodu `galwork-hub` będą publicznie dostępne dla całej społeczności użytkowników i wdrożeniowców.
