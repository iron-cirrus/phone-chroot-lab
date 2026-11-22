# 03 — Bureau distant : TurboVNC, Sunshine, VirGL

## Vue d'ensemble

| Brique | Rôle | Écoute |
|---|---|---|
| **TurboVNC** (`Xvnc`, affichage `:1`) | Le serveur X : c'est lui qui porte le bureau MATE | 127.0.0.1:5901 (TLS), voir pare-feu |
| **Sunshine** (`sunshine-v4l2`) | Encode l'écran en H.264 et le sert à **Moonlight** (PC, téléphone, TV) | ports 47984, 47989, 47990, 48010 (TCP) et 47998-48010 (UDP) |
| **VirGL** (`virgl_test_server_android`, Termux) | GPU Adreno exposé aux applications du chroot (OpenGL ES, WebGL) | socket `/tmp/.virgl_test` dans le chroot |

Démarrage : `vnc-start.sh` (TurboVNC en `:1`, 1920×1080, 24 bits, `-securitytypes tlsvnc,x509vnc`, coeurs 3-7), puis `sunshine-start.sh`
(attend le socket X, lance PulseAudio, Sunshine, et un pont d'entrées Python pour clavier/souris). Voir `src/phone/chroot/`.

## Pare-feu (`src/phone/vnc-fw.sh`)

Chaîne `iptables` `vnc5901`, idempotente : accepte la boucle locale, le Wi-Fi maison (`-s 192.168.1.0/24` : **à adapter**), le partage de connexion
et le tethering ; refuse tout le reste (4G, `wg0`, autres Wi-Fi). Les mêmes règles protègent les ports de Sunshine. `20-vnc-fw.sh` la réapplique toutes
les 30 s parce que netd peut remanier les chaînes.

## L'encodeur matériel de Sunshine

`sunshine-v4l2` n'encode **ni en x264 ni sur le GPU Adreno** (malgré un commentaire périmé « x264 logiciel » dans sa configuration) : il utilise le
bloc d'encodage vidéo du SoC via V4L2 (pilote **`msm_vidc`**, périphérique `/dev/video33`). Le coût CPU de Sunshine est la capture X11 et la
conversion de couleur, pas l'encodage.

Le tableau de bord lit l'encodeur dans `debugfs` (visible depuis le chroot) :
`/sys/kernel/debug/msm_vidc/core/inst_*/info` (taille, compteur ETB → images/s), l'horloge `/sys/kernel/debug/clk/video_cc_mvs0_clk`, la zone
thermique `video`. Charge estimée = pixels/s ÷ (8K30).

### Correctif : keyframes seulement à la demande (`src/sunshine/v4l2-gop.patch`)

La taille de GOP était `fps*5` : un IDR toutes les 5 secondes, donc une rafale de débit périodique à 60 i/s. Le correctif demande au pilote une GOP
« infinie » (essaie `1<<30`, `0x7fffff`, `0x7fff`, `fps*60` en relisant la valeur acceptée) et journalise
`V4L2: GOP size N (keyframes on demand only)`. Les IDR ne viennent plus que de `request_idr_frame` (Moonlight, perte de paquets).
Il s'applique au prochain démarrage de Sunshine (l'éteindre puis le rallumer).

### Reconstruire Sunshine (recette)
L'environnement d'origine est dégradé : ni `cmake`/`ninja`/`pkg-config`, ni paquets `-dev`.
1. Le dossier de compilation attend le chemin `/work` : `mkdir /work && mount --bind /usr/local/src/sunshine-build /work`.
2. `ninja` : `apt install ninja-build` ; récupérer la ligne de compilation avec `ninja -t commands sunshine` et ne recompiler **que** l'objet de
   `v4l2_encode.cpp`.
3. Relier avec des « stubs » : `-L/root/linkstubs` contenant des liens `.so` vers les bibliothèques d'exécution présentes ; remplacer `libopus.a` par `-lopus`.
4. Installer sous `/usr/local/bin/sunshine-v4l2` (garder l'ancien binaire en `.avant-gop`).

## VirGL (GPU)

Le serveur vient de **Termux** (`/data/data/com.termux/files/usr/bin/virgl_test_server_android`), lancé par `10-chroot-ssh.sh` **après** les montages du chroot,
car son socket vit dans le `/tmp` du chroot (`--socket-path …/chroot-distro/debian/tmp/.virgl_test`, `chmod 666` pour l'utilisateur `web`).
Piège : si le chroot est démarré depuis l'application Pilote, son root shell doit être ouvert en **`su -mm`** (espace de montage global), sinon le chroot
vit dans un espace privé et ne voit pas Termux (VirGL échoue).

`chromium-web` active WebGL sur le GPU quand le socket existe, mais **désactive la composition GPU** : VirGL ne sait pas dessiner le texte,
les pages restent vides sinon.

## Wi-Fi faible latence

`30-wifi-lowlatency.sh` : `cmd wifi force-low-latency-mode enabled`, `force-hi-perf-mode enabled`, `iw dev wlan0 set power_save off`, en boucle toutes les 30 s.

## Changements de résolution (Moonlight)

Moonlight change la résolution du bureau en direct (modes RandR `1280x720`, `1920x1080`, `2560x1440` et des modes `*_moon` créés par Sunshine).
`xrandr --output VNC-0 --mode <nom>`. Les applications qui se placent par rapport au bord de l'écran ne suivent pas toutes : Plank est resté
au milieu de l'écran après un passage en 2560×1440. La surveillance est décrite dans [08](08-bureau-mate.md#suivre-la-résolution).
