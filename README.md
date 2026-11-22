# phone-chroot-lab

Un téléphone Android rooté (OnePlus 11, Snapdragon 8 Gen 2, Android 13, Magisk) transformé en petit poste Linux : un chroot Debian avec un bureau MATE diffusé vers un PC par
**Moonlight**, une IA locale (CPU et NPU Hexagon), un tableau de bord de télémétrie, une application Android de pilotage (« Pilote ») et des scripts pour tout démarrer au boot.

## Le projet phare : un encodeur H.264 matériel pour Sunshine

Sunshine (le serveur de streaming compatible Moonlight) ne sait encoder que sur NVIDIA, AMD, Intel ou en logiciel. Sur ce téléphone, l'encodage logiciel coûtait **~205 % de CPU**.
Le bloc d'encodage vidéo Qualcomm (`msm_vidc`, `/dev/video33`) est utilisable depuis le chroot, mais **FFmpeg échoue** : le pilote exige des tampons **DMABUF** alloués dans `/dev/dma_heap`.
Ce dépôt contient :
- les **preuves de concept** qui ont validé le chemin (pas de DRM, flux H.264 standard, PSNR 47 dB, 1080p/60 pour 13 % d'un coeur) ;
- un **encodeur V4L2 natif** (`v4l2_encode.cpp/.h`, ~600 lignes) branché dans Sunshine comme le chemin NVENC, sans FFmpeg, avec conversion `libyuv` directement dans le tampon DMABUF ;
- les modifications de `video.cpp`, `video.h` et CMake, un script d'intégration rejouable, et un correctif de GOP (keyframes à la demande) ;
- les pièces qui rendent Sunshine utilisable dans un chroot Android : émulation de la mémoire partagée System V, pont d'entrées `uinput` → XTest, résolution à la volée.

Résultat mesuré : **12 à 16 % d'un coeur** à 720p/60 au lieu de ~205 %, soit un facteur 13 à 17.
👉 Tout est raconté, avec les impasses, dans **[docs/12-sunshine-encodeur-materiel.md](docs/12-sunshine-encodeur-materiel.md)**.

Le reste du dépôt documente ce qui entoure ce projet et ce qui en a découlé. Les erreurs et les impasses y sont décrites au même titre que les réussites, parce que ce sont elles qui coûtent du temps.

> **Avertissement.** Tout ceci suppose un appareil rooté et modifie le démarrage d'Android. Une erreur peut bloquer le téléphone (c'est arrivé une fois, voir [docs/09-depannage.md](docs/09-depannage.md)).
> Sauvegardez avant d'essayer, ne reproduisez rien à l'aveugle, et lisez [docs/10-securite-et-publication.md](docs/10-securite-et-publication.md) avant de publier quoi que ce soit.

## Architecture

