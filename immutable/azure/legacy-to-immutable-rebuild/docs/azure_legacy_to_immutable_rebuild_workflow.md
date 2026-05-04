# Azure Legacy-to-Immutable Rebuild — Operational Documentation

> Companion document to the playbooks under
> `immutable/azure/legacy-to-immutable-rebuild/`. This document describes the
> operational mental model: what the workflow does, why each step exists,
> what can go wrong, and how SamurAI Shield will integrate with it.

---

## 1. Overview

The Legacy-to-Immutable Rebuild workflow takes an existing Azure VM that was
created **manually** (brownfield, not via IaC) and converts it into a
**governed**, **versioned**, **validated** and **IaC-ready** infrastructure
asset, while preserving:

- The **OS managed disk** (and therefore all in-place data and OS state).
- The **NIC** attached to the VM.
- The **Public IP** (provided it is Static).
- The **Private IP** allocated to the NIC.

Orchestration runs on **Ansible Automation Platform** (AAP); the
**SamurAI Shield** application acts as the control plane (UI, Managed Server
registry, workflow history) and never executes any remediation locally.

---

## 2. Flow diagram (textual)

```
┌─────────────────────────────────────────────────────────────────────┐
│  SamurAI Shield :: Managed Server Detail (UI)                       │
│  user clicks "Run Legacy-to-Immutable Rebuild"                      │
└─────────────┬───────────────────────────────────────────────────────┘
              │  POST /api/v2/workflow_job_templates/<id>/launch
              │  body = { extra_vars: { managed_server_id, ... } }
              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Ansible Automation Platform :: Workflow Template                    │
│  "SamurAI :: Legacy-to-Immutable Rebuild (Azure)"                    │
└─────────────┬───────────────────────────────────────────────────────┘
              │
              ▼
   [1] 01_collect_azure_vm_baseline.yml      (read-only)
              │  → baseline/*.json + validation_report.md
              ▼
   [2] 02_create_safety_snapshots.yml        (mutating)
              │  → snapshots/snapshots.json
              ▼
   [3] 03_preserve_network_identity.yml      (read-only, validates Static IP)
              │  → baseline/network_identity.json
              ▼
   [4] 04_rebuild_vm_from_existing_os_disk.yml  (DESTRUCTIVE-GATED)
              │   gates: dry_run, confirm_destructive_action,
              │   snapshots present, Public IP Static
              │   → rebuild/rebuild_result.json
              ▼
   [5] 05_post_rebuild_validation.yml        (read-only)
              │  → validation/post_rebuild_validation.{json,md}
              │  → validation/diff_report.json
              ▼
   [6] 06_generate_terraform_from_vm.yml     (read-only)
              │  → generated-iac/*.tf + tfvars.example
              ▼
   [7] 07_commit_artifacts_to_git.yml        (mutating: Git only)
              │  → push inventory/managed-servers/<id>-<vm>/
              ▼
       Workflow complete
```

---

## 3. Inputs

The workflow receives all parameters as `extra_vars`. SamurAI Shield builds
the payload server-side and forwards it to AAP.

| Variable                       | Required | Source             | Notes                                                                                  |
| ------------------------------ | -------- | ------------------ | -------------------------------------------------------------------------------------- |
| `managed_server_id`            | yes      | SamurAI Shield     | Ties artifacts to the Managed Server record.                                          |
| `cloud_provider`               | yes      | SamurAI Shield     | Must be `azure`.                                                                       |
| `azure_subscription_id`        | yes      | SamurAI Shield     | Resolved from the Vendor Integration / Cloud Account binding.                         |
| `azure_resource_group`         | yes      | SamurAI Shield     |                                                                                        |
| `azure_vm_name`                | yes      | SamurAI Shield     |                                                                                        |
| `artifact_output_dir`          | yes      | SamurAI Shield     | Per-execution path on the AAP execution environment.                                  |
| `target_host`                  | yes      | SamurAI Shield     | Public IP or DNS used by SSH/WinRM facts.                                             |
| `os_connection_type`           | yes      | SamurAI Shield     | `ssh` or `winrm`.                                                                      |
| `ssh_username`                 | conditional | SamurAI Shield  | Required when `os_connection_type=ssh`.                                                |
| `winrm_username`               | conditional | SamurAI Shield  | Required when `os_connection_type=winrm`.                                              |
| `dry_run`                      | optional | UI toggle          | Default `true`. Step 4 only mutates Azure when `dry_run=false`.                       |
| `confirm_destructive_action`   | optional | UI toggle          | Default `false`. Step 4 only mutates Azure when this is `true`.                       |
| `git_repo_url`                 | optional | SamurAI Shield     | If absent, step 7 will fail; previous steps still produce artifacts.                  |
| `git_branch`                   | optional | SamurAI Shield     | Default `main`.                                                                        |

