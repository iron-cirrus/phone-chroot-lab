# 02 — Accès SSH et contrôle du téléphone

Ce guide explique comment un assistant (ou vous) pilote le téléphone depuis un PC. Il n'y a **aucune magie** : une connexion SSH par clé
vers le chroot, plus le root Magisk pour sortir du chroot vers Android.

## Le chemin

```
PC (terminal, Claude Code)  ──ssh, clé──►  sshd du chroot (root)  ──nsenter -t 1 -m──►  Android (root Magisk)
```

1. **SSH par clé** vers `root@<IP_TEL>`. L'adresse est celle du téléphone sur le tunnel WireGuard (ou sur le Wi-Fi maison, qui
   peut être injoignable par moments : le tunnel est la voie fiable). Aucune connexion par mot de passe.
2. **Dans le chroot** on est `root` Debian : on installe des paquets, on édite le bureau, on relance le tableau de bord.
3. **Vers Android** : le chroot voit `/proc` de l'hôte, donc
   ```sh
   nsenter -t 1 -m -- <commande>
   ```
   exécute la commande dans l'espace de montage du processus 1 d'Android, avec les droits root. C'est ce qui permet `pm`, `am`,
   `screencap`, `settings`, `dumpsys`, `cmd`, `iptables`, et de lancer les serveurs IA (qui doivent tourner côté bionic).
4. **Fichiers** : `scp` dans les deux sens. Le chroot est visible côté Android sous `/data/local/chroot-distro/debian/…`
   (donc un APK construit dans `/root/apk-build` s'installe avec le chemin `/data/local/chroot-distro/debian/root/apk-build/…`).
5. **Bureau graphique** : `DISPLAY=:1` (session TurboVNC). Pour que `gsettings`/`dconf` marchent depuis ssh, il faut l'adresse du bus
   de messages de la session :
   ```sh
   P=$(pgrep -u root -x mate-session | head -1)
   export DBUS_SESSION_BUS_ADDRESS=$(tr '\0' '\n' < /proc/$P/environ | sed -n 's/^DBUS_SESSION_BUS_ADDRESS=//p')
   ```

## Aide-mémoire

```sh
# commande côté Android
ssh root@<IP_TEL> 'nsenter -t 1 -m -- dumpsys battery'

# installer / lancer l'APK construit dans le chroot
ssh root@<IP_TEL> 'nsenter -t 1 -m -- pm install -r /data/local/chroot-distro/debian/root/apk-build/pilote.apk'
ssh root@<IP_TEL> 'nsenter -t 1 -m -- am start -n fr.pilote/.MainActivity'

# vraie capture de l'écran du téléphone (écran allumé : dumpsys window | grep mWakefulness)
ssh root@<IP_TEL> 'nsenter -t 1 -m -- screencap -p /data/local/chroot-distro/debian/tmp/s.png; convert /tmp/s.png -resize 50% /tmp/s2.png'
scp root@<IP_TEL>:/tmp/s2.png .

# capture du bureau MATE
ssh root@<IP_TEL> 'DISPLAY=:1 xwd -root -silent | convert xwd:- -resize 50% /tmp/d.png'

# lancer un script long sans qu'il meure avec la session ssh
ssh root@<IP_TEL> 'setsid nohup /usr/local/bin/mon-script.sh >/tmp/mon.log 2>&1 &'
```

## Pièges propres à cette façon de travailler

- **`pkill -f <mot>` dans une chaîne ssh qui contient ce mot** tue la commande elle-même. Mettre les commandes dans un fichier de script
  (`scp` puis `ssh … bash script.sh`), ou utiliser `pkill -x <nom>` (le nom est tronqué à 15 caractères).
- Même problème pour `dashboard-start.sh` : il fait `pgrep -f /opt/dashboard/app.py`, qui trouve la commande ssh qui contient ce chemin et sort
  sans rien lancer. Le démarrer dans un appel ssh séparé.
- Les scripts d'installation qui dessinent une animation (WhiteSur) ont besoin de `TERM` : sans lui ils s'arrêtent **sans message**.
  Utiliser `ssh -tt … 'export TERM=xterm; …'`.
- Windows : pas de `python` dans le PATH par défaut. Lancer les petits scripts de modification de fichiers avec `wsl -e python3` depuis PowerShell
  (Git Bash déforme les chemins `/mnt/c/…`). Préférer écrire les fichiers avec LF (pas de CRLF) avant de les envoyer au téléphone.
- **Mesurer sans se mesurer soi-même** : un script qui parcourt `/proc` est ordonnancé… sur le gros coeur (sous gouverneur `performance`).
  Lancer les échantillonneurs avec `taskset -c 0`.
- **`xwd -root`** est trompeur quand un compositeur (`xcompmgr`) tourne : bande noire en bas. Photographier une fenêtre avec `xwd -id`, ou
  regarder l'aperçu du flux (`/preview.jpg` du tableau de bord, [06](06-tableau-de-bord.md)).

## Garde-fous

- Ne pas écrire de secret dans les commandes ou les journaux (clés, mots de passe, jetons).
- Si l'assistant utilise un outil qui contrôle les permissions, certaines actions (télécharger et exécuter un script d'un dépôt tiers) seront
  refusées jusqu'à accord explicite : c'est voulu.
- Pour **couper l'accès** : retirer la clé publique du `authorized_keys` de root dans le chroot, ou arrêter sshd (`service ssh stop`).

## Sans root : qu'est-ce qu'on garde ?

Le root fait deux choses ici : donner le shell root Android (`nsenter`) et permettre un vrai chroot avec accès aux compteurs matériels.
Sans root :

| Solution | Ce qu'on obtient | Ce qui manque |
|---|---|---|
| **Débogage ADB sans fil** (Android 11+, Options pour développeurs → Débogage sans fil, appairage par code) | Shell à distance depuis le PC : `pm install`, `am start`, `screencap`, `input tap/swipe`, `settings put` (rotation, luminosité…), `dumpsys` | Pas de root : pas de chroot, pas de réglage CPU, pas de `nsenter`. Appairage à refaire de temps en temps |
| **Termux + serveur SSH** (port 8022) | Terminal Linux dans une application, accessible en SSH ; paquets, scripts, serveur web | Tourne comme une simple application (Android peut la suspendre), pas de bureau accéléré |
| **proot-distro** (dans Termux) | Une Debian sans root | Beaucoup plus lent, ni GPU, ni NPU, ni encodeur matériel : inadapté à Sunshine en 2560×1440 |

Le débogage sans fil donne un accès très large à qui a l'appairage : le laisser éteint quand on ne s'en sert pas.
