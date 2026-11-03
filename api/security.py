# api/security.py
# 2026-10-05: dodana zależność `get_current_principal` (konto + claimy
# tokenu) używana przez moduł wf; `validate_token` przekazuje dalej claim
# `sub`.
import jwt
from jwt import PyJWKClient
from dataclasses import dataclass, field
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from functools import lru_cache
from sqlalchemy.orm import Session

# --- ZMIENIONE IMPORTY I USUNIĘCIE STAREJ KONFIGURACJI ---
from core.config import settings # <--- Zaimportuj centralne ustawienia
from crud import crud_application
from models.application import Application, ApplicationStatus
from models.user import User
from api.dependencies import get_db
from schemas.token_schemas import Token

from typing import Optional

from fastapi.security import OAuth2PasswordBearer

from services.user_service import get_or_create_user_from_token


# --- BUFOROWANIE KLUCZY (JWKS) Z OBSŁUGĄ ROTACJI KLUCZY (kid) ---
@lru_cache(maxsize=1)
def get_jwk_client(certs_url: str) -> PyJWKClient:
    """
    Klient JWKS Keycloak. Wybiera właściwy klucz na podstawie nagłówka `kid`
    tokenu (zamiast zawsze pierwszego klucza z listy) i odświeża zestaw kluczy
    automatycznie, gdy pojawi się nieznany `kid` (np. po rotacji kluczy w Keycloak).
    """
    return PyJWKClient(certs_url, cache_keys=True)


def _keycloak_issuer() -> str:
    return f"{settings.keycloak.server_url}/realms/{settings.keycloak.realm_name}"

# Wspólna zależność, która obsługuje "Bearer"
#
reusable_oauth2 = HTTPBearer()


# schemat OAuth2, który wymusza autoryzację, ale trzeba zdekodować token
#reusable_oauth2 = OAuth2PasswordBearer(
#    tokenUrl="oauth2/token"
#)

# Nowy schemat, który nie wymusza autoryzacji
optional_oauth2 = OAuth2PasswordBearer(
    tokenUrl="oauth2/token",
    auto_error=False  # To jest kluczowy parametr!
)

