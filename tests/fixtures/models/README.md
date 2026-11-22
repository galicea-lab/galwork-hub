<!-- modele używane przez testy huba - kopie z galwork-models -->
# Modele testowe

Kopie modeli z `galwork-models`, na których testy sprawdzają pełną ścieżkę
obiegu: synchronizację katalogu, start, zadania i kroki systemowe. Dzięki temu
testy nie zależą od katalogu obiegów huba (`spiffworkflow/` – lokalnie
dowiązanie do galwork-models, na serwerze wolumen) ani od sąsiednich
repozytoriów. Ścieżki: `tests/fixture_paths.py`.

Po zmianie modelu w `galwork-models`, na której opiera się test, skopiuj folder
tutaj i uruchom testy.

| Folder | Testy |
|---|---|
| `zakupy/faktura-zakupu` | `test_wf_purchase_invoice_flow.py`, `test_seed_process_model.py` |
| `regulacje/dokumenty` | `test_wf_act_start_form.py`, `test_seed_process_model.py` |
| `projekty/przeglad-tygodnia` | `test_pm_taiga_review.py`, `test_seed_process_model.py` |
| `kancelaria/edr-wysylka` | `test_edr_wysylka.py` |
| `edu/edu-pismo` | `test_edu_pismo.py` |
| `wspolne/podpis-dokumentu` | `test_wf_signing.py`, `test_seed_process_model.py` |
| `it/zarzadzanie-it/rejestr-systemow`, `slowniki/it/domeny` | `test_seed_process_model.py` (profil `galicea`) |
| `regulacje/**`, `cyfryzacja/**`, `komunikacja/informacja-z-potwierdzeniem`, `wspolne/podpis-dokumentu` | `test_seed_process_model.py` (profil `cyfryzacja`, publikacja z `callActivity`) |
| `wspolne/druki` | biblioteka szablonów wydruku (`_base`, `_wzory`) dołączana przy synchronizacji katalogu |
| `profiles/cyfryzacja.txt`, `profiles/galicea.txt` | `test_seed_process_model.py` |
