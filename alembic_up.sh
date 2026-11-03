# $1 = opis rewizji
#alembic init alembic

alembic revision --autogenerate -m "$1"
alembic upgrade head