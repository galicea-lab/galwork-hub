# wf/models/immutability.py
# 2026-11-17:  ochrona przed zmianą danych, na które powołują się akty
# i dziennik - wyzwalacze bazy danych (PostgreSQL i SQLite).
"""
Niezmienność rekordów chronionych.

Chronione (UPDATE/DELETE odrzucane przez bazę, niezależnie od kodu, który
je wykonuje):

  * `process_event` - dziennik tylko do dopisywania,
  * `process_model_version` poza stanem `draft` - treść (`content_hash`,
    `bpmn_process_id`, numer, model, `spec_json`, commit git) i dane publikacji
    (w tym tag wydania); dozwolone
    są tylko przejścia statusu (published -> retired, ponowna publikacja
    retired -> published z nowymi danymi publikacji); usunięcie tylko szkicu,
  * `process_model_file` wersji innej niż `draft`,
  * `document_version` - treść i metadane pliku (skrót, nazwa, rozmiar,
    autor, numer); zmieniać wolno stan wysyłki do Paperless, a datę wejścia
    w życie (`effective_from`) tylko ustawić raz; usuwać nie wolno,
  * `document` i `process_instance_document` - usuwanie,
  * `document_relation` - usuwanie i zmiana poza uzupełnieniem
    `target_version_no`,
  * `process_rollout` - usuwanie i zmiana poza zamknięciem (skrócenie okresu),
  * `process_instance` - zmiana trybu użycia (`rollout_id`, `usage_mode`).

Wyjątek dla narzędzi administratora (scripts/admin_delete_process_instances.py,
scripts/clear_process_data.py) - tylko PostgreSQL, w tej samej transakcji:

    SET LOCAL galwork.allow_purge = 'on';

Na SQLite (testy, środowisko deweloperskie) wyjątku nie ma.

Wyzwalacze tworzy migracja (`a9d4e2b7c316`) oraz `Base.metadata.create_all`
(`install`), więc obowiązują także w testach.
"""
from __future__ import annotations

from sqlalchemy import DDL, MetaData, event

PURGE_SETTING = 'galwork.allow_purge'

# --- PostgreSQL -------------------------------------------------------------

_PG_FUNCTIONS = [
    f"""
CREATE OR REPLACE FUNCTION galwork_purge_allowed() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('{PURGE_SETTING}', true), '') = 'on'
$$""",
    """
CREATE OR REPLACE FUNCTION galwork_protect_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF galwork_purge_allowed() THEN RETURN COALESCE(NEW, OLD); END IF;
  RAISE EXCEPTION 'galwork: % na % - rekord chroniony przed zmianą', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'restrict_violation';
END $$""",
    """
CREATE OR REPLACE FUNCTION galwork_protect_model_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF galwork_purge_allowed() OR OLD.status = 'draft' THEN RETURN COALESCE(NEW, OLD); END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'galwork: wersja % modelu procesu jest opublikowana - nie można jej usunąć',
      OLD.version USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.process_model_id IS DISTINCT FROM OLD.process_model_id
     OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.bpmn_process_id IS DISTINCT FROM OLD.bpmn_process_id
     OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.git_commit IS DISTINCT FROM OLD.git_commit
     OR NEW.git_dirty IS DISTINCT FROM OLD.git_dirty
     OR NEW.status = 'draft'
     OR (NEW.status <> 'published' AND (
           NEW.spec_json::text IS DISTINCT FROM OLD.spec_json::text
           OR NEW.published_at IS DISTINCT FROM OLD.published_at
           OR NEW.published_by IS DISTINCT FROM OLD.published_by
           OR NEW.git_tag IS DISTINCT FROM OLD.git_tag)) THEN
    RAISE EXCEPTION 'galwork: wersja % modelu procesu jest opublikowana - treść jest chroniona',
      OLD.version USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$""",
    """
CREATE OR REPLACE FUNCTION galwork_protect_model_file() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF galwork_purge_allowed()
     OR (SELECT status FROM process_model_version WHERE id = OLD.version_id) = 'draft' THEN
    RETURN COALESCE(NEW, OLD);
  END IF;
  RAISE EXCEPTION 'galwork: plik % należy do opublikowanej wersji modelu - jest chroniony',
    OLD.file_name USING ERRCODE = 'restrict_violation';
END $$""",
    """
CREATE OR REPLACE FUNCTION galwork_protect_document_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF galwork_purge_allowed() THEN RETURN COALESCE(NEW, OLD); END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'galwork: wersji dokumentu nie można usunąć' USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.document_id IS DISTINCT FROM OLD.document_id
     OR NEW.version_no IS DISTINCT FROM OLD.version_no
     OR NEW.label IS DISTINCT FROM OLD.label
     OR NEW.file_name IS DISTINCT FROM OLD.file_name
     OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
     OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
     OR NEW.sha256 IS DISTINCT FROM OLD.sha256
     OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by
     OR NEW.uploaded_at IS DISTINCT FROM OLD.uploaded_at
     OR NEW.human_task_id IS DISTINCT FROM OLD.human_task_id
     OR (OLD.effective_from IS NOT NULL AND NEW.effective_from IS DISTINCT FROM OLD.effective_from) THEN
    RAISE EXCEPTION 'galwork: wersja % dokumentu jest chroniona przed zmianą', OLD.version_no
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$""",
    """
CREATE OR REPLACE FUNCTION galwork_protect_document_relation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF galwork_purge_allowed() THEN RETURN COALESCE(NEW, OLD); END IF;
  IF TG_OP = 'DELETE'
     OR NEW.source_document_id IS DISTINCT FROM OLD.source_document_id
     OR NEW.target_document_id IS DISTINCT FROM OLD.target_document_id
     OR NEW.relation_type IS DISTINCT FROM OLD.relation_type
     OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
     OR (OLD.target_version_no IS NOT NULL
         AND NEW.target_version_no IS DISTINCT FROM OLD.target_version_no) THEN
    RAISE EXCEPTION 'galwork: relacja aktów jest chroniona przed zmianą'
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$""",
]

