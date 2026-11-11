#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# 2026-11-09: konfiguracja realmu kandydatów w Keycloak (26.x) dla usługi
# kodów (konto/). Idempotentna - można uruchamiać wielokrotnie.
#
#   * realm bez samorejestracji i resetu hasła (zakładanie kont i reset
#     wyłącznie przez usługę kodów), ochrona przed zgadywaniem haseł,
#     polityka haseł, język polski,
#   * profil użytkownika: atrybuty phone_number, phone_number_verified,
#     pesel_hmac, eid_hmac, identity_verified - edytuje tylko administrator
#     (usługa), bez nich Keycloak pomija atrybuty przy zakładaniu konta,
#   * grupa kandydaci,
#   * klient usługowy hub-konto (client_credentials) z rolami realm-management
#     manage-users, view-users, query-users, query-groups - tylko w tym realmie,
#   * klient publiczny portalu (PKCE) z claimami groups, phone_number_verified,
#     identity_verified.
#
# Użycie (hasło administratora pobierane interaktywnie, nie z argumentów):
#   KC_URL=https://login.example.com KC_ADMIN=admin \
#     python scripts/keycloak_rekrutacja.py --realm rekrutacja \
#       --portal-url https://portal.example.com --secret-file hub-konto.secret
# Bez --apply skrypt tylko pokazuje, co by zmienił.

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

import requests

USER_PROFILE_ATTRIBUTES = {
  'phone_number': 'Telefon',
  'phone_number_verified': 'Telefon potwierdzony',
  'pesel_hmac': 'PESEL (skrót)',
  'eid_hmac': 'Identyfikator eIDAS (skrót)',
  'identity_verified': 'Tożsamość potwierdzona',
}
SERVICE_ROLES = ('manage-users', 'view-users', 'query-users', 'query-groups')


class Admin:

  def __init__(self, url: str, user: str, password: str, realm: str, apply: bool, verify: bool):
    self.url = url.rstrip('/')
    self.realm = realm
    self.apply = apply
    self.session = requests.Session()
    self.session.verify = verify
    resp = self.session.post(f'{self.url}/realms/master/protocol/openid-connect/token', data={
      'grant_type': 'password', 'client_id': 'admin-cli', 'username': user, 'password': password,
    }, timeout=30)
    if not resp.ok:
      sys.exit(f'Logowanie administratora nieudane (HTTP {resp.status_code})')
    self.session.headers['Authorization'] = f'Bearer {resp.json()["access_token"]}'

  def call(self, method: str, path: str, **kwargs) -> requests.Response:
    resp = self.session.request(method, f'{self.url}/admin/realms{path}', timeout=30, **kwargs)
    if resp.status_code >= 400:
      sys.exit(f'{method} {path}: HTTP {resp.status_code} {resp.text[:300]}')
    return resp

  def change(self, description: str, method: str, path: str, **kwargs) -> requests.Response | None:
    print(('ZMIANA  ' if self.apply else 'PLAN    ') + description)
    return self.call(method, path, **kwargs) if self.apply else None


def ensure_realm(admin: Admin, display_name: str) -> None:
  wanted = {
    'realm': admin.realm,
    'enabled': True,
    'displayName': display_name,
    'registrationAllowed': False,
    'resetPasswordAllowed': False,
    'editUsernameAllowed': False,
    'rememberMe': True,
    'loginWithEmailAllowed': True,
    'duplicateEmailsAllowed': False,
    'verifyEmail': False,
    'sslRequired': 'external',
    'bruteForceProtected': True,
    'permanentLockout': False,
    'failureFactor': 10,
    'waitIncrementSeconds': 60,
    'maxFailureWaitSeconds': 900,
    'passwordPolicy': 'length(10) and notUsername and notEmail and passwordHistory(3)',
    'internationalizationEnabled': True,
    'supportedLocales': ['pl', 'en'],
    'defaultLocale': 'pl',
  }
  resp = admin.session.get(f'{admin.url}/admin/realms/{admin.realm}', timeout=30)
  if resp.status_code == 404:
    admin.change(f'utworzenie realmu {admin.realm}', 'POST', '', json=wanted)
    return
  current = resp.json()
  diff = {k: v for k, v in wanted.items() if current.get(k) != v}
  if diff:
    admin.change(f'ustawienia realmu: {", ".join(sorted(diff))}', 'PUT', f'/{admin.realm}',
                 json={**current, **diff})
  else:
    print(f'OK      realm {admin.realm}')


