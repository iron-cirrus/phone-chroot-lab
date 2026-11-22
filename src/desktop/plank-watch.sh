#!/bin/bash
# Relance les docks Plank quand la resolution du bureau change (Plank ne suit pas les changements RandR d'Xvnc).
export DISPLAY=:1 XAUTHORITY=/root/.Xauthority HOME=/root XDG_MENU_PREFIX=mate-
last=""
while true; do
  cur=$(xdotool getdisplaygeometry 2>/dev/null)
  if [ -n "$cur" ] && [ -n "$last" ] && [ "$cur" != "$last" ]; then
    sleep 2                                   # laisse la resolution se stabiliser
    pkill -x plank; sleep 1
    setsid nohup plank >/tmp/plank.log 2>&1 &
    setsid nohup plank -n dock2 >/tmp/plank2.log 2>&1 &
    cur=$(xdotool getdisplaygeometry 2>/dev/null)
  fi
  [ -n "$cur" ] && last="$cur"
  sleep 2
done
