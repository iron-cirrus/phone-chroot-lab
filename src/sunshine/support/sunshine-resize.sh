#!/bin/bash
# Appele par Sunshine au debut ("do") et a la fin ("undo") d'une session Moonlight.
# Met l'ecran X :1 a la resolution demandee par le client (moins de pixels a convertir),
# puis le remet en 1920x1080.
export DISPLAY=:1 XAUTHORITY=/root/.Xauthority
OUT=VNC-0
set_mode() { # $1 largeur $2 hauteur
  local w=$1 h=$2 name="${1}x${2}_moon"
  if ! xrandr | grep -q "^   $name "; then
    xrandr --newmode "$name" $(( w * h * 60 / 1000000 + 1 )) $w $((w+8)) $((w+16)) $((w+24)) $h $((h+1)) $((h+2)) $((h+3)) 2>/dev/null || true
    xrandr --addmode $OUT "$name" 2>/dev/null || true
  fi
  xrandr --output $OUT --mode "$name"
}
case "$1" in
  do)   W=${SUNSHINE_CLIENT_WIDTH:-1280}; H=${SUNSHINE_CLIENT_HEIGHT:-720}
        echo "$(date +%T) debut session: ${W}x${H}@${SUNSHINE_CLIENT_FPS:-?}" >> /var/log/sunshine-resize.log
        set_mode "$W" "$H" ;;
  fixed) echo "$(date +%T) resolution fixe demandee: ${2}x${3}" >> /var/log/sunshine-resize.log
        set_mode "$2" "$3" ;;
  undo) echo "$(date +%T) fin session: retour 1920x1080" >> /var/log/sunshine-resize.log
        set_mode 1920 1080 ;;
esac