---

## 4. Outputs

All outputs are written under `{{ artifact_output_dir }}` on the AAP
execution environment, and step 7 commits them to Git.

```
artifact_output_dir/
├── baseline/
│   ├── azure_vm_facts.json
│   ├── os_facts.json
│   ├── network_facts.json
│   ├── disk_facts.json
│   ├── security_facts.json
│   ├── network_identity.json
│   └── validation_report.md
├── snapshots/
│   └── snapshots.json
├── rebuild/
│   └── rebuild_result.json
├── validation/
│   ├── post_rebuild_validation.json
│   ├── post_rebuild_validation.md
│   └── diff_report.json
└── generated-iac/
    ├── main.tf
    ├── variables.tf
    ├── outputs.tf
    ├── terraform.tfvars.example
    └── README.md
```

In Git, the same tree appears under
`inventory/managed-servers/<managed_server_id>-<azure_vm_name>/`.

---

## 5. Step-by-step

### Step 1 — Collect baseline (read-only)

- **Azure-side:** uses `az vm show -d`, `az network nic show`,
  `az network public-ip show` and `az vm extension list`.
- **OS-side:** SSH (Linux) or WinRM (Windows). Collects packages, services,
  listening ports, firewall, cron/timers.
- **Hard fail:** missing extra-vars, VM with no NIC.

### Step 2 — Safety snapshots (mutating)

- Snapshots the OS disk and every data disk. Naming:
  `samurai-<vm>-<disk>-<timestamp>` (UTC).
- **Hard fail:** any snapshot creation that returns non-zero, or snapshot
  count not matching disk count. The flow refuses to proceed without a
  complete snapshot set.

### Step 3 — Preserve network identity (read-only, gating)

- **Hard fail:** Public IP is not Static (Dynamic IPs may be released
  during deallocate).
- **Hard fail:** VM has no Public IP attached.
- Persists `network_identity.json` for use by step 4.

### Step 4 — Destructive rebuild (DESTRUCTIVE-GATED)

Gates (evaluated **before** any destructive call):

1. `dry_run` — when `true`, only print the plan and exit.
2. `confirm_destructive_action` — must be exactly `true`. Otherwise the play
   aborts with a clear message.
3. `snapshots/snapshots.json` exists and contains a snapshot with
   `role=os`.
4. `network_identity.json` confirms Static Public IP.

When all gates pass, the play:

- `az vm deallocate`
- re-reads OS disk ID directly from Azure (defensive)
- `az vm delete --yes` (deletes only the VM resource — disk/NIC/IP remain)
- `az vm create --attach-os-disk --nics <existing-nic>`
- `az vm disk attach` for each data disk (best effort, idempotent)
- `az vm start` (defensive — `vm create` normally leaves it running)

Every action is recorded in `rebuild/rebuild_result.json` with timestamp,
return code and the dry-run flag.

### Step 5 — Post-rebuild validation (read-only)

Azure-side checks:

- VM exists and `powerState` includes `running`.
- Public IP unchanged.
- Private IP unchanged.
- NIC ID unchanged.
- OS disk ID unchanged.
- Data-disk count unchanged.

OS-side checks (best effort, `ignore_unreachable: true`):

- SSH/WinRM responds.
- Failed services post-rebuild.
- Listening ports.

Final `overall_status`: `passed` / `partial` / `failed`. The play fails
when `failed`.

### Step 6 — Generate Terraform (read-only)

Renders Jinja templates under `templates/terraform/` into
`generated-iac/`. Output is explicitly marked as a baseline and contains
`# TODO: review` markers next to fields that depend on tenant policy.

### Step 7 — Commit artifacts to Git (mutating: Git only)

- Shallow-clones the repo.
- Copies the artifacts under
  `inventory/managed-servers/<id>-<vm>/`.
- Commits with the message
  `Add legacy Azure VM rebuild baseline for <vm_name>`.
- Pushes.
- Removes the local working copy.

The Git token is loaded from `extra_vars.git_token` or from the
`GIT_TOKEN` env var, used in-memory only and `no_log: true` is set on
every task that touches it.

---

## 6. Security decisions

- **No secrets in artifacts.** Every JSON, Markdown and Terraform file
  produced by the flow is free of admin passwords, SSH keys, WinRM
  passwords and Git tokens. The Terraform baseline deliberately omits
  `admin_password`.
- **`no_log: true`** is applied to every task that handles credentials.
- **OS facts gathering avoids application data.** Only system-level
  artifacts are collected (packages, services, ports). No user data,
  no application logs, no DB content.
- **Least-privilege RBAC** is documented in the README. The flow does not
  require Owner / Subscription Contributor.
