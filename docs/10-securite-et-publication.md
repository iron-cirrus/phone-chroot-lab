# 10 — Sécurité et publication

Ce dossier a été préparé pour être publié sur GitHub. **Relisez-le vous-même avant `git push`** : un dépôt public est indexé et copié, même effacé ensuite.

## Ce qui a été retiré ou généralisé dans ce dépôt

- Adresses : le réseau local d'origine a été remplacé par `192.168.1.0/24` (dans `src/phone/vnc-fw.sh`), les guides utilisent `<IP_TEL>`.
  Les plages `10.0.0.0/24` (WireGuard) et privées en général ne sont pas des secrets, mais ce sont des détails de votre réseau : adaptez-les.
- Mot de passe du keystore : `build-pilote.sh` et `build-webui.sh` exigent maintenant la variable `KS_PASS` ; **aucun mot de passe n'est écrit dans les fichiers**.
- Aucune adresse électronique, nom de compte ou identifiant personnel.

## Ce qu'il ne faut JAMAIS publier

| Élément | Où il se trouve sur le téléphone |
|---|---|
| Clés privées WireGuard et SSH, configuration `wg0` | `/etc/wireguard/`, `/root/.ssh/`, clés de votre PC |
| Mot de passe / certificats VNC (`x509vnc`) | `/root/.vnc/` |
| Identifiants et clés de Sunshine, appairages Moonlight | `/root/.config/sunshine/` |
| `webui-secret`, base d'Open WebUI (`webui.db`, comptes, conversations) | `/root/llm/webui-secret`, `/root/llm/webui-data/` |
| **Keystore de l'APK** (`pilote.jks`) et son mot de passe | `/root/apk-build/` |
| Jeton / mot de passe de code-server | `/root/.config/code-server/` |
| Journaux (`/var/log/*.log`, `/data/local/tmp/*.log`) | peuvent contenir adresses, noms de machines, requêtes |
| Modèles GGUF, binaires (llama.cpp, Sunshine, TurboVNC, Ollama) | gros et soumis à des licences |

`.gitignore` du dépôt : voir le fichier à la racine.

## Vérifications avant publication

```sh
# depuis la racine du dépôt
git grep -nIiE '(password|passwd|secret|token|api[_-]?key|BEGIN [A-Z ]*PRIVATE KEY|PrivateKey)' -- . ':!docs/10-securite-et-publication.md'
git grep -nIE '\b(10|172\.(1[6-9]|2[0-9]|3[01])|192\.168)\.[0-9]+\.[0-9]+' -- .        # adresses privées restantes
git grep -nIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' -- .                        # adresses e-mail
find . -name '*.jks' -o -name '*.keystore' -o -name '*.pem' -o -name '*.key' -o -name 'webui.db' -o -name '*.gguf' -o -name '*.apk'
```
Passer aussi un détecteur de secrets (par exemple `gitleaks detect` ou `trufflehog filesystem .`) et relire `git diff --cached` avant chaque commit.

## Si un secret a déjà été publié
Le révoquer **tout de suite** (nouvelle clé WireGuard, nouveau keystore, nouveaux mots de passe) : réécrire l'historique Git ne suffit pas.

## Risques propres à ce projet
- Un téléphone rooté avec SSH root accessible par WireGuard est une cible de choix : clés fortes, pas de mot de passe SSH, pare-feu (`vnc-fw.sh`) maintenu,
  VNC/Sunshine limités au Wi-Fi maison et au tethering, `127.0.0.1` pour tous les services web internes (Open WebUI, Ollama, code-server, serveurs llama).
- L'aperçu du bureau (`/preview.jpg`) n'est servi **qu'en boucle locale** : ne pas retirer ce contrôle.
- Les réglages de l'application Pilote (`su -mm`, commandes root) donnent le contrôle du téléphone à quiconque peut utiliser l'application : verrouillage de l'écran obligatoire.
- Désactiver Play Protect pour installer l'APK est un affaiblissement à rétablir ensuite.
- Désactiver `RUN_IN_BACKGROUND` d'applications tierces change leur comportement (notifications perdues) : à faire en connaissance de cause.

## Licences
- Ce dépôt n'a pas de licence : **choisissez-en une** (MIT, Apache-2.0, GPL…) avant de publier.
- Composants tiers à respecter si vous redistribuez quoi que ce soit au-delà de ces scripts : Sunshine (GPL-3.0, le correctif `v4l2-gop.patch` est un travail dérivé),
  llama.cpp (MIT), Open WebUI (licence propre), Ollama (MIT), TurboVNC (GPL), WhiteSur/Papirus (GPL), Olala (voir la licence de ses auteurs), Qwen2.5 (voir sa licence).
