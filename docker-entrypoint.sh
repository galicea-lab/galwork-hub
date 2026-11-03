#!/bin/sh
# migracje Alembic przed startem API. Bezpieczne przy jednej
# replice hub; przy skalowaniu do wielu replik przenieść ten krok do
# osobnego joba wdrożeniowego (wyścig przy równoległym "upgrade head").
set -e

echo "[docker-entrypoint] alembic upgrade head"
alembic upgrade head

echo "[docker-entrypoint] start: $*"
exec "$@"
