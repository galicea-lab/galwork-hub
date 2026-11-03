# tests/test_connectors_config.py
#
# 1) W repozytorium nie ma modułu `conf`, więc `from conf import config`
#    zawsze kończyło się `ModuleNotFoundError` przy imporcie konektora.
#    Test pilnuje, żeby taki (bądź podobny martwy) import nie wrócił.
# 2) Każdy konektor ma się dać zaimportować bez konfiguracji produkcyjnej
#    i bez bazy danych (same importy + odczyt defaultów).
import ast
import configparser
import pathlib
import unittest

from configobj import ConfigObj
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

ROOT = pathlib.Path(__file__).resolve().parent.parent

FORBIDDEN_IMPORTS = {'conf', 'configobj.config'}

# Konektory, które muszą importować się bez podania konfiguracji.
CONNECTOR_MODULES = [
  'connector.base',
  'connector.edoreczenia.edr_webcon',
  'connector.webcon.db',
  'connector.frappe_education.connector',
  'connector.signius.connector',
]


def _iter_python_files(roots):
  for root in roots:
    for path in pathlib.Path(root).rglob('*.py'):
      if '__pycache__' not in path.parts:
        yield path


class NoDeadConfigImportsTest(unittest.TestCase):
  """Żaden moduł nie może importować nieistniejącego `conf`."""

  def test_no_import_of_missing_conf_module(self):
    offenders = []
    for path in _iter_python_files(['connector', 'api', 'core', 'db', 'lib', 'models',
                                    'pm', 'wf']):
      tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
      for node in ast.walk(tree):
        if isinstance(node, ast.Import):
          names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
          names = [node.module or '']
        else:
          continue
        for name in names:
          if name.split('.')[0] in FORBIDDEN_IMPORTS:
            offenders.append(f'{path.relative_to(ROOT)}:{node.lineno}: {name}')
    self.assertEqual(offenders, [], 'Martwy import konfiguracji: ' + '; '.join(offenders))


class ConnectorImportTest(unittest.TestCase):
  """Konektory importują się bez konfiguracji i bez bazy."""

  def test_connectors_are_importable(self):
    import importlib
    for module in CONNECTOR_MODULES:
      with self.subTest(module=module):
        importlib.import_module(module)

  def test_webcon_connectors_have_own_sections(self):
    ini = ConfigObj(str(ROOT / 'config' / 'config.ini'))
    for section in ('edr_db',):
      with self.subTest(section=section):
        self.assertIn(section, ini, f'Brak sekcji [{section}] w config.ini')

  def test_config_ini_has_no_duplicate_sections(self):
    """`configparser` (core/config.py) rzuca DuplicateSectionError.

    ConfigObj scala duplikaty po cichu, więc sam ConfigObj tego nie wykryje.
    """
    parser = configparser.ConfigParser()
    parser.read(ROOT / 'config' / 'config.ini')
    self.assertIn('edr', parser.sections())
    self.assertEqual(parser['edr']['test_db'], 'false')

  def test_no_secrets_committed(self):
    """Hasła w config.ini mają być puste (wgrywane ze środowiska)."""
    parser = configparser.ConfigParser()
    parser.read(ROOT / 'config' / 'config.ini')
    for section in ('edr_db', 'signius'):
      for key in ('password', 'user', 'dbpassword', 'dbuser', 'p12_password'):
        if key in parser[section]:
          with self.subTest(section=section, key=key):
            self.assertEqual(parser[section][key].strip(), '',
                             f'{section}.{key} nie powinno być wypełnione w repo')


class WebconModuleSmokeTest(unittest.TestCase):
  """`connector.webcon.db` nie otwiera połączenia na starcie modułu."""

  def test_module_import_does_not_connect(self):
    import connector.webcon.db as webcon_db
    for name in ('db_get_edr', 'db_insert_edr', 'sql_select_edr', 'sql_insert_edr',
                 '_connect_edr'):
      self.assertTrue(hasattr(webcon_db, name), f'Brak {name} w connector.webcon.db')

  def test_sqlite_engine_can_be_created(self):
    """Sanity check dla testowej konfiguracji używanej w testach."""
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    try:
      with engine.connect() as conn:
        self.assertEqual(conn.exec_driver_sql('select 1').scalar(), 1)
    finally:
      engine.dispose()


if __name__ == '__main__':
  unittest.main()
