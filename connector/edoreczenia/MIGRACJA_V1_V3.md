# Migracja connectora e-Doręczeń z API v1 na v3

Data audytu: 2026-10-11.

## 1. Streszczenie

Po wygenerowaniu nowych modeli (`ua_model.py`) i modułów API (`api/*.py`, na bazie
`swagger-codegen-cli` - bez aktualizacji api_client)) dokonano ręcznych poprawek:
- poprawa ścieżek importów
- ustawienie poprawnych danych w configuration.py (Poczta Polska)
- modyfikacje modeli - UUID oraz Optional[str] w miejscach, w których generowały się pola z ograniczoną wielkością

Podstawowy problemy: nazwy metod wygenerowane przez `swagger-codegen` nie zgadzają się z tymi używanymi w `edoreczenia.py`, 
co powoduje `AttributeError` przy wywołaniach.


## Problem mapowania nazw metod

`swagger-codegen-cli` generuje nazwy metod z adresu ścieżki (np.
`e_delivery_address_messages_message_id_delete`), bo specyfikacja v3 nie ma
pola `operationId` dla większości endpointów (wyjątek: `downloadMessageAttachment`,
`uploadMessageAttachment`). Trzeba było je ręcznie zmapować na nazwy używane w
`edoreczenia.py`. Zrobiono to tylko częściowo. Pełne mapowanie:

| Klasa API | Metoda w `edoreczenia.py` | Endpoint v3 | Wygenerowana nazwa | Status przed audytem |
|---|---|---|---|---|
| `MessagesApi` | `getMessages` | `GET /messages` | `e_delivery_address_messages_get` | 
| `MessagesApi` | `getMessage` | `GET /messages/{messageId}` | `e_delivery_address_messages_message_id_get` | 
| `MessagesApi` | `postMessage` | `POST /messages` | `e_delivery_address_messages_post` |
| `MessagesApi` | `deleteMessage` | `DELETE /messages/{messageId}` | `e_delivery_address_messages_message_id_delete` |
| `EvidencesApi` | `getEvidencesForMessage` | `GET /messages/{messageId}/evidences` | `e_delivery_address_messages_message_id_evidences_get` | 
| `EvidencesApi` | `getEvidence` | `GET /messages/evidences/{evidenceId}` | `e_delivery_address_messages_evidences_evidence_id_get` | 
| `EvidencesApi` | `getZipEvidences` | `GET /evidences/{messageId}/technical-evidences-file` | `e_delivery_address_evidences_message_id_technical_evidences_file_get` |
| `EvidencesApi` | `deleteEvidence` | — | **endpoint nie istnieje w v3, patrz §4** |
| `SubscriptionsApi` | `putSubscriptions` | `PUT /message_subscriptions` | `e_delivery_address_message_subscriptions_put` |
| `AttachmentsApi` | `getMessageAttachment` | `GET /messages/{messageId}/attachments/{attachmentId}` | `e_delivery_address_messages_message_id_attachments_attachment_id_get` |

Dla każdej zmiany zastosowano ten sam, już obecny w kodzie wzorzec: zmieniono
tylko nazwę metody **publicznej** (z `kwargs['_return_http_data_only'] = True`),
pozostawiając bez zmian metodę `..._with_http_info`, do której ta publiczna
metoda się odwołuje (więc żadne wewnętrzne wywołania nie wymagały poprawek).

Decyzja projektowa: **zmieniono generowane nazwy, a nie `edoreczenia.py`** —
krótkie, czytelne nazwy (`getMessage`, `postMessage`...) są lepszym kontraktem
dla kodu domenowego niż nazwy pochodzące z adresu URL. `edoreczenia.py` nie
wymagał żadnych zmian nazw wywołań dla tego punktu.


## Problem mapowania Modeli

1) W specyfikacji v3 (`uaapi_3.0.8.2.yaml`, linia 384)
`GET /{eDeliveryAddress}/messages/{messageId}` zwraca teraz tablicę:

```yaml
responses:
  200:
    schema:
      type: array
      items:
        $ref: '#/components/schemas/Message'
```

To jest zgodne z tym, że parametr `messageId` w ścieżce jest też tablicą
(`maxItems: 50`) — endpoint wspiera pobranie wielu wiadomości jednym zapytaniem.
Dla pobrania jednej wiadomości odpowiedź to zawsze lista z jednym elementem.
Obserwacja użytkownika („poprzednio słownik, teraz lista z jednym elementem").
Poprawiono `PobranieWiadomosciF` (`data = rdata[0]`).

### Problem z `FileMetadata.fileId` - błędnie wymagane.

W specyfikacji v3 `FileMetadata.fileId` jest formalnie `required`:

```yaml
FileMetadata:
  required:
    - fileId
```

W wygenerowanym module było:

```python
fileId: Optional[UUID] = Field(
    ..., description='UUID pliku', ...
)
```

To jest niespójne: typ `Optional[UUID]`, ale `Field(...)` (elipsa) oznacza w
Pydantic v2 pole **wymagane** — w Pydantic v2 (w przeciwieństwie do v1) typ
`Optional[X]` *nie* implikuje domyślnego `None`.

W praktyce serwer e-Doręczeń **nie wypełnia** `fileId` w załącznikach zwracanych
przy odbieraniu wiadomości (`fileId` nadaje nadawca przy wysyłce/uploadzie —
widać to w przykładzie z dokumentacji API). Potwierdza to zawartość pobranego komunikatu
`message.json`, gdzie żaden z `attachments[].file.fileMetadata` nie ma `fileId`.
Dlatego `Message(**data)` zawsze kończyło się:

```
pydantic_core.ValidationError: 1 validation error for Message
attachments.0.file.fileMetadata.fileId
  Field required [type=missing, ...]
```

To dokładnie odtwarza sytuację opisaną w poprzedniej wersji (v1) kod
jawnie łatał ten brak (`if not 'fileId' in ...: a['file']['fileMetadata']['fileId']=uuid1()`),
co był świadomym obejściem tego samego problemu w starym API.

**Naprawa** (`ua_model.py`): zmieniono domyślną wartość na `None`, zachowując
typ `Optional[UUID]`:

```python
fileId: Optional[UUID] = Field(
    None, description='UUID pliku', ...
)
```

To jedyne pole w całym `ua_model.py`, które miało tę niezgodność
(`Optional[...]` + `Field(...)`) — zweryfikowano programowo całym plikiem.



## Dodatkowe błędy znalezione przy audycie

### 1 Konstruktory `ApiClient` w trzech klasach

`edoreczenia.py` tworzy instancje jako `EvidencesApi(self.access_token)`,
`SubscriptionsApi(self.access_token)`, `AttachmentsApi(self.access_token)` —
tak jak `MessagesApi`, która **ma** poprawiony konstruktor:

```python
def __init__(self, access_token, api_client=None):
    if api_client is None:
        api_client = ApiClient(access_token)
    self.api_client = api_client
```

Pozostałe trzy klasy wciąż miały oryginalny, niezmieniony konstruktor
wygenerowany przez `swagger-codegen`:

```python
def __init__(self, api_client=None):
    if api_client is None:
        api_client = ApiClient()
    self.api_client = api_client
```

Przy wywołaniu `EvidencesApi(access_token)` ten string trafiał jako
`api_client` (bo to pierwszy — i jedyny — parametr pozycyjny), warunek
`if api_client is None` był fałszywy, i `self.api_client` **stawał się
samym tokenem (`str`)**. Każde wywołanie `self.api_client.call_api(...)`
skończyłoby się `AttributeError: 'str' object has no attribute 'call_api'`.
To by wystąpiło przy pierwszym realnym użyciu `PobranieDowodow`,
`PobranieDowodu`, `DefiniujSubskrypcje` czy `PobierzZalacznik` — niezależnie
od poprawności nazw metod.

**Naprawiono** konstruktory w `evidences_api.py`, `subscriptions_api.py`,
`attachments_api.py` (ten sam wzorzec co w `messages_api.py`). Dla
konsekwencji poprawiono też konstruktory w pozostałych, obecnie nieużywanych
modułach (`settings_api.py`, `threads_api.py`, `directories_api.py`,
`drafts_api.py`, `archiving_messages_api.py`, `rules_api.py`) — nic w
repozytorium ich jeszcze nie tworzy, ale gdy ktoś zacznie z nich korzystać,
nie wpadnie w tę samą pułapkę.

### 2 `getMessageAttachment` i niezgodny kontrakt zwrotny w `PobierzZalacznik`

`PobierzZalacznik` rozpakowywał wynik jako trójkę:
`(result, code, headers) = api.getMessageAttachment(...)`. Taki kontrakt miała
metoda w v1 (`api.1/attachments_api.py`), bo była napisana jako **jedna,
niepodzielona** metoda zwracająca bezpośrednio wynik `call_api(...)` (bez
forsowania `_return_http_data_only`).

Metoda wygenerowana w v3 przez `swagger-codegen` idzie za standardowym
wzorcem "publiczna metoda + `_with_http_info`" (tak jak `getMessage`,
`getMessages` itd.) — wymusza `_return_http_data_only=True` i zwraca
**jedną wartość**, nie trójkę. Po zmianie nazwy ta niezgodność ujawniłaby się
jako `ValueError: too many values to unpack` (albo odwrotnie, w zależności od
tego co dokładnie zwraca `call_api`).

**Naprawiono** `PobierzZalacznik`, żeby był zgodny z resztą pliku (wzorzec
używany wszędzie indziej w `edoreczenia.py`): wywołanie z
`_preload_content=False`, odczyt `response.status` / `response.data` z
pojedynczego, surowego obiektu odpowiedzi `urllib3`.

### 3 `deleteEvidence` / `UsuniecieDowodu` — endpoint nie istnieje w v3

W specyfikacji v3 sekcja *Evidences* (linie 1827–2073 w `uaapi_3.0.8.2.yaml`)
zawiera wyłącznie operacje `GET` — nie ma żadnego `DELETE` dla dowodów. W v1
taki endpoint istniał. To nie jest błąd migracji nazw — **dowodu po prostu
nie da się usunąć** w nowym API (co ma sens: to dokumenty dowodowe/prawne).

`UsuniecieDowodu` (bez odbiorców w repozytorium — zweryfikowano) zmieniono,
by jawnie zgłaszać `NotImplementedError` z wyjaśnieniem, zamiast wołać
nieistniejącą metodę `EvidencesApi.deleteEvidence` (ta metoda nigdy nie
istniała w wygenerowanym kodzie — call by się wysypał na `AttributeError`
przy pierwszym użyciu).

### 4 Zmiana kontraktu odpowiedzi — "lista z jednym elementem" to wzorzec, nie wyjątek

To samo co w §2 dla `getMessage` dotyczy też `getEvidence`
(`GET /messages/evidences/{evidenceId}` → `type: array, items: Evidence`).
`PobranieDowodu`/`PobranieDowoduF` nie parsują odpowiedzi do modelu (zwracają
surowy obiekt odpowiedzi), więc nie ujawni się to jako błąd teraz — ale jeśli
ktoś doda tam w przyszłości mapowanie Pydantic, trzeba pamiętać o rozpakowaniu
listy, tak jak zrobiono to dla `Message`.

### 5 Martwy/duplikowany kod w `edoreczenia.py`

Podczas migracji powstały równoległe, prawie identyczne metody (efekt
iteracyjnego debugowania). Po audycie użycia w całym repozytorium
(`grep` po `edr_connector.py`, `edr_webcon.py`, `edr_paperless.py`,
`wf/routers/`, `tests/`):

- `PobranieWiadomosciF` — **ma żywego odbiorcę** (`edr_connector.py:231`).
  Zamieniono jej ciało na wołanie `self.PobranieWiadomosci(...)` (ta sama
  logika, bez duplikacji).
- `PobranieDowodowF` — **brak odbiorców** (duplikat `PobranieDowodow`) —
  usunięto.
- `PobranieDowoduF0` — **brak odbiorców** (duplikat `PobranieDowodu` /
  `PobranieDowoduF`) — usunięto.
- `PobranieDowoduF` — **ma żywych odbiorców** (`edr_webcon.py`,
  `edr_paperless.py`, `tests/test_edr_paperless_api.py`) — zachowano.
- `PobranieDowodu` — **ma żywych odbiorców** (`edr_webcon.py`) — zachowano.

### 6 Martwy kod odwołujący się do starego kontraktu (nie wymaga naprawy)

`wf/routers/edr_api.py:575` zawiera `(odpowiedz, kod, tmp) = edr.PobranieWiadomosci(messageId)`
— rozpakowanie trójki, niezgodne z obecnym (i już przed tym audytem
obowiązującym) kontraktem `PobranieWiadomosci() -> Message`. **To nie jest
aktywny kod** — cały endpoint `Inbox` jest wewnątrz zakomentowanego bloku
(`"""..."""`, linie 503–589 tego pliku). Zostawiono bez zmian, odnotowane
tutaj dla porządku — gdyby ktoś odkomentował ten endpoint, trzeba go
dopasować do obecnego API.

## Zmienione pliki

| Plik | Zmiana |
|---|---|
| `ua_model.py` | `FileMetadata.fileId`: `Field(...)` → `Field(None, ...)` |
| `api/messages_api.py` | rename: `postMessage`, `deleteMessage` |
| `api/evidences_api.py` | fix konstruktora (`access_token`); rename: `getEvidencesForMessage`, `getEvidence`, `getZipEvidences` |
| `api/subscriptions_api.py` | fix konstruktora (`access_token`); rename: `putSubscriptions` |
| `api/attachments_api.py` | fix konstruktora (`access_token`); rename: `getMessageAttachment` |
| `api/settings_api.py`, `threads_api.py`, `directories_api.py`, `drafts_api.py`, `archiving_messages_api.py`, `rules_api.py` | fix konstruktora (`access_token`) — dla konsekwencji, nieużywane jeszcze przez `edoreczenia.py` |
| `edoreczenia.py` | `PobranieWiadomosci`: rozpakowanie listy + `Message(**data)` zamiast ręcznej budowy pól; `PobranieWiadomosciF`: delegacja do `PobranieWiadomosci`; `PobierzZalacznik`: nowy kontrakt zwrotny (`response.status`/`response.data`); `UsuniecieDowodu`: `NotImplementedError`; usunięto martwe duplikaty `PobranieDowodowF`, `PobranieDowoduF0`; usunięto nieużywane importy (`UUID`, `uuid1`, `datetime`, kilka klas modelu) |
| `tests/test_edr_paperless_api.py` | helper `_message()` ustawiał `MessageMetadata` bez `shippingService` (pole wymagane w v3) — dodano `shippingService='electronic'` |

Wszystkie zmiany zweryfikowano: `py_compile` na wszystkich zmienionych
plikach, instancjonowanie każdej klasy `*Api(access_token)` z potwierdzeniem,
że `self.api_client` jest realnym `ApiClient`, obecność wszystkich
przemianowanych metod, oraz pełny przebieg testów
(`tests/test_edr_auth.py`, `tests/test_edr_paperless_api.py`,
`tests/test_connectors_config.py`) — **wszystkie przechodzą**.


## Zalecenia

Rozważyć dodanie testu na `PobranieWiadomosci`/`PobranieWiadomosciF` z
prawdziwym (zanonimizowanym) payloadem w stylu `message.json`, żeby
przyszłe zmiany w `ua_model.py` nie powtórzyły problemu z `fileId`.
