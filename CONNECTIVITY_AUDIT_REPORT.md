# Qwen Collective — Comprehensive Connectivity Audit Report

## 1. Executive Summary

This report documents the end-to-end network connectivity audit conducted between the Windows production controller (`desktop-44h6qvp`) and the Linux execution node (`qwen-vps`), along with tailnet isolation verification and endpoint security auditing.

All live tests were executed on production infrastructure without modifying production access control policies or service states.

---

## 2. Infrastructure Identity & Addressing

| Node Identifier | Operating System | Tailscale IP | Public IP | Tailscale DNS FQDN | Role |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`desktop-44h6qvp`** | Windows 11 (build 26100) | `100.122.228.19` | Dynamic Residential | `desktop-44h6qvp.tailb5d90b.ts.net` | Central Controller & API Hub |
| **`qwen-vps`** | Ubuntu 24.04.3 LTS | `100.71.194.68` | `172.233.140.74` | `qwen-vps.tailb5d90b.ts.net` | Execution Worker & Sandbox Node |
| **`hvm-chicago`** | Linux | `100.97.174.81` | Dynamic Cloud | `hvm-chicago.tailb5d90b.ts.net` | Auxiliary Tailnet Node (Distinct VM) |
| **`office-pi`** | Linux (Raspberry Pi) | `100.104.134.43` | LAN Local | `office-pi.tailb5d90b.ts.net` | Auxiliary Infrastructure |
| **`msi`** | Windows | `100.109.111.79` | Dynamic | `msi.tailb5d90b.ts.net` | Workstation (Offline) |

> [!IMPORTANT]
> `hvm-chicago` (`100.97.174.81`) and `qwen-vps` (`100.71.194.68`) are verified as completely separate machines. No configuration, keys, or authorization rules are shared between them.

---

## 3. Bidirectional Connectivity Verification

### 3.1 Windows Controller → Linux VPS (`qwen-vps`)

- **Tailscale Ping Test**:
  - Command: `tailscale ping --c 3 100.71.194.68`
  - Result: `pong from qwen-vps (100.71.194.68) via [2a01:7e03::2000:7ff:fe8d:bbd]:34877 in 13ms`
  - Latency: ~13ms round trip. Direct WireGuard peer-to-peer connection established over IPv6 transport.
- **SSH Connectivity**:
  - Tailscale endpoint `100.71.194.68:22`: Accepts connections. Advertised methods: `publickey,password`.
  - Public endpoint `172.233.140.74:22`: Accepts connections. Advertised methods: `publickey,password`.
- **Key Expiration & Health**:
  - Both nodes are active on tailnet `tailb5d90b.ts.net`.
  - No DERP relay required; direct encrypted path active.

### 3.2 Linux VPS → Windows Controller (`desktop-44h6qvp`)

- **Tailscale Serve Configuration**:
  - Reverse Proxy: Tailscale Serve proxies inbound encrypted traffic on `https://desktop-44h6qvp.tailb5d90b.ts.net` to local backend `http://127.0.0.1:8000`.
  - Access Scope: **Tailnet only** (`tailnet only` mode active). The endpoint is completely inaccessible from the public internet.
- **TLS Certificate Validation**:
  - Valid Let's Encrypt / Tailscale TLS certificate provisioned for `desktop-44h6qvp.tailb5d90b.ts.net`.
  - HTTPS requests validate with standard system CA certificates without `--insecure` or TLS bypasses.
- **HTTP/FastAPI Endpoint Health**:
  - Live HTTPS GET `https://desktop-44h6qvp.tailb5d90b.ts.net/health`:
    - Status: `404 Not Found` (Standard FastAPI routing behavior; root application mounts `/` web UI and `/api/v1` routes).
  - Live HTTPS POST `https://desktop-44h6qvp.tailb5d90b.ts.net/api/v1/worker/register` (unauthenticated):
    - Status: `401 Unauthorized`
    - Response: `{"detail":"Worker enrollment authorization required. Please provide a valid single-use enrollment token via the 'X-Enrollment-Token' header or authenticate with an administrator session."}`
  - Observation: Protocol negotiation, encryption, DNS resolution, and security middleware are fully operational.

---

## 4. Public Network Exposure Audit

### 4.1 Windows Controller Exposure

- **FastAPI Listener**:
  - Bound address: `127.0.0.1:8000` (IPv4 loopback only).
  - External exposure: None on physical network adapters.
- **Port 443 Listener**:
  - Bound address: `100.122.228.19:443` and `[fd7a:115c:a1e0::b036:e414]:443` (Tailscale network interface only).
  - Windows Firewall: No inbound public rules open for port 8000 or 443 on public/domain profiles.
- **Public Surface**: 0 listening Qwen Collective ports exposed to the public internet.

### 4.2 Linux VPS Exposure

- **Public Port 22 (SSH)**:
  - Accessible on `172.233.140.74:22`.
  - Accepts password authentication for `root`.
- **Public Port 80 / 443**: Not listening.
- **Daemon Inbound Ports**:
  - The worker daemon (`vps_agent`) operates purely as an **outbound client**. It requires 0 listening ports on either public or tailnet interfaces.
- **Sandbox Exposure**:
  - Sandbox executions are isolated via `systemd-run -p IPAddressDeny=any`. No ingress or egress traffic is permitted inside the execution jail.

---

## 5. Tailscale Access Controls & Routing

- **Subnet Routers**: None enabled.
- **Exit Nodes**: None advertised or consumed.
- **Tailnet Isolation**: Windows controller only accepts traffic from authenticated tailnet peers.
- **Impersonation Defenses**: Tailscale cryptographically authenticates device identities using node keys. Tailscale Serve terminates TLS with automated certificates tied to the tailnet node name.
