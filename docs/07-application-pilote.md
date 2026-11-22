# 07 — Application Pilote

Application Android (`fr.pilote`) : une **WebView** de `http://127.0.0.1:8090/resume` (le tableau de bord, [06](06-tableau-de-bord.md)) plus des commandes root.
Code : `src/pilote/MainActivity.java`, `src/pilote/AndroidManifest.xml`. Toute la logique des commandes est dans **`src/phone/phone-ctl.sh`** (installé dans `/data/adb/phone-ctl.sh`).

Une seconde petite application, `fr.pilote.webui` (`src/pilote/webui/`), est une WebView de `127.0.0.1:3000` (Open WebUI).

## Architecture

```
WebView ──JavaScriptInterface "Android"──► MainActivity ──shell root persistant (su -mm)──► /data/adb/phone-ctl.sh <action>
```

### Pont JavaScript (valeurs filtrées côté Java)
| Méthode | Effet |
|---|---|
| `Android.gov(g)` | gouverneur des 3 blocs : `auto\|powersave\|uag\|performance` |
| `Android.boost()` | réarme la performance (`perf-boost`) |
| `Android.chroot(a)` | `start\|stop\|reboot`, avec dialogue de confirmation natif pour arrêter/redémarrer |
| `Android.svc(n, a)` | `n` ∈ `vnc\|sunshine\|virgl`, `a` ∈ `start\|stop` ; confirmation seulement pour arrêter VNC (la session de bureau est perdue, Sunshine s'arrête aussi) |
| `Android.ai(n, a)` | `n` ∈ `llm\|olala`, `a` ∈ `start\|stop` ; confirmation pour démarrer Olala (Qwen est arrêté) |
| `Android.open(w)` | `moonlight` (ouvre l'application Moonlight) ou `webui` (arrête puis relance Open WebUI) |
| `Android.h(px)` | la page indique sa hauteur : la WebView s'y ajuste |

La page détecte le pont (`window.Android`) : sans lui (navigateur) elle ne montre que des voyants.

### Actions de `phone-ctl.sh`
`status`, `chroot-start|stop|reboot`, `perf-boost`, `vnc-restart`, `vnc-start|stop`, `sunshine-start|stop`, `llm-start|stop`, `olala-start|stop`,
`virgl-start|stop`, `open-moonlight`, `open-webui`, `gov <0|3|7|all> <gouverneur|auto>`. Journal : `/data/local/tmp/phone-ctl.log`.
`status` imprime `chroot, vnc, sunshine, llm, olala, virgl, gov, cap, hw, clamp, gov0/3/7, man0/3/7`.

Règles encodées dans le script : l'arrêt de VNC arrête Sunshine ; démarrer Sunshine est refusé si Xvnc est arrêté ; Qwen et Olala ne tournent jamais ensemble ;
ouvrir une application se fait par `cmd package resolve-activity` puis `am start -n` (**`monkey` ne marche pas** depuis ce shell root).

## Pièges (appris à la dure)

- **`su -mm`** (espace de montage global) : sans lui, un chroot démarré depuis l'application vit dans un espace privé et ne voit pas Termux → VirGL échoue.
- **Un `su` persistant par shell** (shell d'état et shell d'actions) : sinon Magisk affiche une notification à chaque appel.
- **Play Protect** bloque `pm install` (`INSTALL_FAILED_VERIFICATION_FAILURE`) tant qu'il n'est pas désactivé dans le Play Store.
- Ne jamais copier `phone-ctl.sh` dans `service.d` ([01](01-base-root-chroot.md)).
- `pkill -x llama-server` est interdit : Qwen et Olala portent le même nom ([05](05-ia-locale.md)).
- L'activité gère `configChanges` : la rotation ne recrée pas l'activité, la page se réorganise seule.
- Coût d'affichage : l'application seule pouvait coûter 93 % d'un coeur (WebView, surfaceflinger) à cause d'animations quasi permanentes ; transitions raccourcies et badge THROTTLE statique → 36 %.

## Compilation sur le téléphone (dans le chroot)

Chaîne : `javac --release 8` contre `android-23/android.jar`, `d8` (dans `r8.jar`), `aapt`, `zipalign`, `apksigner`. L'enveloppe `ecj` de Debian est cassée : utiliser `javac`.
`targetSdk 30`, `minSdk 23`, `usesCleartextTraffic` (la page est en http local).

```sh
# dans le chroot, avec les sources dans /root/apk-build/app
export KS_PASS='un-mot-de-passe-que-vous-choisissez'     # protège le keystore ; ne le publiez jamais
bash /root/apk-build/build.sh                              # → /root/apk-build/pilote.apk
bash /root/apk-build/webui/build.sh                        # → webui.apk (même keystore)

# installer depuis le chroot (côté Android)
nsenter -t 1 -m -- pm install -r /data/local/chroot-distro/debian/root/apk-build/pilote.apk
```
Les scripts du dépôt (`src/phone/llm/build-pilote.sh`, `build-webui.sh`) **exigent** `KS_PASS` ; le keystore (`pilote.jks`) est créé au premier lancement et ne doit **jamais** être versionné.

## Disposition de l'interface (décisions prises)

- Titre : icône Debian, ↻ et interrupteur du chroot à droite près de l'heure.
- Une carte par thème (puissance, mémoire, CPU, GPU, NPU, encodeur, réseau, disque, IA) avec son icône en haut à droite de la carte.
- Gouverneur : une cellule dans le bloc CPU, à côté du Cortex-X3 (pastilles Auto/Éco/Normal/Max, cadenas si plafond, éclair pour réarmer).
- Interrupteurs en bas de leur bloc : VirGL (GPU), Sunshine et VNC (encodeur) ; le libellé « Sunshine » ouvre Moonlight.
- Bloc « IA locale » dans la page (Qwen et Olala côte à côte, noms soulignés → Open WebUI), donc il suit les colonnes.
- Paysage (largeur ≥ 640 px) : deux colonnes ; le titre occupe une colonne comme les autres blocs ; les lignes se remplissent de gauche à droite pour que le CPU ne soit pas coupé en bas.
- Écran « Tableau de bord indisponible » avec un bouton Démarrer le chroot et un nouvel essai automatique.
