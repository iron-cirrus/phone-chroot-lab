#!/bin/bash
# Lance le tableau de bord CPU/GPU/NPU (Flask, port 8090) s'il ne tourne pas deja.
pgrep -f "/opt/dashboard/app.py" >/dev/null && exit 0
mkdir -p /var/log
setsid nohup nice -n 10 python3 /opt/dashboard/app.py >>/var/log/dashboard.log 2>&1 </dev/null &
exit 0