_PG_FUNCTIONS += [
    # 2026-11-17:  wdrożenia obiegów - wolno tylko zamknąć (skrócić okres)
    """
CREATE OR REPLACE FUNCTION galwork_protect_rollout() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF galwork_purge_allowed() THEN RETURN COALESCE(NEW, OLD); END IF;
  IF TG_OP = 'DELETE'
     OR NEW.process_model_id IS DISTINCT FROM OLD.process_model_id
     OR NEW.mode IS DISTINCT FROM OLD.mode
     OR NEW.org_unit_ids::text IS DISTINCT FROM OLD.org_unit_ids::text
     OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
     OR NEW.act_document_id IS DISTINCT FROM OLD.act_document_id
     OR NEW.act_version_no IS DISTINCT FROM OLD.act_version_no
     OR NEW.previous_procedure IS DISTINCT FROM OLD.previous_procedure
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR (OLD.valid_to IS NOT NULL AND (NEW.valid_to IS NULL OR NEW.valid_to > OLD.valid_to))
     OR (OLD.closed_at IS NOT NULL AND NEW.closed_at IS DISTINCT FROM OLD.closed_at) THEN
    RAISE EXCEPTION 'galwork: wdrożenie obiegu jest chronione - można je tylko zamknąć'
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$""",
    """
CREATE OR REPLACE FUNCTION galwork_protect_instance_mode() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF galwork_purge_allowed() THEN RETURN NEW; END IF;
  IF NEW.rollout_id IS DISTINCT FROM OLD.rollout_id
     OR NEW.usage_mode IS DISTINCT FROM OLD.usage_mode THEN
    RAISE EXCEPTION 'galwork: tryb użycia sprawy ustalony przy starcie jest chroniony'
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END $$""",
]

# (tabela, zdarzenia, funkcja)
_PG_TRIGGERS = [
    ('process_event', 'UPDATE OR DELETE', 'galwork_protect_append_only'),
    ('document', 'DELETE', 'galwork_protect_append_only'),
    ('process_instance_document', 'DELETE', 'galwork_protect_append_only'),
    ('process_model_version', 'UPDATE OR DELETE', 'galwork_protect_model_version'),
    ('process_model_file', 'UPDATE OR DELETE', 'galwork_protect_model_file'),
    ('document_version', 'UPDATE OR DELETE', 'galwork_protect_document_version'),
    ('document_relation', 'UPDATE OR DELETE', 'galwork_protect_document_relation'),
    ('process_rollout', 'UPDATE OR DELETE', 'galwork_protect_rollout'),
    ('process_instance', 'UPDATE', 'galwork_protect_instance_mode'),
]


