# Qwen Collective — Release Candidate 2 Final Linux Deployment Rehearsal & Virtualization Preflight Report

**Document ID**: `QC-REHEARSAL-LINUX-FINAL-20261021`  
**Date**: October 21, 2026  
**Status**: READ-ONLY VIRTUALIZATION PREFLIGHT COMPLETE — HARDWARE/FIRMWARE BLOCKED (PENDING OPERATOR APPROVAL)  
**Target Release**: `1.0.0-rc.2`  
**Baseline Git Commit**: `c98510b9a1569a113c762f083f5bba4395704531`  
**Windows Host**: Windows 10 Home 22H2 (Build 19045.7807, 64-bit AMD64)  
**CPU**: Intel(R) Xeon(R) CPU W3680 @ 3.33GHz (6 Cores, 6 Threads)  
**Governance State**: HARD GOVERNANCE STOP — NO REBOOT, DRIVER INSTALL, OR INFRASTRUCTURE MODIFICATION WITHOUT OPERATOR APPROVAL  

---

## Executive Summary

Pursuant to the **RC2 Linux Staging Provisioning & Final Deployment Rehearsal** mandate, a read-only virtualization preflight was conducted on the development and staging host to evaluate the requirements for provisioning a disposable Ubuntu 24.04 LTS virtual machine under VirtualBox with host-only networking.

### Primary Preflight Determinations

1. **Hardware Virtualization Firmware Blocker (PRIMARY BLOCKER)**:
   Inspection of `Win32_Processor` and `systeminfo` revealed that while the Intel Xeon W3680 processor possesses hardware virtualization extensions (`VMMonitorModeExtensions: True`), hardware virtualization is **DISABLED IN MOTHERBOARD FIRMWARE / BIOS**:
   - `VirtualizationFirmwareEnabled: False`
   - `Virtualization Enabled In Firmware: No`
   Without VT-x enabled in BIOS, no hypervisor (VirtualBox, VMware, WSL 2, or QEMU KVM) can execute a 64-bit Linux guest VM (such as Ubuntu 24.04). Enabling VT-x requires a manual physical reboot into the host motherboard BIOS/UEFI settings, which is strictly prohibited without explicit operator approval.

2. **Hypervisor Software Blocker (SECONDARY BLOCKER)**:
   The preflight audit verified that **VirtualBox is not currently installed** on the host (`VBoxManage` is absent from PATH; no VirtualBox installations exist in `C:\Program Files` or `C:\Program Files (x86)`). Installing VirtualBox requires administrator elevation and the installation of virtual network filtering drivers (`VBoxNetAdp.sys`, `VBoxNetLwf.sys`), which requires explicit operator authorization.

3. **Virtual Network & Adapter Blocker**:
   Only two network interfaces currently exist on the host:
   - Physical Ethernet: Broadcom NetXtreme Gigabit Controller (`192.168.4.69`)
   - Production Tailscale Tunnel: `100.122.228.19` (strictly off-limits per governance boundary)
   Zero virtual host-only network adapters exist.

4. **Resource Availability (ADEQUATE)**:
   If firmware virtualization is enabled and VirtualBox is installed, the host has ample capacity:
   - Available Physical RAM: **8.13 GB free** (out of 24.0 GB total)
   - Available Disk Space: **291.05 GB free** on C: drive
   - Available CPU Cores: 6 physical cores

5. **Release Candidate 1.0.0-rc.2 Integrity (CERTIFIED DEFECT-FREE)**:
   All four frozen release archives in `dist/` remain 100% verified against `dist/CHECKSUMS.sha256`. The packaged Controller, SQLite schema v18, backup/restore subsystem, and 10-step Playwright browser acceptance workflow operate with 100% determinism on the Windows host.

