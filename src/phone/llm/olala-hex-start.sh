#!/system/bin/sh
# Olala sur le CPU avec le moteur compile par le NDK Android (cote Android, bionic) : prefill ~2x plus rapide que la version du chroot.
# API compatible OpenAI sur 127.0.0.1:8082 (alias olala-7a1b). Arret : bouton Pilote ou phone-ctl.sh olala-stop.
A=/data/local/chroot-distro/debian/root/llm/olala
B=$A/hex
pgrep -f 'Olala-v1.0-mini-7A1B-q5_k_m.ggu[f]' >/dev/null && exit 0
export LD_LIBRARY_PATH=$B/lib:/vendor/lib64:/system/lib64
cd $B
setsid nohup nice -n 10 ./bin/llama-server -m $A/gguf/Olala-v1.0-mini-7A1B-q5_k_m.gguf --device none -ngl 0 -t 5 -fa 1 \
  -c 8192 -np 1 -cms 256 -ctxcp 16 -cram 2048 --reasoning-effort disabled --host 127.0.0.1 --port 8082 --alias olala-7a1b \
  >/data/local/tmp/olala-server.log 2>&1 </dev/null &