- **Static Public IP is enforced** before any destructive action.

---

## 7. Failure points and recovery

| Stage | Failure              | Recovery                                                    |
| ----- | -------------------- | ----------------------------------------------------------- |
| 1     | `az` permission denied | Add Reader role on the resource group; re-run.            |
| 1     | OS unreachable       | Re-run with corrected SSH/WinRM credentials; baseline still produces Azure-side JSONs. |
| 2     | Snapshot quota       | Free quota or change subscription; re-run.                 |
| 3     | Public IP Dynamic    | Convert PIP to Static in Azure; re-run.                    |
| 4     | Delete succeeded, create failed | Manually recreate VM from preserved OS disk; the disk is still in the resource group. Snapshots are the last-resort rollback. |
| 5     | OS unreachable       | Investigate NSG/firewall; the VM may still be functionally rebuilt. |
| 6     | Template error       | Re-run step 6 only; idempotent.                            |
| 7     | Push rejected        | Fix branch protection / token scope; re-run step 7 only.   |

---

## 8. Rollback

Snapshots in `snapshots/snapshots.json` are the rollback boundary:

1. From each snapshot ID, create a managed disk
   (`az disk create --source <snapshot-id>`).
2. Create a fresh VM that attaches the rolled-back OS disk.
3. Reattach the recovered data disks.
4. Reattach the preserved NIC and Public IP.

A formal `08_rollback_from_snapshots.yml` is **out of scope** for this
first iteration — see *Future work*.

---

## 9. Integration with SamurAI Shield

### Trigger surface

The Managed Server detail page in SamurAI Shield will gain a new action:

> **Run Legacy-to-Immutable Rebuild** (Azure only, brownfield VMs)

When the user clicks the action, SamurAI Shield calls the AAP API:

```
POST /api/v2/workflow_job_templates/<workflow_id>/launch
Authorization: Bearer <aap-token>
Content-Type: application/json

{
  "extra_vars": {
    "managed_server_id": 123,
    "cloud_provider": "azure",
    "azure_subscription_id": "...",
    "azure_resource_group": "...",
    "azure_vm_name": "...",
    "artifact_output_dir": "/tmp/samurai/legacy-to-immutable-rebuild/<vm>",
    "git_repo_url": "https://github.com/<org>/<evidence-repo>.git",
    "git_branch": "main",
    "dry_run": true,
    "confirm_destructive_action": false,
    "os_connection_type": "ssh",
    "target_host": "<pip-or-dns>",
    "ssh_username": "<user>"
  }
}
```

Credentials (Azure SPN, Machine SSH/WinRM, Git PAT) are bound to the AAP
Workflow Template via AAP credential types. SamurAI Shield does **not** send
secret material to AAP.

### Evidence ingestion

After the workflow finishes, SamurAI Shield can either:

1. Poll AAP for the final `samurai_shield_result` set-stat (preferred for
   live status), or
2. Fetch the artifact tree directly from the evidence Git repository at
   `inventory/managed-servers/<managed_server_id>-<azure_vm_name>/`.

Both modes are supported because every step emits `set_stats` and step 7
commits the same data to Git.

### Managed Server detail UI

Recommended UI surface (purely indicative — the SamurAI Shield repo owns
the UI definition):

- Section *Immutable Infrastructure Engine*
  - Banner with the latest run status (read from workflow history).
  - Link to the evidence directory in Git.
  - Link to the rendered `validation_report.md` and
    `post_rebuild_validation.md`.
  - Action buttons:
    - *Run dry-run baseline* — launches with `dry_run=true`,
      `confirm_destructive_action=false`.
    - *Run real rebuild* — launches with `dry_run=false`,
      `confirm_destructive_action=true` (with an explicit modal confirm).
    - *Open generated Terraform* — links to `generated-iac/` in Git.

---

## 10. Future work

- `08_rollback_from_snapshots.yml` — automated rollback procedure.
- Support for VMs with **unmanaged (page-blob) OS disks**.
- Support for **NIC recreation** (parameterised, off by default).
- Replicate **NSG rules** automatically into the generated Terraform.
- Replicate **VM extensions** automatically into the generated Terraform.
- Optional remote-state backend bootstrap for the generated Terraform.
- Multi-cloud variant (AWS legacy EC2 → Immutable rebuild).

---

## 11. References

- [`README.md`](../README.md) — flow overview, prerequisites and usage.
- [`workflow.yml`](../workflow.yml) — declarative AAP Workflow Template.
- [`sample_extra_vars.yml`](../sample_extra_vars.yml) — example launch payload.
- [Playbooks](../playbooks/) — implementation of each stage.
- [Templates](../templates/) — Terraform and report Jinja templates.
