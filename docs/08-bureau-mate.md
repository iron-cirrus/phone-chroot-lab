# 08 — Bureau MATE

Le bureau est une session **MATE** dans TurboVNC (`DISPLAY=:1`), vue par Moonlight. Ce guide décrit l'**état final**, la façon de l'obtenir
(`src/desktop/setup-desktop.sh`), et surtout ce qui a été essayé puis abandonné, pour ne pas le refaire.

## État final

- MATE **aux réglages d'usine** (thème Menta, police par défaut, boutons de fenêtre standard) : tout le reste a été remis à zéro avec `dconf reset`.
- **Plus de panneau MATE** : il a été retiré de la liste des composants de session. Le bureau n'a que :
  - **dock1** (Plank, centré, bas) : menu (logo Debian), Chromium, fichiers (Caja), terminal, VS Code, moniteur système, volume, puis les fenêtres ouvertes ;
  - **dock2** (Plank, coin droit) : batterie et horloge ;
  - une **fenêtre de résumé** du chroot posée sur le fond d'écran (Chromium en mode application, sans cadre).
- Les docks sont **transparents** (thème Plank `Clair`) grâce à `xcompmgr`. Icônes de 36 px, sans zoom au survol, sans rebond.
- Le menu est un lanceur **rofi** (liste des applications avec recherche), avec l'icône Debian.

## Reproduire

Lire puis lancer par morceaux `src/desktop/setup-desktop.sh` (il a été **reconstitué** à partir des commandes passées une à une ; il n'a pas été exécuté d'un bloc).
Il installe `plank xcompmgr xdotool wmctrl rofi`, crée le thème d'icônes `Menta-Debian`, le thème Plank `Clair`, les deux docks, les démarrages automatiques,
et retire le panneau de la session. Sauvegarde de la liste d'origine : `/root/backup-mate-theme/required-components-avant.txt` (valeur d'origine
`['windowmanager', 'panel', 'dock']`).

Pour **retrouver le panneau** :
```sh
gsettings set org.mate.session required-components-list "['windowmanager','panel','dock']"
# puis, dans la session graphique : mate-panel --replace
```

## Les deux docks (et pourquoi deux)

Plank place toujours les **fenêtres ouvertes après les éléments épinglés**. Pour finir la rangée par batterie et horloge, un second dock
(`plank -n dock2`) est aligné à droite, en `pinned-only`. Points délicats :
- `dock2` en `hide-mode 'none'` réserve de la place et **pousse dock1 vers le haut** d'une hauteur de dock : il est en `'window-dodge'` (pas de réservation).
- `icon-size` minimum = **24** (hors de la plage 24-128, la valeur est ignorée et Plank retombe sur 48).
- Plank doit être **arrêté** pendant qu'on écrit `dock-items` : en marche, il ajoute les nouveaux `.dockitem` à la fin et réécrit la liste.
- `lock-items true` : on ne déplace plus les icônes en cliquant/glissant. `hide-mode 'none'` sur dock1 : il ne glisse plus quand une fenêtre passe dessus.
- Les docklets disponibles : applications, battery, clippy, clock, cpumonitor, desktop, trash. **Pas de zone de notification** (tray).
- Le docklet « Applications » de Plank **ne s'ouvrait pas** : remplacé par un lanceur `rofi -show drun -show-icons` (le premier lancement prend quelques secondes).
  Lancer Plank avec `XDG_MENU_PREFIX=mate-` si on veut tenter le docklet.

## Transparence

Elle n'est obtenue que pour **Plank** (fenêtres ARGB) avec **`xcompmgr`** (autostart). Chemins abandonnés :
- Le **compositeur de Marco** (`org.mate.Marco.general compositing-manager true`) : aucun alpha visible, et `xwd -root` échoue (`BadMatch`).
- Le **panneau MATE** : même avec `xcompmgr`, son fond restait opaque (le thème peint par-dessus). La clé d'opacité est un entier à régler avec `gsettings`
  (`opacity`, 65535 = opaque) ; une valeur chargée par `dconf load` n'a pas de type et est **ignorée**. Le panneau doit en plus être relancé après activation du compositeur.
