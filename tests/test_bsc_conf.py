# tests/conftest.py
# Klient API na SQLite w pamięci - testy routerów bez Postgresa i bez
# Keycloaka: require_operator / get_current_principal są podmienione na
# stałego aktora (ochrona tokenem jest testowana osobno, kiedy powstanie).
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import bcs.models.audit  # noqa: F401 - rejestracja wszystkich tabel w Base.metadata
import bcs.models.cycle  # noqa: F401
import bcs.models.dispute  # noqa: F401
import bcs.models.member  # noqa: F401
import bcs.models.network  # noqa: F401
import bcs.models.obligation  # noqa: F401
import bcs.models.settlement  # noqa: F401
import bcs.models.source_system  # noqa: F401
import bcs.models.statement  # noqa: F401
from api.dependencies import get_db
from api.security import BCS_OPERATOR_ROLE, CurrentPrincipal, get_current_principal, require_operator
from db.base import Base
from main import app
from router import app_bcs
from models.user import User
from schemas.token_schemas import Token

TEST_SUB = "user-sub-1"


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    principal = CurrentPrincipal(
        user=User(id=1, keycloak_sub=TEST_SUB, name="tester", email="tester@example.com"),
        token=Token(username="tester", email="tester@example.com", message="", scope="", roles=[], sub=TEST_SUB),
        claims={"sub": TEST_SUB, "realm_access": {"roles": [BCS_OPERATOR_ROLE]}},
    )
    # bcs jest zamontowany w hubie jako osobna aplikacja
    # (router.py: v1router.mount("/bcs", app_bcs)), a dependency_overrides
    # działa per aplikacja - podmieniamy zależności w app_bcs, a ścieżki
    # testów (/api/...) liczymy od /v1/bcs.
    app_bcs.dependency_overrides[get_db] = _db
    app_bcs.dependency_overrides[require_operator] = lambda: principal
    app_bcs.dependency_overrides[get_current_principal] = lambda: principal
    yield TestClient(app, base_url="http://testserver/v1/bcs")
    app_bcs.dependency_overrides.clear()
    engine.dispose()
