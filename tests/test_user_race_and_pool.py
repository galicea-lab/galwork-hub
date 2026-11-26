# tests/test_user_race_and_pool.py
# 2026-11-25: zakładanie konta przy równoległych żądaniach pierwszego logowania
# (services/auth_service.get_or_create_local_user) i parametry puli połączeń
# ([db] pool_size itd., db/session.py).
from __future__ import annotations

import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config.config import DBSettings
from db.base import Base
from models.user import User
import wf.models  # noqa: F401  (rejestracja metadanych)
from services import auth_service


class LocalUserRaceTest(unittest.TestCase):

  def setUp(self) -> None:
    self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                                poolclass=StaticPool)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
    self.db = self.Session()

  def tearDown(self) -> None:
    self.db.close()
    self.engine.dispose()

  def _existing(self, **extra) -> User:
    other = self.Session()
    user = User(email='jan@example.com', name='jan', hashed_password='SSO_USER_NO_PASSWORD', **extra)
    other.add(user)
    other.commit()
    user_id = user.id
    other.close()
    return user_id

  def test_creates_user_once(self):
    user = auth_service.get_or_create_local_user(self.db, 'jan@example.com', 'jan', sub='sub-1')
    again = auth_service.get_or_create_local_user(self.db, 'jan@example.com', 'jan', sub='sub-1')
    self.assertEqual(user.id, again.id)
    self.assertEqual(self.db.query(User).count(), 1)

  def test_concurrent_insert_returns_existing_user(self):
    # inne żądanie zapisało konto między wyszukaniem a zapisem: pierwsze
    # wyszukanie nic nie znajduje, zapis łamie unikalność e-maila
    existing_id = self._existing(keycloak_sub='sub-1')
    real_find = auth_service._find_local_user
    with mock.patch.object(auth_service, '_find_local_user',
                           side_effect=[None, real_find(self.db, email='jan@example.com', sub='sub-1')]):
      user = auth_service.get_or_create_local_user(self.db, 'jan@example.com', 'jan', sub='sub-1')
    self.assertEqual(user.id, existing_id)
    self.assertEqual(self.db.query(User).count(), 1)

  def test_integrity_error_without_existing_user_is_raised(self):
    from sqlalchemy.exc import IntegrityError
    with mock.patch.object(auth_service.crud_user, 'create_user',
                           side_effect=IntegrityError('insert', {}, Exception('x'))):
      with self.assertRaises(IntegrityError):
        auth_service.get_or_create_local_user(self.db, 'nowy@example.com', 'nowy', sub='sub-9')

  def test_sub_assigned_to_user_found_by_email(self):
    existing_id = self._existing()
    user = auth_service.get_or_create_local_user(self.db, 'jan@example.com', 'jan', sub='sub-7')
    self.assertEqual(user.id, existing_id)
    self.assertEqual(user.keycloak_sub, 'sub-7')


class PoolOptionsTest(unittest.TestCase):

  def test_defaults_for_postgresql(self):
    options = DBSettings(url='postgresql+psycopg://u:p@h/db').pool_options()
    self.assertEqual(options, {'pool_size': 2, 'max_overflow': 8, 'pool_recycle': 1800,
                               'pool_timeout': 30})

  def test_values_from_config(self):
    options = DBSettings(url='postgresql+psycopg://u:p@h/db', pool_size='4',
                         max_overflow='0').pool_options()
    self.assertEqual((options['pool_size'], options['max_overflow']), (4, 0))

  def test_no_pool_options_for_sqlite(self):
    self.assertEqual(DBSettings(driver='sqlite', name=':memory:').pool_options(), {})
