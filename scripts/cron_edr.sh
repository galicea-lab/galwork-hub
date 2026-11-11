#! /bin/bash
# e-Doręczenia (scripts/edr.py, opis w scripts/README.md).
# inbox     - odbiór -> proces edr-korespondencja + Paperless-ngx
# poll      - wynik ingestu w Paperless-ngx
# send      - kolejka "Wysyłka EDR" procesu edr-wysylka
# evidences - potwierdzenia doręczenia (przychodzące i wysłane)

PROJECT_HOME=/srv/galwork
HUB_HOME=$PROJECT_HOME/galwork-hub/
VIRTUALENV=$PROJECT_HOME/venv
SCRIPT_DIR=scripts
LOG=$PROJECT_HOME/cron_edr.log

source $VIRTUALENV/bin/activate
cd $HUB_HOME

for cmd in inbox poll send evidences; do
  echo "[$(date '+%F %T')] edr.py $cmd" >> $LOG
  $VIRTUALENV/bin/python3 $SCRIPT_DIR/edr.py $cmd >> $LOG 2>&1
done

exit 0