def postgresql_statements(tables: set[str] | frozenset[str] | None = None) -> list[str]:
    """Funkcje i wyzwalacze; `tables` - tylko wyzwalacze tych tabel (migracja
    zna tylko tabele istniejące w jej chwili - nowsze dokładają kolejne)."""
    statements = [f.strip() for f in _PG_FUNCTIONS]
    for table, events, function in _PG_TRIGGERS:
        if tables is not None and table not in tables:
            continue
        name = f'trg_{table}_protect'
        statements.append(f'DROP TRIGGER IF EXISTS {name} ON {table}')
        statements.append(f'CREATE TRIGGER {name} BEFORE {events} ON {table} '
                          f'FOR EACH ROW EXECUTE FUNCTION {function}()')
    return statements


#: 2026-11-17:  tabele chronione od migracji a9d4e2b7c316 (pierwsze wyzwalacze)
TABLES_V1 = frozenset({'process_event', 'document', 'process_instance_document',
                       'process_model_version', 'process_model_file', 'document_version',
                       'document_relation'})
#: 2026-11-17:  od migracji a9d4e2b7c318 (wdrożenia obiegów, tryb sprawy)
TABLES_V2 = TABLES_V1 | {'process_rollout', 'process_instance'}


def postgresql_drop_statements() -> list[str]:
    statements = [f'DROP TRIGGER IF EXISTS trg_{table}_protect ON {table}'
                  for table, _events, _function in _PG_TRIGGERS]
    statements += [f'DROP FUNCTION IF EXISTS {name}()' for name in (
        'galwork_protect_instance_mode', 'galwork_protect_rollout',
        'galwork_protect_document_relation', 'galwork_protect_document_version',
        'galwork_protect_model_file', 'galwork_protect_model_version',
        'galwork_protect_append_only', 'galwork_purge_allowed')]
    return statements


# --- SQLite (testy, dev) ----------------------------------------------------

def _sqlite(name: str, when: str, table: str, condition: str | None, message: str) -> str:
    where = f' WHEN {condition}' if condition else ''
    return (f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {when} ON {table}{where} "
            f"BEGIN SELECT RAISE(ABORT, 'galwork: {message}'); END")


_DV_CHANGED = ' OR '.join(
    f'NEW.{c} IS NOT OLD.{c}' for c in (
        'document_id', 'version_no', 'label', 'file_name', 'mime_type', 'size_bytes', 'sha256',
        'uploaded_by', 'uploaded_at', 'human_task_id'))
_PMV_CHANGED = (
    "NEW.process_model_id IS NOT OLD.process_model_id OR NEW.version IS NOT OLD.version "
    "OR NEW.bpmn_process_id IS NOT OLD.bpmn_process_id OR NEW.content_hash IS NOT OLD.content_hash "
    "OR NEW.created_at IS NOT OLD.created_at OR NEW.git_commit IS NOT OLD.git_commit "
    "OR NEW.git_dirty IS NOT OLD.git_dirty OR NEW.status = 'draft' "
    "OR (NEW.status <> 'published' AND (NEW.spec_json IS NOT OLD.spec_json "
    "OR NEW.published_at IS NOT OLD.published_at OR NEW.published_by IS NOT OLD.published_by "
    "OR NEW.git_tag IS NOT OLD.git_tag))")
_PMF_LOCKED = "(SELECT status FROM process_model_version WHERE id = OLD.version_id) <> 'draft'"