# ---  WALIDACJA ---
def validate_token_claims(token: str) -> dict:
    """
    Dekoduje token JWT i PEŁNI weryfikuje go względem Keycloak:
    - podpis RSA kluczem publicznym dobranym po `kid` z nagłówka tokenu (obsługa rotacji kluczy),
    - algorytm ograniczony do RS256 (bez ataku alg=none / confusion z HS256),
    - `aud` (audience) i `iss` (issuer) zgodne z konfiguracją,
    - `exp`/`iat` wymagane i sprawdzane przez PyJWT.
    Zwraca surowe claimy tokenu. Używane zarówno dla tokenów użytkownika, jak i M2M
    (service account), które nie mają claimów email/preferred_username.
    """
    try:
        jwk_client = get_jwk_client(certs_url=settings.keycloak.certs_url)
        signing_key = jwk_client.get_signing_key_from_jwt(token)

        return jwt.decode(
            token,
            signing_key.key,
            algorithms=['RS256'],
            audience=settings.keycloak.audience,
            issuer=_keycloak_issuer(),
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")


def validate_token_payload(token: str) -> tuple[Token, dict]:
    """
    Jak validate_token(), ale zwraca parę (Token, surowe claimy) - claimy są
    potrzebne tam, gdzie Token nie wystarcza (np. `groups` z Keycloak dla
    modułu wf, zob. wf/dependencies.py).
    """
    decoded_token = validate_token_claims(token)

    if decoded_token.get('azp') != settings.keycloak.client_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token client (azp)")

    roles = decoded_token.get('resource_access', {}).get('account', {}).get('roles', [])
    scope = decoded_token.get('scope', '')

    try:
        return Token(
            username=decoded_token['preferred_username'],
            email=decoded_token['email'],
            scope=scope,
            roles=roles,
            message='',
            #`sub` (klucz główny) - zapisywany w
            # models.user.User.keycloak_sub przez services/user_service,
            # dzięki czemu konto jest rozpoznawane.
            sub=decoded_token.get('sub'),
        ), decoded_token
    except KeyError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing required claims")


def validate_token(token: str) -> Token:
    """
    Jak validate_token_claims(), ale zwraca wąski schemat Token dla tokenów
    użytkownika (wymaga claimów preferred_username/email oraz zgodnego `azp`).
    """
    return validate_token_payload(token)[0]



#def get_current_token_payload(auth: HTTPAuthorizationCredentials = Depends(reusable_oauth2)) -> Token:
#    return validate_token(auth.credentials)


# Zależność zwracająca obiekt użytkownika z bazy danych
def get_current_user(
        db: Session = Depends(get_db),
        credentials: HTTPAuthorizationCredentials = Depends(reusable_oauth2),
) -> User:
    """
    Najbardziej użyteczna zależność:
    1. Pobiera i PEŁNI waliduje token względem Keycloak (validate_token).
    2. Używa `sub` i emaila z tokenu, aby ZNALEŹĆ lub UTWORZYĆ użytkownika
       w lokalnej bazie (models.user.User - jedna tabela dla całego huba).
    3. Zwraca pełny obiekt SQLAlchemy `User`.
    """
    token_payload = validate_token(credentials.credentials)
    return get_or_create_user_from_token(db, token_data=token_payload)


# para (użytkownik + claimy tokenu). Moduł wf potrzebuje obu:
# lokalnego `users.id` do kluczy obcych oraz claimów (role/grupy) do
# uprawnień procesu - wszystko z jednego tokenu Keycloak.
@dataclass(frozen=True)
class CurrentPrincipal:
    user: User
    token: Token
    claims: dict = field(default_factory=dict)


def get_current_principal(
        db: Session = Depends(get_db),
        credentials: HTTPAuthorizationCredentials = Depends(reusable_oauth2),
) -> CurrentPrincipal:
    """Waliduje token Keycloak i zwraca konto razem z claimami."""
    token_payload, claims = validate_token_payload(credentials.credentials)
    user = get_or_create_user_from_token(db, token_data=token_payload)
    return CurrentPrincipal(user=user, token=token_payload, claims=claims)


# operator BCS (moduł bcs) - rola Keycloak `bcs_operator`.
# Uznajemy ją zarówno jako rolę realmu (`realm_access`), jak i rolę klienta
# huba (`resource_access.<client_id>`); Token.roles zawiera tylko role
# klienta `account`, więc sprawdzamy surowe claimy.
BCS_OPERATOR_ROLE = 'bcs_operator'


def _token_roles(claims: dict) -> set[str]:
    roles = set((claims.get('realm_access') or {}).get('roles') or ())
    client_access = (claims.get('resource_access') or {}).get(settings.keycloak.client_id) or {}
    roles.update(client_access.get('roles') or ())
    return roles


def require_operator(
        principal: CurrentPrincipal = Depends(get_current_principal),
) -> CurrentPrincipal:
    """Zależność FastAPI: zalogowany użytkownik z rolą `bcs_operator`."""
    if BCS_OPERATOR_ROLE not in _token_roles(principal.claims):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Brak uprawnień operatora BCS (wymagana rola: {BCS_OPERATOR_ROLE})")
    return principal


#  opcjonalny schemat
def get_optional_current_user(
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(optional_oauth2)
) -> Optional[User]:
    if token is None:
        # Jeśli nie ma tokena, po prostu zwróć None.
        return None

    # validate_token PEŁNI weryfikuje podpis/aud/iss/exp i rzuca HTTPException 401
    # gdy token jest nieprawidłowy - nie łapiemy tego wyjątku, ma polecieć do klienta.
    token_payload = validate_token(token)
    return get_or_create_user_from_token(db, token_data=token_payload)


def get_current_application(
        db: Session = Depends(get_db),
        token: HTTPAuthorizationCredentials = Depends(reusable_oauth2),
) -> Application:
    """
    Zależność analogiczna do get_current_user, ale dla tokenów M2M wystawionych
    service accountowi zarejestrowanej aplikacji zewnętrznej. W przeciwieństwie do
    get_current_user zawsze w pełni weryfikuje podpis tokenu (validate_token_claims) -
    to nowa ścieżka bez obciążenia kompatybilnością wsteczną.
    """
    claims = validate_token_claims(token.credentials)

    if 'email' in claims:
        # Tokeny service-account nie mają claimu email - to token zwykłego użytkownika
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token nie jest tokenem M2M aplikacji")

    azp = claims.get('azp')
    application = crud_application.get_by_keycloak_client_id(db, keycloak_client_id=azp or '')
    if not application or application.status != ApplicationStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Aplikacja nieznana lub nieaktywna")
    return application