#! /bin/bash
# 2026-11-13: kroki systemowe obiegów (scripts/wf_wake.py) - np. status
# podpisu w Signius. Uruchamiać co kilka minut (crontab: */5 * * * *).

PROJECT_HOME=/srv/galwork
HUB_HOME=$PROJECT_HOME/galwork-hub/
VIRTUALENV=$PROJECT_HOME/venv
LOG=$PROJECT_HOME/cron_wf.log

source $VIRTUALENV/bin/activate
cd $HUB_HOME
echo "[$(date '+%F %T')] wf_wake.py" >> $LOG
$VIRTUALENV/bin/python3 scripts/wf_wake.py >> $LOG 2>&1
exit 0
