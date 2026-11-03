"""
Modele Pydantic (v2) dla systemu obiegu dokumentów.
SpiffWorkflow + FastAPI + Keycloak + Paperless-ngx (3.x).

Konwencje:
  *Create  – dane wejściowe API (extra="forbid", walidacja)
  *Update  – częściowa aktualizacja (wszystkie pola opcjonalne)
  *Read    – odpowiedź API, budowana z modeli ORM (from_attributes=True)
  *Internal– używane tylko w serwisach i workerze, nigdy nie zwracane przez API

Wymagania: Python 3.11+, pydantic>=2.6, pydantic[email]

Zmiany względem pierwotnego schematu (integracja z Paperless-ngx):
  * pliki przechowuje wyłącznie Paperless – usunięto document_version.storage_key,
  * document.paperless_document_id – dokument w Paperlessie (po pierwszym ingeście),
  * document_version: ingest_status, paperless_task_id, paperless_version_id,
    staging_path, ingest_error, ingested_at, label,
  * nowe typy zadań w tle: paperless_submit, paperless_poll,
  * nowe zdarzenia audytu: document_ingested, document_ingest_failed.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Generic, TypeVar
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)


# =====================================================================
# Bazy i typy pomocnicze
# =====================================================================

class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ProcessKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
Title = Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)]
PaperlessId = Annotated[int, Field(gt=0)]
JsonDict = dict[str, Any]

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class Pagination(InputModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


# =====================================================================
# Enumy (odpowiadają ograniczeniom CHECK w bazie)
# =====================================================================

class ProcessModelVersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class ProcessFileType(StrEnum):
    BPMN = "bpmn"
    DMN = "dmn"
    JSON_SCHEMA = "json_schema"
    UI_SCHEMA = "ui_schema"
    OTHER = "other"


class ProcessInstanceStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class PurchaseExpenseType(StrEnum):
    """Rodzaj wydatku wniosku zakupowego (`PurchaseRequest.expense_type`) -
    zob. wf/models/wf.py::PurchaseRequest i galwork-book,
    czesc-3-obszary/dzialy/zakupy.md ("Reguły akceptacji"). Inwestycja
    wymaga dodatkowej akceptacji niezależnie od kwoty."""
    OPERATING = "operating"
    INVESTMENT = "investment"


class PurchaseKind(StrEnum):
    """Zakup usługi/kosztu bieżącego a zakup towaru "na magazyn"
    (`PurchaseRequest.purchase_kind`) - magazyn dodaje krok potwierdzenia
    odbioru przed dopasowaniem faktury (zob. zakupy.md)."""
    SERVICE = "service"
    STOCK = "stock"


class HumanTaskStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PrincipalType(StrEnum):
    USER = "user"
    GROUP = "group"
    ROLE = "role"


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class IngestStatus(StrEnum):
    """Cykl życia pliku w drodze do Paperlessa."""
    STAGED = "staged"          # zapisany lokalnie, jeszcze nie wysłany
    SUBMITTED = "submitted"    # wysłany, czekamy na zadanie konsumpcji
    DONE = "done"              # Paperless przyjął plik, znamy jego ID
    FAILED = "failed"          # błąd konsumpcji (szczegóły w ingest_error)
    DUPLICATE = "duplicate"    # Paperless odrzucił identyczny plik


class DocumentLinkRole(StrEnum):
    MAIN = "main"
    ATTACHMENT = "attachment"
    RELATED = "related"


class JobType(StrEnum):
    RUN_ENGINE = "run_engine"
    DELIVER_MESSAGE = "deliver_message"
    SERVICE_CALL = "service_call"
    SEND_NOTIFICATION = "send_notification"
    PAPERLESS_SUBMIT = "paperless_submit"
    PAPERLESS_POLL = "paperless_poll"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class EventType(StrEnum):
    INSTANCE_STARTED = "instance_started"
    INSTANCE_COMPLETED = "instance_completed"
    INSTANCE_CANCELLED = "instance_cancelled"
    INSTANCE_SUSPENDED = "instance_suspended"
    INSTANCE_RESUMED = "instance_resumed"
    INSTANCE_ERROR = "instance_error"
    TASK_CREATED = "task_created"
    TASK_CLAIMED = "task_claimed"
    TASK_RELEASED = "task_released"
    TASK_COMPLETED = "task_completed"
    TASK_CANCELLED = "task_cancelled"
    DOCUMENT_ADDED = "document_added"
    DOCUMENT_VERSION_ADDED = "document_version_added"
    DOCUMENT_INGESTED = "document_ingested"
    DOCUMENT_INGEST_FAILED = "document_ingest_failed"
    COMMENT_ADDED = "comment_added"
    SIGNATURE_REQUESTED = "signature_requested"
    SIGNATURE_COMPLETED = "signature_completed"


# =====================================================================
# 1. Użytkownicy i organizacja
# =====================================================================

class CurrentUser(BaseModel):
    """Kontekst zalogowanego użytkownika: token Keycloak + lokalne `users.id`.

    2026-10-05 (TODO.md pkt 2/4): `id` to int z tabeli `users`
    (models/user.py) - ta sama tożsamość, którą zwraca
    api.security.get_current_user. Buduje go zależność
    wf.dependencies.get_current_wf_user.
    """
    model_config = ConfigDict(frozen=True)

    id: int
    sub: str
    username: str
    email: EmailStr | None = None
    display_name: str
    groups: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    substituting_for: frozenset[int] = frozenset()   # aktywne zastępstwa

    @property
    def acting_user_ids(self) -> frozenset[int]:
        return self.substituting_for | {self.id}


class UserSummary(ReadModel):
    """Skrót użytkownika osadzany w odpowiedziach (relacja do `users`)."""
    # 2026-10-05: było UserSummary z UUID - po połączeniu tabel
    # użytkowników id jest int (models/user.py), a nazwa przeszła z display_name
    # na name, bo tak nazywa się kolumna w models/user.py.
    id: int
    name: str | None = None
    email: EmailStr | None = None


class UserRead(ReadModel):
    """Pełny odczyt użytkownika - odpowiada models/user.py:User."""
    id: int
    keycloak_sub: str | None
    name: str | None
    email: EmailStr
    nip: str | None = None
    phone: str | None = None
    is_active: bool
    creation_date: datetime | None = None


class OrgUnitCreate(InputModel):
    parent_id: UUID | None = None
    code: Annotated[str, StringConstraints(min_length=1, max_length=50)]
    name: Title
    keycloak_group: str | None = Field(default=None, pattern=r"^/.+")


class OrgUnitUpdate(InputModel):
    parent_id: UUID | None = None
    name: Title | None = None
    keycloak_group: str | None = Field(default=None, pattern=r"^/.+")


class OrgUnitRead(ReadModel):
    id: UUID
    parent_id: UUID | None
    code: str
    name: str
    keycloak_group: str | None


class OrgMembershipCreate(InputModel):
    user_id: UUID
    org_unit_id: UUID
    is_manager: bool = False


class OrgMembershipRead(ReadModel):
    user_id: UUID
    org_unit_id: UUID
    is_manager: bool


class UserSubstitutionCreate(InputModel):
    user_id: UUID
    substitute_id: UUID
    process_model_id: UUID | None = None      # None = wszystkie procesy
    valid_from: datetime
    valid_to: datetime

    @model_validator(mode="after")
    def _check(self) -> "UserSubstitutionCreate":
        if self.valid_to <= self.valid_from:
            raise ValueError("valid_to musi być późniejsze niż valid_from")
        if self.user_id == self.substitute_id:
            raise ValueError("Użytkownik nie może zastępować samego siebie")
        return self


class UserSubstitutionRead(ReadModel):
    id: UUID
    user: UserSummary
    substitute: UserSummary
    process_model_id: UUID | None
    valid_from: datetime
    valid_to: datetime
    created_by: UUID
    created_at: datetime


# =====================================================================
# 2. Definicje procesów
# =====================================================================

class ProcessModelCreate(InputModel):
    key: ProcessKey
    name: Title
    description: str | None = None
    category: str | None = None


class ProcessModelUpdate(InputModel):
    name: Title | None = None
    description: str | None = None
    category: str | None = None
    is_active: bool | None = None


class ProcessModelRead(ReadModel):
    id: UUID
    key: str
    name: str
    description: str | None
    category: str | None
    is_active: bool
    created_at: datetime


class ProcessModelFileCreate(InputModel):
    file_name: Annotated[str, StringConstraints(pattern=r"^[\w.\-]{1,200}$")]
    file_type: ProcessFileType
    content: NonEmptyStr                     # sha256 liczy serwer


class ProcessModelFileRead(ReadModel):
    """Bez treści – do list. Treść pobiera się osobnym endpointem."""
    id: UUID
    file_name: str
    file_type: ProcessFileType
    sha256: Sha256


class ProcessModelFileContent(ProcessModelFileRead):
    content: str


class ProcessModelVersionCreate(InputModel):
    bpmn_process_id: NonEmptyStr
    files: list[ProcessModelFileCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_files(self) -> "ProcessModelVersionCreate":
        names = [f.file_name for f in self.files]
        if len(names) != len(set(names)):
            raise ValueError("Nazwy plików w wersji muszą być unikalne")
        if not any(f.file_type == ProcessFileType.BPMN for f in self.files):
            raise ValueError("Wersja musi zawierać co najmniej jeden plik BPMN")
        return self


class ProcessModelVersionRead(ReadModel):
    id: UUID
    process_model_id: UUID
    version: int
    status: ProcessModelVersionStatus
    bpmn_process_id: str
    content_hash: Sha256
    published_at: datetime | None
    published_by: UUID | None
    created_at: datetime
    files: list[ProcessModelFileRead] = []


# =====================================================================
# 3. Instancje procesów
# =====================================================================

class ProcessInstanceCreate(InputModel):
    process_model_key: ProcessKey
    title: Title
    business_key: str | None = None
    data: JsonDict = Field(default_factory=dict)          # dane startowe dla task.data
    document_ids: list[UUID] = Field(default_factory=list)  # dokumenty dołączone na starcie


class ProcessInstanceRead(ReadModel):
    id: UUID
    process_model_version_id: UUID
    process_model_key: str | None = None    # denormalizacja w serwisie
    process_model_name: str | None = None
    business_key: str | None
    title: str
    status: ProcessInstanceStatus
    summary: JsonDict
    started_by: UserSummary | None
    started_at: datetime
    completed_at: datetime | None
    updated_at: datetime
    error_message: str | None
    lock_version: int


class ProcessInstanceFilter(Pagination):
    status: list[ProcessInstanceStatus] | None = None
    process_model_key: ProcessKey | None = None
    business_key: str | None = None
    started_by_me: bool = False
    started_from: datetime | None = None
    started_to: datetime | None = None


class ProcessInstanceAction(InputModel):
    """Anulowanie, wstrzymanie, wznowienie."""
    expected_lock_version: int = Field(ge=0)
    reason: Annotated[str, StringConstraints(max_length=2000)] | None = None


class ProcessInstanceStateInternal(ReadModel):
    process_instance_id: UUID
    serializer_version: str
    state: JsonDict
    updated_at: datetime


# =====================================================================
# 4. Zadania użytkowników
# =====================================================================

class HumanTaskCandidate(ReadModel):
    principal_type: PrincipalType
    principal_value: str


class HumanTaskRead(ReadModel):
    id: UUID
    process_instance_id: UUID
    bpmn_task_id: str
    name: str
    lane: str | None
    status: HumanTaskStatus
    claimed_by: UserSummary | None
    claimed_at: datetime | None
    priority: int
    due_at: datetime | None
    created_at: datetime
    completed_at: datetime | None
    completed_by: UserSummary | None
    completed_on_behalf_of: UserSummary | None
    candidates: list[HumanTaskCandidate] = []
    # 2026-10-20: potrzebne do claim/complete (expected_lock_version) -
    # bez tego klient nie znał bieżącej wersji blokady i wysyłał 0 (409).
    process_instance_lock_version: int


class MyTaskItem(ReadModel):
    """Wiersz skrzynki 'Moje zadania' – zadanie z kontekstem instancji."""
    id: UUID
    name: str
    status: HumanTaskStatus
    priority: int
    due_at: datetime | None
    created_at: datetime
    claimed_by_me: bool
    on_behalf_of: UserSummary | None     # gdy widoczne dzięki zastępstwu
    process_instance_id: UUID
    process_title: str
    process_model_name: str
    business_key: str | None
    # 2026-10-20: zob. HumanTaskRead.process_instance_lock_version.
    process_instance_lock_version: int


class MyTaskFilter(Pagination):
    only_claimed: bool = False
    overdue: bool = False
    process_model_key: ProcessKey | None = None


class TaskFormRead(BaseModel):
    """Formularz dla react-jsonschema-form."""
    task_id: UUID
    task_name: str
    json_schema: JsonDict
    ui_schema: JsonDict = Field(default_factory=dict)
    form_data: JsonDict = Field(default_factory=dict)
    documents: list["DocumentRead"] = []
    process_lock_version: int


class ProcessFormRead(BaseModel):
    """Formularz startowy procesu (startEvent) dla react-jsonschema-form."""
    process_model_key: str
    bpmn_task_id: str | None = None
    json_schema: JsonDict = Field(default_factory=dict)
    ui_schema: JsonDict = Field(default_factory=dict)
    form_data: JsonDict = Field(default_factory=dict)


class TaskCompleteRequest(InputModel):
    data: JsonDict
    expected_lock_version: int = Field(ge=0)


class TaskClaimRequest(InputModel):
    expected_lock_version: int = Field(ge=0)


# =====================================================================
# 5. Dokumenty (pliki w Paperless-ngx)
# =====================================================================

class DocumentCreate(InputModel):
    """Metadane wysyłane razem z plikiem (multipart/form-data)."""
    doc_type: Annotated[str, StringConstraints(min_length=1, max_length=50)]
    title: Title
    metadata: JsonDict = Field(default_factory=dict)
    assign_registry_number: bool = False
    process_instance_id: UUID | None = None
    link_role: DocumentLinkRole = DocumentLinkRole.ATTACHMENT
    human_task_id: UUID | None = None


class DocumentUpdate(InputModel):
    title: Title | None = None
    metadata: JsonDict | None = None
    status: DocumentStatus | None = None


class DocumentVersionCreate(InputModel):
    """Metadane nowej wersji pliku (multipart/form-data)."""
    label: Annotated[str, StringConstraints(max_length=200)] | None = None
    human_task_id: UUID | None = None


class DocumentVersionRead(ReadModel):
    id: UUID
    document_id: UUID
    version_no: int
    label: str | None
    file_name: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    sha256: Sha256
    ingest_status: IngestStatus
    ingest_error: str | None
    ingested_at: datetime | None
    paperless_version_id: PaperlessId | None
    uploaded_by: UserSummary
    uploaded_at: datetime
    human_task_id: UUID | None

    @property
    def is_available(self) -> bool:
        """Plik da się pobrać: z Paperlessa albo jeszcze ze stagingu."""
        return self.ingest_status in (IngestStatus.DONE, IngestStatus.STAGED,
                                      IngestStatus.SUBMITTED)


class DocumentVersionInternal(DocumentVersionRead):
    """Pola techniczne – tylko dla serwisu i workera."""
    staging_path: str | None
    paperless_task_id: str | None


class DocumentRead(ReadModel):
    id: UUID
    doc_type: str
    registry_number: str | None
    title: str
    status: DocumentStatus
    # W ORM atrybut nazywa się doc_metadata ("metadata" jest zarezerwowane
    # w klasach deklaratywnych SQLAlchemy); w JSON-ie pozostaje "metadata".
    metadata: JsonDict = Field(validation_alias=AliasChoices("doc_metadata", "metadata"))
    paperless_document_id: PaperlessId | None
    current_version_no: int | None
    created_by: UserSummary
    created_at: datetime
    current_version: DocumentVersionRead | None = None


class DocumentDetail(DocumentRead):
    versions: list[DocumentVersionRead] = []
    process_instance_ids: list[UUID] = []


class ProcessInstanceDocumentLink(InputModel):
    document_id: UUID
    role: DocumentLinkRole = DocumentLinkRole.ATTACHMENT


class ProcessInstanceDocumentRead(ReadModel):
    document: DocumentRead
    role: DocumentLinkRole
    added_at: datetime


# ---------------------------------------------------------------------
# DTO klienta Paperless-ngx
# Odpowiedzi zewnętrznego API: extra="ignore", żeby nowe pola w kolejnych
# wersjach Paperlessa nie psuły integracji. Pola zweryfikuj z dokumentacją
# API swojej wersji (3.0 przeprojektował system zadań).
# ---------------------------------------------------------------------

class PaperlessModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class PaperlessTaskState(StrEnum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REVOKED = "REVOKED"


class PaperlessTask(PaperlessModel):
    task_id: str
    status: PaperlessTaskState
    related_document: int | None = None
    result: str | None = None

    @property
    def finished(self) -> bool:
        return self.status in (PaperlessTaskState.SUCCESS,
                               PaperlessTaskState.FAILURE,
                               PaperlessTaskState.REVOKED)


class PaperlessDocument(PaperlessModel):
    id: PaperlessId
    title: str
    created: datetime | None = None
    added: datetime | None = None
    original_file_name: str | None = None
    mime_type: str | None = None
    tags: list[int] = []
    document_type: int | None = None
    custom_fields: list[JsonDict] = []


class PaperlessUploadMeta(BaseModel):
    """Metadane przekazywane przy uploadzie. Tagi i typ dokumentu to ID
    obiektów w Paperlessie – mapowanie doc_type → ID trzymaj w konfiguracji."""
    title: str
    document_type: int | None = None
    tags: list[int] = []
    custom_fields: dict[int, Any] = {}


# =====================================================================
# 6. Komentarze
# =====================================================================

class CommentCreate(InputModel):
    body: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
    human_task_id: UUID | None = None
    document_id: UUID | None = None


class CommentRead(ReadModel):
    id: UUID
    process_instance_id: UUID
    human_task_id: UUID | None
    document_id: UUID | None
    author: UserSummary
    body: str
    created_at: datetime


# =====================================================================
# 7. Audyt
# =====================================================================

class ProcessEventRead(ReadModel):
    id: int
    process_instance_id: UUID
    human_task_id: UUID | None
    event_type: EventType
    actor: UserSummary | None          # None = system
    on_behalf_of: UserSummary | None
    payload: JsonDict
    created_at: datetime


class ProcessEventCreateInternal(BaseModel):
    process_instance_id: UUID
    event_type: EventType
    human_task_id: UUID | None = None
    # 2026-10-05: id użytkowników to int (users.id) po połączeniu tabel
    # użytkowników - patrz UserSummary i wf/dependencies.get_current_wf_user.
    actor_id: int | None = None
    on_behalf_of_id: int | None = None
    payload: JsonDict = Field(default_factory=dict)


# =====================================================================
# 8. Zadania w tle i powiadomienia
# =====================================================================

class EngineJobCreateInternal(BaseModel):
    job_type: JobType
    process_instance_id: UUID | None = None
    payload: JsonDict = Field(default_factory=dict)
    run_at: datetime | None = None          # None = teraz
    max_attempts: int = Field(default=5, ge=1, le=50)


class EngineJobRead(ReadModel):
    id: int
    process_instance_id: UUID | None
    job_type: JobType
    payload: JsonDict
    run_at: datetime
    status: JobStatus
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime


# Typowane payloady zadań – walidowane przy tworzeniu i w workerze.

class PaperlessSubmitPayload(BaseModel):
    document_version_id: UUID


class PaperlessPollPayload(BaseModel):
    document_version_id: UUID
    paperless_task_id: str


class DeliverMessagePayload(BaseModel):
    message_name: str
    correlation: JsonDict = Field(default_factory=dict)
    data: JsonDict = Field(default_factory=dict)


class NotificationRead(ReadModel):
    id: int
    kind: str
    title: str
    link: str | None
    payload: JsonDict
    created_at: datetime
    read_at: datetime | None


class NotificationMarkRead(InputModel):
    ids: list[int] = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------
# Signius (wf/routers/signius_api.py) - podpis elektroniczny dokumentu
# korespondencji przechowywanego jako Frappe Education Student
# Correspondence; ProcessInstance.business_key = nazwa dokumentu Frappe.
# ---------------------------------------------------------------------

class SigniusRequestSignatureRequest(InputModel):
    signee_email: EmailStr
    signee_phone: Annotated[str, StringConstraints(min_length=6, max_length=32)]
    qualified: bool = True


class SigniusStatusRead(BaseModel):
    """Stan integracji podpisu dla instancji procesu - nie ma odpowiednika
    tabeli w bazie huba (dane trzymane w Frappe), stąd `BaseModel`, nie
    `ReadModel`."""
    process_instance_id: UUID
    business_key: str
    signature_status: str
    signius_folder_id: str | None = None
    signius_document_id: str | None = None
    signed_document_url: str | None = None


# ---------------------------------------------------------------------
# e-Doręczenia (wf/routers/edr_api.py - alternatywa dla WEBCON, zob.
# analiza_edr.md): korespondencja w wf.Document/DocumentVersion, pliki w
# Paperless-ngx (connector/paperless), silnik - ten sam `wf`.
# ---------------------------------------------------------------------

class EdrSyncInboxResponse(BaseModel):
    """Wynik `connector.edoreczenia.edr_paperless.sync_inbox` - nowe
    instancje utworzone dla wiadomości, których jeszcze nie było
    (deduplikacja przez `ProcessInstance.business_key`)."""
    created: int
    process_instance_ids: list[UUID]


class EdrFetchEvidencesResponse(BaseModel):
    fetched: int


class EdrSendRequest(InputModel):
    test: bool = False


class EdrSendResponse(BaseModel):
    process_instance_id: UUID
    message_id: str


# ---------------------------------------------------------------------
# 2026-10-15: słowniki wyboru (wf/routers/dictionary_api.py,
# wf/routers/user_directory_api.py) - jeden generyczny schemat odpowiedzi
# dla pól `AsyncSelectWidget`/`TreeSelectWidget` wygenerowanych migracją
# Webcon -> RJSF (zob. rjsf.md i spiffworkflow-core/zaleznosci.md). Nie jest
# to `ReadModel` z `from_attributes=True`, bo nie ma jednego modelu ORM -
# wartości pochodzą z `ProcessInstance`, z REST Frappe Education albo z
# `User`/`OrgUnit`, zależnie od słownika (wf/services/dictionary_service.py).
# ---------------------------------------------------------------------

class OptionRead(BaseModel):
    value: str
    label: str


# ---------------------------------------------------------------------
# 2026-10-18: wiersze słowników płaskich (kod/etykieta) - tabela
# `dictionary_entry` (wf/models/wf.py::DictionaryEntry), zarządzana przez
# wf/routers/dictionary_entry_api.py. Zastępuje dla PROSTYCH słowników
# dotychczasowy obieg BPMN "Krok słownikowy" (zob. komentarz przy
# DictionaryEntry) - admin CRUD, nie proces.
# ---------------------------------------------------------------------

class DictionaryEntryRead(ReadModel):
    id: UUID
    dictionary_key: str
    code: str
    label: str
    sort_order: int
    is_active: bool
    extra: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DictionaryEntryCreate(InputModel):
    dictionary_key: NonEmptyStr
    code: NonEmptyStr
    label: NonEmptyStr
    sort_order: int = 0
    is_active: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class DictionaryEntryUpdate(InputModel):
    """`dictionary_key` celowo nieedytowalny - przeniesienie wiersza między
    słownikami nie ma sensownej semantyki; usuń i stwórz nowy."""
    code: NonEmptyStr | None = None
    label: NonEmptyStr | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    extra: dict[str, Any] | None = None


class DictionaryEntryPage(BaseModel):
    items: list[DictionaryEntryRead]
    total: int


# ---------------------------------------------------------------------
# 2026-10-19: kontrahenci i ich adresy - tabele `contact`/`contact_address`
# (wf/models/wf.py) zastępujące dla słownika "Adres" (adres-e-doreczen)
# dotychczasowy hack "ProcessInstance = kontrahent".
# Zob. docstring modelu `Contact` o podziale tożsamość/adres.
# ---------------------------------------------------------------------

class ContactAddressRead(ReadModel):
    id: UUID
    contact_id: UUID
    label: str
    ade: str | None = None
    is_primary: bool
    valid_from: date | None = None
    address: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ContactAddressCreate(InputModel):
    label: NonEmptyStr
    ade: str | None = None
    is_primary: bool = False
    valid_from: date | None = None
    address: dict[str, Any] = Field(default_factory=dict)


class ContactAddressUpdate(InputModel):
    label: NonEmptyStr | None = None
    ade: str | None = None
    is_primary: bool | None = None
    valid_from: date | None = None
    address: dict[str, Any] | None = None


class ContactRead(ReadModel):
    id: UUID
    nip: str | None = None
    pesel: str | None = None
    krs: str | None = None
    regon: str | None = None
    company_name: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    title: str | None = None
    legal_form: str | None = None
    assigned_operator: str | None = None
    is_active: bool
    extra: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    addresses: list[ContactAddressRead] = Field(default_factory=list)


class ContactCreate(InputModel):
    nip: str | None = None
    pesel: str | None = None
    krs: str | None = None
    regon: str | None = None
    company_name: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    title: str | None = None
    legal_form: str | None = None
    assigned_operator: str | None = None
    is_active: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class ContactUpdate(InputModel):
    nip: str | None = None
    pesel: str | None = None
    krs: str | None = None
    regon: str | None = None
    company_name: str | None = None
    last_name: str | None = None
    first_name: str | None = None
    title: str | None = None
    legal_form: str | None = None
    assigned_operator: str | None = None
    is_active: bool | None = None
    extra: dict[str, Any] | None = None


class ContactPage(BaseModel):
    items: list[ContactRead]
    total: int


# ---------------------------------------------------------------------
# 2026-10-20: Wniosek zakupowy (wf/models/wf.py::PurchaseRequest) - zob.
# galwork-book, czesc-3-obszary/dzialy/zakupy.md. `*Create` niesie tylko
# pola, które wypełnia zamawiający - `requires_*` liczy
# wf/services/purchase_request_service.py (nie klient), `status` czyta się
# z `ProcessInstanceRead` powiązanej instancji (zob. docstring modelu).
# ---------------------------------------------------------------------

class PurchaseRequestCreate(InputModel):
    # 2026-10-20: opcjonalny - dostawca może zostać przypisany później (np. po wyborze dostawcy)
    contact_id: UUID | None = None
    title: Title
    justification: str | None = None
    amount_estimated: Decimal = Field(gt=0)
    currency: NonEmptyStr = "PLN"
    expense_type: PurchaseExpenseType
    purchase_kind: PurchaseKind
    project_code: str | None = None


class PurchaseRequestRead(ReadModel):
    id: UUID
    process_instance_id: UUID | None = None
    tender_process_instance_id: UUID | None = None
    contact_id: UUID | None = None
    title: str
    justification: str | None = None
    amount_estimated: Decimal
    currency: str
    expense_type: PurchaseExpenseType
    purchase_kind: PurchaseKind
    project_code: str | None = None
    requires_investment_approval: bool
    requires_project_owner_approval: bool
    requires_additional_approval: bool
    requires_tender: bool
    requires_warehouse_receipt: bool
    created_at: datetime
    updated_at: datetime
    contact: ContactRead | None = None
    process_instance: ProcessInstanceRead | None = None


class PurchaseRequestPage(BaseModel):
    items: list[PurchaseRequestRead]
    total: int


# ---------------------------------------------------------------------
# 2026-10-17: synchronizacja ProcessModel z plikami w spiffworkflow/
# (wf/services/filesystem_sync.py, wf/routers/process_model_router.py::
# sync_process_models_from_disk) - ręczne, pojedyncze seedowanie
# (scripts/seed_process_model.py) było łatwo zapomnieć po edycji pliku na
# dysku. Synchronizacja tworzy/aktualizuje WYŁĄCZNIE wersje draft - nigdy
# nie publikuje automatycznie (require_process_admin + ręczne "Publikuj" w
# UI to świadoma bariera przed niekontrolowanym wejściem zmiany na żywo).
# ---------------------------------------------------------------------

class ProcessModelSyncRequest(InputModel):
    dry_run: bool = False


class ProcessModelSyncResult(ReadModel):
    folder: str
    key: str
    status: str
    detail: str = ''


# ---------------------------------------------------------------------
# 2026-10-18: faktury zakupu z ERPNext (connector/erpnext,
# wf/services/erpnext_invoice_service.py) oczekujące na akcję workflow
# "Zatwierdź" - widget "Faktury do zatwierdzenia" na pulpicie
# galwork-dashboard. Źródłem jest REST ERPNext (Frappe) odpytywany na
# żywo, nie baza huba - stąd `BaseModel`, nie `ReadModel`, tak jak
# `SigniusStatusRead`/`OptionRead` powyżej.
# ---------------------------------------------------------------------

class PurchaseInvoiceSummary(BaseModel):
    """Jedna faktura zakupu czekająca na zatwierdzenie (albo wcześniejszy
    stan tego samego workflow - zob. `[erpnext] invoice_pending_states`
    w config/config.ini). Nazwy pól standardowe dla Doctype "Purchase
    Invoice" we Frappe/ERPNext; `extra` niesie dodatkowe pola skonfigurowane
    w `[erpnext] invoice_extra_fields` (np. numer/status KSeF, gdy to pole
    we własnej instalacji ERPNext zostanie ustalone)."""
    name: str
    title: str
    supplier: str | None = None
    supplier_name: str | None = None
    bill_no: str | None = None
    grand_total: float | None = None
    currency: str | None = None
    posting_date: str | None = None
    due_date: str | None = None
    workflow_state: str
    url: str
    extra: dict[str, Any] = Field(default_factory=dict)


class PurchaseInvoicePendingApprovalPage(BaseModel):
    items: list[PurchaseInvoiceSummary]
    total: int


# ---------------------------------------------------------------------
# 2026-10-18: kolejka dokumentów z Paperless-ngx filtrowana po tagu
# (wf/services/paperless_document_service.py) - widget "kafelek z
# określonym rodzajem dokumentów w paperless". Pomysł:
# n8n ląduje PDF-y faktur z maila w Paperless z ustalonym tagiem (np. gdy
# PDF nie da się pobrać z KSeF - tam jest tylko XML), bez żadnej analizy
# treści w galwork - tu tylko wyświetlamy, co n8n już tam wgrał.
# ---------------------------------------------------------------------

class PaperlessDocumentSummary(BaseModel):
    id: int
    title: str
    created: str | None = None
    original_file_name: str | None = None
    url: str


class PaperlessDocumentPage(BaseModel):
    items: list[PaperlessDocumentSummary]
    total: int


# Rozwiązanie referencji w przód (TaskFormRead → DocumentRead)
TaskFormRead.model_rebuild()
