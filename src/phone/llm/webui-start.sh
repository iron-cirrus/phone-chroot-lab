#!/bin/bash
# Open WebUI sur 127.0.0.1:3000 (local seulement), relie a Ollama, sur les petits coeurs 0-2.
pgrep -f 'webui-venv/bin/open-webui' >/dev/null && exit 0
export HOME=/root DATA_DIR=/root/llm/webui-data HF_HOME=/root/llm/hf-cache
export OLLAMA_BASE_URL=http://127.0.0.1:11434 ENABLE_OPENAI_API=true
export WEBUI_SECRET_KEY="$(cat /root/llm/webui-secret)"
export SCARF_NO_ANALYTICS=true DO_NOT_TRACK=true ANONYMIZED_TELEMETRY=false
cd /root/llm
setsid nohup nice -n 15 taskset -c 3-7 /root/llm/webui-venv/bin/open-webui serve --host 127.0.0.1 --port 3000 >/var/log/open-webui.log 2>&1 &
