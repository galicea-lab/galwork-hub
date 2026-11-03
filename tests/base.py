# tests/base.py
# wspólna baza dla testów - testowa sesja bazy (SQLite w pamięci)
# i podmiana zależności uwierzytelniania, żeby dało się testować moduł wf bez
# realnego Keycloaka (patrz README.md).
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.base import Base
from models.application import Application  # noqa: F401  (rejestracja metadanych)
from models.user import User
import wf.models  # noqa: F401  (rejestracja metadanych workflow)

from wf.schemas.wf_schemas import CurrentUser

BPMN_PROCESS = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:flowable="http://flowable.org/bpmn"
  id="def_1" targetNamespace="urn:galwork:test">
  <bpmn:process id="proc_test" isExecutable="true">
    <bpmn:startEvent id="start_1"/>
    <bpmn:sequenceFlow id="flow_1" sourceRef="start_1" targetRef="task_recenzja"/>
    <bpmn:userTask id="task_recenzja" name="Recenzja" flowable:candidateGroups="recenzenci"/>
    <bpmn:sequenceFlow id="flow_2" sourceRef="task_recenzja" targetRef="task_podpis"/>
    <bpmn:userTask id="task_podpis" name="Podpis" flowable:assignee="jurek"/>
    <bpmn:sequenceFlow id="flow_3" sourceRef="task_podpis" targetRef="end_1"/>
    <bpmn:endEvent id="end_1"/>
  </bpmn:process>
</bpmn:definitions>
"""


class ApiTestCase(unittest.TestCase):
  """TestClient z testową bazą w pamięci i jednym zalogowanym użytkownikiem."""

  # Ustawiane w prepare() (kontekst z tokenu Keycloak).
  current_user: CurrentUser

  def setUp(self) -> None:
    from main import app
    # aplikacje są montowane jako osobne FastAPI (router.py:
    # app_wf, app_pm, app_galwork pod v1router), a `dependency_overrides`
    # działa per aplikacja - trzeba podmienić zależności we wszystkich.
    from router import app_galwork, app_pm, app_wf, v1router

    # SQLite ':memory:' + StaticPool - bez tego każde połączenie
    # (a TestClient obsługuje żądania w innym wątku niż create_all) dostałoby
    # własną, pustą bazę i testy padałyby na "no such table".
    self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                                poolclass=StaticPool)
    self.session_factory = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
    Base.metadata.create_all(self.engine)

    self.app = app
    self.sub_apps = (app, v1router, app_wf, app_pm, app_galwork)
    self.db = self.session_factory()

    from api.dependencies import get_db
    from wf.dependencies import get_current_wf_user

    def _get_db():
      yield self.db

    for sub_app in self.sub_apps:
      sub_app.dependency_overrides[get_db] = _get_db
      sub_app.dependency_overrides[get_current_wf_user] = lambda: self.current_user
    self.client = TestClient(app)
    self.prepare()

  def prepare(self) -> None:
    """Hook dla podklas: dane testowe (użytkownik, proces) po inicjalizacji bazy."""

  def tearDown(self) -> None:
    for sub_app in self.sub_apps:
      sub_app.dependency_overrides.clear()
    self.db.close()
    self.engine.dispose()

  # --- pomocniki -------------------------------------------------------

  def create_user(self, email: str = 'jurek@example.com', name: str = 'jurek',
                  keycloak_sub: str = 'sub-1') -> User:
    user = User(email=email, name=name, keycloak_sub=keycloak_sub,
                hashed_password='SSO_USER_NO_PASSWORD')
    self.db.add(user)
    self.db.commit()
    self.db.refresh(user)
    return user

  def create_process_model(self, key: str = 'test-proces',
                           bpmn: str = BPMN_PROCESS,
                           bpmn_process_id: str = 'proc_test') -> str:
    """Tworzy model procesu z opublikowaną wersją i plikiem BPMN.

    `bpmn_process_id` wydzielony jako parametr (był
    zaszyty jako 'proc_test') - testy, które podają własny `bpmn` z innym
    `<bpmn:process id="...">` (np. tests/test_edr_paperless_api.py), muszą
    móc też podać zgodny `bpmn_process_id`, inaczej
    `wf/services/process_service.py::load_spec` nie znajduje procesu."""
    from wf.models import ProcessModel, ProcessModelFile, ProcessModelVersion
    from wf.schemas.wf_schemas import ProcessFileType, ProcessModelVersionStatus

    model = ProcessModel(key=key, name='Proces testowy')
    self.db.add(model)
    self.db.flush()
    version = ProcessModelVersion(
      process_model_id=model.id,
      version=1,
      status=ProcessModelVersionStatus.PUBLISHED,
      bpmn_process_id=bpmn_process_id,
      content_hash='sha256:test',
      spec_json={'forms': {'task_recenzja': {
        'type': 'object',
        'required': ['ocena'],
        'properties': {'ocena': {'type': 'integer'}},
      }}},
    )
    self.db.add(version)
    self.db.flush()
    self.db.add(ProcessModelFile(version_id=version.id, file_name='proc_test.bpmn',
                                 file_type=ProcessFileType.BPMN, content=bpmn,
                                 sha256='sha256:test'))
    self.db.commit()
    self.db.refresh(model)
    return model.id
