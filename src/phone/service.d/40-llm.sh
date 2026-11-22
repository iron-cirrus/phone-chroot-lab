#!/system/bin/sh
# IA locale (NPU puis Open WebUI), lancee APRES le demarrage complet, en basse priorite.
# Arret d'urgence: creer le fichier /data/adb/no-llm (touch /data/adb/no-llm) puis redemarrer.
# Ne jamais mettre de copie de ce fichier dans service.d (Magisk execute tous les fichiers).
(
  LOG=/data/local/tmp/llm-boot.log
  echo "=== $(date) : 40-llm.sh lance" >> $LOG
  [ -e /data/adb/no-llm ] && { echo "no-llm present: abandon" >> $LOG; exit 0; }
  until [ "$(getprop sys.boot_completed)" = "1" ]; do sleep 3; done
  sleep 180
  [ -e /data/adb/no-llm ] && { echo "no-llm present: abandon" >> $LOG; exit 0; }
  if pidof llama-server >/dev/null 2>&1; then echo "NPU deja lance" >> $LOG; else
    echo "$(date) : lancement du serveur NPU" >> $LOG
    nice -n 10 sh /data/local/chroot-distro/debian/root/llm/llama-npu-start.sh >> $LOG 2>&1
  fi
  sleep 30
  [ -e /data/adb/no-llm ] && { echo "no-llm present: abandon" >> $LOG; exit 0; }
  echo "$(date) : lancement d'Open WebUI" >> $LOG
  /system/bin/chroot-distro command debian "/usr/local/bin/webui-start.sh" >> $LOG 2>&1
  echo "$(date) : fin" >> $LOG
) &