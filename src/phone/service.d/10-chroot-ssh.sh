#!/system/bin/sh
# Lance le chroot Debian (chroot-distro) et sshd au demarrage, en root (Magisk service.d)
LOG=/data/local/tmp/chroot-boot.log
exec >>"$LOG" 2>&1
echo "=== $(date) service.d: debut ==="
until [ "$(getprop sys.boot_completed)" = "1" ]; do sleep 3; done
# attendre le stockage /sdcard (FUSE), monte dans le chroot par android_bind
i=0; until [ -d /sdcard/Android ] || [ "$i" -ge 60 ]; do sleep 2; i=$((i+1)); done
sleep 5
export PATH=/system/bin:/system/xbin:/apex/com.android.runtime/bin:/data/adb/magisk:$PATH
/system/bin/chroot-distro command debian "mkdir -p /run/sshd; chown 0:0 /run/sshd; chmod 755 /run/sshd; service ssh start"
echo "chroot-distro exit=$? ($(date))"
# WireGuard: monte wg0 (si absent) + regle de routage Android pour le reseau du tunnel
/system/bin/chroot-distro command debian "ip link show wg0 >/dev/null 2>&1 || wg-quick up wg0; ip rule | grep -q '^9000:' || ip rule add to 10.0.0.0/24 lookup main pref 9000"
echo "wireguard exit=$? ($(date))"
# Keepalive dans le tunnel: declenche le handshake WireGuard (utile en 4G) et le maintient
/system/bin/chroot-distro command debian "/usr/local/bin/wg-poke-start.sh"
echo "wg-poke exit=$? ($(date))"
# VS Code (code-server) sur 127.0.0.1:8080
/system/bin/chroot-distro command debian "/usr/local/bin/code-server-start.sh"
echo "code-server exit=$? ($(date))"
# Bureau MATE via TurboVNC (127.0.0.1:5901)
/system/bin/chroot-distro command debian "/usr/local/bin/vnc-start.sh"
echo "vnc exit=$? ($(date))"
# Serveur VirGL (GPU Adreno via OpenGL ES). Lance APRES les montages du chroot: la socket vit dans le /tmp du chroot
mkdir -p /data/local/tmp/virgl-home
rm -f /data/local/chroot-distro/debian/tmp/.virgl_test
HOME=/data/local/tmp/virgl-home TMPDIR=/data/local/tmp/virgl-home setsid nohup /data/data/com.termux/files/usr/bin/virgl_test_server_android --socket-path /data/local/chroot-distro/debian/tmp/.virgl_test >/data/local/tmp/virgl-server.log 2>&1 &
# socket VirGL accessible a l utilisateur "web" (Firefox) une fois cree
( for i in $(seq 1 30); do [ -S /data/local/chroot-distro/debian/tmp/.virgl_test ] && chmod 666 /data/local/chroot-distro/debian/tmp/.virgl_test && break; sleep 1; done ) &
echo "virgl lance ($(date))"
# Sunshine (Moonlight) sur la session X :1
/system/bin/chroot-distro command debian "/usr/local/bin/sunshine-start.sh"
echo "sunshine exit=$? ($(date))"
# Tableau de bord CPU/GPU/NPU (Flask, port 8090)
/system/bin/chroot-distro command debian "/usr/local/bin/dashboard-start.sh"
echo "dashboard exit=$? ($(date))"
