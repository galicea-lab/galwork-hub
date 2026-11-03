# crud/crud_application.py
import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import lib.keycloak_utils as keycloak_admin
from models.application import Application, ApplicationStatus


def create_application(db: Session, owner_id: int, name: str, description: Optional[str],
                       redirect_uris: List[str], issue_limit: float) -> tuple[Application, str]:
    """Tworzy aplikację w bazie oraz powiązanego, dedykowanego confidential
    clienta w Keycloak (service account). Zwraca (aplikacja, client_secret) -
    sekret nie jest nigdzie zapisywany, to jedyny moment jego ujawnienia."""
    external_client_id = f"app-{uuid.uuid4()}"
    db_application = Application(
        owner_id=owner_id,
        name=name,
        description=description or '',
        keycloak_client_id=external_client_id,
        keycloak_internal_id="",
        redirect_uris=redirect_uris,
        issue_limit=float(issue_limit),
        issued_total=0.0,
        status=ApplicationStatus.PROVISIONING,
    )
    db.add(db_application)
    db.commit()
    db.refresh(db_application)

    try:
        kc_internal_id, client_secret = keycloak_admin.create_application_client(name, external_client_id)
    except Exception:
        db_application.status = ApplicationStatus.DELETED
        db.commit()
        raise

    db_application.keycloak_internal_id = kc_internal_id
    db_application.status = ApplicationStatus.ACTIVE
    db.commit()
    db.refresh(db_application)

    return db_application, client_secret


def get_application(db: Session, application_id: int) -> Optional[Application]:
    return db.query(Application).filter(Application.id == application_id).first()


def get_by_keycloak_client_id(db: Session, keycloak_client_id: str) -> Optional[Application]:
    return db.query(Application).filter(Application.keycloak_client_id == keycloak_client_id).first()


def get_applications_by_owner(db: Session, owner_id: int) -> List[Application]:
    return db.query(Application).filter(
        Application.owner_id == owner_id,
        Application.status != ApplicationStatus.DELETED,
    ).all()


def issue_currency(db: Session, application_id: int, amount: float) -> Application:
    """Emituje walutę na konto właściciela aplikacji, w granicach issue_limit.

    Blokuje wiersz Application (SELECT ... FOR UPDATE) na czas sprawdzenia limitu
    i zapisu salda, żeby dwa równoległe żądania /m2m/issue nie mogły łącznie
    przekroczyć issue_limit (ochrona przed TOCTOU)."""
    if amount <= 0:
        raise ValueError("Kwota emisji musi być dodatnia.")

    application = db.execute(
        select(Application).where(Application.id == application_id).with_for_update()
    ).scalar_one_or_none()
    if not application:
        raise ValueError("Aplikacja nie istnieje.")

    if application.issued_total + amount > application.issue_limit:
        raise ValueError("Przekroczono limit emisji dla tej aplikacji.")

    application.issued_total += amount

    db.commit()
    db.refresh(application)
    return application


def update_application(db: Session, application: Application, description: Optional[str] = None,
                       redirect_uris: Optional[List[str]] = None,
                       webhook_url: Optional[str] = None) -> Application:
    if description is not None:
        application.description = description
    if redirect_uris is not None:
        application.redirect_uris = redirect_uris
    if webhook_url is not None:
        application.webhook_url = webhook_url
    db.commit()
    db.refresh(application)
    return application


def suspend(db: Session, application: Application) -> Application:
    application.status = ApplicationStatus.SUSPENDED
    keycloak_admin.disable_client(application.keycloak_internal_id)
    db.commit()
    db.refresh(application)
    return application


def activate(db: Session, application: Application) -> Application:
    application.status = ApplicationStatus.ACTIVE
    keycloak_admin.enable_client(application.keycloak_internal_id)
    db.commit()
    db.refresh(application)
    return application


def soft_delete(db: Session, application: Application) -> Application:
    application.status = ApplicationStatus.DELETED
    keycloak_admin.delete_client(application.keycloak_internal_id)
    db.commit()
    db.refresh(application)
    return application


def rotate_secret(application: Application) -> str:
    return keycloak_admin.rotate_client_secret(application.keycloak_internal_id)
