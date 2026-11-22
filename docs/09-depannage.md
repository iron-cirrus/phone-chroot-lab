# 09 — Dépannage et pièges

Classés par symptôme. Chaque ligne vient d'un vrai incident.

## Démarrage et système

| Symptôme | Cause | Remède |
|---|---|---|
| Android gelé après le code PIN, plusieurs `chroot-distro` en parallèle | Des **sauvegardes dans `service.d`** : Magisk exécute tous les fichiers | Aucune sauvegarde dans `service.d` ; mettre en `service.d-sauvegardes/` ([01](01-base-root-chroot.md)) |
| `/system/bin/chroot-distro` a disparu, modules désactivés | Passage en **mode sans échec** : marqueur `disable` dans chaque module | `rm -f /data/adb/modules/*/disable` en root, puis redémarrer |
| Le démon de gouverneurs se lance deux fois / PID faux | `$$` dans un sous-shell `mksh` = PID du parent | `read MYPID _ < /proc/self/stat` |
| Ça fige quand tout démarre ensemble | 8 coeurs seulement, IA lourde | Retarder l'IA (180 s), `nice`, `taskset 3-7`, `touch /data/adb/no-llm` en secours |
| Sunshine, navigateurs plantent (XShm) | Noyau sans SYSVIPC | `LD_PRELOAD=libsysvshm-emu.so`, vider `/dev/shm/sysvshm-emu` à chaque session X |
| VirGL absent depuis l'application | Chroot démarré dans un espace de montage privé | Shell root de l'application en `su -mm` |
| `pm install` : `INSTALL_FAILED_VERIFICATION_FAILURE` | Play Protect | Le désactiver dans le Play Store le temps de l'installation |
| `adb` USB ne voit plus le téléphone après un reboot | — | SSH root + `nsenter -t 1 -m --` atteint Android sans `adb` |

## Commandes et scripts

| Symptôme | Cause | Remède |
|---|---|---|
| Une commande ssh avec `pkill -f mot` se tue elle-même | Le motif matche la ligne de commande ssh | Script dans un fichier ; ou `pkill -x nom` |
| `dashboard-start.sh` ne fait rien | `pgrep -f /opt/dashboard/app.py` trouve la commande ssh | L'appeler dans un ssh séparé |
| `pkill -x` avec un nom de plus de 15 caractères ne tue rien | Nom de processus tronqué | Utiliser les 15 premiers caractères |
| Un installateur (WhiteSur) s'arrête sans message | `TERM` non défini | `ssh -tt`, `export TERM=xterm` |
| `monkey` ne lance pas d'application | Contexte `su` | `cmd package resolve-activity` puis `am start -n` |
| `pkill -x llama-server` éteint les deux IA | Même nom de processus | `pgrep -f <fichier modèle>` |
| Un `sed` change `\uD83E…` en `D83E…` | `\u` = majuscules en GNU sed | Passer les échappements Unicode par un éditeur |
| Un emoji hors BMP fait une erreur 500 | Substitut Unicode dans une chaîne Python de la page | Entité HTML (`&#128274;`) |
| Un script de mesure affiche 77 % sur le gros coeur | Il s'exécute lui-même sur ce coeur (gouverneur `performance`) | `taskset -c 0` |
| Les lignes de commande Windows (CRLF) cassent les scripts du téléphone | Fins de ligne | `sed -i 's/\r$//'` avant envoi |
| `python3` introuvable sous Windows | Raccourci du Store | `wsl -e python3` depuis **PowerShell** (Git Bash déforme les chemins) |

## Mesures et tableau de bord

| Symptôme | Cause | Remède |
|---|---|---|
| Le gros coeur semble à 52-77 % | `core_ctl` compte le parcage en `steal` | Exclure `steal` ([06](06-tableau-de-bord.md)) |
| Puissance batterie fausse de ×2 / 1000 | `current_now` en mA et pour **une** des deux cellules | mA ×2 (`BATT_CELLS=2`) |
| Débit réseau par interface incohérent | `map_netd_stats_map_A/B` remises à zéro irrégulièrement | Utiliser `map_netd_app_uid_stats_map` |
| Tableau de bord à 26-33 s et > 2 coeurs | Requêtes empilées dans Flask | Cache 1,5 s avec verrou à un seul calculateur |
| L'application consomme 93 % d'un coeur | Transitions CSS quasi permanentes | 0,45 s, badge THROTTLE statique |
| Une carte change de hauteur quand la température monte | Badge ajouté en plus de la fréquence | Badge **à la place** de la fréquence |
| Icône cerveau = tache blanche | Épaisseur de trait mise à l'échelle deux fois | Fixer `setStrokeWidth` après `canvas.scale` |
| Le HTML casse après suppression de lignes | Un `</div>` supprimé avec la plage | Vérifier le `</div>` voisin |

## Compilation de Sunshine
Voir la recette dans [03](03-bureau-distant.md) : `/work`, `ninja -t commands`, objet unique, relier avec `-L/root/linkstubs` et `-lopus`.

## IA
- Olala ne se quantifie pas en Q4_0 de façon exploitable ; il ne tient pas sur le NPU v73 ([05](05-ia-locale.md)).
- Open WebUI envoie de très longs prompts tant que les outils intégrés et les tâches automatiques ne sont pas désactivés.
- `webui-start.sh` : les variables d'environnement ne remplacent plus la configuration déjà en base SQLite.

## Bureau
Voir le tableau d'essais de [08](08-bureau-mate.md#ce-qui-a-été-essayé-puis-abandonné-journal-honnête).
