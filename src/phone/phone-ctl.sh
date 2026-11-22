#!/system/bin/sh
# Pilote du telephone : appele par l'app Android "Pilote" (via su) ou a la main.
# Usage : phone-ctl.sh status | chroot-start | chroot-stop | chroot-reboot | perf-boost
#                      | vnc-restart | llm-start | llm-stop | virgl-start | virgl-stop
# Ne jamais mettre de copie de ce fichier dans /data/adb/service.d (Magisk execute tous les fichiers).
export PATH=/system/bin:/system/xbin:/apex/com.android.runtime/bin:/data/adb/magisk:$PATH
CH=/data/local/chroot-distro/debian
C=/sys/devices/system/cpu/cpufreq
LOG=/data/local/tmp/phone-ctl.log
SOCK=$CH/tmp/.virgl_test
VIRGL=/data/data/com.termux/files/usr/bin/virgl_test_server_android

say() { echo "$*"; echo "$(date '+%F %T') $*" >> $LOG; }
inch() { /system/bin/chroot-distro command debian "$1" >>$LOG 2>&1; }

# --- etat ---------------------------------------------------------------
virgl_pid() { pgrep -f virgl_test_server_androi[d] | head -n 1; }
# sshd renomme son titre de processus : on lit son fichier pid dans le chroot
chroot_up() { p=$(cat $CH/run/sshd.pid 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
status() {
  chroot_up && echo chroot=1 || echo chroot=0
  [ -n "$(pidof Xvnc)" ] && echo vnc=1 || echo vnc=0
  [ -n "$(pidof sunshine-v4l2)" ] && echo sunshine=1 || echo sunshine=0
  [ -n "$(pgrep -f 'Qwen2.5-3B-Instruct-Q4_0.ggu[f]' | head -n 1)" ] && echo llm=1 || echo llm=0
  [ -n "$(pgrep -f 'Olala-v1.0-mini-7A1B-q5_k_m.ggu[f]' | head -n 1)" ] && echo olala=1 || echo olala=0
  [ -n "$(virgl_pid)" ] && echo virgl=1 || echo virgl=0
  echo gov=$(cat $C/policy3/scaling_governor 2>/dev/null)
  echo cap=$(cat $C/policy3/scaling_max_freq 2>/dev/null)
  echo hw=$(cat $C/policy3/cpuinfo_max_freq 2>/dev/null)
  # plafond inattendu sur le bloc Equilibre (constructeur ou garde-fou thermique) : max actuel < max attendu
  g3=$(cat $C/policy3/scaling_governor); m3=$(grep -c "^3 " $GOVF 2>/dev/null)
  if [ "$m3" = 1 ] && [ "$g3" = powersave ]; then exp=$(pick 3 "$PS_MAX")
  elif { [ "$m3" = 1 ] && [ "$g3" = performance ]; } || on_power; then exp=$(cat $C/policy3/cpuinfo_max_freq)
  else exp=$(pick 3 "$ECO_MAX"); fi
  [ "$(cat $C/policy3/scaling_max_freq)" -lt "${exp:-0}" ] && echo clamp=1 || echo clamp=0
  for n in 0 3 7; do
    echo gov$n=$(cat $C/policy$n/scaling_governor 2>/dev/null)
    grep -q "^$n " $GOVF 2>/dev/null && echo man$n=1 || echo man$n=0
  done
}

# --- gouverneur par bloc (0 = Eco, 3 = Equilibre, 7 = Turbo) ----------------------
# Le choix manuel est lu par le demon 50-power-profile.sh (le garde-fou thermique reste prioritaire).
GOVF=/data/local/tmp/gov-manual
on_power() {
  st=$(cat /sys/class/power_supply/battery/status 2>/dev/null)
  [ "$(cat /sys/class/power_supply/usb/online 2>/dev/null)" = 1 ] || [ "$(cat /sys/class/power_supply/wireless/online 2>/dev/null)" = 1 ] || [ "$st" = Charging ] || [ "$st" = Full ]
}
gov_all() {
  ok=0
  for n in 0 3 7; do gov_set $n "$1" >/dev/null && ok=$((ok+1)); done
  g=$(cat $C/policy3/scaling_governor)
  [ "$ok" = 3 ] && say "3 blocs CPU : gouverneur $g$( [ "$1" = auto ] && echo " (automatique)")" || say "ECHEC : gouverneur $1 applique a $ok bloc(s) sur 3"
}
# Plafonds de frequence (kHz) : "Econome" (powersave choisi a la main) et eco sur batterie (memes valeurs que le demon).
# Le systeme Oplus releve la frequence minimale ; le noyau ne la laisse pas depasser le plafond.
PS_MAX="0:1228800 3:1286400 7:1248000"
ECO_MAX="0:1459200 3:1785600 7:1593600"
pick() { for e in $2; do [ "${e%%:*}" = "$1" ] && echo ${e##*:}; done; }
apply_cap() {
  n=$1; g=$2
  if [ "$g" = powersave ]; then c=$(pick $n "$PS_MAX")
  elif [ "$g" = performance ] || on_power; then c=$(cat $C/policy$n/cpuinfo_max_freq)   # Max : sans plafond, meme sur batterie
  else c=$(pick $n "$ECO_MAX"); fi
  echo $c > $C/policy$n/scaling_max_freq 2>/dev/null
}
gov_set() {
  n=$1; g=$2
  case "$n" in 0) nom=Eco ;; 3) nom=Equilibre ;; 7) nom=Turbo ;; *) say "ECHEC : bloc inconnu ($n)"; return 1 ;; esac
  touch $GOVF; grep -v "^$n " $GOVF > $GOVF.tmp; mv $GOVF.tmp $GOVF
  if [ "$g" = auto ]; then
    on_power && g=performance || g=uag
    echo $g > $C/policy$n/scaling_governor 2>/dev/null
    apply_cap $n $g
    say "$nom : gouverneur automatique ($g)"
  else
    grep -qw "$g" $C/policy$n/scaling_available_governors || { say "ECHEC : gouverneur $g indisponible"; return 1; }
    echo "$n $g" >> $GOVF
    echo $g > $C/policy$n/scaling_governor 2>/dev/null
    apply_cap $n $g
    [ "$(cat $C/policy$n/scaling_governor)" = "$g" ] && say "$nom : gouverneur $g" || say "ECHEC : $nom refuse $g"
  fi
}

