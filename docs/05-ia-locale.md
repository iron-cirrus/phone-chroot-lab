# 05 — IA locale

Dossier de travail dans le chroot : `/root/llm/` (~9,6 Go avec les modèles). Les modèles et binaires ne sont **pas** dans le dépôt.

## Les composants

| Composant | Où | Écoute | Démarrage |
|---|---|---|---|
| **Ollama** 0.40 | `/opt/ollama` (chroot), modèles `/root/llm/models` | 127.0.0.1:11434 | `ollama-start.sh` (installé, **non démarré**, désactivé dans Open WebUI) |
| **Open WebUI** 0.13 | venv Python 3.12 `/root/llm/webui-venv` | 127.0.0.1:3000 | `webui-start.sh` |
| **llama.cpp NPU** (paquet Snapdragon b12561) | `/root/llm/llama-hex/llama-b12561`, modèle `Qwen2.5-3B-Instruct-Q4_0.gguf` | 127.0.0.1:8081, alias `qwen2.5-3b-npu` | `llama-npu-start.sh` (côté Android) |
| **Olala-v1.0-mini-7A1B** sur CPU | `/root/llm/olala/hex` (compilé avec le NDK), `…q5_k_m.gguf` (5,6 Go) | 127.0.0.1:8082, alias `olala-7a1b` | `olala-hex-start.sh` + `olala-warmup.sh` |

Le port 8080 est déjà pris par code-server : Open WebUI est sur 3000.

## Règle d'or : les serveurs llama.cpp tournent côté Android

Le NPU et la compilation NDK sont en **bionic** : on les lance avec
`nsenter -t 1 -m -- sh /data/local/chroot-distro/debian/root/llm/llama-npu-start.sh`, avec `LD_LIBRARY_PATH` incluant `/vendor/lib64` (libOpenCL)
et `ADSP_LIBRARY_PATH` vers les bibliothèques du paquet. Le NPU et Olala portent le même nom de processus `llama-server` :
les distinguer avec `pgrep -f <fichier modèle>`, **jamais** `pkill -x llama-server`.

**Un seul des deux à la fois** (mémoire) : les boutons de Pilote l'imposent (démarrer Olala arrête Qwen).

## Mesures

| Configuration | Prompt (prefill) | Génération |
|---|---|---|
| Qwen2.5-3B Q4_0 sur NPU Hexagon v73 | 652 tok/s | ~17 tok/s |
| Qwen2.5-3B sur CPU (Ollama) | 30 tok/s | 8,4 tok/s |
| Olala 7A1B Q5_K_M, CPU, moteur NDK | 47-52 tok/s | 15-17 tok/s |
| Olala, ancien moteur gcc du chroot (`taskset 3-7`) | ~20 tok/s | — |

Le NPU fait monter la température d'environ 20 °C en rafale.

### Réglages d'Olala (et pourquoi)
`--device none -ngl 0 -t 5 -fa 1 -c 8192 -np 1 -cms 256 -ctxcp 16 -cram 2048 --reasoning-effort disabled`.
Avec le réglage par défaut `--checkpoint-min-step 8192`, l'état Mamba du modèle hybride n'était presque jamais réutilisable en milieu de conversation ;
`-cms 256` règle cela. Le premier appel après un redémarrage est lent (pages mmap froides) tant que `olala-warmup.sh` n'a pas pré-rempli le gabarit
(283 jetons). Journal : `/data/local/tmp/olala-server.log`.

### Impasses documentées (ne pas les refaire)
- **Olala sur le NPU v73** : ~3000 Mio de mémoire mappable par session et le backend force une session en dessous de v75, donc au plus ~12 couches sur 36 ;
  les `MUL_MAT` denses (attention) sur le HTP **corrompent le texte** ; et même juste, c'est plus lent que le CPU (prompt 32 contre 47 tok/s).
- **Quantification Q4_0 faite maison** d'Olala : toutes les variantes, même en gardant les tenseurs sensibles en plus haute précision, produisent du charabia.
  On garde le fichier Q5_K_M des auteurs.

## Open WebUI

- La configuration vit dans la table SQLite `config` (clé/valeur) : **les variables d'environnement ne la remplacent plus** une fois en base.
  La connexion OpenAI pointe sur le serveur NPU, Ollama est désactivé, et les tâches titre / tags / suggestions / requêtes sont **désactivées**
  (elles envoyaient des prompts de 5700 jetons qui prenaient 70 s sur CPU).
- Les deux modèles ont une entrée dans la table `model` avec `capabilities.builtin_tools=false` : sans cela Open WebUI injectait ~7300 jetons d'outils
  intégrés à chaque message (sauvegarde `webui.db.avant-modeles`).
- `WEBUI_SECRET_KEY` est lu dans un fichier (`/root/llm/webui-secret`), jamais écrit dans un script.

## Démarrage automatique

`40-llm.sh` : après le boot + 180 s, serveur NPU (`nice -n 10`), 30 s plus tard Open WebUI (`nice -n 15`, coeurs 3-7). Vérifié par un redémarrage :
tout est prêt ~4,5 min après le boot, sans gel. Arrêt d'urgence : `touch /data/adb/no-llm` puis redémarrer. Depuis, Pilote démarre/arrête Qwen et Olala à la demande
(`phone-ctl.sh llm-start|llm-stop|olala-start|olala-stop`).
