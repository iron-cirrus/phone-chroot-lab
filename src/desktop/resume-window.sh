#!/bin/bash
# Fenetre Chromium (mode application, profil dedie) qui affiche le resume du chroot, sans barre de titre ni bordure,
# posee sur le fond d'ecran (sous les autres fenetres).
export DISPLAY=:1 XAUTHORITY=/root/.Xauthority HOME=/root
W=${1:-470}; H=${2:-740}; X=${3:-2060}; Y=${4:-20}
# attend le tableau de bord
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:8090/resume && break; sleep 2; done
setsid nohup /usr/local/bin/chromium-web --app="http://127.0.0.1:8090/resume?bare" \
  --user-data-dir=/home/web/.config/chromium-resume --class=ResumeChroot \
  --window-size=$W,$H --window-position=$X,$Y --no-first-run --disable-session-crashed-bubble \
  --disable-features=Translate --disable-infobars >/tmp/resume-chromium.log 2>&1 &
# attend la fenetre, retire la decoration, la met sous les autres et sur tous les bureaux
for i in $(seq 1 40); do
  id=$(xdotool search --class ResumeChroot 2>/dev/null | tail -1)
  [ -n "$id" ] && break; sleep 1
done
[ -z "$id" ] && exit 1
for t in 3 3 5 8 10; do                    # Chromium reecrit ses hints au demarrage : on les remet plusieurs fois, sur chaque fenetre
  sleep $t
  for id in $(xdotool search --class ResumeChroot 2>/dev/null); do
    xprop -id $id -f _MOTIF_WM_HINTS 32c -set _MOTIF_WM_HINTS "2, 0, 0, 0, 0"
    wmctrl -i -r $id -b add,below,skip_taskbar,skip_pager 2>/dev/null
    xdotool windowmove $id $X $Y windowsize $id $W $H 2>/dev/null
  done
done