# --- VirGL (serveur GPU Adreno, cote Android, socket dans le /tmp du chroot) ---
virgl_start() {
  if [ -n "$(virgl_pid)" ]; then say "VirGL deja actif"; return 0; fi
  mkdir -p /data/local/tmp/virgl-home
  rm -f $SOCK
  HOME=/data/local/tmp/virgl-home TMPDIR=/data/local/tmp/virgl-home setsid nohup $VIRGL --socket-path $SOCK >/data/local/tmp/virgl-server.log 2>&1 </dev/null &
  i=0; while [ $i -lt 20 ]; do [ -S $SOCK ] && break; sleep 1; i=$((i+1)); done
  [ -S $SOCK ] && chmod 666 $SOCK
  [ -n "$(virgl_pid)" ] && say "VirGL demarre" || say "ECHEC : VirGL ne demarre pas (voir /data/local/tmp/virgl-server.log)"
}
virgl_stop() {
  pkill -f virgl_test_server_androi[d]
  sleep 1
  rm -f $SOCK
  [ -z "$(virgl_pid)" ] && say "VirGL arrete" || say "ECHEC : VirGL tourne encore"
}

# --- chroot ---------------------------------------------------------------
chroot_start() {
  inch "mkdir -p /run/sshd; chown 0:0 /run/sshd; chmod 755 /run/sshd; service ssh start"
  inch "ip link show wg0 >/dev/null 2>&1 || wg-quick up wg0; ip rule | grep -q '^9000:' || ip rule add to 10.0.0.0/24 lookup main pref 9000"
  inch /usr/local/bin/wg-poke-start.sh
  inch /usr/local/bin/code-server-start.sh
  inch /usr/local/bin/vnc-start.sh
  virgl_start >/dev/null
  inch /usr/local/bin/sunshine-start.sh
  inch /usr/local/bin/dashboard-start.sh
  chroot_up && say "Chroot demarre" || say "ECHEC : sshd absent apres le demarrage"
}
chroot_pids() {
  for d in /proc/[0-9]*; do
    [ "$(readlink $d/root 2>/dev/null)" = "$CH" ] && echo ${d#/proc/}
  done
}
chroot_stop() {
  virgl_stop >/dev/null
  for s in 15 9; do
    for p in $(chroot_pids); do kill -$s $p 2>/dev/null; done
    sleep 3
  done
  /system/bin/chroot-distro unmount debian >>$LOG 2>&1
  chroot_up && say "ECHEC : des processus du chroot tournent encore" || say "Chroot arrete"
}

# --- performance ------------------------------------------------------------
perf_boost() {
  for p in $C/policy*; do
    cat $p/cpuinfo_max_freq > $p/scaling_max_freq 2>/dev/null
    echo performance > $p/scaling_governor 2>/dev/null
  done
  # relance le demon de profil s'il est mort (sauf si le kill switch est pose)
  PIDF=/data/local/tmp/power-profile.pid
  if [ ! -f /data/adb/no-power-profile ] && ! { [ -f $PIDF ] && kill -0 "$(cat $PIDF)" 2>/dev/null; }; then
    setsid sh /data/adb/service.d/50-power-profile.sh </dev/null >/dev/null 2>&1 &
  fi
  cap=$(cat $C/policy3/scaling_max_freq); hw=$(cat $C/policy3/cpuinfo_max_freq)
  gov=$(cat $C/policy3/scaling_governor)
  st=$(cat /sys/class/power_supply/battery/status 2>/dev/null)
  usb=$(cat /sys/class/power_supply/usb/online 2>/dev/null); wl=$(cat /sys/class/power_supply/wireless/online 2>/dev/null)
  if [ "$usb" != 1 ] && [ "$wl" != 1 ] && [ "$st" != Charging ] && [ "$st" != Full ]; then
    say "Sur batterie : le profil eco reprend la main dans 5 s. Branchez le telephone pour la performance."
  elif [ "$cap" -lt "$hw" ]; then
    say "Plafond constructeur actif : $((cap/1000)) MHz sur $((hw/1000)). Reglages > Batterie > Mode haute performance : desactiver puis reactiver."
  else
    say "Performance appliquee : $gov, $((cap/1000)) MHz"
  fi
}

# --- VNC / Sunshine -----------------------------------------------------------
sunshine_stop() {
  inch "for f in /run/sunshine.pid /run/sunshine-input-bridge.pid; do [ -f \$f ] && kill \$(cat \$f) 2>/dev/null; rm -f \$f; done; pkill -x sunshine-v4l2; true"
  sleep 1
  [ -z "$(pidof sunshine-v4l2)" ] && say "Sunshine arrete" || say "ECHEC : Sunshine tourne encore"
}
sunshine_start() {
  [ -n "$(pidof Xvnc)" ] || { say "ECHEC : demarrez d'abord VNC (Sunshine capture son bureau)"; return 1; }
  inch /usr/local/bin/sunshine-start.sh
  sleep 2
  [ -n "$(pidof sunshine-v4l2)" ] && say "Sunshine demarre" || say "ECHEC : Sunshine absent (voir /var/log/sunshine.log dans le chroot)"
}
vnc_stop() {
  sunshine_stop >/dev/null     # Sunshine n'a plus rien a capturer sans le bureau
  inch "pkill -x Xvnc; sleep 2; pkill -9 -x Xvnc; true"
  [ -z "$(pidof Xvnc)" ] && say "VNC arrete" || say "ECHEC : Xvnc tourne encore"
}
vnc_start() {
  inch /usr/local/bin/vnc-start.sh
  [ -n "$(pidof Xvnc)" ] && say "VNC demarre" || say "ECHEC : Xvnc absent (voir /var/log/vnc-start.log dans le chroot)"
}
# --- ouverture d'applications Android depuis l'app (Moonlight, Open WebUI) -----------------------
open_app() {      # open_app <paquet> <nom> [kill]
  pkg=$1; nom=$2
  pm path $pkg >/dev/null 2>&1 || { say "ECHEC : $nom n'est pas installe ($pkg)"; return 1; }
  [ "$3" = kill ] && am force-stop $pkg      # repart d'une session neuve
  sleep 1
  act=$(cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER $pkg | tail -n 1)
  am start -n "$act" >/dev/null 2>&1
  say "$nom ouvert"
}
vnc_restart() {
  inch "for f in /run/sunshine.pid /run/sunshine-input-bridge.pid; do [ -f \$f ] && kill \$(cat \$f) 2>/dev/null; rm -f \$f; done; pkill -x sunshine-v4l2; pkill -x Xvnc; sleep 2; pkill -9 -x Xvnc; true"
  sleep 1
  inch /usr/local/bin/vnc-start.sh
  inch /usr/local/bin/sunshine-start.sh
  [ -n "$(pidof Xvnc)" ] && say "VNC redemarre" || say "ECHEC : Xvnc absent (voir /var/log/vnc-start.log dans le chroot)"
}

# --- IA locale ------------------------------------------------------------------
# Deux serveurs portent le meme nom de processus (llama-server) : on les distingue par leur modele.
# (le crochet [f] evite que pgrep -f se retrouve lui-meme)
npu_pid() { pgrep -f 'Qwen2.5-3B-Instruct-Q4_0.ggu[f]' | head -n 1; }
olala_pid() { pgrep -f 'Olala-v1.0-mini-7A1B-q5_k_m.ggu[f]' | head -n 1; }
npu_kill() { pkill -f 'Qwen2.5-3B-Instruct-Q4_0.ggu[f]'; sleep 2; pkill -9 -f 'Qwen2.5-3B-Instruct-Q4_0.ggu[f]'; true; }
olala_kill() { pkill -f 'Olala-v1.0-mini-7A1B-q5_k_m.ggu[f]'; sleep 2; pkill -9 -f 'Olala-v1.0-mini-7A1B-q5_k_m.ggu[f]'; true; }

llm_stop() {
  npu_kill
  inch "pkill -f 'webui-venv/bin/open-webu[i]'; true"
  [ -z "$(npu_pid)" ] && say "IA arretee (NPU + Open WebUI)" || say "ECHEC : le serveur NPU tourne encore"
}
llm_start() {
  [ -n "$(olala_pid)" ] && olala_kill   # memoire : un seul modele a la fois
  nice -n 10 sh $CH/root/llm/llama-npu-start.sh >>$LOG 2>&1
  sleep 2
  inch /usr/local/bin/webui-start.sh
  [ -n "$(npu_pid)" ] && say "IA NPU demarree (le modele se charge, ~1 min)" || say "ECHEC : serveur NPU absent (voir /data/local/tmp/llama-npu.log)"
}
olala_stop() {
  olala_kill
  [ -z "$(olala_pid)" ] && say "Olala arrete" || say "ECHEC : Olala tourne encore"
}
olala_start() {
  [ -n "$(npu_pid)" ] && npu_kill       # memoire : un seul modele a la fois
  sh $CH/root/llm/olala/olala-hex-start.sh >>$LOG 2>&1
  ( inch /usr/local/bin/olala-warmup.sh & )   # prechauffage en arriere-plan
  inch /usr/local/bin/webui-start.sh
  sleep 3
  [ -n "$(olala_pid)" ] && say "Olala demarre (pret et prechauffe dans ~1 min)" || say "ECHEC : Olala absent (voir /data/local/tmp/olala-server.log)"
}

case "$1" in
  status) status ;;
  chroot-start) chroot_start ;;
  chroot-stop) chroot_stop ;;
  chroot-reboot) chroot_stop; chroot_start ;;
  perf-boost) perf_boost ;;
  vnc-restart) vnc_restart ;;
  open-moonlight) open_app com.limelight Moonlight ;;
  open-webui) open_app fr.pilote.webui "Open WebUI" kill ;;
  vnc-start) vnc_start ;;
  vnc-stop) vnc_stop ;;
  sunshine-start) sunshine_start ;;
  sunshine-stop) sunshine_stop ;;
  llm-start) llm_start ;;
  llm-stop) llm_stop ;;
  olala-start) olala_start ;;
  olala-stop) olala_stop ;;
  virgl-start) virgl_start ;;
  virgl-stop) virgl_stop ;;
  gov) if [ "$2" = all ]; then gov_all "$3"; else gov_set "$2" "$3"; fi ;;
  *) echo "usage: $0 status|chroot-start|chroot-stop|chroot-reboot|perf-boost|vnc-restart|open-moonlight|open-webui|vnc-start|vnc-stop|sunshine-start|sunshine-stop|llm-start|llm-stop|olala-start|olala-stop|virgl-start|virgl-stop|gov <0|3|7|all> <gouverneur|auto>"; exit 2 ;;
esac
