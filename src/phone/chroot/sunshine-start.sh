#!/bin/bash
# Lance Sunshine (encodeur materiel V4L2, x264 en secours) sur la session X :1, et le pont d'entrees
export HOME=/root DISPLAY=:1 XAUTHORITY=/root/.Xauthority
export LD_PRELOAD=/usr/local/lib/libsysvshm-emu.so
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
for i in $(seq 1 60); do [ -S /tmp/.X11-unix/X1 ] && break; sleep 2; done
# serveur audio virtuel (PulseAudio) : necessaire a Chromium/Firefox et a Sunshine (son vers Moonlight)
/usr/local/bin/pulse-start.sh
cd /root
if ! alive /run/sunshine.pid; then
  taskset -c 3-7 setsid nohup /usr/local/bin/sunshine-v4l2 /root/.config/sunshine/sunshine-v4l2.conf >/var/log/sunshine.log 2>&1 < /dev/null &
  echo $! > /run/sunshine.pid
fi
if ! alive /run/sunshine-input-bridge.pid; then
  taskset -c 3-7 setsid nohup python3 /usr/local/bin/sunshine-input-bridge.py >/var/log/sunshine-input-bridge.log 2>&1 < /dev/null &
  echo $! > /run/sunshine-input-bridge.pid
fi
