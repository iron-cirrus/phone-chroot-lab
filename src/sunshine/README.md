# src/sunshine — encodeur H.264 matériel pour Sunshine (Qualcomm msm_vidc)

Lire d'abord : [../../docs/12-sunshine-encodeur-materiel.md](../../docs/12-sunshine-encodeur-materiel.md).

```
poc/
  captest.c               la capture X11 (classique et XShm) renvoie-t-elle bien le bureau ?
  vidc-enc-poc.c          encodage H.264 d'une mire sur /dev/video33 avec des tampons DMABUF (PSNR, vitesse)
  vidc-screen-poc.c       chaîne complète : capture XShm -> libyuv -> encodeur matériel, au rythme imposé
integration/
  v4l2_encode.h / .cpp    NOUVEAUX fichiers de Sunshine : src/platform/linux/ (l'encodeur)
  vs-upstream.patch   git diff de cmake/compile_definitions/linux.cmake, src/video.cpp, src/video.h
                                           contre Sunshine v2026.914.233613 (commit 63d35f7)
  patch_sunshine.py       applique ces modifications par des ancres (rejouable ; sauvegarde les originaux dans .orig-v4l2)
  v4l2-gop.patch          correctif ultérieur : GOP « infinie », keyframes seulement à la demande
support/
  sysvshm-emu.c           émulation de la mémoire partagée System V (LD_PRELOAD) : le noyau Android n'a pas SYSVIPC
  sunshine-input-bridge.py   uinput de Sunshine -> XTest de Xvnc (Android adoptait ces périphériques)
  sunshine-resize.sh      résolution du bureau suivant le client (global_prep_cmd de Sunshine)
  sunshine-v4l2.conf      configuration utilisée (le commentaire « pas d'encodeur matériel » est périmé)
  apps.json               liste d'applications de Moonlight (Desktop, Desktop 2K, 1080p, basse résolution, Steam Big Picture)
```

## Appliquer l'intégration à une copie de Sunshine

```sh
git clone --recursive https://github.com/LizardByte/Sunshine.git   # le dépôt a été renommé (SunshineStream/Sunshine) : la redirection fonctionne
cd Sunshine && git checkout v2026.914.233613
cp /chemin/vers/src/sunshine/integration/v4l2_encode.{h,cpp} src/platform/linux/
git apply /chemin/vers/src/sunshine/integration/vs-upstream.patch
# le correctif GOP est déjà inclus dans v4l2_encode.cpp de ce dépôt (v4l2-gop.patch est fourni à titre d'historique)
# dépendances : libyuv-dev ; configurer avec les captures désactivées sauf X11 (voir docs/12 §5)
```
Les fichiers `.cpp/.h` fournis **contiennent déjà** le correctif de GOP : `v4l2-gop.patch` montre seulement la différence apportée le 21 novembre.

Licence : Sunshine est sous GPL-3.0 ; ces fichiers en sont des travaux dérivés. Voir [../../docs/10-securite-et-publication.md](../../docs/10-securite-et-publication.md).

Vérification faite : `git apply -R --check` du patch contre l'arbre de compilation du téléphone réussit (le patch correspond exactement aux modifications en place) et
`v4l2_encode.cpp` de ce dépôt est identique (même somme md5) à celui qui a servi à compiler le binaire courant. L'application **directe** sur un clone neuf n'a pas été rejouée.
