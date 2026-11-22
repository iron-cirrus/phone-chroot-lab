#!/system/bin/sh
# llama-server sur le NPU Hexagon (cote Android, bionic). Ecoute locale 127.0.0.1:8081, API compatible OpenAI.
A=/data/local/chroot-distro/debian/root/llm
B=$A/llama-hex/llama-b12561
pidof llama-server >/dev/null 2>&1 && exit 0
export LD_LIBRARY_PATH=$B/lib:/vendor/lib64:/system/lib64 ADSP_LIBRARY_PATH=$B/lib
cd $B
setsid nohup ./bin/llama-server -m $A/gguf/Qwen2.5-3B-Instruct-Q4_0.gguf --device HTP0 -ngl 99 -c 8192 -np 1 --jinja --threads 2 \
  --host 127.0.0.1 --port 8081 --alias qwen2.5-3b-npu >/data/local/tmp/llama-npu.log 2>&1 &
