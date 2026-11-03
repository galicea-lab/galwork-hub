# services/user_service.py
# ynchronizacja konta z tokenem Keycloak przeniesiona do
# services/auth_service.get_or_create_local_user (jedna ścieżka rozpoznawania
# użytkownika po keycloak_sub/email.
from sqlalchemy.orm import Session
from models.user import User

from schemas.token_schemas import Token
from schemas.account_schemas import AccountCreate
from services.auth_service import get_or_create_local_user


# --- Funkcje CRUD dla Użytkownika/Konta ---

def get_or_create_user_from_token(db: Session, token_data: Token) -> User:
  """Wyszukuje użytkownika po `sub` (keycloak_sub), a w drugiej kolejności po
  adresie email z tokenu. Jeśli nie istnieje - tworzy nowy na podstawie danych
  z tokenu. Zawsze zwraca obiekt użytkownika (models.user.User), czyli to samo
  konto, którego używa api/security.get_current_user oraz moduł wf."""
  return get_or_create_local_user(
    db,
    email=token_data.email,
    username=token_data.username,
    sub=token_data.sub,
  )


def get_user_by_email(db: Session, email: str) -> User | None:
  return db.query(User).filter(User.email == email).first()


def get_user_by_keycloak_sub(db: Session, keycloak_sub: str) -> User | None:
  """ rozpoznanie użytkownika po stabilnym `sub` z Keycloaka."""
  return db.query(User).filter(User.keycloak_sub == keycloak_sub).first()


def get_user_email(db: Session, id: int) -> str:
  user = db.query(User).filter(User.id == id).first()
  return user.email if user else ''


def create_user(db: Session, user: AccountCreate) -> User:
  # hasła lokalne nie są używane (uwierzytelnianie przez
  # Keycloak) - pole zostaje wypełnione znacznikiem konta SSO.
  db_user = User(
    email=user.email,
    hashed_password='SSO_USER_NO_PASSWORD',
    name=user.name,
    nip=user.nip,
    phone=user.phone,
  )
  db.add(db_user)
  db.commit()
  db.refresh(db_user)
  return db_user
