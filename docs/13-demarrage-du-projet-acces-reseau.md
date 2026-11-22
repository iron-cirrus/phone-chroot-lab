# 13 — Démarrage du projet : accès SSH, réseau, WireGuard, premier bureau

Ce guide couvre le **début** de la session (soirée du 19 novembre 2026), avant Sunshine : tout ce qui a permis d'avoir un téléphone joignable de n'importe où et un premier
bureau distant. Les noms de réseaux Wi-Fi, les adresses publiques et toutes les clés ont été retirés.

## 1. Point de départ

- OnePlus 11, Android 13, noyau 5.15, Magisk. Chroot **Debian 12** (module chroot-distro, `/data/local/chroot-distro/debian`, 595 Mo) lancé depuis Termux avec `su`.
- Accessible en SSH sur le Wi-Fi maison, au début avec un mot de passe temporaire.
- `/data` plein à 89 % (26 Go libres) : une image disque **Linux Deploy de 128 Go** (`/data/media/0/debianstable`) n'était plus utilisée (non montée, aucun processus, aucun
  périphérique loop) ; elle a été supprimée sur accord explicite, puis l'application désinstallée.

## 2. SSH par clé

Sous Windows il n'y a pas de `ssh-copy-id` ; équivalent PowerShell (la clé **publique** seulement, jamais la privée) :
```powershell
Get-Content "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh root@<IP_TEL> "umask 077; mkdir -p ~/.ssh && tr -d '\r' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```
Échecs rencontrés et causes :
- `Ignored authorized keys: bad ownership or modes for directory /root` : le rootfs extrait avait un mauvais propriétaire pour `/root` ; `chown root:root /root /root/.ssh` et `chmod 700`.
- Sur ce chroot les dossiers sont créés en 777 : `mkdir -p /run/sshd` produit un dossier que sshd refuse (`/run/sshd must be owned by root and not group or world-writable`) ;
  le script de boot fait donc `mkdir -p /run/sshd; chown 0:0 /run/sshd; chmod 755 /run/sshd` avant `service ssh start`.
- `chroot-distro` « commande inconnue » depuis un `su` de Termux : utiliser le chemin complet `/system/bin/chroot-distro`.
- L'assistant ne saisit jamais de mot de passe : **un mot de passe collé dans une conversation doit être changé** (et l'authentification par mot de passe désactivée).

Durcissement appliqué (`/etc/ssh/sshd_config.d/10-hardening.conf`) : `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin prohibit-password`, `AddressFamily inet`
(plus d'écoute sur les adresses IPv6 publiques). Vérifié : clé acceptée, mot de passe refusé, IPv6 publique refusée.

## 3. Démarrage automatique du chroot

`/data/adb/service.d/10-chroot-ssh.sh` (Magisk, en root, après `sys.boot_completed` et le montage de `/sdcard`) lance `chroot-distro command debian "service ssh start"`. Journal : `/data/local/tmp/chroot-boot.log`.
Termux n'a pas besoin de démarrer. Voir [01](01-base-root-chroot.md) pour la suite du script et le piège de `service.d`.

## 4. Un Wi-Fi qui change tout seul

Le téléphone se reconnectait à un autre réseau (21 réseaux mémorisés) et devenait injoignable. Android 13 n'a plus de **priorité** entre réseaux et `cmd wifi` n'en règle pas.
Remède : mettre `AutoJoinEnabled` à `false` pour tous les réseaux sauf ceux voulus, dans le fichier XML de configuration du Wi-Fi d'Android, sauvegarde faite avant, droits et contexte SELinux
conservés, puis redémarrage ; Android a gardé les réglages.

## 5. WireGuard : accéder au téléphone de n'importe où

Pourquoi WireGuard plutôt qu'un SSH inversé : tunnel en UDP dans le noyau (ChaCha20 efficace sur ARM), pas d'empilement TCP-sur-TCP, reprise rapide Wi-Fi → 4G. Le noyau du téléphone
l'a déjà (`CONFIG_WIREGUARD=y`, vérifié en créant une interface de test) : **pas besoin de `wireguard-go`**, seulement `wireguard-tools` dans le chroot (`wg`, `wg-quick`).

Côté téléphone (`/etc/wireguard/wg0.conf`, lisible par root seul, clé privée générée sur place et jamais sortie) :
```ini
[Interface]
PrivateKey = <clé privée du téléphone>
Address = 10.0.0.3/32

[Peer]
PublicKey = <clé publique du serveur>
Endpoint = <serveur>:<port>
AllowedIPs = 10.0.0.1/32          # seulement le serveur : le reste du trafic ne passe pas par le tunnel
PersistentKeepalive = 25
```
Côté serveur : un bloc `[Peer]` avec la clé publique du téléphone et `AllowedIPs = 10.0.0.3/32`.

Pièges, dans l'ordre où ils sont tombés :
1. **Pas de handshake depuis le même réseau local** : le téléphone visait l'adresse publique de la box, et beaucoup de box ne font pas de « hairpin NAT ». Test de validité : pointer temporairement sur l'adresse locale du serveur.
2. **Android ignorait le tunnel pour les programmes en root** (routage par règles) : `ip rule add to 10.0.0.0/24 lookup main pref 9000`, rejoué au boot.
3. **Aucun handshake en 4G** alors que les paquets arrivaient au serveur (`tcpdump` : des `In`, jamais de `Out`) : le **pare-feu du serveur** jetait le port. Subtilités : la règle `ufw allow <port>/udp` apparaissait dans `ufw status` mais n'était pas chargée dans le noyau (deux moteurs `iptables-legacy` et `nft`,
   `ufw` déclaré inactif au rechargement). Remède durable : ajouter l'ouverture du port dans les `PostUp`/`PostDown` de `wg0.conf` du serveur, avec les règles inverses `-D`.