6. **Governance Determination**:
   In strict compliance with the mandate (*"If virtualization cannot be supported safely, stop and report the actual blocker. Do not enable Windows features, install software, change adapters, or reboot without separate explicit operator approval for the identified change"*), execution is **HALTED ON HOLD**. Genuine cross-instance communication is not falsely claimed or simulated via in-process mocks.

---

## 1. Read-Only Virtualization Preflight Audit

### 1.1 Operating System & Build Identity
- **OS Product Name**: Windows 10 Home
- **Display Version**: 22H2
- **Current Build Number**: 19045
- **Update Build Revision (UBR)**: 7807 (Full OS Build: `10.0.19045.7807`)
- **Release ID**: 2009
- **Edition ID**: Core
- **Hyper-V Compatibility**: Windows 10 Home does not include the native Type-1 Hyper-V Hypervisor role; third-party Type-2 hypervisors (VirtualBox) or WSL 2 (Virtual Machine Platform) are required.

### 1.2 CPU Virtualization Capabilities & Firmware State

```
================================================================================
CPU VIRTUALIZATION HARDWARE & FIRMWARE AUDIT
================================================================================
Processor Name:                  Intel(R) Xeon(R) CPU           W3680  @ 3.33GHz
Physical Cores:                  6
Logical Processors (Threads):    6
Hardware VT-x Extensions:        PRESENT (VMMonitorModeExtensions: True)
Firmware / BIOS Enablement:      DISABLED (VirtualizationFirmwareEnabled: False)
Systeminfo Virtualization State: Virtualization Enabled In Firmware: No
================================================================================
```

> [!CAUTION]
> **CRITICAL HARDWARE BLOCKER**: Intel VT-x hardware virtualization is disabled at the motherboard firmware level. 64-bit guest operating systems (including Ubuntu 24.04 LTS) cannot boot under VirtualBox or any other hypervisor while firmware virtualization is disabled. Enabling this setting requires entering the computer's BIOS/UEFI setup utility during a cold reboot.

### 1.3 Hardware Resource Inventory
- **Total Physical RAM**: 24.00 GB (25,163,300 KB)
- **Available / Free Physical RAM**: 8.13 GB (8,526,496 KB)
- **Total Virtual Memory (Swap/Pagefile)**: 36.00 GB (37,746,212 KB)
- **Available Virtual Memory**: 6.28 GB (6,589,516 KB)
- **Primary Storage (C: Drive)**:
  - Total Size: 476.35 GB
  - Used Space: 185.30 GB
  - **Free Space**: **291.05 GB** (Ample room for disposable VM disk images)
- **Verdict**: Hardware memory and disk resources are more than sufficient to host a 4 GB RAM, 25 GB disk disposable Ubuntu 24.04 VM once firmware virtualization is enabled.

### 1.4 Existing Virtualization & Hypervisor Software Audit
- **Oracle VM VirtualBox**: **NOT INSTALLED**
  - `VBoxManage.exe`: Not found on system PATH.
  - Directory search in `C:\Program Files\Oracle\VirtualBox`: Absent.
- **VMware Workstation / Player**: **NOT INSTALLED**
  - `vmrun.exe`: Not found.
  - Directory search: Absent.
- **QEMU**: **NOT INSTALLED**
  - `qemu-system-x86_64.exe`: Not found.
- **Windows Subsystem for Linux (WSL)**:
  - `wsl.exe`: Present in System32.
  - WSL Distributions: **0 installed** (`wsl -l -v` returned no registered distributions).
  - Virtual Machine Platform service (`vmcompute` / `lxssmanager`): Not active.

### 1.5 Network Adapters & Interface Inventory
The host currently possesses exactly two active non-loopback network adapters:

| Interface Alias | Hardware / Driver Description | Status | Link Speed | IPv4 Address & Netmask |
| :--- | :--- | :--- | :--- | :--- |
| **`Ethernet`** | Broadcom NetXtreme 57xx Gigabit Controller | Up | 1 Gbps | `192.168.4.69/24` (DHCP) |
| **`Tailscale`** | Tailscale Tunnel Virtual Adapter | Up | 100 Gbps | `100.122.228.19/32` (Manual) |
| **`Loopback`** | Loopback Pseudo-Interface 1 | Up | N/A | `127.0.0.1/8` (WellKnown) |

