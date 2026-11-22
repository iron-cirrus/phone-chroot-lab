#!/system/bin/sh
# Wi-Fi faible latence: modes Android forces + economie d'energie du pilote coupee.
# Le pilote la reactive a chaque reconnexion: on reverifie toutes les 30 s.
setsid nohup sh -c '
until [ "$(getprop sys.boot_completed)" = "1" ]; do sleep 3; done
sleep 10
while true; do
  cmd wifi force-low-latency-mode enabled >/dev/null 2>&1
  cmd wifi force-hi-perf-mode enabled >/dev/null 2>&1
  iw dev wlan0 get power_save 2>/dev/null | grep -q "on" && iw dev wlan0 set power_save off 2>/dev/null
  sleep 30
done' >/dev/null 2>&1 &
