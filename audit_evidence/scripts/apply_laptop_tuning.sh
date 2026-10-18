set -e
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=/var/backups/qc-audit-$TS
sudo mkdir -p $BK
sudo systemctl cat ollama > /tmp/ollama.unit.txt 2>&1 || true
sudo cp /tmp/ollama.unit.txt $BK/ollama.unit.before.txt
systemctl list-unit-files 'sleep.target' 'suspend.target' 'hibernate.target' 'hybrid-sleep.target' --no-legend | sudo tee $BK/sleep.before.txt >/dev/null
sudo mkdir -p /etc/systemd/system/ollama.service.d /etc/systemd/logind.conf.d
[ -f /etc/systemd/system/ollama.service.d/qc-tuning.conf ] && sudo cp /etc/systemd/system/ollama.service.d/qc-tuning.conf $BK/ || true
sudo tee /etc/systemd/system/ollama.service.d/qc-tuning.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=15m"
Environment="OLLAMA_CONTEXT_LENGTH=4096"
EOF
sudo tee /etc/systemd/logind.conf.d/qc-nosuspend.conf >/dev/null <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1
sudo systemctl daemon-reload
sudo systemctl kill -s HUP systemd-logind
sudo tee $BK/ROLLBACK.sh >/dev/null <<'EOF'
sudo rm -f /etc/systemd/system/ollama.service.d/qc-tuning.conf /etc/systemd/logind.conf.d/qc-nosuspend.conf
sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
sudo systemctl daemon-reload; sudo systemctl restart ollama; sudo systemctl kill -s HUP systemd-logind
EOF
echo "backup dir: $BK"
# restart ollama only if idle (no loaded model)
LOADED=$(curl -s 127.0.0.1:11434/api/ps | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("models",[])))')
echo "loaded models: $LOADED"
if [ "$LOADED" = "0" ]; then sudo systemctl restart ollama; sleep 4; fi
systemctl show ollama -p Environment --no-pager
systemctl is-active ollama qwen-worker
systemctl list-unit-files 'suspend.target' --no-legend
curl -s 127.0.0.1:11434/api/tags | python3 -c 'import sys,json;print([m["name"] for m in json.load(sys.stdin)["models"]])'
