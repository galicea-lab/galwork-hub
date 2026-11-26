# db/session.py
# 2026-10-05: connection string jest budowany z ustawień (core/config.py,  sekcja [db])

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings

# 2026-10-05: `sqlite` wymaga check_same_thread=False, bo FastAPI obsługuje
# żądania w puli wątków (a sesja z get_db może powstać w innym wątku niż
# zapytanie). Dla PostgreSQL taki argument nie jest obsługiwany.

def _connect_args() -> dict:
  url = settings.db.connection_string()
  if url.startswith('sqlite'):
    return {'check_same_thread': False}
  return {}


def database_url() -> str:
  """Connection string dla huba (jeden silnik na całe życie aplikacji)."""
  return settings.db.connection_string()


# Tworzymy silnik bazy danych - JEDEN RAZ na całe życie aplikacji
# 2026-11-25: parametry puli z [db] (pool_size, max_overflow, pool_recycle,
# pool_timeout; zmienne DB_POOL_SIZE itd.) - domyślnie 2 stałe połączenia na
# proces zamiast 5 (dashboard wysyła po zalogowaniu kilka żądań naraz, a pula
# zatrzymywała po nich 5 bezczynnych połączeń na każdy proces uvicorn).
engine = create_engine(
  database_url(),
  connect_args=_connect_args(),
  echo=settings.db.echo,
  pool_pre_ping=True,
  **settings.db.pool_options(),
)

# Tworzymy fabrykę sesji - JEDEN RAZ na całe życie aplikacji
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)