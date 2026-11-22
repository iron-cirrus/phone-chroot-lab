# 06 — Tableau de bord

Application Flask : `src/dashboard/app.py` → `/opt/dashboard/app.py` dans le chroot. Port **8090** sur 0.0.0.0 (pas 8080, pris par code-server).
Démarrage : `dashboard-start.sh` (`nice -n 10`, journal `/var/log/dashboard.log`) ; au boot via `10-chroot-ssh.sh`.

| URL | Contenu |
|---|---|
| `/` | page détaillée |
| `/resume` | page de synthèse (celle de l'application Pilote) ; `?bare` = sans bandeau de navigation, pour une fenêtre de bureau |
| `/api/stats` | JSON de toutes les mesures |
| `/preview.jpg` | aperçu du bureau, **uniquement pour les clients en boucle locale** (403 pour les autres : un aperçu du bureau ne doit pas être lisible sur le réseau) |

Découpage : un fil d'échantillonnage, un fil de télémétrie (toutes les 2 s), `_pv_loop` (aperçu), `_screen_loop` (écran, toutes les 15 min).
`/api/stats` est servi depuis un **cache de 1,5 s calculé par un seul fil à la fois** : sans lui, sous charge, les requêtes s'empilaient dans les fils Flask
(30 s de latence, plus de 2 coeurs consommés) ; avec lui ~25 % d'un coeur et 8 ms.

## Comment chaque mesure est calculée

### CPU
- Compteurs `/proc/stat`. **Piège majeur** : sur ce noyau, le temps pendant lequel `core_ctl` garde un coeur « parqué » est compté en **`steal`**.
  Le gros coeur (cpu7) semblait occupé à 50-77 % alors qu'il l'était à ~18 % (vérifié par une trace `sched_switch`). `cpu_stat()` **exclut `steal`**
  du travail et du total (usage mesuré sur le temps où le coeur était disponible). `htop` fait la même erreur.
- Parts par coeur et par type : chroot (jiffies par thread des processus du chroot), vidéo (Sunshine), IA (processus `llama-server`, comptés à part), reste du système.
- Fréquences et gouverneurs lus dans sysfs ; températures dans les zones thermiques ; badges WARNING/THROTTLE (seuils CPU 75/85, GPU 70/85, NPU 65/80 °C)
  qui **remplacent** le texte de fréquence (pour qu'une carte ne change jamais de hauteur).

### Puissance
- **CPU estimée** : modèle d'énergie du noyau par point de fonctionnement, `/sys/kernel/debug/energy_model` (valeurs recopiées dans `app.py`),
  part du chroot par ses jiffies, moyenne sur 60 s.
- **Totale mesurée** : branché → tension × courant USB (moins tension batterie × |courant| si le statut est « Charging » : approximatif) ;
  sur batterie → tension × courant batterie.
  - `/sys/class/power_supply/battery/current_now` est en **mA** (et non µA) et correspond à **UNE** des **deux cellules en parallèle** : le courant réel est ×2
    (`BATT_CELLS = 2`). Vérifié contre `charge_counter` (µAh, pas de 2 mAh toutes les ~10 s) : V × 2 × I colle à ~10 %.
  - Hors charge, le statut est « Not charging » et `usb/online` vaut 0.
- **Écran** : estimation Android (`dumpsys batterystats`, « screen: X mAh over Y h » × 3,85 V, relu toutes les 15 min), ~0,63 W ; 0 si le rétroéclairage est à 0.
  Ne tient pas compte de la luminosité.
- **Batterie** : ligne « Batterie : x W (moy. 10 min) · y Wh depuis N min » seulement si la jauge montre ≥ 0,05 W tirés de la batterie
  (énergie = somme des baisses du compteur ≥ 2 mAh × tension ; la charge n'est jamais comptée). `battery_rm`/`battery_fcc` (mAh) dans `/sys/class/oplus_chg/battery`.
- Pas de mesure par rail (aucun `android.hardware.power.stats` ; le PMIC `iio:device0` n'a que les courants des chargeurs).

### Mémoire
Chroot en **PSS** (`smaps_rollup`, relu toutes les 10 s : le coût est de ~160 ms sur Olala), VRAM, « RAM IA » = `Pss_Anon` de `llama-server` + dma-buf du NPU, swap.

### Réseau et disque
- Les taux par interface viennent d'Android (le chroot partage sa pile réseau). La **part du chroot** (bleu) vient des compteurs eBPF par UID de netd :
  appel système `bpf` (n° 280 sur arm64) sur `/proc/1/root/sys/fs/bpf/netd_shared/map_netd_app_uid_stats_map` (clé u32 uid, valeur 4×u64 : paquets/octets rx, tx),
  pour les uid des processus du chroot, plafonnée aux totaux Wi-Fi+mobile. **Inutilisables** : `map_netd_stats_map_A/B` (netd les remet à zéro de façon irrégulière) et la
  comptabilité `conntrack` (désactivée).
- Disque : total = secteurs de `/proc/diskstats` pour `sd[a-z]` (sda = UFS) ; part du chroot = `read_bytes/write_bytes` de ses processus (le total est `max(périphérique, chroot)`).
- Échelle dynamique commune entrée/sortie, lecture/écriture ; la couleur du chroot est le **bleu** (l'utilisateur est daltonien : éviter orange/vert pour distinguer).

### Encodeur
Voir [03](03-bureau-distant.md) (`msm_vidc` en debugfs). Un aperçu du bureau (3 images/s, 480 px) vient d'**un seul** processus `ffmpeg x11grab` appartenant au tableau de bord :
démarré à la première requête en boucle locale, tué après 8 s sans requête (~7 % d'un coeur pour ffmpeg + 7 % pour le tableau de bord). Un flux H.264 miniature
coûtait plus cher (x264 ~0,5 coeur ; `h264_v4l2m2m` échoue sur `msm_vidc`).

## Services et IA
`svc_info()` (cache 4 s) parcourt `/proc/*/comm` : `Xvnc`, `sunshine-v4l2`, `virgl_test_ser*`, et `llama-server` dont la **ligne de commande** distingue Qwen (`Qwen2.5-3B`) et Olala (`Olala-v1.0`).

## Page `/resume`
- Une page HTML unique avec des icônes SVG intégrées (sprite en tête de page), un bloc par thème, mise à jour **en place** (`patch()`, pas de recréation du DOM) pour que les transitions s'animent.
  Transitions courtes (0,45 s) : les longues (1,8 s) animaient presque en permanence et coûtaient 93 % d'un coeur à l'application ; ramené à 36 %.
- En paysage (largeur ≥ 640 px, dans l'application seulement), disposition en grille de deux colonnes ; sur l'application, la classe `inapp` est ajoutée quand le pont Android existe.
- Dans un navigateur ordinaire la page est en **affichage seul** ; les commandes n'existent que dans Pilote ([07](07-application-pilote.md)).
- Pièges d'édition : (1) `sed` GNU transforme `\u` dans le remplacement en commutateur de majuscules : passer les échappements Unicode par un éditeur, pas par `sed` ;
  (2) en supprimant du HTML par plages de lignes, vérifier le `</div>` voisin (un oubli a imbriqué Encodeur/Réseau/Disque dans la grille GPU|NPU) ;
  (3) un emoji hors BMP dans une chaîne Python de la page provoquait une erreur 500 (substitut) : utiliser l'entité HTML.
