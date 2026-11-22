#!/system/bin/sh
# Profil CPU : branche = performance + frequences max ; sur batterie = eco (uag + plafonds d'origine).
# Le chroot n'est jamais touche. Kill switch : touch /data/adb/no-power-profile
LOG=/data/local/tmp/power-profile.log
PIDF=/data/local/tmp/power-profile.pid
C=/sys/devices/system/cpu/cpufreq
# plafonds eco (valeurs d'origine relevees) : policy:max
ECO_MAX="0:1459200 3:1785600 7:1593600"
# gouverneur choisi a la main (app Pilote) : lignes "policy gouverneur" ; le garde-fou thermique reste prioritaire
GOVF=/data/local/tmp/gov-manual
gov_for() { g=$(grep "^$1 " $GOVF 2>/dev/null | cut -d" " -f2); echo ${g:-$2}; }
# plafond "Econome" : quand powersave est choisi a la main, on borne aussi la frequence max
# (le systeme Oplus releve la frequence minimale ; le noyau ne la laisse pas depasser le plafond)
PS_MAX="0:1228800 3:1286400 7:1248000"
ps_cap() { for e in $PS_MAX; do [ "${e%%:*}" = "$1" ] && echo ${e##*:}; done; }
(
  [ -f /data/adb/no-power-profile ] && exit 0
  [ -f $PIDF ] && kill -0 "$(cat $PIDF)" 2>/dev/null && exit 0
  read MYPID _ < /proc/self/stat; echo $MYPID > $PIDF
  while [ "$(getprop sys.boot_completed)" != "1" ]; do sleep 5; done
  sleep 60
  last=""; clamp=0; alerted=0
  while [ ! -f /data/adb/no-power-profile ]; do
    st=$(cat /sys/class/power_supply/battery/status 2>/dev/null)
    usb=$(cat /sys/class/power_supply/usb/online 2>/dev/null)
    wl=$(cat /sys/class/power_supply/wireless/online 2>/dev/null)
    if [ "$usb" = "1" ] || [ "$wl" = "1" ] || [ "$st" = "Charging" ] || [ "$st" = "Full" ]; then mode=perf; else mode=eco; fi
    # garde-fou thermique : peau > 47 C -> on n'insiste pas, eco jusqu'a < 43 C
    skin=0
    for z in /sys/class/thermal/thermal_zone*; do [ "$(cat $z/type)" = "skin-msm-therm" ] && skin=$(cat $z/temp) && break; done
    if [ "$mode" = perf ] || grep -q " performance" $GOVF 2>/dev/null; then   # aussi quand Max est choisi a la main (meme sur batterie)
      if [ "$skin" -ge 47000 ]; then mode=hot; elif [ "$last" = hot ] && [ "$skin" -ge 43000 ]; then mode=hot; fi
    fi
    if [ "$mode" = perf ]; then
      for p in $C/policy*; do
        if [ "$(gov_for ${p##*policy} x)" = powersave ]; then ps_cap ${p##*policy} > $p/scaling_max_freq 2>/dev/null
        else echo $(cat $p/cpuinfo_max_freq) > $p/scaling_max_freq 2>/dev/null; fi
        echo $(gov_for ${p##*policy} performance) > $p/scaling_governor 2>/dev/null
      done
      cap=$(cat $C/policy3/scaling_max_freq); hw=$(cat $C/policy3/cpuinfo_max_freq)
      [ "$(gov_for 3 x)" = powersave ] && cap=$hw   # plafond volontaire : pas d'alerte
      if [ "$cap" -lt "$hw" ]; then clamp=$((clamp+1)); else clamp=0; alerted=0; fi
      if [ "$clamp" -ge 6 ] && [ "$alerted" != 1 ]; then
        cmd notification post -S bigtext -t "Mode haute performance perdu" perfprofile "Les CPU sont plafonnes (${cap} kHz) alors que le telephone est branche. Reglages > Batterie > Mode haute performance : desactiver puis reactiver." >/dev/null 2>&1
        echo "$(date +%T) ALERTE plafond cpu3-6=$cap hw=$hw" >> $LOG; alerted=1
      fi
    elif [ "$last" != "$mode" ] || [ "$mode" = hot ]; then
      clamp=0; alerted=0
      for p in $C/policy*; do
        n=${p##*policy}
        # plafond : Econome = bas ; Max choisi a la main = frequence materielle (sauf garde-fou thermique) ; sinon eco
        if [ "$(gov_for $n x)" = powersave ]; then ps_cap $n > $p/scaling_max_freq 2>/dev/null
        elif [ "$mode" != hot ] && [ "$(gov_for $n x)" = performance ]; then cat $p/cpuinfo_max_freq > $p/scaling_max_freq 2>/dev/null
        else for e in $ECO_MAX; do [ "${e%%:*}" = "$n" ] && echo ${e##*:} > $p/scaling_max_freq 2>/dev/null; done; fi
        if [ "$mode" = hot ]; then g=uag; else g=$(gov_for $n uag); fi
        echo $g > $p/scaling_governor 2>/dev/null
      done
    fi
    [ "$mode" != "$last" ] && echo "$(date '+%F %T') mode=$mode status=$st skin=$skin" >> $LOG
    last=$mode
    sleep 5
  done
) >/dev/null 2>&1 &
