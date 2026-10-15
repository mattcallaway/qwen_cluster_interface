# Remaining Gaps and Roadmap

## P0 — needs your approval (blocks D/E and fixes F/G)
1. Deploy audit branch to controller (WinSW stop/start, admin rights needed), reconcile drifted prod tree. Plan in `THREE_NODE_ACCEPTANCE_TEST.md`.
2. Deploy updated `vps_agent/` to laptop and VPS (inference handler, no behaviour change otherwise).
3. Re-run Tests D, E, F4, G. Expected: genuine Qwen/Llama responses via controller route; F4 only laptop; dead worker `offline` ≤60 s.
4. Rotate exposed credentials (admin password, laptop `emceemcee` password).

## P1
- Supported worker pause/drain (`is_enabled` honored by lease endpoint).
- Tailscale IP/status reported by workers; fix laptop hostname `qwen-laptopt`.
- Exact model matching (replace substring match); expose `required_model` as first-class task field.
- Controller-side validation of worker-reported RAM; periodic stale-lease/offline sweeper that persists status (not only display overlay).
- Inference routing from controller to laptop without opening Ollama: worker-mediated (already designed via the handler); register real backend rows only after Test D passes.
- Review VPS UFW port 4000 and Windows listener 8181.
- Inspect Tailscale ACLs/grants in admin console.

## P2
- Automated nightly DB backup + restore drill; log rotation.
- Persistent cold-load mitigation on laptop (HDD-class disk): `KEEP_ALIVE=15m` applied; consider SSD.
- Upgrade VPS Tailscale (1.102.3 vs 1.104.1); UPS or battery replacement for laptop.
- Add scheduled acceptance smoke test (`accept.py`) as a CI-style job.
- Proper Class A real-sandbox execution (currently "simulated verification").