def sqlite_statements() -> list[str]:
    return [
        _sqlite('trg_process_event_no_update', 'UPDATE', 'process_event', None,
                'dziennik zdarzeń jest tylko do dopisywania'),
        _sqlite('trg_process_event_no_delete', 'DELETE', 'process_event', None,
                'dziennik zdarzeń jest tylko do dopisywania'),
        _sqlite('trg_document_no_delete', 'DELETE', 'document', None,
                'dokumentu nie można usunąć'),
        _sqlite('trg_process_instance_document_no_delete', 'DELETE', 'process_instance_document',
                None, 'powiązania dokumentu ze sprawą nie można usunąć'),
        _sqlite('trg_process_model_version_protect', 'UPDATE', 'process_model_version',
                f"OLD.status <> 'draft' AND ({_PMV_CHANGED})",
                'opublikowana wersja modelu procesu jest chroniona'),
        _sqlite('trg_process_model_version_no_delete', 'DELETE', 'process_model_version',
                "OLD.status <> 'draft'", 'opublikowanej wersji modelu nie można usunąć'),
        _sqlite('trg_process_model_file_protect', 'UPDATE', 'process_model_file', _PMF_LOCKED,
                'plik opublikowanej wersji modelu jest chroniony'),
        _sqlite('trg_process_model_file_no_delete', 'DELETE', 'process_model_file', _PMF_LOCKED,
                'plik opublikowanej wersji modelu jest chroniony'),
        _sqlite('trg_document_version_protect', 'UPDATE', 'document_version',
                f"{_DV_CHANGED} OR (OLD.effective_from IS NOT NULL "
                f"AND NEW.effective_from IS NOT OLD.effective_from)",
                'wersja dokumentu jest chroniona przed zmianą'),
        _sqlite('trg_document_version_no_delete', 'DELETE', 'document_version', None,
                'wersji dokumentu nie można usunąć'),
        _sqlite('trg_document_relation_protect', 'UPDATE', 'document_relation',
                "NEW.source_document_id IS NOT OLD.source_document_id "
                "OR NEW.target_document_id IS NOT OLD.target_document_id "
                "OR NEW.relation_type IS NOT OLD.relation_type "
                "OR NEW.effective_from IS NOT OLD.effective_from "
                "OR (OLD.target_version_no IS NOT NULL "
                "AND NEW.target_version_no IS NOT OLD.target_version_no)",
                'relacja aktów jest chroniona przed zmianą'),
        _sqlite('trg_document_relation_no_delete', 'DELETE', 'document_relation', None,
                'relacji aktów nie można usunąć'),
        # 2026-11-17:  wdrożenia obiegów i tryb sprawy
        _sqlite('trg_process_rollout_protect', 'UPDATE', 'process_rollout',
                "NEW.process_model_id IS NOT OLD.process_model_id OR NEW.mode IS NOT OLD.mode "
                "OR NEW.org_unit_ids IS NOT OLD.org_unit_ids OR NEW.valid_from IS NOT OLD.valid_from "
                "OR NEW.act_document_id IS NOT OLD.act_document_id "
                "OR NEW.act_version_no IS NOT OLD.act_version_no "
                "OR NEW.previous_procedure IS NOT OLD.previous_procedure "
                "OR NEW.created_at IS NOT OLD.created_at "
                "OR (OLD.valid_to IS NOT NULL AND (NEW.valid_to IS NULL OR NEW.valid_to > OLD.valid_to)) "
                "OR (OLD.closed_at IS NOT NULL AND NEW.closed_at IS NOT OLD.closed_at)",
                'wdrożenie obiegu jest chronione - można je tylko zamknąć'),
        _sqlite('trg_process_rollout_no_delete', 'DELETE', 'process_rollout', None,
                'wdrożenia obiegu nie można usunąć'),
        _sqlite('trg_process_instance_mode', 'UPDATE', 'process_instance',
                "NEW.rollout_id IS NOT OLD.rollout_id OR NEW.usage_mode IS NOT OLD.usage_mode",
                'tryb użycia sprawy ustalony przy starcie jest chroniony'),
    ]


def install(metadata: MetaData) -> None:
    """Wyzwalacze po `metadata.create_all` (testy, nowa baza bez migracji)."""
    for statement in postgresql_statements():
        # DDL formatuje tekst operatorem % - komunikaty RAISE używają %
        event.listen(metadata, 'after_create',
                     DDL(statement.replace('%', '%%')).execute_if(dialect='postgresql'))
    for statement in sqlite_statements():
        event.listen(metadata, 'after_create', DDL(statement).execute_if(dialect='sqlite'))
