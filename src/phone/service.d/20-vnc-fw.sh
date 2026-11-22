#!/system/bin/sh
# Applique le pare-feu VNC au demarrage, puis le reverifie toutes les 30 s (netd peut modifier les chaines)
setsid nohup sh -c 'until [ "$(getprop sys.boot_completed)" = "1" ]; do sleep 3; done; while true; do /data/adb/vnc-fw.sh; sleep 30; done' >/dev/null 2>&1 &
