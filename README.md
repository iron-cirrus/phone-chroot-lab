# phone-chroot-lab

Un téléphone Android rooté (OnePlus 11, Android 13, Magisk) transformé en petit poste Linux : un chroot Debian 13 avec un bureau MATE
affiché sur un PC via Moonlight (encodeur matériel), une IA locale (CPU et NPU Hexagon), un tableau de bord de télémétrie,
une application Android de pilotage (« Pilote ») et des scripts pour tout démarrer au boot.

Ce dépôt documente **comment tout a été construit et comment le refaire**, avec les scripts et le code utilisés.
Il est issu d'une longue session de mise au point ; les erreurs et les impasses sont documentées au même titre que les réussites,
parce que ce sont elles qui coûtent du temps.

> **Avertissement.** Tout ceci suppose un appareil rooté et modifie le démarrage d'Android. Une erreur peut bloquer le téléphone
> (c'est arrivé une fois, voir [docs/09-depannage.md](docs/09-depannage.md)). Sauvegardez avant d'essayer, ne reproduisez rien à l'aveugle,
> et lisez [docs/10-securite-et-publication.md](docs/10-securite-et-publication.md) avant de publier quoi que ce soit.

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
│  │    30-wifi-lowlatency.sh                ├─ Sunshine (encodeur matériel V4L2)  ──► Moonlight│
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
| [01 — Base : root, chroot, démarrage](docs/01-base-root-chroot.md) | Magisk, chroot-distro, séquence de boot, interrupteurs d'arrêt, le piège de `service.d` |
| [02 — Accès SSH et contrôle du téléphone](docs/02-acces-ssh-et-controle.md) | Comment on pilote le téléphone depuis un PC, `nsenter`, captures, et les alternatives sans root |
| [03 — Bureau distant : VNC, Sunshine, VirGL](docs/03-bureau-distant.md) | TurboVNC, pare-feu, encodeur matériel, GOP, Wi-Fi faible latence, changements de résolution |
| [04 — Profil de puissance](docs/04-profil-puissance.md) | Démon de gouverneurs, plafonds, garde-fou thermique, applications gloutonnes |
| [05 — IA locale](docs/05-ia-locale.md) | Ollama, Open WebUI, llama.cpp sur le NPU, Olala sur le CPU, mesures |
| [06 — Tableau de bord](docs/06-tableau-de-bord.md) | L'application Flask, comment chaque mesure est calculée, les pièges de mesure |
| [07 — Application Pilote](docs/07-application-pilote.md) | WebView + pont JavaScript, `phone-ctl.sh`, compilation sur le téléphone |
| [08 — Bureau MATE](docs/08-bureau-mate.md) | Habillage, Plank, transparence, fenêtre de résumé, ce qui n'a pas marché |
| [09 — Dépannage et pièges](docs/09-depannage.md) | Tableau des erreurs rencontrées et de leurs corrections |
| [10 — Sécurité et publication](docs/10-securite-et-publication.md) | Ce qu'il ne faut pas publier, vérifications avant `git push`, licences |
| [11 — Chronologie](docs/11-chronologie.md) | Ce qui a été fait, dans l'ordre |

## Contenu du dépôt

```
docs/                 guides (ce que vous lisez)
src/dashboard/        app.py : tableau de bord Flask (page /resume + API /api/stats)
src/pilote/           application Android : MainActivity.java, manifeste, application Open WebUI (webui/)
src/phone/            scripts côté Android : service.d/, phone-ctl.sh, vnc-fw.sh
src/phone/llm/        scripts de démarrage IA, du tableau de bord, scripts de compilation des APK
src/phone/chroot/     scripts de démarrage dans le chroot (VNC, Sunshine, code-server, WireGuard)
src/sunshine/         correctif de l'encodeur V4L2 (GOP « infinie »)
src/desktop/          habillage du bureau : setup-desktop.sh, plank-watch.sh, resume-window.sh
```

## Ce qui n'est pas dans le dépôt

- Les **secrets** : clés WireGuard et SSH, certificats et mots de passe VNC, identifiants Sunshine, `webui-secret`, keystore de signature
  de l'APK. Voir [docs/10](docs/10-securite-et-publication.md).
- Les **binaires et modèles** : llama.cpp/NDK, modèles GGUF (Qwen2.5-3B Q4_0, Olala 7A1B Q5_K_M), Sunshine compilé, TurboVNC, Ollama.
- Quelques scripts d'appoint non récupérés : `pulse-start.sh`, `wg-poke.sh`, `sunshine-input-bridge.py`,
  `libsysvshm-emu.so` (émulation de la mémoire partagée System V, indispensable sur ce noyau). Ils sont mentionnés là où ils servent.

## Adapter à votre cas

Les adresses du dépôt sont des exemples : `192.168.1.0/24` (réseau local), `10.0.0.0/24` (WireGuard), `<IP_TEL>` dans les guides.
Les numéros de coeurs (`0-2` petits, `3-6` moyens, `7` gros), les plafonds de fréquence et les chemins d'énergie sont ceux d'un
Snapdragon 8 Gen 2 : ils sont à réviser pour un autre SoC.
