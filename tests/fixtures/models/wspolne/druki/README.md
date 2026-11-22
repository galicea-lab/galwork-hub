# Szablony druków PDF

Szablony druków (pism, decyzji, zaświadczeń, protokołów, umów, kart
i formularzy) dla obiegów z tego repozytorium. Hub (`galwork-hub`,
`wf/services/print_service.py`) składa z nich PDF: Jinja2 → HTML → WeasyPrint.

## Układ

```
wspolne/druki/
  _base/
    strona.html.j2      # szkielet <html>: nagłówek, tytuł, bloki treści, stopka
    styl.html.j2        # arkusz stylów druku (A4, typografia, tabele, podpisy)
    makra.j2            # makra: pola, tabele, podpisy, adnotacje, RODO
  _wzory/
    decyzja.html.j2  zawiadomienie.html.j2  pismo.html.j2  podanie.html.j2
    wniosek.html.j2  umowa.html.j2  protokol.html.j2  zaswiadczenie.html.j2
    karta.html.j2  formularz.html.j2
  README.md  SPIS.md

<obieg>/
  <obieg>-…-schema.json                     # "x-print": wskazuje szablon i krok
  <katalog obiegu>_<krok>[_<wariant>].html.j2   # plik druku obok schematu
```

Plik druku leży w folderze obiegu, obok schematu formularza. Nazwa:
`<nazwa katalogu obiegu>_<krok BPMN>[_<kod wariantu>].html.j2`, np.
`dziekanat/postepowanie/postepowanie_Task_03_ZBB.html.j2`. Pełna lista: `SPIS.md`.

Biblioteka (`_base`, `_wzory`) jest wspólna. Synchronizacja obiegów w hubie
(`POST /v1/wf/process-models/sync-from-disk`) dołącza ją do każdej wersji obiegu,
który ma szablony - dokument drukuje się szablonem z wersji obiegu, w której
powstał. Zmiana biblioteki tworzy więc nowe wersje robocze tych obiegów.
Katalog biblioteki ustawia `[print] library_dir` w `config.ini` huba.

## Powiązanie z obiegiem: `x-print`

Szablon działa dopiero wtedy, gdy wskazuje go schemat formularza obiegu.
Klucz `x-print` w głównym obiekcie `*-schema.json` (lista):

```json
"x-print": [
  {
    "template": "postepowanie_Task_03_ZBB.html.j2",
    "label": "Zawiadomienie o wszczęciu postępowania – ZBB",
    "tasks": ["Task_03"],
    "when": {"Systemowe/Formularz zawiadomienia - akronim": "ZBB"}
  }
]
```

| klucz | znaczenie |
|---|---|
| `template` | plik szablonu w folderze obiegu (wymagane) |
| `label` | nazwa wydruku na przycisku i tytuł dokumentu |
| `title` | opcjonalnie inny tytuł dokumentu (zwykły tekst) |
| `tasks` | id zadań BPMN, w których wydruk jest dostępny (brak = wszędzie) |
| `when` | warunki: `Sekcja/Pole` → wartość albo lista wartości |

Zasady:

- **Bez Jinja w `x-print`.** Hub renderuje schemat zawierający `{{` przed
  pokazaniem formularza, a tam nie ma kontekstu wydruku - formularz by się
  nie otworzył. Tytuł i nazwa pliku PDF dostają numer sprawy automatycznie.
- **Który schemat:** jeśli zadanie BPMN wskazuje formularz
  (`formJsonSchemaFilename`), liczy się tylko `x-print` tego schematu.
  Jeśli nie wskazuje (obiegi wariantowe: `postepowanie`,
  `realizacja-procedury-skreslenia`, `umowy`, `aneks-umowa`), hub zbiera
  `x-print` ze wszystkich schematów wersji.
- **`when` odrzuca wydruk tylko przy innej wartości.** Puste pole nie odrzuca:
  wariant nieustalony oznacza listę do wyboru. W `prosba_studenta` warunkiem
  jest `Rodzaj pisma`, w `postepowanie` - akronim formularza zawiadomienia.
  Decyzje D* (`realizacja-procedury-skreslenia`) i umowy UM/UAN nie mają w
  danych pola z kodem wariantu - użytkownik wybiera wydruk z listy.
- `prosba_studenta`: schemat jest generowany (`scripts/formularze.py zbuduj`),
  więc `x-print` edytujemy w `src/wydruki.json`, nie w `zgloszenie-schema.json`.

