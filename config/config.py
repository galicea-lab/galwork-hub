# config/config.py

import configparser
import os

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Helper do wczytywania i parsowania pliku .ini ---
# Tworzymy parser i wczytujemy plik. Ścieżka jest budowana
# względem lokalizacji tego pliku konfiguracyjnego.
_parser = configparser.ConfigParser()
ini_path = os.path.join(os.path.dirname(__file__), 'config.ini')
_parser.read(ini_path)


def _section_values(section: str) -> dict:
    """Wartości sekcji z config.ini z nadpisaniami ze środowiska.

    Zmienna środowiskowa ma postać <SEKCJA>_<KLUCZ>, np. DB_PASSWORD,
    KEYCLOAK_SERVER_URL, APP_CORS_ORIGINS. Używane dla sekcji krytycznych
    (zwłaszcza haseł), żeby nie trzymać poświadczeń w pliku.
    """
    if not _parser.has_section(section):
        return {}
    values = {k: v for k, v in _parser[section].items()}
    for key in list(values):
        env_value = os.environ.get(f"{section.upper()}_{key.upper()}")
        if env_value is not None:
            values[key] = env_value
    return values


def section(name: str) -> dict:
    """Surowe wartości sekcji [name] z config.ini (z nadpisaniem ze środowiska).

    Dla kodu spoza katalogu `connector/` (ten używa `connector/base.py`),
    np. `section('edr')` w `wf/routers/edr_api.py`.
    """
    return _section_values(name)


def _split_list(values: dict, key: str, default: str = '') -> list:
    """Lista z jednego klucza konfiguracyjnego (separator przecinek)."""
    raw = values.get(key, default) or ''
    return [item.strip() for item in raw.split(',') if item.strip()]


class AppSettings(BaseModel):
  """Model dla sekcji [app]"""
  frontend_url: str = 'http://localhost:3000'
  # adres dokumentacji API (router.py::api_tags_metadata) -
  docs_url: str = ''
  # originy CORS przeniesione z main.py.
  # Pustka = brak CORS (bezpieczne domyślnie); '*' tylko na świadome żądanie.
  cors_origins: list[str] = Field(default_factory=list)
  cors_allow_credentials: bool = True
  # zawsze false na produkcji.
  debug: bool = False


class DBSettings(BaseModel):
  """Model dla sekcji [db]

  Kolejność rozstrzygania connection stringu (db/session.py):
    1. `url` z konfiguracji (w całości, wraz z hasłem) albo zmienna
       środowiskowa DATABASE_URL,
    2. `driver` + `host`/`port`/`user`/`password`/`name`,
    3. `name` jako plik SQLite (zachowanie kompatybilności ze starym
       `name=cm_msqlite.db`).
  """
  name: str = 'cm_msqlite.db'
  driver: str = 'sqlite'
  host: str = 'localhost'
  port: int = 5432
  user: str = ''
  password: str = ''
  url: str = ''
  echo: bool = False

  def connection_string(self) -> str:
    """Buduje connection string dla SQLAlchemy (zob. TODO.md pkt 7)."""
    url = self.url or os.environ.get('DATABASE_URL', '')
    if url:
      return url

    if self.driver.startswith('sqlite'):
      if self.name == ':memory:':
        return 'sqlite://'
      db_path = os.path.abspath(os.path.join(os.getcwd(), self.name))
      return f'sqlite:///{db_path}'

    port = f':{self.port}' if self.port else ''
    credentials = self.user
    if self.password:
      credentials = f'{credentials}:{self.password}'
    credentials = f'{credentials}@' if credentials else ''
    return f'{self.driver}://{credentials}{self.host}{port}/{self.name}'


class LoggingSettings(BaseModel):
  """Model dla sekcji [logging]"""
  level: str = 'INFO'
  file: str = 'ds.log'


class KeycloakSettings(BaseModel):
  """Model dla sekcji [keycloak]"""
  certs_url: str
  audience: str
  client_id: str
  admin: str = ''
  admin_password: str = ''
  server_url: str
  realm_name: str
  # grupy/role Keycloak uprawnione do administracji definicjami
  # procesów (wf/dependencies.py: require_process_admin). Lista po przecinku.
  admin_groups: list[str] = Field(default_factory=lambda: ['galwork-admin', 'wf-admin'])
  model_config = SettingsConfigDict(extra='ignore')

class EtalarSettings(BaseModel):
    """Sekcja [etalar] - konfiguracja connector/etalar/etalar.py."""
    base_url: str = ''
    api_key: str = ''
    api_secret: str = ''
    timeout: int = 30



# --- Główna klasa ustawień, która agreguje wszystkie sekcje ---

class Settings(BaseSettings):
  # Pydantic automatycznie zrozumie, że ma szukać obiektu 'db' , 'tm' etc...
  app: AppSettings
  db: DBSettings
  logging: LoggingSettings
  keycloak: KeycloakSettings
  etalar: EtalarSettings


def _as_bool(value, default: bool) -> bool:
  if value is None:
    return default
  return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _build_settings() -> Settings:
  """Buduje Settings z pliku ini;"""
  app_values = _section_values('app')
  keycloak_values = _section_values('keycloak')
  return Settings(
    app=AppSettings(
      frontend_url=app_values.get('frontend_url', 'http://localhost:3000'),
      docs_url=app_values.get('docs_url', ''),
      cors_origins=_split_list(app_values, 'cors_origins'),
      cors_allow_credentials=_as_bool(app_values.get('cors_allow_credentials'), True),
      debug=_as_bool(app_values.get('debug'), False),
    ),
    db=DBSettings(**_section_values('db')),
    logging=LoggingSettings(**_section_values('logging')),
    keycloak=KeycloakSettings(**{
      **keycloak_values,
      'admin_groups': _split_list(keycloak_values, 'admin_groups',
                                 'galwork-admin,wf-admin'),
    }),
    etalar=EtalarSettings(**_section_values('etalar')),
  )


settings = _build_settings()

