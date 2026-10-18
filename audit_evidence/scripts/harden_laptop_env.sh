set -e
F=/etc/qwen-collective/worker.env
TS=$(date -u +%Y%m%dT%H%M%SZ)
sudo cp -p $F /var/backups/qc-audit-20261015T105938Z/worker.env.$TS.bak
sudo chmod 600 /var/backups/qc-audit-20261015T105938Z/worker.env.$TS.bak
echo "keys BEFORE:"; sudo sed -E 's/=.*/=<redacted>/' $F
sudo sed -i '/^QC_ENROLLMENT_TOKEN=/d' $F
for kv in QC_ENV=production QC_ALLOW_SUBPROCESS_FALLBACK=0 QC_MAX_CONCURRENT_JOBS=1 QC_ALLOW_ALT_ISOLATION=0; do
  k=${kv%%=*}
  if sudo grep -q "^$k=" $F; then sudo sed -i "s/^$k=.*/$kv/" $F; else echo "$kv" | sudo tee -a $F >/dev/null; fi
done
echo "keys AFTER:"; sudo sed -E 's/=.*/=<redacted>/' $F | sort
sudo ls -l $F
echo "non-secret values:"; sudo grep -E '^(QC_ENV|QC_ALLOW_SUBPROCESS_FALLBACK|QC_MAX_CONCURRENT_JOBS|QC_ALLOW_ALT_ISOLATION)=' $F
sudo systemctl restart qwen-worker
sleep 8
systemctl is-active qwen-worker
journalctl -u qwen-worker --since '-30 sec' --no-pager | grep -ciE 'traceback|401|403|error' || true
echo "rollback: sudo cp -p /var/backups/qc-audit-20261015T105938Z/worker.env.$TS.bak $F && sudo systemctl restart qwen-worker"
