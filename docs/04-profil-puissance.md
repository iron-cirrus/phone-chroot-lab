# 04 — Profil de puissance

Fichier : `src/phone/service.d/50-power-profile.sh` (un seul démon, sondage toutes les 5 s, démarre au boot + 60 s).

## Topologie du SoC

| Bloc | Coeurs | Politique cpufreq | Zones thermiques |
|---|---|---|---|
| petits (Cortex-A510) | cpu0-2 | policy0 | `cpu-0-*` |
| moyens (A715/A710) | cpu3-6 | policy3 | `cpu-1-*` |
| gros (Cortex-X3) | cpu7 | policy7 | `cpu-1-*` |

## Comportement

- **Sur secteur** : gouverneur `performance` sur toutes les politiques.
- **Sur batterie** : `uag` avec les plafonds d'origine (policy0 1459200, policy3 1785600, policy7 1593600 kHz).
- **Garde-fou thermique** : température de peau ≥ 47 °C → mode économe jusqu'à < 43 °C (il s'applique aussi quand « Max » est choisi à la main).
- **Choix manuel** (depuis l'application Pilote ou `phone-ctl.sh gov`) : écrit dans `/data/local/tmp/gov-manual` (lignes « politique gouverneur »), respecté par le démon.
  Un seul curseur à 4 positions pour les 3 blocs : **Auto / Éco / Normal / Max** (`phone-ctl.sh gov all <gouverneur|auto>`).
  - « Éco » (powersave manuel) **plafonne aussi la fréquence max** (policy0 1228800, policy3 1286400, policy7 1248000) : le HAL de performance
    d'Oplus/Qualcomm remonte sans cesse `scaling_min_freq` (jusqu'à 1,9 GHz sur les moyens), donc `powersave` seul économisait peu.
  - « Max » lève les plafonds même sur batterie.
- Journal : `/data/local/tmp/power-profile.log`. Interrupteur : `touch /data/adb/no-power-profile` puis redémarrer.
- Le chroot n'est jamais touché par ce démon (WireGuard l'occupe en permanence).

## Limite connue

Le plafond de policy3 (cpu3-6, ~1785600) et policy7 (~2227200) est imposé par un plafond noyau/constructeur que l'écriture dans sysfs ne lève pas
(SIGSTOP de `perf-hal`, `perfservice` et des services thermiques n'y change rien). Seule policy0 atteint son maximum matériel. L'application signale
`clamp=1` quand le plafond constaté est inférieur à l'attendu.

## Pièges

- Dans le `mksh` d'Android, **`$$` dans un sous-shell vaut le PID du parent** : le verrou du démon lit son PID avec `read MYPID _ < /proc/self/stat`.
- **Jamais de sauvegarde dans `service.d`** ([01](01-base-root-chroot.md)). Les sauvegardes du démon : `/data/adb/service.d-sauvegardes/`.
- Une ancienne boucle « performance toutes les 30 s » dans `10-chroot-ssh.sh` combattait le mode éco : elle a été supprimée.

## Applications gloutonnes

Mesurées sur ce téléphone (zones CPU à 76-80 °C au repos) :
- `com.amazon.clouddrive.photos` se relançait seule et brûlait ~100 % d'un coeur. Un `force-stop` revenait après reboot ; remède durable :
  ```sh
  cmd appops set com.amazon.clouddrive.photos RUN_IN_BACKGROUND deny
  cmd appops set com.amazon.clouddrive.photos RUN_ANY_IN_BACKGROUND deny
  # annuler : mêmes commandes avec "allow"
  ```
- Les applications de caméras `com.mcu.reolink` et `com.ezviz` gardent des threads P2P constants sur cpu0 : même traitement (on perd leurs notifications en arrière-plan).

À ne faire qu'avec l'accord du propriétaire de l'appareil, application par application.