def ensure_user_profile(admin: Admin) -> None:
  if not admin.apply and admin.session.get(f'{admin.url}/admin/realms/{admin.realm}',
                                           timeout=30).status_code == 404:
    print('PLAN    profil użytkownika: ' + ', '.join(USER_PROFILE_ATTRIBUTES))
    return
  profile = admin.call('GET', f'/{admin.realm}/users/profile').json()
  names = {a['name'] for a in profile.get('attributes', [])}
  missing = [n for n in USER_PROFILE_ATTRIBUTES if n not in names]
  if not missing:
    print('OK      profil użytkownika')
    return
  for name in missing:
    profile.setdefault('attributes', []).append({
      'name': name,
      'displayName': USER_PROFILE_ATTRIBUTES[name],
      'permissions': {'view': ['admin'], 'edit': ['admin']},
      'multivalued': False,
    })
  admin.change(f'profil użytkownika: {", ".join(missing)}', 'PUT', f'/{admin.realm}/users/profile',
               json=profile)


def ensure_group(admin: Admin, name: str) -> None:
  if admin.apply or admin.session.get(f'{admin.url}/admin/realms/{admin.realm}', timeout=30).ok:
    groups = admin.call('GET', f'/{admin.realm}/groups', params={'search': name, 'exact': 'true'}).json()
    if any(g.get('name') == name for g in groups):
      print(f'OK      grupa {name}')
      return
  admin.change(f'grupa {name}', 'POST', f'/{admin.realm}/groups', json={'name': name})


def find_client(admin: Admin, client_id: str) -> dict | None:
  if not admin.session.get(f'{admin.url}/admin/realms/{admin.realm}', timeout=30).ok:
    return None
  clients = admin.call('GET', f'/{admin.realm}/clients', params={'clientId': client_id}).json()
  return clients[0] if clients else None


def ensure_service_client(admin: Admin, client_id: str, secret_file: str | None) -> None:
  client = find_client(admin, client_id)
  if client is None:
    admin.change(f'klient usługowy {client_id}', 'POST', f'/{admin.realm}/clients', json={
      'clientId': client_id, 'name': 'Usługa kodów jednorazowych', 'enabled': True,
      'protocol': 'openid-connect', 'publicClient': False, 'clientAuthenticatorType': 'client-secret',
      'serviceAccountsEnabled': True, 'standardFlowEnabled': False,
      'directAccessGrantsEnabled': False, 'implicitFlowEnabled': False,
    })
    client = find_client(admin, client_id) if admin.apply else None
  else:
    print(f'OK      klient {client_id}')
  if client is None:
    print(f'PLAN    role {", ".join(SERVICE_ROLES)} dla konta usługowego {client_id}')
    return

  service_user = admin.call('GET', f'/{admin.realm}/clients/{client["id"]}/service-account-user').json()
  management = find_client(admin, 'realm-management')
  available = admin.call('GET', f'/{admin.realm}/clients/{management["id"]}/roles').json()
  assigned = admin.call('GET', f'/{admin.realm}/users/{service_user["id"]}/role-mappings/clients/'
                               f'{management["id"]}').json()
  have = {r['name'] for r in assigned}
  roles = [r for r in available if r['name'] in SERVICE_ROLES and r['name'] not in have]
  if roles:
    admin.change(f'role konta usługowego: {", ".join(r["name"] for r in roles)}', 'POST',
                 f'/{admin.realm}/users/{service_user["id"]}/role-mappings/clients/{management["id"]}',
                 json=roles)
  else:
    print('OK      role konta usługowego')

  if secret_file and admin.apply:
    secret = admin.call('GET', f'/{admin.realm}/clients/{client["id"]}/client-secret').json()['value']
    fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
      handle.write(secret + '\n')
    print(f'ZAPIS   sekret klienta {client_id} -> {secret_file} (uprawnienia 600)')


def _attribute_mapper(name: str) -> dict:
  return {
    'name': name, 'protocol': 'openid-connect', 'protocolMapper': 'oidc-usermodel-attribute-mapper',
    'config': {'user.attribute': name, 'claim.name': name, 'jsonType.label': 'String',
               'id.token.claim': 'true', 'access.token.claim': 'true', 'userinfo.token.claim': 'true',
               'introspection.token.claim': 'true'},
  }


