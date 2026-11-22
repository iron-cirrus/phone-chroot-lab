#!/bin/bash
# Habillage du bureau MATE du chroot : deux docks Plank (icones seules, fond transparent), menu rofi, icone de menu Debian,
# fenetre de resume du chroot, surveillance de la resolution.
#
# ATTENTION : script RECONSTITUE a partir des commandes passees une par une pendant la mise au point ; il n'a pas ete lance
# d'un seul bloc. Lisez-le, lancez-le par morceaux dans le chroot, en root, et verifiez avec une capture a chaque etape.
# Prerequis : session MATE sur DISPLAY=:1, tableau de bord sur 127.0.0.1:8090 (voir docs/06-tableau-de-bord.md).
set -e
export DISPLAY=:1 XAUTHORITY=/root/.Xauthority HOME=/root
export DEBIAN_FRONTEND=noninteractive

apt-get install -y plank xcompmgr xdotool wmctrl rofi x11-utils imagemagick

# --- session : adresse du bus de messages (necessaire pour gsettings/dconf depuis ssh) ---
P=$(pgrep -u root -x mate-session | head -1)
export DBUS_SESSION_BUS_ADDRESS=$(tr '\0' '\n' < /proc/$P/environ | sed -n 's/^DBUS_SESSION_BUS_ADDRESS=//p')

# --- retirer le panneau MATE de la session (le dock le remplace) ; sauvegarde de la liste d'origine ---
mkdir -p /root/backup-mate-theme
gsettings get org.mate.session required-components-list > /root/backup-mate-theme/required-components-avant.txt
gsettings set org.mate.session required-components-list "['windowmanager', 'dock']"

# --- icone du menu : theme d'icones qui herite de menta et remplace le logo MATE par celui de Debian ---
# (necessite une icone Debian ; ici celle du theme Papirus : apt install papirus-icon-theme)
D=/usr/share/icons/Menta-Debian
mkdir -p $D/scalable/apps
cat > $D/index.theme <<'T'
[Icon Theme]
Name=Menta-Debian
Comment=Menta avec le logo Debian pour le menu
Inherits=menta,mate,hicolor
Directories=scalable/apps

[scalable/apps]
Size=48
MinSize=8
MaxSize=512
Type=Scalable
Context=Applications
T
S=/usr/share/icons/Papirus/48x48/apps/start-here-debian.svg
for n in start-here start-here-symbolic mate-logo-icon; do cp $S $D/scalable/apps/$n.svg; done
gtk-update-icon-cache -f $D >/dev/null 2>&1 || true
gsettings set org.mate.interface icon-theme Menta-Debian

# --- menu des applications : lanceur rofi (le docklet "Applications" de Plank ne s'ouvrait pas) ---
cat > /usr/local/share/applications/menu-debian.desktop <<'D'
[Desktop Entry]
Type=Application
Name=Menu
Comment=Toutes les applications
Exec=rofi -show drun -show-icons
Icon=debian-menu
Terminal=false
Categories=Utility;
D

# --- theme Plank transparent "Clair" : theme par defaut, fond blanc tres leger, sans rebond ---
T=/root/.local/share/plank/themes/Clair
mkdir -p $T
sed -e 's/^FillStartColor=.*/FillStartColor=255;;255;;255;;70/' \
    -e 's/^FillEndColor=.*/FillEndColor=255;;255;;255;;40/' \
    -e 's/^OuterStrokeColor=.*/OuterStrokeColor=255;;255;;255;;90/' \
    -e 's/^InnerStrokeColor=.*/InnerStrokeColor=255;;255;;255;;0/' \
    -e 's/^LaunchBounceHeight=.*/LaunchBounceHeight=0/' \
    -e 's/^UrgentBounceHeight=.*/UrgentBounceHeight=0/' \
    /usr/share/plank/themes/Default/dock.theme > $T/dock.theme

# --- Plank doit etre ARRETE pendant qu'on ecrit la liste des elements (sinon il la reecrit) ---
pkill -x plank || true; sleep 2

