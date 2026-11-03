#! /bin/bash

PROJECT_HOME=/srv/galwork
HUB_HOME=$PROJECT_HOME/galwork-hub/
VIRTUALENV=$PROJECT_HOME/venv
SCRIPT_DIR=scripts

source $VIRTUALENV/bin/activate
cd $HUB_HOME

$VIRTUALENV/bin/python3 $SCRIPT_DIR/edr.py inbox >> $PROJECT_HOME/cron_edr.log
$VIRTUALENV/bin/python3 $SCRIPT_DIR/edr.py poll >> $PROJECT_HOME/cron_edr.log
$VIRTUALENV/bin/python3 $SCRIPT_DIR/edr.py send >> $PROJECT_HOME/cron_edr.log
$VIRTUALENV/bin/python3 $SCRIPT_DIR/edr.py evidences >> $PROJECT_HOME/cron_edr.log



exit 0