4. **WireGuard arrête de retenter** ~90 s après un handshake sans réponse, et ne reprend que si du trafic part : `wg-poke.sh` (`src/phone/chroot/wg-poke.sh`) envoie un octet UDP vers le serveur toutes les 20 s,
   lancé par `wg-poke-start.sh` au boot.
5. **Un PC du réseau ne joignait pas le téléphone** (`10.0.0.3`) à travers le tunnel : le téléphone n'acceptait que `10.0.0.1/32` en source. Remède côté serveur : **masquerade sur `wg0`** (`iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE`) pour que les paquets arrivent avec l'adresse du serveur.
6. **Une clé privée a été collée dans la conversation** (sortie de `cat wg0.conf`) : la clé du serveur a été **renouvelée** juste après (`wg genkey`, remplacement dans `wg0.conf`, `wg set … private-key`, `shred -u` des fichiers temporaires,
   mise à jour de la clé publique côté téléphone). Coller plutôt `grep -v PrivateKey /etc/wireguard/wg0.conf`.

Résultat vérifié par redémarrage : sshd, `wg0`, la règle 9000 et le keepalive reviennent seuls ; ping et SSH par `10.0.0.3` fonctionnent **en 4G**. Le tunnel a aussi survécu au redémarrage du pare-feu du serveur.

## 6. VS Code dans le navigateur (code-server) et outils Python

`code-server` (arm64) écoute **seulement sur 127.0.0.1:8080**, avec un mot de passe aléatoire dans `/root/.config/code-server/config.yaml`. Depuis le PC, passer par un tunnel SSH
(`ssh -L 18080:127.0.0.1:8080 root@<IP_TEL>`) : le port local 8080 est souvent réservé par Windows (Hyper-V, Docker, WSL), d'où `bind: Permission denied` ; `18080` marche.
Outils Python : paquets `apt` (`python3`, `pip`, `venv`, `pipx`, outils de compilation) puis `black`, `ipython`, `mypy`, `poetry`, `pytest`, `ruff`, `uv` via `pipx`.

## 7. Premier bureau : MATE + TurboVNC, rendu logiciel, puis GPU

Besoin : une interface graphique « headless » vue par un client TurboVNC, et si possible le GPU du téléphone. Solutions étudiées avant d'installer :
- **Le GPU n'a pas de pilote DRM Linux** dans ce noyau (`CONFIG_DRM_MSM` absent) : Mesa ne peut pas s'en servir directement. Le noeud `/dev/kgsl-3d0` est pourtant ouvert.
- Choix : **MATE + TurboVNC + Mesa `llvmpipe`** d'abord, puis le GPU via **VirGL** (le serveur `virgl_test_server_android` de Termux traduit l'OpenGL ES d'Android).

Installé : MATE 1.26, TurboVNC 3.3.1 (paquet arm64 officiel ; pas de somme de contrôle publiée, taille vérifiée), `llvmpipe` (OpenGL 4.5, `glxgears` ~213 i/s au départ).
VirGL côté Termux (sans `su`, avec l'identité de Termux) : `pkg update`, `pkg install x11-repo`, `pkg install virglrenderer-android`. Mesure `glmark2` 800×600 :
terrain 5 → 24 FPS, shadow 37 → 53, jellyfish 24 → 51, buffer 53 → 45, score global 28 → 42 (une passe par pilote, téléphone chargé).

### Optimisation (objectif : retrouver les 500 i/s de glxgears qu'on avait sous Linux Deploy)
Causes du ralentissement, mesurées :
- le gouverneur d'Android (`uag`) laissait les gros coeurs à 600-900 MHz alors qu'ils montent à 2,8-3,2 GHz ;
- le compositeur de MATE (`marco`) recomposait chaque image par le CPU ;
- `llvmpipe` répartissait ses threads sur les petits coeurs ;
- bruit ambiant (applications Android gloutonnes).

Résultat : `glxgears` à **~900 i/s** (contre 200-276 au départ et 541 au test précédent) avec le gouverneur `performance` sur les coeurs 3 à 7 et `llvmpipe` limité à ces coeurs ; petits coeurs laissés en `uag`
(aucun gain mesuré). Plus tard remplacé par le démon de [04](04-profil-puissance.md). Pour éviter de faire passer le bureau par SSH (consomme bande passante et encodage), TurboVNC a ensuite écouté
directement sur le Wi-Fi maison (avec certificat auto-signé : comparer l'empreinte SHA-256 à la première connexion), protégé par le pare-feu de [03](03-bureau-distant.md).

Un compositeur `picom` a été essayé pour la fluidité (« garder les effets ») puis retiré : il ne servait à rien.

## 8. Puis Sunshine et Moonlight

Voir [12](12-sunshine-encodeur-materiel.md) : c'est la suite logique et le projet le plus intéressant du dépôt.
