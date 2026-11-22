# 11 — Chronologie

Ce qui a été fait, dans l'ordre. Les dates sont celles des notes de la session (novembre 2026) ; les premières étapes (chroot, SSH, bureau) précèdent les notes
datées et ne sont donc pas datées.

## Fondations (avant le 20 novembre)
1. Chroot Debian 13 avec le module Magisk chroot-distro ; SSH root par clé ; WireGuard pour l'accès à distance (avec un keepalive).
2. Bureau MATE dans TurboVNC (`:1`), vu par Moonlight via **Sunshine** avec l'encodeur matériel V4L2 ; pare-feu `vnc-fw.sh` ; Wi-Fi faible latence ; GPU via VirGL (Termux).
3. Navigateurs sous un utilisateur sans privilèges (`web`), WebGL via VirGL ; code-server.

## 20 novembre
4. **Démon de puissance** (`50-power-profile.sh`) : performance sur secteur, `uag` sur batterie, garde-fou thermique ; plafond constructeur constaté sur policy3/policy7.
5. **Premier tableau de bord** Flask (CPU, GPU, NPU, températures), port 8090 ; suppression de la boucle « performance » qui combattait le mode éco.
6. **IA locale** : Ollama (CPU), Open WebUI, llama.cpp sur le NPU Hexagon (Qwen2.5-3B Q4_0 : 652 tok/s en prompt, ~17 en génération).
7. **Incident** : des sauvegardes laissées dans `service.d` ont toutes été exécutées au boot → Android figé. Règle « aucune sauvegarde dans `service.d` » ;
   réparation après mode sans échec (`/data/adb/modules/*/disable`). Démarrage de l'IA retardé de 3 minutes, avec interrupteur d'arrêt (`/data/adb/no-llm`).
8. Démarrage automatique de l'IA vérifié par un redémarrage ; applications gloutonnes (`appops deny`).
9. **Application Pilote** (WebView + boutons root + `phone-ctl.sh`), application Open WebUI ; compilation sur le téléphone.
10. Personnalisation initiale du bureau (dock, boutons Win11, correctif du moniteur système via `LD_PRELOAD`) ; plus tard remise à zéro.
11. **Olala 7B** installé sur CPU avec un moteur NDK ; essais NPU et Q4_0 abandonnés ; Open WebUI corrigé (outils intégrés, tâches automatiques).

## 21 novembre
12. Interface de Pilote : interrupteurs, curseur de gouverneur unique, icônes, carte IA, mise en page, confirmations natives.
13. Télémétrie du tableau de bord : puissance CPU estimée, puissance totale mesurée, énergie batterie, écran, réseau et disque avec la part du chroot, aperçu du bureau,
    mémoire PSS, parts vidéo/IA. Correction du `steal` (gros coeur), de l'unité du courant batterie (mA, ×2 cellules), du cache de `/api/stats`.
14. Correctif Sunshine (keyframes à la demande), recompilation partielle.
15. Disposition en deux colonnes en paysage ; bloc IA dans la page ; résumé de l'en-tête.
16. Essais de bureau « macOS » (WhiteSur, dock, menu global) → jugés laids → retour aux réglages d'usine.

## 22 novembre
17. Panneau MATE retiré ; deux docks **Plank** (icônes seules, transparents avec `xcompmgr`), menu rofi, icône Debian, VS Code ; horloge et batterie dans le second dock.
18. Surveillance de la résolution pour recoller les docks en bas (`plank-watch.sh`) ; test 2560×1440 ↔ 1920×1080.
19. Résumé du chroot sur le fond d'écran : Conky essayé et abandonné, remplacé par une fenêtre Chromium sans cadre (`resume-window.sh`, mode `?bare`).
20. Documentation : ce dépôt.

## Questions posées en cours de route
- *Comment pilote-t-on le téléphone en SSH ?* → [02](02-acces-ssh-et-controle.md).
- *Peut-on faire pareil sans root ?* → [02, section finale](02-acces-ssh-et-controle.md#sans-root--quest-ce-quon-garde-).
- *Existe-t-il un bureau plus moderne que MATE pour une expérience proche de macOS ?* → KDE Plasma 6 (lourd sous VNC), GNOME (à éviter sous VNC), XFCE habillé ;
  essai WhiteSur sur MATE, finalement abandonné ([08](08-bureau-mate.md)).

## Sensibilités de l'utilisateur à retenir
- **Dyslexie** : droite/gauche peuvent être permutés ; décrire les choix de mise en page avec des mots (« coin haut droit de la carte »).
- **Daltonisme** : éviter orange contre vert ; chroot en bleu, autres éléments en gris/jaune/rose.
- Ne pas perturber le bureau en cours d'utilisation (pas de fenêtre de test, pas de relance de Sunshine/VNC/chroot sans nécessité).
