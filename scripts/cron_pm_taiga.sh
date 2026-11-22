#! /bin/bash
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Integracja Taiga - galwork-pm - ERPNext: rekoncyliacja z timeline'u
# Taigi i klasyfikacja kolejki zdarzeń (scripts/pm_taiga.py). Uruchamiać co
# 15-30 min (crontab: */20 * * * *); audyt i retencja raz dziennie (--daily).

PROJECT_HOME=/srv/galwork
HUB_HOME=$PROJECT_HOME/galwork-hub/
VIRTUALENV=$PROJECT_HOME/venv
LOG=$PROJECT_HOME/cron_pm_taiga.log

source $VIRTUALENV/bin/activate
cd $HUB_HOME
echo "[$(date '+%F %T')] pm_taiga.py reconcile/process" >> $LOG
$VIRTUALENV/bin/python3 scripts/pm_taiga.py reconcile >> $LOG 2>&1
$VIRTUALENV/bin/python3 scripts/pm_taiga.py process >> $LOG 2>&1
# 2026--11-21: Timesheety w ERPNext - bez zmian, dopóki [taiga] erp_sync=false
$VIRTUALENV/bin/python3 scripts/pm_taiga.py erp-sync >> $LOG 2>&1
# 2026--11-21: przegląd tygodnia (poniedziałek) i przypomnienie o godzinach (czwartek)
if [ "$1" == "--weekly-review" ]; then
  $VIRTUALENV/bin/python3 scripts/pm_taiga.py review-start >> $LOG 2>&1
fi
if [ "$1" == "--remind" ]; then
  $VIRTUALENV/bin/python3 scripts/pm_taiga.py remind >> $LOG 2>&1
fi
if [ "$1" == "--daily" ]; then
  $VIRTUALENV/bin/python3 scripts/pm_taiga.py audit >> $LOG 2>&1
  $VIRTUALENV/bin/python3 scripts/pm_taiga.py erp-check >> $LOG 2>&1
  $VIRTUALENV/bin/python3 scripts/pm_taiga.py purge >> $LOG 2>&1
fi
exit 0