```
                         Réseau (WireGuard / Wi-Fi maison)
   PC (Moonlight, ssh, navigateur) ───────────────────────────────┐
                                                                  │
┌─────────────────────────── Téléphone (Android 13, root Magisk) ─▼──────────────────────────┐
│                                                                                              │
│  Android (bionic)                          Chroot Debian 13 (chroot-distro)                  │
│  ├─ Magisk service.d  ──démarre──►         ├─ sshd (accès root)                              │
│  │    10-chroot-ssh.sh  (chroot, VNC, …)   ├─ WireGuard wg0                                  │
│  │    20-vnc-fw.sh      (pare-feu)         ├─ TurboVNC :1  +  bureau MATE                    │
│  │    30-wifi-lowlatency.sh                ├─ Sunshine + encodeur V4L2  ──DMABUF──► /dev/video33│
│  │    40-llm.sh         (IA)               ├─ tableau de bord Flask :8090  (/resume, /api)   │
│  │    50-power-profile.sh (gouverneurs)    ├─ Open WebUI :3000, code-server :8080            │
│  ├─ virgl_test_server (Termux) ─ GPU       └─ Plank, rofi, fenêtre de résumé (Chromium)      │
│  ├─ llama-server NPU :8081 / Olala :8082                                                     │
│  └─ app « Pilote » (WebView du tableau de bord + boutons root via phone-ctl.sh)              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Deux mondes cohabitent et se parlent grâce au root :
- le **chroot** (Debian, glibc) : bureau, Sunshine, tableau de bord, Open WebUI ;
- le côté **Android** (bionic) : tout ce qui touche au matériel (NPU, GPU, gouverneurs CPU) et l'application Pilote.
  Le chroot l'atteint avec `nsenter -t 1 -m -- <commande>` (voir [docs/02](docs/02-acces-ssh-et-controle.md)).

## Sommaire

| Guide | Contenu |
|---|---|
| **[12 — Sunshine et l'encodeur matériel](docs/12-sunshine-encodeur-materiel.md)** | **Le projet phare** : POC, encodeur V4L2, intégration, compilation, mesures |
| [13 — Démarrage du projet : accès, réseau, premier bureau](docs/13-demarrage-du-projet-acces-reseau.md) | SSH par clé, Wi-Fi, WireGuard et ses pannes, code-server, MATE + TurboVNC + VirGL, optimisation |
| [01 — Base : root, chroot, démarrage](docs/01-base-root-chroot.md) | Magisk, chroot-distro, séquence de boot, interrupteurs d'arrêt, le piège de `service.d` |
| [02 — Accès SSH et contrôle du téléphone](docs/02-acces-ssh-et-controle.md) | Comment on pilote le téléphone depuis un PC, `nsenter`, captures, alternatives sans root |
| [03 — Bureau distant : VNC, Sunshine, VirGL](docs/03-bureau-distant.md) | TurboVNC, pare-feu, rappel sur l'encodeur, Wi-Fi faible latence, changements de résolution |
| [04 — Profil de puissance](docs/04-profil-puissance.md) | Démon de gouverneurs, plafonds, garde-fou thermique, applications gloutonnes |
| [05 — IA locale](docs/05-ia-locale.md) | Ollama, Open WebUI, llama.cpp sur le NPU, Olala sur le CPU, mesures |
| [06 — Tableau de bord](docs/06-tableau-de-bord.md) | L'application Flask, comment chaque mesure est calculée, les pièges de mesure |
| [07 — Application Pilote](docs/07-application-pilote.md) | WebView + pont JavaScript, `phone-ctl.sh`, compilation sur le téléphone |
| [08 — Bureau MATE](docs/08-bureau-mate.md) | Habillage, Plank, transparence, fenêtre de résumé, ce qui n'a pas marché |
| [09 — Dépannage et pièges](docs/09-depannage.md) | Tableau des erreurs rencontrées et de leurs corrections |
| [10 — Sécurité et publication](docs/10-securite-et-publication.md) | Ce qu'il ne faut pas publier, vérifications avant `git push`, licences |
| [11 — Chronologie](docs/11-chronologie.md) | Ce qui a été fait, heure par heure, du 19 au 22 novembre 2026 |

## Contenu du dépôt

```
docs/                       guides (ce que vous lisez)
src/sunshine/
  poc/                      preuves de concept : captest.c, vidc-enc-poc.c, vidc-screen-poc.c
  integration/              v4l2_encode.cpp/.h, patch contre Sunshine, patch_sunshine.py, correctif GOP
  support/                  sysvshm-emu.c, sunshine-input-bridge.py, sunshine-resize.sh, configuration
src/dashboard/              app.py : tableau de bord Flask (page /resume + API /api/stats)
src/pilote/                 application Android : MainActivity.java, manifeste, application Open WebUI (webui/)
src/phone/                  scripts côté Android : service.d/, phone-ctl.sh, vnc-fw.sh
src/phone/llm/              scripts de démarrage IA, du tableau de bord, de compilation des APK
src/phone/chroot/           scripts de démarrage dans le chroot (VNC, Sunshine, PulseAudio, code-server, WireGuard)
src/desktop/                habillage du bureau : setup-desktop.sh, plank-watch.sh, resume-window.sh
```

## Ce qui n'est pas dans le dépôt

- Les **secrets** : clés WireGuard et SSH, certificats et mots de passe VNC, identifiants et appairages de Sunshine, `webui-secret`, keystore de signature de l'APK. Voir [docs/10](docs/10-securite-et-publication.md).
- Les **binaires et modèles** : llama.cpp/NDK, modèles GGUF (Qwen2.5-3B Q4_0, Olala 7A1B Q5_K_M), Sunshine compilé, TurboVNC, Ollama, et le code de Sunshine lui-même (seuls les fichiers ajoutés et le diff sont fournis).
- `libsysvshm-emu.so` compilé : la source est fournie (`src/sunshine/support/sysvshm-emu.c`).

## Adapter à votre cas

Les adresses du dépôt sont des exemples : `192.168.1.0/24` (réseau local), `10.0.0.0/24` (WireGuard), `<IP_TEL>` dans les guides.
Les numéros de coeurs (`0-2` petits, `3-6` moyens, `7` gros), les plafonds de fréquence, `/dev/video33` et les chemins d'énergie sont ceux d'un Snapdragon 8 Gen 2 sous un noyau 5.15 d'OnePlus :
ils sont à réviser pour un autre SoC.
