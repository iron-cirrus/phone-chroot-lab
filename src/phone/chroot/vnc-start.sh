#!/bin/bash
# Lance la session TurboVNC :1 (ecoute locale seulement) si elle n'existe pas
export HOME=/root
# emulation de la memoire partagee System V (XShm) : noyau Android sans SYSVIPC
export LD_PRELOAD=/usr/local/lib/libsysvshm-emu.so
if [ -S /tmp/.X11-unix/X1 ] && pgrep -x Xvnc >/dev/null; then exit 0; fi
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1
rm -rf /dev/shm/sysvshm-emu   # nouvelle session : aucun segment ne peut etre en cours
taskset -c 3-7 /opt/TurboVNC/bin/vncserver :1 -geometry 1920x1080 -depth 24 -securitytypes tlsvnc,x509vnc -xstartup /root/.vnc/xstartup.turbovnc >/var/log/vnc-start.log 2>&1
