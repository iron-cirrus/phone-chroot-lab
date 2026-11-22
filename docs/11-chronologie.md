# 11 — Chronologie

Reconstituée à partir du journal complet de la conversation (heures locales, 2026). Elle commence le **19 novembre à 20 h 20**, avec un chroot Debian déjà présent mais peu exploitable,
et se termine le 22 novembre. Le fil rouge est le **projet Sunshine / encodeur matériel** ([12](12-sunshine-encodeur-materiel.md)) ; tout le reste l'a rendu possible ou en a découlé.

## 19 novembre — du chroot isolé au bureau distant

| Heure | Étape | Guide |
|---|---|---|
| 20 h 20 | Départ : chroot Debian 12 sous Android 13 (Magisk, chroot-distro), SSH sur le Wi-Fi maison, `/data` à 89 % | [13](13-demarrage-du-projet-acces-reseau.md) |
| 20 h 34 → 20 h 42 | Clé SSH depuis Windows ; cause du refus (`bad ownership or modes for directory /root`) ; premier audit de la configuration | [13](13-demarrage-du-projet-acces-reseau.md) |
| 20 h 45 | Image **Linux Deploy de 128 Go** repérée puis supprimée (accord explicite) ; application désinstallée | [13](13-demarrage-du-projet-acces-reseau.md) |
| 20 h 48 → 21 h 03 | Démarrage automatique du chroot par Magisk `service.d` ; correction du `/run/sshd` en 777 | [01](01-base-root-chroot.md), [13](13-demarrage-du-projet-acces-reseau.md) |
| 21 h 04 → 21 h 08 | Wi-Fi qui change tout seul → reconnexion automatique limitée aux bons réseaux ; durcissement de sshd (clé seule, IPv4 seulement) | [13](13-demarrage-du-projet-acces-reseau.md) |
| 21 h 09 → 21 h 58 | **WireGuard** : choix, installation, quatre pannes (hairpin NAT, routage Android, pare-feu du serveur, keepalive), clé de serveur renouvelée après une fuite dans la conversation ; tests par redémarrages | [13](13-demarrage-du-projet-acces-reseau.md) |
| 21 h 58 → 22 h 09 | code-server (VS Code dans le navigateur) et outils Python | [13](13-demarrage-du-projet-acces-reseau.md) |
| 22 h 23 → 22 h 45 | **MATE + TurboVNC**, rendu logiciel `llvmpipe` ; GPU via **VirGL** (Termux) ; `glmark2` ; redémarrage complet validé | [13](13-demarrage-du-projet-acces-reseau.md) |
| 22 h 53 → 23 h 07 | Performances « médiocres » (« j'avais 500 fps sur glxgears ») → causes mesurées, **900 i/s** ; VNC en direct sur le Wi-Fi maison | [13](13-demarrage-du-projet-acces-reseau.md) |
| 23 h 09 → 23 h 21 | Fenêtres qui clignotent dans TurboVNC ; `iperf` ; essai (puis retrait) de `picom` | [13](13-demarrage-du-projet-acces-reseau.md) |
| **23 h 29** | « Y a pas moyen de passer par un Apollo / Moonlight ou équivalent ? » → **Sunshine + Moonlight** retenu (Apollo n'existe que sous Windows) | [12](12-sunshine-encodeur-materiel.md) |
| 23 h 35 → 23 h 37 | Sunshine officiel (paquet Ubuntu 22.04 arm64, SHA-256 vérifié) installé : encodage x264 | [12 §1](12-sunshine-encodeur-materiel.md) |
| 23 h 39 → 23 h 48 | **Émulation de la mémoire partagée System V** (`sysvshm-emu.c`) : le noyau Android n'a pas SYSVIPC | [12 §6a](12-sunshine-encodeur-materiel.md) |
| 23 h 51 → 23 h 58 | Chromium sous un utilisateur sans privilèges ; appairage de Moonlight (PIN + `pairing_id`) | [12 §9](12-sunshine-encodeur-materiel.md) |

## 20 novembre — l'encodeur matériel, puis la mise en production

| Heure | Étape | Guide |
|---|---|---|
| 00 h 00 → 00 h 06 | Écran blanc dans Moonlight (niveau H.264 6.0 à 63 Mbit/s) ; **pas de souris** → **pont d'entrées** `uinput` → XTest | [12 §6b, §9](12-sunshine-encodeur-materiel.md) |
| 00 h 08 → 00 h 30 | WebGL (démo Aquarium) mesuré sur 8 configurations ; Firefox/Chromium en mode GPU | [13 §7](13-demarrage-du-projet-acces-reseau.md) |
| 00 h 30 | « Sunshine mange vraiment beaucoup de CPU, pas moyen d'utiliser le GPU Android ? » (~205 % de CPU) | [12 §1](12-sunshine-encodeur-materiel.md) |
| 00 h 31 → 00 h 41 | Piste matérielle explorée : `/dev/video33` (`msm_vidc`), FFmpeg échoue ; en attendant, x264 `ultrafast` et résolution à la demande — **« on ne gagne rien »** (Moonlight demande du 1440p) | [12 §1-2](12-sunshine-encodeur-materiel.md) |
| **00 h 41** | « Allez, pour l'encodeur matériel ! Fais un POC de compression pour valider qu'il n'y ait pas de DRM » | [12 §3](12-sunshine-encodeur-materiel.md) |
| 00 h 46 | POC concluant : tampons **DMABUF** dans `/dev/dma_heap/system`, H.264 en clair, PSNR 47 dB, 351 i/s en 720p | [12 §3](12-sunshine-encodeur-materiel.md) |
| 00 h 49 | Chaîne complète (capture XShm → libyuv → encodeur) : **1080p/60 pour 13 % d'un coeur** | [12 §3](12-sunshine-encodeur-materiel.md) |
| 00 h 50 → 01 h 16 | Lecture du code de Sunshine, choix du chemin natif (comme NVENC) ; **Debian 13 imbriqué** (`debootstrap`) car le Debian 12 est trop vieux ; compilation (241 étapes) ; écriture de `v4l2_encode.cpp/.h` et du script d'intégration ; **limite d'usage atteinte** | [12 §4-5](12-sunshine-encodeur-materiel.md) |
| 07 h 03 → 07 h 07 | Reprise : recompilation (40 étapes) réussie ; Sunshine valide `h264_v4l2` ; **12 à 16 % d'un coeur** contre ~205 % | [12 §8](12-sunshine-encodeur-materiel.md) |
| 07 h 08 → 07 h 16 | Menu « Desktop 2K » ; image « très floue » → Moonlight avait demandé du 720p et le redimensionnement était brut : **filtre `box`** ; « tout est net » | [12 §4](12-sunshine-encodeur-materiel.md) |
| 07 h 21 → 07 h 46 | Bascule du chroot principal en **Debian 13** (sauvegarde de 9,3 Go, ~800 paquets, pipx à réinstaller, erreur `exim4`) ; redémarrage | [12 §5](12-sunshine-encodeur-materiel.md) |
| 07 h 53 → 09 h 30 | Chromium/WebGL sur le GPU, polices, clavier AZERTY (et correspondance des touches dans le pont d'entrées), son (PulseAudio), YouTube plein écran (images perdues, erreur 153, VP9/H.264 forcé), entrée « Desktop 1080p » dans Moonlight ; clé WireGuard du serveur renouvelée | [08](08-bureau-mate.md), [13](13-demarrage-du-projet-acces-reseau.md) |
| 09 h 31 → 10 h 23 | MATE complet, dock et thèmes ; **enquête sur des saccades** (souris, panneau, image clé toutes les 5 s) | [12 §7](12-sunshine-encodeur-materiel.md), [08](08-bureau-mate.md) |
| 10 h 25 → 10 h 59 | Existe-t-il un NPU ? Ollama + Open WebUI + Qwen sur CPU : trop lent ; **llama.cpp sur le NPU Hexagon** | [05](05-ia-locale.md) |
| 11 h 02 → 12 h | **Incident** : sauvegardes laissées dans `service.d` exécutées en parallèle → Android figé après le PIN ; mode sans échec ; réparation | [01](01-base-root-chroot.md) |
| après-midi (heures non relevées) | Démarrage de l'IA retardé et vérifié par un redémarrage ; applications gloutonnes ; **démon de puissance** ; premier **tableau de bord** | [04](04-profil-puissance.md), [05](05-ia-locale.md), [06](06-tableau-de-bord.md) |
| soir (heures non relevées) | **Application Pilote** (WebView + boutons root), application Open WebUI ; **Olala 7B** sur CPU, essais NPU et Q4_0 abandonnés | [05](05-ia-locale.md), [07](07-application-pilote.md) |

## 21 novembre — interface, télémétrie, polissage de l'encodeur

- Interface de Pilote : interrupteurs, curseur de gouverneur, icônes, carte IA, disposition, confirmations ([07](07-application-pilote.md)).
- Télémétrie du tableau de bord : puissance CPU estimée, puissance totale mesurée, énergie batterie, écran, réseau et disque avec la part du chroot, aperçu du bureau, mémoire PSS, parts vidéo/IA ;
  corrections du `steal`, de l'unité du courant batterie (mA, deux cellules), du cache de `/api/stats` ([06](06-tableau-de-bord.md)).
- **15 h 24 : correctif de l'encodeur — GOP « infinie »** : keyframes seulement à la demande ([12 §7](12-sunshine-encodeur-materiel.md)).
- Deux colonnes en paysage ; bloc IA dans la page.
- Essai d'un bureau « macOS » (WhiteSur, dock, menu global) → jugé laid → retour aux réglages d'usine ([08](08-bureau-mate.md)).

## 22 novembre — bureau minimal, résumé, documentation

- Panneau MATE retiré ; deux docks **Plank** (icônes seules, transparents avec `xcompmgr`), menu rofi, icône Debian, VS Code ; surveillance de la résolution ([08](08-bureau-mate.md)).
- Résumé du chroot sur le fond d'écran : Conky abandonné, fenêtre Chromium sans cadre.
- Documentation et dépôt Git (ce dépôt).

## Questions posées en cours de route
- *Comment pilote-t-on le téléphone en SSH ? Peut-on faire pareil sans root ?* → [02](02-acces-ssh-et-controle.md).
- *Existe-t-il un bureau plus moderne que MATE, proche de macOS ?* → KDE Plasma 6 (lourd sous VNC), GNOME (à éviter sous VNC), XFCE habillé ; essai WhiteSur sur MATE ([08](08-bureau-mate.md)).

## Sensibilités de l'utilisateur à retenir
- **Dyslexie** : droite/gauche peuvent être permutés ; décrire les choix de mise en page avec des mots (« coin haut droit de la carte »).
- **Daltonisme** : éviter orange contre vert ; chroot en bleu, autres éléments en gris/jaune/rose.
- Ne pas perturber le bureau en cours d'utilisation (pas de fenêtre de test, pas de relance de Sunshine/VNC/chroot sans nécessité).