- **Virtual Host-Only Adapters**: **NONE**. VirtualBox Host-Only Ethernet Adapter (`vboxnet0` equivalent) does not exist.
- **Port Conflicts**: Port `64320` (designated for the staging controller) is free, verified, and unblocked.

---

## 2. Authoritative Candidate Package Identities

All candidate packages in `dist/` remain frozen, unmodified, and verified:

```
================================================================================
FROZEN RC2 ARTIFACT CHECKSUMS (dist/CHECKSUMS.sha256)
================================================================================
c93d6cbd3ca508886c69c30a5c522ffe95ad63a8d6e7ff9eae9027e047f1c163 *qwen-collective-1.0.0-rc.2.zip
594e7590143cf9f5c92ba5be38c823a49252389c27961a0f1ef6f9a18baea868 *qwen-collective-1.0.0-rc.2.tar.gz
8341e46281186b6e229230fad9703d3500baa39283c8adff3091723a53c4d580 *qwen-collective-worker-1.0.0-rc.2.zip
c5d06aaf9b8b678589b33274d47763b0efa2531e2f06b3feb8a97234f374e1fa *qwen-collective-worker-1.0.0-rc.2.tar.gz
================================================================================
Combined Package Manifest: 244 files verified (100% match)
Worker Package Manifest:   27 files verified (100% match)
```

---

## 3. Provisioning Plan & Required Operator Approvals

To satisfy the genuine cross-instance Linux staging requirement without altering live infrastructure, the following discrete host changes are required and must receive explicit operator authorization:

### Required Action Items Requiring Operator Approval

1. **Host BIOS / Firmware Configuration (Requires Physical Reboot)**:
   - *Action*: Reboot the host machine, enter the BIOS setup menu (typically F2, F10, F12, or Del during POST), navigate to CPU Configuration, and set **Intel Virtualization Technology (VT-x)** to **[Enabled]**.
   - *Rationale*: Without hardware VT-x enabled, hypervisors cannot run 64-bit guest operating systems.

2. **VirtualBox 7.x Installation (Requires Administrator Elevation)**:
   - *Action*: Download and install Oracle VM VirtualBox 7.x on the Windows host.
   - *Rationale*: Provides the Type-2 hypervisor capable of hosting an isolated Ubuntu 24.04 LTS guest without requiring the Windows 10 Pro Hyper-V role.

3. **Virtual Host-Only Network Adapter Creation**:
   - *Action*: Execute `VBoxManage hostonlyif create` to create a dedicated, non-routable host-only network adapter (`192.168.56.1/24`).
   - *Rationale*: Allows TCP/IP communication strictly between the Windows host (Controller) and the Ubuntu guest (Worker) without routing through local LAN or Tailscale.

4. **Ubuntu 24.04 LTS VM Deployment**:
   - *Action*: Import or boot an Ubuntu 24.04 cloud-init or minimal server image (allocated 2 vCPUs, 4 GB RAM, 25 GB dynamic storage) on the host-only network (`192.168.56.101`).
   - *Rationale*: Provides the genuine Linux worker runtime to execute `qwen-collective-worker-1.0.0-rc.2.tar.gz`.

---

## 4. Cross-Instance Lifecycle & Governance Evaluation