PORTAL_MAPPERS = [
  {'name': 'groups', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-group-membership-mapper',
   'config': {'claim.name': 'groups', 'full.path': 'false', 'id.token.claim': 'true',
              'access.token.claim': 'true', 'userinfo.token.claim': 'true',
              'introspection.token.claim': 'true'}},
  _attribute_mapper('phone_number_verified'),
  _attribute_mapper('identity_verified'),
]


def ensure_portal_client(admin: Admin, client_id: str, portal_url: str) -> None:
  base = portal_url.rstrip('/')
  wanted = {
    'clientId': client_id, 'name': 'Portal kandydata', 'enabled': True, 'protocol': 'openid-connect',
    'publicClient': True, 'standardFlowEnabled': True, 'directAccessGrantsEnabled': False,
    'implicitFlowEnabled': False, 'serviceAccountsEnabled': False,
    'redirectUris': [f'{base}/*'], 'webOrigins': [base], 'rootUrl': base,
    'attributes': {'pkce.code.challenge.method': 'S256', 'post.logout.redirect.uris': f'{base}/*'},
  }
  client = find_client(admin, client_id)
  if client is None:
    admin.change(f'klient portalu {client_id} ({base})', 'POST', f'/{admin.realm}/clients',
                 json={**wanted, 'protocolMappers': PORTAL_MAPPERS})
    return
  diff = [k for k in ('publicClient', 'redirectUris', 'webOrigins', 'directAccessGrantsEnabled')
          if client.get(k) != wanted[k]]
  if client.get('attributes', {}).get('pkce.code.challenge.method') != 'S256':
    diff.append('pkce')
  if diff:
    merged = {**client, **{k: v for k, v in wanted.items() if k != 'attributes'}}
    merged['attributes'] = {**client.get('attributes', {}), **wanted['attributes']}
    admin.change(f'klient portalu {client_id}: {", ".join(diff)}', 'PUT',
                 f'/{admin.realm}/clients/{client["id"]}', json=merged)
  else:
    print(f'OK      klient {client_id}')
  existing = {m['name'] for m in admin.call(
    'GET', f'/{admin.realm}/clients/{client["id"]}/protocol-mappers/models').json()}
  for mapper in PORTAL_MAPPERS:
    if mapper['name'] not in existing:
      admin.change(f'maper {mapper["name"]} w kliencie {client_id}', 'POST',
                   f'/{admin.realm}/clients/{client["id"]}/protocol-mappers/models', json=mapper)


def main() -> None:
  parser = argparse.ArgumentParser(description='Realm kandydatów w Keycloak dla usługi kodów')
  parser.add_argument('--realm', default='rekrutacja')
  parser.add_argument('--display-name', default='Rekrutacja na studia')
  parser.add_argument('--group', default='kandydaci')
  parser.add_argument('--service-client', default='hub-konto')
  parser.add_argument('--portal-client', default='galwork-portal')
  parser.add_argument('--portal-url', required=True)
  parser.add_argument('--secret-file', help='plik na sekret klienta usługowego (uprawnienia 600)')
  parser.add_argument('--apply', action='store_true', help='wprowadź zmiany (domyślnie tylko plan)')
  parser.add_argument('--insecure', action='store_true', help='bez weryfikacji TLS (tylko dev)')
  args = parser.parse_args()

  url = os.environ.get('KC_URL') or sys.exit('Ustaw KC_URL, np. https://login.example.com')
  user = os.environ.get('KC_ADMIN') or input('Administrator Keycloak (realm master): ')
  password = os.environ.get('KC_ADMIN_PASSWORD') or getpass.getpass('Hasło administratora: ')
  admin = Admin(url, user, password, args.realm, args.apply, not args.insecure)

  ensure_realm(admin, args.display_name)
  ensure_user_profile(admin)
  ensure_group(admin, args.group)
  ensure_service_client(admin, args.service_client, args.secret_file)
  ensure_portal_client(admin, args.portal_client, args.portal_url)
  if not args.apply:
    print('\nTylko plan - uruchom ponownie z --apply, żeby wprowadzić zmiany.')
  else:
    print(json.dumps({
      'issuer': f'{admin.url}/realms/{args.realm}',
      'keycloak_admin': {'server_url': admin.url, 'realm': args.realm, 'client_id': args.service_client},
      'konto': {'group': f'/{args.group}'},
    }, indent=2, ensure_ascii=False))


if __name__ == '__main__':
  main()
