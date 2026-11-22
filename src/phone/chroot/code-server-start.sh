#!/bin/bash
# Lance code-server s'il ne tourne pas deja (pidfile)
if [ -f /run/code-server.pid ] && kill -0 "$(cat /run/code-server.pid)" 2>/dev/null; then exit 0; fi
HOME=/root setsid nohup /usr/bin/code-server >/var/log/code-server.log 2>&1 < /dev/null &
echo $! > /run/code-server.pid
