# models/application.py
import datetime
import enum
from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey, JSON, Enum as PyEnum
from sqlalchemy.orm import relationship
from db.base import Base


class ApplicationStatus(str, enum.Enum):
    PROVISIONING = "PROVISIONING"  # tworzenie klienta Keycloak w toku
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"        # zablokowana ręcznie / po naruszeniu limitu
    DELETED = "DELETED"            # soft-delete, klient Keycloak usunięty


class Application(Base):
    __tablename__ = 'applications'

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    name = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)

    keycloak_client_id = Column(String(150), unique=True, index=True, nullable=False)
    keycloak_internal_id = Column(String(64), nullable=False)

    redirect_uris = Column(JSON, nullable=False, default=list)
    webhook_url = Column(String(500), nullable=True)

    issue_limit = Column(Float, nullable=False, default=0.0)
    issued_total = Column(Float, nullable=False, default=0.0)

    status = Column(PyEnum(ApplicationStatus), nullable=False, default=ApplicationStatus.PROVISIONING)

    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    owner = relationship("User")
