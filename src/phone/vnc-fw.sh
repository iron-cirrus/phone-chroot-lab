#!/system/bin/sh
# Pare-feu du port VNC 5901 (idempotent). Autorise: boucle locale, Wi-Fi maison, partage de connexion. Refuse le reste (4G, wg0, autres Wi-Fi).
IPT=/system/bin/iptables
$IPT -N vnc5901 2>/dev/null
$IPT -F vnc5901
$IPT -A vnc5901 -i lo -j ACCEPT
$IPT -A vnc5901 -i wlan0 -s 192.168.1.0/24 -j ACCEPT
for IF in swlan+ ap+ wlan1 rndis+ ncm+ bt-pan eth+; do $IPT -A vnc5901 -i $IF -j ACCEPT; done
$IPT -A vnc5901 -j DROP
$IPT -C INPUT -p tcp --dport 5901 -j vnc5901 2>/dev/null || $IPT -I INPUT 1 -p tcp --dport 5901 -j vnc5901
# Sunshine / Moonlight: memes regles reseau que VNC
$IPT -C INPUT -p tcp -m multiport --dports 47984,47989,47990,48010 -j vnc5901 2>/dev/null || $IPT -I INPUT 1 -p tcp -m multiport --dports 47984,47989,47990,48010 -j vnc5901
$IPT -C INPUT -p udp -m multiport --dports 47998:48010 -j vnc5901 2>/dev/null || $IPT -I INPUT 1 -p udp -m multiport --dports 47998:48010 -j vnc5901
