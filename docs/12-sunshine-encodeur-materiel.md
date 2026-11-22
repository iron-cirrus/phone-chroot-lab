# 12 — Sunshine avec l'encodeur matériel du téléphone (le projet phare)

Objectif : diffuser le bureau Linux du chroot vers **Moonlight** en consommant le moins de processeur possible. Avec l'encodage logiciel (x264),
Sunshine prenait **~205 % de CPU** (deux coeurs) à 1440p/60 ; avec l'encodeur H.264 matériel du Snapdragon 8 Gen 2, **12 à 16 % d'un coeur** à 720p/60
(mesure pendant un vrai stream), soit un facteur **13 à 17**. Ce guide raconte comment on y est arrivé, le code, et comment le reproduire.

Tout le code est dans [`src/sunshine/`](../src/sunshine/) : `poc/` (preuves de concept), `integration/` (l'encodeur et son branchement dans Sunshine),
`support/` (ce qui entoure Sunshine : mémoire partagée, entrées, résolution).

## 1. Le problème de départ

Sunshine ne connaît que NVENC (NVIDIA), AMF (AMD), Quick Sync / VA-API (Intel), Vulkan et l'encodage logiciel. Le téléphone n'en a aucun.
Mesures de départ, Sunshine officiel (paquet Ubuntu 22.04 arm64, installé d'abord pour valider la chaîne) avec x264 :

| Étape (1080p) | Coût |
|---|---|
| Capture XShm | 0,17 % par image (négligeable grâce à l'émulation de mémoire partagée, voir §6) |
| **Conversion de couleurs RVB → YUV** | **2,8 %** par image : le plus gros poste |
| Encodage x264 `ultrafast` | ~1,4 % par image |

Conclusion qui a orienté tout le reste : la charge dépend du **nombre de pixels** (c'est la résolution demandée par Moonlight qui décide), et pas seulement de
l'encodage. Régler `sw_preset = ultrafast` et redimensionner le bureau à la demande du client (`sunshine-resize.sh`) n'a presque rien changé quand Moonlight
demandait du 2560×1440 : le gain a été mangé par la résolution. D'où l'idée de sortir du logiciel.

## 2. Quel encodeur matériel, et pourquoi FFmpeg échoue

Sur Android, l'encodage matériel est fait par un bloc dédié de Qualcomm (**iris / `msm_vidc`**), **pas par le GPU Adreno**. Il est visible depuis le chroot comme
`/dev/video33` (V4L2 mem2mem multi-plan) : il accepte du **NV12** et sort du **H.264** ou du **HEVC**. Il n'y a **pas de DRM** : le flux est du H.264 standard en clair.

`ffmpeg -c:v h264_v4l2m2m` échoue (`Cannot allocate memory`, puis `EINVAL`) : FFmpeg, comme tous les outils V4L2 génériques, demande au pilote d'allouer lui-même ses tampons
(`V4L2_MEMORY_MMAP`). Le pilote Qualcomm de ce noyau **refuse** : il veut des tampons **DMABUF** fournis par l'appelant, alloués dans **`/dev/dma_heap/system`**,
en entrée comme en sortie. Il suffit donc de ne pas passer par FFmpeg.

## 3. Preuves de concept (`src/sunshine/poc/`)

1. **`vidc-enc-poc.c`** — encode une mire animée avec des tampons DMABUF et écrit la mire brute pour comparer.
   Résultats : flux H.264 profil High, 300/300 images décodées sans erreur, **PSNR moyen 47 dB** (minimum 44,8 : invisible à l'oeil).
   Vitesse de l'encodeur seul : **351 images/s en 720p, 172 en 1080p**, environ 0,6 ms de CPU par image en 1080p, contre ~1,4 % de CPU par image pour x264 `ultrafast` : gain de 20 à 40 sur l'encodage.
   (Un premier PSNR bas venait de la mesure — FFmpeg comparait des images décalées dans le temps —, pas de l'encodeur.) Les erreurs de licence du micrologiciel dans `dmesg` ne bloquent pas l'encodage.
2. **`captest.c`** — vérifie que la capture X11 (classique et XShm) renvoie bien le bureau (97-98 % de pixels colorés).
3. **`vidc-screen-poc.c`** — la chaîne complète : capture XShm → conversion `libyuv` BGRX→NV12 **écrite directement dans le tampon DMABUF d'entrée** → encodage matériel,
   au rythme imposé. Mesures (écran en mouvement, glxgears) :

| Cas | Obtenu | CPU du programme |
|---|---|---|
| 1080p, 60 i/s | 60,0 (0 retard) | **13 % d'un coeur** |
| 1080p, 30 i/s | 30,0 | 6 % |
| 720p, 60 i/s | 60,0 | 6 % |

À 1080p/60 : capture 1,14 ms, conversion 1,86 ms, latence capture → image encodée 10,7 ms.
Ces chiffres ne comptent ni `Xvnc` (~12 % d'un coeur), ni le travail réseau de Sunshine (RTSP, chiffrement, correction d'erreurs).

## 4. L'intégration dans Sunshine (`src/sunshine/integration/`)

Sunshine a un chemin d'encodage **natif**, sans FFmpeg, utilisé par NVENC : une session d'encodage n'a que quatre méthodes à fournir
(`convert`, `request_idr_frame`, `request_normal_frame`, `invalidate_ref_frames`) plus une fonction qui renvoie le paquet encodé, et le programme choisit le type de session
par `dynamic_cast`. L'encodeur V4L2 s'y insère proprement :

| Fichier | Contenu |
|---|---|
| `v4l2_encode.h/.cpp` (nouveaux, ~600 lignes) | `platf::v4l2::encoder_t` : ouvre `/dev/video33` et `/dev/dma_heap/system`, configure le H.264 (CBR, profil High, SPS/PPS avant chaque IDR, pas de B-frames), alloue 4 tampons DMABUF d'entrée et 4 de sortie, convertit avec `libyuv`, soumet, relit le flux. `v4l2_encode_device_t` est le `encode_device_t` de Sunshine |
| `video.cpp`, `video.h`, `linux.cmake` (modifiés) | classe `v4l2_encode_session_t`, `encode_v4l2()`, l'encodeur `v4l2` (nom FFmpeg fictif `h264_v4l2`, drapeau `H264_ONLY`) ajouté en dernier à la liste, fabrique de session, option CMake `SUNSHINE_ENABLE_V4L2` |
| `vs-upstream.patch` | le `git diff` de ces trois fichiers contre `v2026.914.233613` |
| `patch_sunshine.py` | applique ces modifications par des **ancres** (chacune doit exister exactement une fois ; le script repart toujours de l'original, donc il est rejouable) |
| `v4l2-gop.patch` | correctif ultérieur : GOP « infinie » (voir §7) |

Points de conception à retenir :
- **Couleurs** : conversion `libyuv` en BT.601, plage pleine si le client la demande (`ARGBToJ420` puis `MergeUVPlane`), plage limitée sinon (`ARGBToNV12`) ; le pilote reçoit les indications de colorimétrie (SPS/VUI).
  Si Moonlight demande du Rec. 709, on encode quand même en 601 (avertissement dans le journal).
- **Redimensionnement** : si l'image capturée n'a pas la taille demandée, `libyuv::ARGBScale` avec le filtre **`kFilterBox`**. Le premier essai utilisait le filtre le plus brut (voisin le plus proche) : le 1440p semblait « très flou » alors que Moonlight avait en réalité demandé du 720p, puis agrandi. SSIM mesuré après correction : 0,993 (image clé), 0,976 (image suivante).
- **Alignement** : le pilote impose une hauteur alignée sur 32 pour le plan de chrominance ; on la déduit de `sizeimage`.
- **Images clés** : `V4L2_CID_MPEG_VIDEO_FORCE_KEY_FRAME` à la demande ; `invalidate_ref_frames` demande un IDR (pas d'invalidation de références).
- **Limites assumées** : H.264 8 bits 4:2:0 uniquement (pas de HEVC, AV1, 10 bits, 4:4:4), largeur et hauteur paires et ≥ 64.
  Au démarrage, Sunshine journalise `Found H.264 encoder: h264_v4l2` et `V4L2 hardware encoder ready: 1920x1080 @ 60 fps, driver msm_vidc_encoder`.
  Comme aucun autre encodeur ne valide sur ce téléphone, `v4l2` est retenu automatiquement ; on peut le forcer avec `encoder = v4l2` dans la configuration.

## 5. Compiler Sunshine pour ce téléphone

Le chroot de départ était un Debian 12 : trop ancien (C++23 avec `std::format` utilisé 43 fois dans 11 fichiers → GCC ≥ 13 ; Boost 1.89 exigé contre 1.74 ; CMake ≥ 3.24 ok).
Plutôt que de risquer le bureau en production, la compilation a été faite dans un **second système Debian 13 imbriqué** (`/opt/trixie`, créé avec `debootstrap`, GCC 14, CMake 3.31),
qui a ensuite servi de preuve que la bascule du chroot principal en Debian 13 était possible (elle a été faite plus tard : sauvegarde de 9,3 Go, ~800 paquets, vérification de
chaque outil installé à la main).

Étapes :
1. Cloner **la même version** que celle installée (`v2026.914.233613`, ~2,2 Go avec les sous-modules) : base identique à celle qui marchait.
2. Configurer. Les options de capture sont toutes désactivées sauf X11 (CUDA, DRM, KWin, Portal, Tray, VA-API, Vulkan, Wayland) ; paquets de développement à ajouter :
   `libpipewire-0.3-dev` (exigé même pour la capture X11), `libglib2.0-dev`, `libtiff-dev`, `libyuv-dev`. Boost 1.89 est téléchargé et compilé automatiquement
   (« Falling back to FetchContent ») ; FFmpeg est un binaire précompilé arm64 du projet (`build-deps`). Les erreurs `git` de détection de version sont bénignes avec un clone superficiel.
3. Compiler `sunshine` seul, à faible priorité et 5 tâches en parallèle : 241 étapes, 0 erreur pour la version non modifiée ; puis recompilation (40 étapes) avec l'encodeur V4L2.
4. Faire tourner dans le chroot cible : le binaire installé est `/usr/local/bin/sunshine-v4l2` (il utilise la bibliothèque système `libopus.so`).
   Configuration : `src/sunshine/support/sunshine-v4l2.conf`.

Pour refaire **une recompilation partielle** dans l'environnement dégradé d'aujourd'hui, voir la recette de [03](03-bureau-distant.md#reconstruire-sunshine-recette).

## 6. Ce qui entoure Sunshine (`src/sunshine/support/`)

Sunshine ne marche pas tout seul dans un chroot sur Android : trois obstacles, trois petites pièces.

### a. Mémoire partagée System V : `sysvshm-emu.c`
Le noyau Android n'a pas `CONFIG_SYSVIPC` : `shmget` renvoie `ENOSYS`. `Xvnc` annonce quand même MIT-SHM mais toute tentative échoue, et Sunshine affichait `shmget failed` en
retombant sur une capture lente. La solution : une **bibliothèque d'environ 260 lignes de C** chargée par `LD_PRELOAD` qui remplace `shmget/shmat/shmdt/shmctl` par des
fichiers de `/dev/shm/sysvshm-emu/` projetés en `MAP_SHARED` (un segment = un fichier ; `IPC_RMID` renomme en `.del` et efface au dernier détachement, avec des verrous OFD).
Tous les processus qui la chargent (serveur X + clients) partagent la même mémoire. Elle est chargée par `Xvnc`, la session MATE, Sunshine, les navigateurs.
Compilation : `gcc -O2 -Wall -Wextra -fPIC -shared -o /usr/local/lib/libsysvshm-emu.so sysvshm-emu.c -lpthread` ; vider `/dev/shm/sysvshm-emu` à chaque nouvelle session X.

### b. Souris et clavier : `sunshine-input-bridge.py`
Sunshine crée des périphériques `uinput` (« libvirtualhid ») : `Xvnc` ne les lit pas et **Android les adoptait** (les clics partaient vers l'écran du téléphone).
Le pont les prend **en exclusivité** (`EVIOCGRAB`) et rejoue chaque événement dans l'écran X `:1` par l'extension **XTest**, en suivant leur apparition toutes les 2 s.
Sunshine convertit les touches Windows en positions de clavier **américain** ; pour un client AZERTY, le pont remet chaque touche à sa position physique AZERTY
(`/etc/sunshine-bridge.conf`, `client_layout`). Injection de 2000 mouvements/s mesurée : le pont n'est pas un goulot.

### c. Résolution à la volée : `sunshine-resize.sh` + `apps.json`
Appelé par Sunshine au début (`do`) et à la fin (`undo`) d'une session (`global_prep_cmd`) : il crée un mode `LxH_moon` avec `xrandr --newmode`, l'ajoute à la sortie `VNC-0`, et revient en 1920×1080
à la fin. Des entrées d'application dans Moonlight (« Desktop 2K », « Desktop 1080p ») forcent une résolution fixe (`sunshine-resize.sh fixed 2560 1440`).
Attention : **Sunshine encode à la résolution que le client demande** ; régler le bureau en 1440p ne sert à rien si Moonlight demande du 720p (c'est exactement ce qui avait fait croire à un flou).

## 7. Les images clés et le débit (correctif GOP)

La GOP était `fps*5` : un IDR toutes les ~5 s. Mesuré côté téléphone : **une image clé de 680 Ko toutes les 5,02 s**, entre-temps des salves de ~40 Ko toutes les 0,1 s (mises à jour d'écran) ;
l'utilisateur voyait une pulsation périodique sur ses graphiques. Correctif (`v4l2-gop.patch`) : demander une GOP « infinie » (`1<<30`, puis `0x7fffff`, `0x7fff`, `fps*60`) en relisant la valeur acceptée et en journalisant
`V4L2: GOP size N (keyframes on demand only)`. Les IDR ne viennent plus que de `request_idr_frame` / `invalidate_ref_frames` (Moonlight, perte de paquets). Effet à vérifier : éteindre puis rallumer Sunshine.

## 8. Mesures finales et ce qui reste ouvert

| | Avant (x264, 1440p/60) | Après (matériel, 720p/60) |
|---|---|---|
| CPU de Sunshine | ~205 % | **12 à 16 % d'un coeur** |
| Débit sortant | ~2,5 Mbit/s | 6,3 à 8,1 Mbit/s |
| Température max | 72 °C (tests WebGL) | 52 °C |

En régime courant (1440p/60, avec `Xvnc` à ~12 %), Sunshine se stabilise autour de 32-46 % d'un coeur.
Le débit d'un vrai 2K demande 30 à 40 Mbit/s côté Moonlight ; à 7 Mbit/s le 1440p serait de toute façon très compressé.

Tests d'une miniature H.264 pour l'aperçu du tableau de bord : `h264_v4l2m2m` échoue (même cause que §2) et x264 `ultrafast` coûte ~0,5 coeur ; la capture `x11grab` à 2560×1440 seule coûte 0,5 à 0,85 coeur.
L'aperçu reste donc un flux JPEG à 3 images/s ([06](06-tableau-de-bord.md)).

Pistes ouvertes : HEVC (le bloc le sait faire, il manque la gestion côté Sunshine : `MaxLumaPixelsHEVC` est à 0), invalidation de références, plusieurs sessions, débit adaptatif,
et publier le travail sous forme de PR propre contre Sunshine (le correctif dérive d'une version datée, voir licences dans [10](10-securite-et-publication.md)).

## 9. Pièges rencontrés pendant ce travail

- **Écran blanc dans Moonlight** alors que le téléphone envoyait bien 17 Mbit/s : Moonlight demandait 63 Mbit/s, x264 produisait du **H.264 niveau 6.0**, que beaucoup de décodeurs matériels de PC refusent. Remède : 1280×720 à 60 i/s, 15-20 Mbit/s, H.264 ; test décisif : « forcer le décodage logiciel » dans Moonlight.
- Le viewer TurboVNC avait redimensionné le bureau en **3058×1537** (hauteur impaire) ; la sortie virtuelle `VNC-0` s'est retrouvée déconnectée. Désactiver le redimensionnement automatique du viewer pendant les tests.
- **Pas de souris** : voir §6b.
- Sunshine veut, en plus du PIN, l'identifiant de la demande d'appairage (`pairing_id`) : le relever dans l'interface web (`https://<ip>:47990`).
- Les journaux de licence du micrologiciel dans `dmesg` sont sans conséquence.
- Pendant la compilation le téléphone chauffe : faible priorité, 5 tâches, et ne rien lancer d'autre.
