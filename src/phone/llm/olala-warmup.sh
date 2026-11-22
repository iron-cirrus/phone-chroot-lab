#!/bin/bash
# Prechauffe Olala apres son demarrage : charge les poids en memoire et met le preambule du modele en cache
for i in $(seq 1 90); do curl -sf -o /dev/null http://127.0.0.1:8082/health && break; sleep 2; done
curl -s -m 300 -o /dev/null http://127.0.0.1:8082/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"olala-7a1b","messages":[{"role":"user","content":"Bonjour"}],"max_tokens":1}'
echo "$(date '+%F %T') olala prechauffe"
