# Cluster Security — Final (2026-10-15)

**Overall: PARTIAL.** No critical/high findings found; several medium issues fixed live or fixed in dev awaiting deploy.

## Verified live
- Controller API: unauthenticated worker routes 401 (register 422); UI 302→login; bogus bearer 401 — on 127.0.0.1 and Tailscale HTTPS.
- Port 8000 bound to 127.0.0.1 only; external access only via Tailscale Serve (HTTPS, Let's Encrypt, TLS 1.3, valid to Jan 2027).
- Worker identity: two distinct tokens, bound to `qwen-vps` and `qwen-laptop`, unrevoked, each used in the last seconds.
- Sandbox (both Linux nodes, live): launcher rejects `/etc`, prefix-collision workspace, `--allow-network`; DynamicUser ephemeral UID; writes to `/etc`,`/root`,`/usr` blocked; outbound network blocked; artifact returned; workspace removed. Runner binary SHA identical on both; sudoers restricted to the runner.
- No unrestricted fallback: VPS `QC_ALLOW_SUBPROCESS_FALLBACK=0`; laptop now explicitly `0` (was implicit).
- Laptop Ollama bound to 127.0.0.1:11434 (not exposed; Windows→laptop:11434 unreachable).
- Secret scan: no admin/laptop password in tracked files or git history; Windows service logs clean (3 files). One tracked test file matches a generic bearer pattern (test fixture, not a live token). Note: after this scan, a repo script containing a password fragment was excluded from commit.

## Fixed
- Laptop `worker.env` retained a consumed enrollment token → removed (backup 0600 in `/var/backups`).
- Dashboard falsely advertised unreachable laptop inference backends as ready → corrected.

## Outstanding risks
| Risk | Sev | Note |
|---|---|---|
| Scheduler RAM check fail-open (prod) | Medium | Fixed in dev; unsafe resource placement until deployed |
| "online" never decays (prod) | Medium | Operators may trust a dead worker; fixed in dev |
| No supported pause/drain | Medium | Only service stop or token revoke |
| VPS public 4000/tcp (unrelated app), UFW 22/80/443/4000 any | Low–Med | Not touched (could disconnect admin); review |
| Unknown Windows listener 0.0.0.0:8181 and other non-Qwen listeners | Low | Not Qwen; review |
| Laptop SSH password known to chat history | Medium | I did not change SSH/keys; **rotate the `emceemcee` password and the controller admin password**, both appeared in chat. Key auth already works |
| Tailscale ACL/grants not inspected (no admin console access) | Unknown | Not verified |
| Worker records lack Tailscale IP/status | Low | cosmetic/inventory |
| Class B script approval auto-granted for queued tasks | Low | design note |
| Prod tree drifted from git | Medium | reconcile on deploy |
| Dead laptop battery | Low | power loss = hard shutdown |
