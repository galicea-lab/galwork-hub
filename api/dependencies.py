# api/dependencies.py
from db.session import SessionLocal
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer


reusable_oauth2 = HTTPBearer(auto_error=False)
# reusable_oauth2 = HTTPBearer()
# HTTPBearer() domyślnie ma auto_error=True. Oznacza to, że FastAPI automatycznie zwraca 401 gdy nagłówek Authorization: Bearer ... jest
#      nieobecny lub nieprawidłowy — jeszcze przed uruchomieniem ciała funkcji get_current_user.
#

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


