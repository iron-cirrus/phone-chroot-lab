#!/bin/bash
# Ollama en ecoute locale seulement, modeles dans /root/llm/models, sur les gros coeurs 3-7.
pgrep -x ollama >/dev/null && exit 0
export HOME=/root OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/root/llm/models OLLAMA_KEEP_ALIVE=10m OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1
setsid nohup taskset -c 3-7 /opt/ollama/bin/ollama serve >/var/log/ollama.log 2>&1 &
