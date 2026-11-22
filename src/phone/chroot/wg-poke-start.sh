#!/bin/bash
# Lance wg-poke.sh s'il ne tourne pas deja (motif ancre pour ne pas se reconnaitre soi-meme)
pgrep -f '^/bin/bash /usr/local/bin/wg-poke\.sh$' >/dev/null && exit 0
setsid nohup /usr/local/bin/wg-poke.sh >/dev/null 2>&1 < /dev/null &
