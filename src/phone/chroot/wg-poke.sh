#!/bin/bash
# Envoie un petit paquet UDP dans le tunnel toutes les 20 s: declenche/maintient le handshake WireGuard
while true; do
  ip link show wg0 >/dev/null 2>&1 && echo -n x > /dev/udp/10.0.0.1/9 2>/dev/null
  sleep 20
done
