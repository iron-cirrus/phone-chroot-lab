#!/bin/bash
# Lance le serveur PulseAudio systeme (puits virtuel "hote") s'il ne tourne pas deja
pgrep -x pulseaudio >/dev/null && exit 0
mkdir -p /run/pulse; chown pulse:pulse /run/pulse
pulseaudio --system --daemonize=yes --disallow-exit --exit-idle-time=-1 --log-target=file:/var/log/pulseaudio.log
for i in $(seq 1 20); do [ -S /run/pulse/native ] && break; sleep 0.5; done