- Effets de bord de `xcompmgr` : dans les captures `xwd -root` une bande noire en bas et des cadres noirs autour des fenêtres (artefact de capture, pas d'affichage).
  Il consomme peu (~0,6 % d'un coeur).

## Fenêtre de résumé (`src/desktop/resume-window.sh`)

Chromium en `--app=http://127.0.0.1:8090/resume?bare`, avec un **profil dédié** (`--user-data-dir=/home/web/.config/chromium-resume`, `--class=ResumeChroot`),
taille 470×740 en haut à droite, sans cadre, sous les autres fenêtres. Il faut :
1. un premier lancement pour créer le profil, puis, Chromium fermé, dans `…/Default/Preferences` : `browser.custom_chrome_frame=false` (sinon Chromium dessine son
   propre bandeau de titre, impossible à retirer) et `translate.enabled=false` ;
2. retirer la décoration du gestionnaire de fenêtres : `xprop -id <fenêtre> -f _MOTIF_WM_HINTS 32c -set _MOTIF_WM_HINTS "2, 0, 0, 0, 0"` ;
3. **répéter** cette opération plusieurs fois après le démarrage, **sur chaque fenêtre de la classe** : Chromium réécrit ses indications au démarrage ;
4. `wmctrl -i -r <id> -b add,below,skip_taskbar,skip_pager`.

La position `X=2060` suppose un écran de 2560 px de large. Arrêter : `pkill -u web -f chromium-resume` (et supprimer l'autostart). C'est un navigateur complet
qui reste ouvert : plus lourd qu'un afficheur de bureau.

### Pourquoi pas Conky
Essayé (script Python qui lit `/api/stats` et formate pour Conky, fond ARGB sombre) : fonctionnel mais jugé laid. Retiré. Il fallait
`override_utf8_locale = true` + `LANG=C.UTF-8` sinon les barres et flèches Unicode étaient illisibles.

## Suivre la résolution

Plank ne suit **pas** les changements RandR d'Xvnc : après le passage 1280×720 → 2560×1440 demandé par Moonlight, les docks sont restés à `y=675`, sur 1280 px.
`src/desktop/plank-watch.sh` (autostart) interroge `xdotool getdisplaygeometry` toutes les 2 s et **relance les deux docks** quand la géométrie change.
Testé : 2560×1440 → 1920×1080 → 2560×1440, docks recollés en bas à chaque fois (quelques secondes sans dock pendant la relance).
Modes : `xrandr` liste `1280x720`, `1920x1080`, `2560x1440` et des `*_moon` ; `xrandr --output VNC-0 --mode <nom>`.

## Ce qui a été essayé puis abandonné (journal honnête)

| Essai | Résultat |
|---|---|
| Thème **WhiteSur** (GTK + icônes, clone GitHub) + dock macOS + barre du haut avec menu global (`mate-applet-appmenu`) | Installé et fonctionnel (le menu global affichait les menus de Caja), mais **jugé laid** : tout remis en configuration d'usine |
| `mate-panel --reset --layout default` | **Quitte immédiatement** et a laissé la configuration vide ; la bonne méthode a été de convertir le fichier `/usr/share/mate-panel/layouts/default.layout` en clés dconf (`layout2dconf`) puis `dconf load` et `mate-panel --replace` |
| Déplacer le panneau haut en bas via `dconf write` | Sans effet à chaud : **relancer le panneau** (`pkill -x mate-panel; mate-panel --replace`, dans un script) |
| `panel-right-stick`, `relative-to-edge` pour caler horloge/notifications à droite | Ignorés ; positions **absolues** en pixels depuis la gauche |
| Menu MATE « Applications Lieux Système » **en icône seule** | Impossible : masquer ses trois entrées retire aussi l'icône ; remplacé par l'applet Brisk, puis par rofi dans Plank |
| Liste des fenêtres **en icônes seules** par CSS (`font-size:0` sur les `label` de `wnck-tasklist`) | Boutons **blancs et vides** : abandonné ; la liste de MATE n'a pas de mode icônes seules ; solution = Plank |
| Bluetooth dans le dock | Impossible : le chroot ne voit pas la puce (pas de `/sys/class/bluetooth`, ni `bluetoothctl`) ; Bluetooth appartient à Android |
| Menu des applications de Plank (docklet) | Ne s'ouvre pas |
| Zone de notification dans Plank | N'existe pas ; le volume passe par un lanceur `mate-volume-control` et les touches du clavier |

## Retour en arrière complet

Sauvegardes dans `/root/backup-mate-theme/` (`mate-avant-usine.dconf` : `dconf load /org/mate/ < fichier`, `gtk.css.avant-usine`). Le thème Marco personnalisé
`GreenLaguna-Win11` (boutons Win11 générés par un script depuis GreenLaguna) est resté dans `/root/.themes` mais n'est plus actif.
Réinitialisation d'usine : `dconf reset -f` sur `/org/mate/panel/`, `/org/mate/interface/`, `/org/mate/marco/`, `/org/mate/Marco/`, suppression de `~/.config/gtk-3.0/gtk.css`.