L1=/root/.config/plank/dock1/launchers
L2=/root/.config/plank/dock2/launchers
mkdir -p $L1 $L2
launcher() {  # launcher <dossier> <nom> <fichier .desktop ou docklet://...>
  case "$3" in docklet://*) l="$3" ;; *) l="file://$3" ;; esac
  printf '[PlankDockItemPreferences]\nLauncher=%s\n' "$l" > "$1/$2.dockitem"
}
launcher $L1 menu            /usr/local/share/applications/menu-debian.desktop
launcher $L1 chromium        /usr/local/share/applications/chromium.desktop
launcher $L1 caja            /usr/share/applications/caja.desktop
launcher $L1 mate-terminal   /usr/share/applications/mate-terminal.desktop
launcher $L1 vscode          /usr/share/applications/com.microsoft.VSCode.desktop
launcher $L1 mate-system-monitor /usr/share/applications/mate-system-monitor.desktop
launcher $L1 volume          /usr/share/applications/mate-volume-control.desktop
launcher $L2 battery         docklet://battery
launcher $L2 clock           docklet://clock

# dock1 : centre, avec les fenetres ouvertes ; dock2 : coin droit, seulement batterie + horloge.
# dock2 en hide-mode "window-dodge" : en "none" il reserve de la place et pousse dock1 vers le haut.
# icon-size minimum = 24 (une valeur hors plage est ignoree et Plank revient a 48).
Q=/net/launchpad/plank/docks
dconf write $Q/dock1/dock-items "['menu.dockitem', 'chromium.dockitem', 'caja.dockitem', 'mate-terminal.dockitem', 'vscode.dockitem', 'mate-system-monitor.dockitem', 'volume.dockitem']"
dconf write $Q/dock2/dock-items "['battery.dockitem', 'clock.dockitem']"
for d in dock1 dock2; do
  dconf write $Q/$d/theme "'Clair'"
  dconf write $Q/$d/icon-size 36
  dconf write $Q/$d/position "'bottom'"
  dconf write $Q/$d/zoom-enabled false
  dconf write $Q/$d/zoom-percent 100
  dconf write $Q/$d/lock-items true
done
dconf write $Q/dock1/alignment "'center'"; dconf write $Q/dock1/hide-mode "'none'"
dconf write $Q/dock2/alignment "'end'";    dconf write $Q/dock2/hide-mode "'window-dodge'"; dconf write $Q/dock2/pinned-only true

# --- scripts de support ---
install -m 755 "$(dirname "$0")/plank-watch.sh"   /usr/local/bin/plank-watch.sh
install -m 755 "$(dirname "$0")/resume-window.sh" /usr/local/bin/resume-window.sh

# --- demarrage automatique ---
A=/root/.config/autostart; mkdir -p $A
mk() { printf '[Desktop Entry]\nType=Application\nName=%s\nExec=%s\nX-GNOME-Autostart-enabled=true\n' "$1" "$2" > $A/$3.desktop; }
mk "xcompmgr"      "xcompmgr"                                   xcompmgr
mk "Plank"         "env XDG_MENU_PREFIX=mate- plank"            plank
mk "Plank 2"       "env XDG_MENU_PREFIX=mate- plank -n dock2"   plank2
mk "Plank watch"   "/usr/local/bin/plank-watch.sh"              plank-watch
mk "Resume chroot" "/usr/local/bin/resume-window.sh"            resume-window

# --- profil Chromium dedie a la fenetre de resume : cadre systeme (sinon Chromium dessine son propre titre) ---
# A faire UNE FOIS, Chromium ferme, apres un premier lancement qui cree le profil :
#   python3 - <<'PY'
#   import json; p='/home/web/.config/chromium-resume/Default/Preferences'; d=json.load(open(p))
#   d.setdefault('browser',{})['custom_chrome_frame']=False; d.setdefault('translate',{})['enabled']=False
#   json.dump(d,open(p,'w'))
#   PY
#   chown web:web /home/web/.config/chromium-resume/Default/Preferences

echo "Termine. Relancez la session, ou lancez a la main : xcompmgr & plank & plank -n dock2 & resume-window.sh &"