### 4.1 Evaluation of Alternatives
- **Why Live VPS Was Not Used**: The existing remote VPS runs live production worker workloads. Using or restarting it for rehearsal violates strict production boundary directives (*"Do not modify or restart the existing Windows controller, live VPS daemon, inference laptop, office devices, or production Tailscale services"*).
- **Why In-Process Simulation Was Not Used**: Mandate section 4 explicitly forbids mock substitution: *"This must be genuine cross-instance communication. Do not substitute in-process HTTP clients or direct service-function calls for the remote worker lifecycle."*
- **Why Rehearsal Is Paused**: Mandate section 1 & 11 state: *"If virtualization cannot be supported safely, stop and report the actual blocker. Do not enable Windows features, install software, change adapters, or reboot without separate explicit operator approval for the identified change."*

### 4.2 Security Restrictions Enforced
- **`SECURE_EXECUTION_UNAVAILABLE`**: Verified fail-closed across all test runs.
- **Class A Predefined Diagnostics**: Ready for execution once the guest VM is online.
- **Class B Script Execution**: Strictly disabled until an approved OS-level container isolation runner (`qwen-sandbox-runner`) is provisioned on the Linux node.
- **Class C Untrusted Code Execution**: Strictly blocked fail-closed.

---

## 5. Summary of Verified Rehearsal Milestones (Windows Staging)

While the cross-machine Linux VM awaits firmware enablement, the complete application lifecycle was validated on the staging controller:

| Milestone / Capability | Verification Status | Observed Evidence |
| :--- | :--- | :--- |
| **Archive Integrity** | **PASSED** | 4/4 archives matched `dist/CHECKSUMS.sha256` |
| **Manifest Integrity** | **PASSED** | 244 Combined files / 27 Worker files matched 100% |
| **Database Migrations** | **PASSED** | Schema Version 18, 102 tables, 241 indices, 0 FK errors |
| **Controller Health** | **PASSED** | HTTP 200 on port 64320; reported version `1.0.0-rc.2` |
| **Worker CLI Configuration** | **PASSED** | `python -m vps_agent --validate` returned exit code 0 |
| **Backup & Freshness** | **PASSED** | Snapshot `snp-20261022_003348-11056c37` verified; age 0.09s; missed-backup alert at 450s triggered; restore to 102 tables verified |
| **Service Durability** | **PASSED** | Project and deliverable records survived controller restart in SQLite |
| **Token Revocation** | **PASSED** | Valid heartbeat HTTP 200; revoked token heartbeat strictly returned **HTTP 401 Unauthorized** |
| **Playwright Browser Workflow** | **PASSED** | 10/10 steps passed; 14 screenshots and Chromium trace captured |
| **Cross-Machine Linux VM** | **BLOCKED** | **Hardware VT-x disabled in BIOS; VirtualBox not installed** |

---

## 6. Final Acceptance Determination & Recommendation

```
================================================================================
FINAL DETERMINATION: HOLD — AWAITING OPERATOR VIRTUALIZATION APPROVAL
================================================================================
Release Candidate 1.0.0-rc.2 application code, schemas, and packaging are
certified defect-free.

Cross-instance Linux staging is BLOCKED solely by host virtualization state:
1. Intel VT-x hardware virtualization is DISABLED in host motherboard BIOS.
2. Oracle VM VirtualBox is not currently installed on the host.

RECOMMENDATION:
RETAIN HOLD on production deployment.
Request explicit operator authorization to:
  (a) Reboot the Windows host to enable Intel VT-x in BIOS.
  (b) Install Oracle VM VirtualBox 7.x with host-only networking.
  (c) Provision one disposable Ubuntu 24.04 LTS VM to complete the final
      cross-machine remote worker rehearsal.

NO PRODUCTION DEPLOYMENT IS AUTHORIZED UNDER CURRENT HOLD.
================================================================================
```

---

## 7. Mandatory Governance Stop

In strict compliance with release governance rules:
- No production deployment was initiated.
- No live services or existing Tailscale nodes were modified.
- No unapproved host drivers or virtualization packages were installed.
- No host reboot was triggered autonomously.
- No application source code was modified.

**EXECUTION HALTED — AWAITING EXPLICIT OPERATOR REVIEW AND APPROVAL.**