Poza szablonami z `x-print` każdy krok ma **wydruk standardowy**: nagłówek
organizacji, tabela „etykieta pola: wartość” w kolejności `ui:order`
(bez pól ukrytych, kody `oneOf`/słowników zamienione na nazwy), stopka
z numerem strony.

## Gdzie wydruki są dostępne

| program | miejsce |
|---|---|
| galwork-dashboard | zadanie i instancja: „Pobierz PDF”, „Zapisz w dokumentach sprawy” |
| galwork-edu | zadanie i instancja: „Pobierz PDF” (bez archiwizacji) |
| galwork-arena | „Edytor wydruków” (`/prints`): edycja `*.j2`, podgląd HTML i PDF na danych przykładowych |

API huba: `GET /v1/wf/process-instances/{id}/prints[?task_id=]`,
`GET …/print.pdf[?task_id=&template=]`, `POST …/print` (dokument sprawy,
nowa wersja przy kolejnym zapisie, wysyłka do Paperless),
`POST /v1/wf/print/preview` (podgląd dla administratora obiegów).

## Format

`*.html.j2` – szablon Jinja2 generujący pełny dokument HTML z CSS do druku
(`@page A4`). Hub rozpoznaje ten rodzaj plików jako `print`
(`wf/services/process_files.py`); konwersja HTML → PDF odbywa się po stronie
generatora (`WeasyPrint` obsługuje numerację stron w `@page`; przy druku
z przeglądarki numeracja z `@page` nie zadziała, ale stopka dokumentu jest
zawsze widoczna).

## Kontekst renderowania

```python
Szablon.render(f=dane_formularza, meta=metadane_sprawy)
```

- `f` – słownik danych formularza instancji: klucze sekcji, w nich nazwy pól
  dokładnie takie jak w `*-schema.json` (ze spacjami i polskimi znakami).
  W szablonie dostępny też jako `dane` (alias, zawsze słownik – brak `f` nie
  wywraca szablonu).
- `meta` – metadane sprawy. Hub wypełnia je z `[print]` w `config.ini`
  (organizacja) i z instancji (`znak` = numer sprawy, `data`, `wygenerowano`,
  `proces`, `instancja_tytul`, `zadanie`); brak = wartości domyślne:

| klucz | znaczenie | domyślnie |
|---|---|---|
| `uczelnia` | pełna nazwa uczelni | `Nazwa Uczelni` |
| `skrot` | skrót nazwy (stopka, pieczątki) | pusty |
| `miejscowosc` | miejscowość wystawienia | `Miejscowość` |
| `miejscowosc_miejscownik` | ta sama nazwa w miejscowniku („w Krakowie") | `miejscowosc` |
| `adres_uczelni` | adres do nagłówka i RODO | pusty |
| `logo` | URL/data URI logo | brak (bez logo) |
| `jednostka` | instytut / dział / wydział w nagłówku | pusty |
| `data` | data dokumentu, gotowa do druku („16 listopada 2026 r.") | pusty |
| `znak` | znak sprawy | pusty |
| `dokument_id` | numer dokumentu elektronicznego | brak |
| `wygenerowano` | data i godzina wygenerowania | brak |

Zmienne ustawiane przez `{% set %}` w pliku druku (`tytul`, `podtytul`,
`nr_dokumentu`, `znak`, `organ`, …) nadpisują metadane.

## Makra (`_base/makra.j2`)

Import w pliku druku: `{% import "_base/makra.j2" as m %}`. Pierwszy argument
makr polowych to zawsze słownik danych (`dane`, nigdy `f` bezpośrednio).

| makro | rola |
|---|---|
| `m.w(dane, sekcja, pole)` | wartość pola jako tekst (pusta, gdy brak) |
| `m.w1(dane, pole)` | wartość pola najwyższego poziomu schematu |
| `m.wm(dane, sekcja, pole, mapa)` | kod `oneOf`/`const` → etykieta ze słownika |
| `m.forma(dane, "studentka", "student")` | wybór formy wg pola „Forma gramatyczna" (`K`/`M`) |
| `m.p(dane, sekcja, pole, etykieta=None)` | wiersz „Etykieta: wartość" z linią kropkowaną |
| `m.p2(...)` | to samo w siatce (`<div>`) |
| `m.siatka(dane, pola, kolumny=2)` | siatka pól; `pola` = lista krotek `(sekcja, pole[, etykieta])` |
| `m.wp(dane, sekcja, pole, domyslne)` | wartość w toku zdania (podkreślenie kropkowane) |
| `m.linia(szerokosc)` | pusta linia do wpisu ręcznego |
| `m.wybor(etykieta, zaznaczone)` | pole wyboru `☒`/`☐` |
| `m.tabela(kolumny, wiersze, klasa)` | tabela: nagłówki + wiersze (listy lub słowniki) |
| `m.tabela_pol(lista, [(klucz, etykieta), …])` | tabela z listy obiektów formularza |
| `m.sekcja(tytul)` | śródtytuł sekcji (wersaliki, rozstrzelenie) |
| `m.formula(tresc)` | formuła rozstrzygająca („s k r e ś l a m") |
| `m.adresat(linie)` | blok adresata; `linie` = lista stringów lub `(klasa, tekst)` |
| `m.podpisy(lista, klasa)` | obszar podpisów: `(rola, osoba[, data])`, klasa `pionowo` |
| `m.adnotacje(lista)` | adnotacje urzędowe: `(etykieta, wartość)` |
| `m.ramka(tresc, tytul=None)` | ramka informacyjna / oświadczenie |
| `m.zalaczniki(lista)` | numerowana lista załączników |
| `m.klauzula_rodo(uczelnia, miejscowosc_m, cel=…, podstawa=…, …)` | klauzula informacyjna RODO |

## Wzorce dokumentów (`_wzory/`)

Plik druku dziedziczy wzorzec i wypełnia bloki. Bloki, które wzorzec
udostępnia (`self.nazwa()` w środku innego bloku – tak wzorzec komponuje treść):

| wzorzec | bloki do nadpisania |
|---|---|
| `decyzja` | `podstawa`, `rozstrzygniecie`, `uzasadnienie`, `pouczenie`, `adnotacje`, `podpisy`, `rodo` |
| `zawiadomienie` | `podstawa`, `przedmiot`, `okolicznosci`, `dalsze_czynnosci`, `pouczenie`, `adnotacje`, `podpisy`, `rodo` |
| `pismo` | `wstep_dodatkowy`, `tresc_pisma`, `zestawienie`, `zalaczniki`, `podpisy`, `rodo` |
| `podanie` | `uzasadnienie`, `dane_sprawy`, `oswiadczenie`, `zalaczniki_podania`, `podpisy`, `adnotacje`, `rodo` |
| `wniosek` | `uzasadnienie`, `zestawienie`, `dodatkowe_informacje`, `adnotacje`, `podpisy`, `rodo` |
| `umowa` | `strony`, `paragrafy`, `postanowienia_koncowe`, `klauzule_dodatkowe`, `rodo` |
| `protokol` | `uczestnicy`, `przedmiot`, `ustalenia`, `wnioski`, `adnotacje`, `podpisy`, `rodo` |
| `zaswiadczenie` | `tresc_zaswiadczenia`, `cel_wydania`, `adnotacje`, `podpisy`, `rodo` |
| `karta` | `tresc_karty`, `podsumowanie`, `uwagi`, `adnotacje`, `podpisy`, `rodo` |
| `formularz` | `sekcje`, `oswiadczenia`, `zgody`, `adnotacje`, `podpisy`, `rodo` |

Zmienne sterujące wzorcami (ustawiane w pliku druku): `tytul`, `podtytul`,
`nr_dokumentu`, `znak`, `organ`, `organ_dop` (dopełniacz: „Rektora"),
`pouczenie_typ` (`rektor`/`dziekan`), `adresat_linie`, `dane_studenta`,
`dane_wnioskodawcy`, `formularz`, `osoba_podpisujaca`, `podpis_rola`,
`uzasadnienie_text`, `przedmiot`, `wstep`, `akceptacje`, `uczestnicy`,
`podpisy_lista`, `zalaczniki_lista`, `rodo_cel`, `rodo_podstawa`.

## Szkielet pliku druku

```jinja
{#- AI RRRR-MM-DD: jedno zdanie, co to za druk. -#}
{#- Obieg: <ścieżka modelu> | krok: <Task> "<nazwa>" | formularz: <plik-schema.json> | priorytet: A|B|C #}
{% extends "_wzory/decyzja.html.j2" %}
{% import "_base/makra.j2" as m %}
{%- set dane = f if (f is defined and f) else {} -%}

{%- set tytul = "DECYZJA" -%}
{%- set student = m.w(dane, "Wszczęcie postępowania", "Student(ka)") | trim -%}
…
{% block uzasadnienie %}<p>…</p>{% endblock %}
```

Zasady:

1. **Pola tylko z makr** (`m.w`, `m.w1`, `m.wm`, `m.forma`) i tylko pola
   istniejące w schemacie – nazwy przepisujemy ze schematu razem z literówkami
   (np. „Iłość rat", „Wartość zobowjązania").
2. **Schematy płaskie i listy.** Większość formularzy w `galwork-models` nie ma
   sekcji – pola leżą na najwyższym poziomie. Wtedy używamy `m.w1`:

   ```jinja
   {#- schemat płaski: ["Pracownik"], ["Typ urlopu"], ["Od"], ["Do"] #}
   {{ m.w1(dane, "Pracownik") }} · {{ m.w1(dane, "Typ urlopu") }}

   {#- pole listy (w polu_druku: sekcje ze znakiem []) #}
   {%- for pozycja in m.w1(dane, "Plan") or [] %}
     {{ pozycja.get("Forma zajęć") }} – {{ pozycja.get("Ile godzin") }} h
   {%- endfor %}

   {#- albo od razu tabela #}
   {{ m.tabela_pol(m.w1(dane, "Efekty") or [],
                   [("Kod", "Kod"), ("Opis efektów", "Opis")]) }}

   {#- pole w prawdziwej sekcji #}
   {{ m.w(dane, "DANE STUDENTA", "Nr albumu") }}
   ```

   Alternatywa dla schematów płaskich: jednorazowe opakowanie danych w sekcję
   techniczną (stosowane w części szablonów) — wtedy dalsza treść czyta pola
   przez `m.w`:

   ```jinja
   {%- set d = {"pola": dane} -%}          {#- albo set dane = {"Formularz": dane} -#}
   {{ m.w(d, "pola", "Pracownik") }}
   {%- for pozycja in lista_zajec %}
   {%- set p = {"pozycja": pozycja} -%}     {#- opakowanie pojedynczego elementu listy -#}
   {{ m.w(p, "pozycja", "Sala") }}
   {%- endfor %}
   ```

   Nie wolno natomiast wymyślać nazw sekcji bez takiego opakowania („Formularz",
   „pola", „pozycja" nie istnieją w danych) — druk wyjdzie pusty. Weryfikator
   (`galwork-dev-jw/druki/narzedzia/sprawdz_gotowe.py`) rozpoznaje opakowania i sprawdza pola
   względem schematu.
3. **Brak HTML poza szkieletem**: nie piszemy `<html>`, `<head>`, `<style>`;
   dopuszczalne są elementy semantyczne (`<p>`, `<table>` przez makra, `<span
   class="wypelnienie">`, `class="ramka"`, `class="drobny"`).
4. **Klasy z `_base/styl.html.j2`** – bez stylów w linii (`style="…"` tylko
   wyjątkowo, np. szerokość kolumny).
5. **Język dokumentu**: polski, strona bierna/czynna jak w `../przyklady`,
   podstawa prawna przed rozstrzygnięciem, pouczenie po uzasadnieniu.
6. **Dane osobowe**: każdy druk z danymi osoby fizycznej kończy się klauzulą
   RODO (blok `rodo`), chyba że dokument jest wyłącznie wewnętrzny i nie
   zawiera danych osobowych.
7. **Kody zamiast etykiet**: pola `oneOf`/`AsyncSelectWidget` zwracają kod –
   używamy `m.wm` z jawną mapą kodów.
8. **Brak wartości = puste pole lub kropki**, nigdy „None"/„Undefined".
   Formę „do wypełnienia ręcznego" dają `m.linia`/`m.wp`.
9. **Uczelnia w odmianie**: w treści piszemy „Uczelnia"/„Uczelni" (nazwa
   własna jest w nagłówku), a miejscowość w zdaniach z „w" – przez
   `miejscowosc_m`.

Hub przekazuje też `fields` - pola formularza już sformatowane (etykiety,
Tak/Nie, nazwy opcji, tabele) - i `schema`.

## Weryfikacja

Podgląd w arenie: „Edytor wydruków” → wybór pliku → „Podgląd” / „PDF”.
Dane przykładowe powstają ze schematu, którego `x-print` wskazuje szablon.
Narzędzia autorskie (sprawdzanie pól względem schematów) są w
`galwork-dev-jw/druki/narzedzia`.
