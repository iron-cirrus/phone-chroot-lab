# 01 — Base : root, chroot, démarrage

## Prérequis

| Élément | Valeur utilisée |
|---|---|
| Appareil | OnePlus 11 (CPH2449), Snapdragon 8 Gen 2, ~16 Go de RAM, Android 13 |
| Root | Magisk |
| Chroot | module Magisk **chroot-distro**, distribution `debian` (Debian 13), dans `/data/local/chroot-distro/debian` |
| Termux | requis uniquement pour `virgl_test_server_android` (GPU, voir [03](03-bureau-distant.md)) |
| Réseau | Wi-Fi maison + tunnel WireGuard (accès à distance) |

Le chroot est un vrai `chroot` avec les systèmes de fichiers de l'hôte montés dedans (`/proc`, `/sys`, `/dev`, stockage) : c'est ce qui permet
d'y lire les compteurs du noyau (énergie, `msm_vidc`, `kgsl`, batterie) et d'en sortir vers Android avec `nsenter`.

## Séquence de démarrage

Magisk exécute au boot les fichiers de `/data/adb/service.d/`. Chacun est autonome et attend `sys.boot_completed`.

| Fichier | Rôle |
|---|---|
| `10-chroot-ssh.sh` | Démarre le chroot, **sshd**, **WireGuard** (+ règle de routage Android vers le réseau du tunnel), un keepalive WireGuard, **code-server** (127.0.0.1:8080), **TurboVNC** (127.0.0.1:5901), le serveur **VirGL** (Termux), **Sunshine**, le **tableau de bord** |
| `20-vnc-fw.sh` | Applique `vnc-fw.sh` puis le réapplique toutes les 30 s (netd peut modifier les chaînes `iptables`) |
| `30-wifi-lowlatency.sh` | Force les modes Wi-Fi faible latence et coupe l'économie d'énergie du pilote, réverifié toutes les 30 s (le pilote la réactive à chaque reconnexion) |
| `40-llm.sh` | IA locale : attend le boot + 180 s, démarre le serveur NPU puis, 30 s plus tard, Open WebUI, en basse priorité |
| `50-power-profile.sh` | Démon des gouverneurs CPU (voir [04](04-profil-puissance.md)) |

Les scripts du chroot (`vnc-start.sh`, `sunshine-start.sh`, `code-server-start.sh`, `wg-poke-start.sh`, `dashboard-start.sh`) vivent dans
`/usr/local/bin` du chroot ; ils sont idempotents (ils sortent si le service tourne déjà). Voir `src/phone/chroot/` et `src/phone/llm/`.

### Pourquoi les services sont lancés avec `nice`, `taskset` et un délai
Le téléphone n'a que 8 coeurs, dont un seul gros. Tout lancer en même temps au boot, avec l'IA, a figé Android. Donc : IA 3 minutes après le boot,
serveurs IA à faible priorité, Open WebUI sur les coeurs 3-7, Sunshine/VNC sur 3-7, petits coeurs laissés à Android.

### Interrupteurs d'arrêt d'urgence
Créer un fichier, puis redémarrer :
- `touch /data/adb/no-llm` : l'IA ne démarre pas ;
- `touch /data/adb/no-power-profile` : le démon de gouverneurs ne démarre pas.

## Le piège n°1 : Magisk exécute TOUS les fichiers de `service.d`

Le 2026-11-20, des sauvegardes de `10-chroot-ssh.sh` nommées `.bak-llm` et `.bak-npu` avaient été laissées dans `/data/adb/service.d/`.
Magisk les a **toutes exécutées en parallèle** : trois lancements concurrents de `chroot-distro` (montages et démontages qui se marchent dessus)
plus des démarrages d'IA ont figé Android après le code PIN.

**Règle :** aucune copie, aucune sauvegarde dans `service.d`. Les sauvegardes vont dans `/data/adb/service.d-sauvegardes/` (non exécuté).

Conséquence collatérale : après un **mode sans échec** (Magisk ou Android), un marqueur `disable` reste dans chaque `/data/adb/modules/*/`
et `/system/bin/chroot-distro` peut disparaître aux démarrages suivants. Remède :
```sh
# en root
rm -f /data/adb/modules/*/disable
reboot
```
En mode sans échec Android, `su` est refusé : on ne peut pas se réparer depuis le téléphone ; il faut `adb` avec une fenêtre Magisk acceptée
sur l'écran, ou redémarrer en mode normal.

## Autres points à connaître

- **Mémoire partagée System V** : le noyau Android n'a pas SYSVIPC. TurboVNC, Sunshine et les navigateurs sont lancés avec
  `LD_PRELOAD=/usr/local/lib/libsysvshm-emu.so` (une petite bibliothèque d'émulation, non incluse ici) ; `/dev/shm/sysvshm-emu` est vidé à
  chaque nouvelle session X.
- **État du chroot** : `sshd` change son titre de processus ; l'état « chroot actif » se lit donc dans
  `/data/local/chroot-distro/debian/run/sshd.pid`, pas avec `pgrep sshd`.
- **Ne jamais lancer en root** les navigateurs : `chromium-web` (dans `/usr/local/bin`) les lance sous l'utilisateur `web`, sandbox active,
  sur les coeurs 3-7, avec WebGL via VirGL si le serveur GPU tourne.
- **Applications gloutonnes d'Android** : voir [04](04-profil-puissance.md#applications-gloutonnes).
