from keycloak import KeycloakAdmin, KeycloakError
from core.config import settings
import logging

logger = logging.getLogger(__name__)

def admin_int():
    return KeycloakAdmin(server_url=settings.keycloak.server_url,
                         realm_name=settings.keycloak.realm_name,
                         username=settings.keycloak.admin,
                         password=settings.keycloak.admin_password,
                         verify=True)

def get_user_groups(username):
  adm = admin_int()
  user_id = adm.get_user_id(username)
  user_groups = adm.get_user_groups(user_id or '')
  return user_groups

# Tworzenie nowego użytkownika
def get_or_create_user(email, firstName, lastName, password):
  admin = admin_int()
  try:
    user_id = admin.get_user_id(email)
  except:
    user_id=None
  if not user_id:
    try:
      user_id = admin.create_user({
        "email": email,
        "username": email,
        "enabled": True,
        "firstName": firstName,
        "lastName": lastName,
        "credentials": [{
            "value": password,
            "type": "password",
            "temporary": False,
        }]
      })
    except KeycloakError as ke:
      print(ke)
      # Password policy not met
    except Exception as e:
      print(e)
  return user_id

def set_password(username, password):
  admin = admin_int()
  user_id = admin.get_user_id(username)
  admin.set_user_password(user_id=user_id,
                          password=password,
                          temporary=False)

def set_password_id(user_id, password):
  admin = admin_int()
  admin.set_user_password(user_id=user_id,
                          password=password,
                          temporary=False)


def get_admin_client() -> KeycloakAdmin:
    return KeycloakAdmin(
        server_url=settings.keycloak.server_url,
        username=settings.keycloak.admin,
        password=settings.keycloak.admin_password,
        realm_name=settings.keycloak.realm_name,
        user_realm_name=settings.keycloak.realm_name,
        verify=True,
    )


def create_application_client(app_name: str, external_client_id: str) -> tuple[str, str]:
    """Tworzy confidential client z włączonym service accountem.

    Zwraca (keycloak_internal_id, client_secret).
    """
    admin = get_admin_client()
    payload = {
        "clientId": external_client_id,
        "name": app_name,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": False,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "redirectUris": [],
    }
    kc_internal_id = admin.create_client(payload)
    _add_audience_mapper(admin, kc_internal_id)
    secret_repr = admin.get_client_secrets(kc_internal_id)
    return kc_internal_id, secret_repr["value"]


def _add_audience_mapper(admin: KeycloakAdmin, kc_internal_id: str) -> None:
    """Bez audience mappera tokeny M2M nie przejdą sprawdzenia `audience`
    w api.security.validate_token_claims()."""
    admin.add_mapper_to_client(kc_internal_id, {
        "name": "audience-mapper",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "consentRequired": False,
        "config": {
            "included.custom.audience": settings.keycloak.audience,
            "id.token.claim": "false",
            "access.token.claim": "true",
        },
    })


def rotate_client_secret(kc_internal_id: str) -> str:
    admin = get_admin_client()
    secret_repr = admin.generate_client_secrets(kc_internal_id)
    return secret_repr["value"]


def disable_client(kc_internal_id: str) -> None:
    admin = get_admin_client()
    admin.update_client(kc_internal_id, {"enabled": False})


def enable_client(kc_internal_id: str) -> None:
    admin = get_admin_client()
    admin.update_client(kc_internal_id, {"enabled": True})


def delete_client(kc_internal_id: str) -> None:
    admin = get_admin_client()
    admin.delete_client(kc_internal_id)


if __name__ == '__main__':
  # dane bootstrapu użytkownika czytane ze środowiska; hasła nie trzymamy w kodzie.
  import os

  email = os.environ.get('KC_BOOTSTRAP_EMAIL', '')
  if email:
    user_id = get_or_create_user(
      email,
      os.environ.get('KC_BOOTSTRAP_FIRST_NAME', ''),
      os.environ.get('KC_BOOTSTRAP_LAST_NAME', ''),
      os.environ.get('KC_BOOTSTRAP_PASSWORD', ''),
    )
    print(user_id)
  #print(get_user_groups('demo'))
