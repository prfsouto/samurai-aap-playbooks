# SamurAI Shield :: Legacy-to-Immutable Rebuild (Azure)

This directory contains the Ansible Automation Platform (AAP) playbooks that
implement the **Legacy-to-Immutable Rebuild** demo workflow of the SamurAI
Shield *Immutable Infrastructure Engine*.

The flow takes an existing brownfield Azure VM (created manually, outside any
IaC pipeline) and converts it into a governed, versioned, validated and
IaC-ready infrastructure asset, while preserving the OS managed disk, the
network interface and the Public IP whenever possible.

> ⚠️ This is a **controlled demonstration** of legacy-to-IaC conversion.
> It is destructive in step 4 (the VM resource is deleted and recreated from
> the existing OS disk). Always run with `dry_run: true` first and only flip
> `confirm_destructive_action: true` after reviewing the snapshots.

---

## Roles in the architecture

| Component                      | Responsibility                                                                 |
| ------------------------------ | ------------------------------------------------------------------------------ |
| **SamurAI Shield**             | UI, Managed Server registry, workflow history, orchestration. Calls AAP only. |
| **Ansible Automation Platform**| Executes the playbooks in this repository against Azure and the target VM.    |
| **samurai-aap-playbooks**      | This repository — playbooks, templates, docs, sample extra-vars.              |

The SamurAI Shield application **does not** store playbooks. It only invokes
the AAP Workflow Template and forwards `extra_vars`.

---

## Workflow stages

| # | Playbook                                | Mode             | Produces                                          |
| - | --------------------------------------- | ---------------- | ------------------------------------------------- |
| 1 | `01_collect_azure_vm_baseline.yml`      | read-only        | `baseline/*.json`, `baseline/validation_report.md`|
| 2 | `02_create_safety_snapshots.yml`        | mutating         | `snapshots/snapshots.json`                        |
| 3 | `03_preserve_network_identity.yml`      | read-only        | `network_identity.json` (in baseline/)            |
| 4 | `04_rebuild_vm_from_existing_os_disk.yml`| destructive-gated| `rebuild/rebuild_result.json`                     |
| 5 | `05_post_rebuild_validation.yml`        | read-only        | `validation/post_rebuild_validation.{json,md}`, `diff_report.json` |
| 6 | `06_generate_terraform_from_vm.yml`     | read-only        | `generated-iac/*.tf`, `terraform.tfvars.example`  |
| 7 | `07_commit_artifacts_to_git.yml`        | mutating (Git)   | Commit on `inventory/managed-servers/<id>-<vm>/`  |

The full sequence is documented in [`workflow.yml`](workflow.yml) and in
[`docs/azure_legacy_to_immutable_rebuild_workflow.md`](docs/azure_legacy_to_immutable_rebuild_workflow.md).

---

## Execution modes

The 7 playbooks need to share artifacts (`baseline/*.json`, `snapshots.json`,
`network_identity.json`, ...) across stages. AAP Workflow Templates spawn a
**separate ephemeral Execution Environment container per node**, so files
written to `/tmp/...` in one node do **not** persist into the next node by
default. There are two supported ways to handle this:

### Mode A — Single Job Template (recommended for demos)

Use the wrapper playbook
[`playbooks/00_full_workflow.yml`](playbooks/00_full_workflow.yml). It
imports all 7 playbooks in sequence inside **one** Ansible run, so the
artifacts stay on the same Execution Environment filesystem and the
inter-stage handoff works without any persistent storage.

Setup:

1. AAP UI → **Templates → Add → Job Template**.
2. **Playbook:** `immutable/azure/legacy-to-immutable-rebuild/playbooks/00_full_workflow.yml`
3. Attach the Azure RM credential and the target VM Machine credential.
4. Check **Prompt on launch** for *Extra variables* (so SamurAI Shield can
   override at launch time).
5. Save. Launch.

Trade-off: you lose per-stage gating in the AAP UI (the workflow runs as a
single job), but the entire pipeline runs end-to-end with zero infra setup.

### Mode B — Multi-node Workflow Template (production)

Use the workflow definition in [`workflow.yml`](workflow.yml). Each of the
7 nodes points to its own Job Template. Per-stage gating, pause and
restart are available.

**Hard requirement for Mode B:** the Execution Environment must mount a
**persistent volume** at the path used by `artifact_output_dir`. Options:

- Azure Files share mounted into the EE container (recommended on Azure).
- NFS share mounted at `/mnt/samurai-artifacts/`.
- Kubernetes PersistentVolumeClaim (when AAP is deployed on OpenShift / k8s).

Without a persistent mount, every node re-clones the repo into a fresh
`/tmp/` and the inter-stage handoff fails with
`Baseline not found at <path>/baseline/disk_facts.json`. See the section
"Failure points and recovery" in
[`docs/azure_legacy_to_immutable_rebuild_workflow.md`](docs/azure_legacy_to_immutable_rebuild_workflow.md)
for the EE configuration cookbook.

### Which one am I running?

| Symptom | You're in… |
| ------- | ---------- |
| One Job Template, one log, all 7 PLAYs scroll past in sequence | Mode A |
| Workflow Template, 7 nodes in the visualiser, each its own log | Mode B (needs persistent volume) |

---

## Prerequisites

### Azure

- An existing Azure VM (Linux or Windows) with:
  - **Public IP allocation method = Static.** Dynamic IPs are rejected by
    `03_preserve_network_identity.yml` because rebuild cannot guarantee IP
    preservation otherwise.
  - OS managed disk (the playbook does not support unmanaged/page-blob disks).
  - Reachable via SSH (Linux) or WinRM (Windows) for OS-level fact gathering.
- Azure CLI (`az`) ≥ 2.50 available on the AAP execution environment, **or**
  the `azure.azcollection` Ansible collection.
- A service principal / managed identity authorized to:
  - Read the resource group, VM, NIC, Public IP, NSG, VNet, subnet.
  - Create snapshots in the resource group.
  - Stop/deallocate the VM.
  - Delete the VM resource (only) and create a new VM that attaches the
    existing OS disk.

### Minimum Azure RBAC permissions

The following roles, scoped to the target resource group, are sufficient:

- `Virtual Machine Contributor`
- `Disk Snapshot Contributor` (or `Contributor` if the former is unavailable
  in the tenant)
- `Network Contributor` (read-only of NIC/IP/NSG; no mutation in this flow)
- `Reader` on the subscription (to list)

Custom role alternative (least privilege): grant only the actions used by the
playbooks — `Microsoft.Compute/virtualMachines/{read,deallocate/action,delete,write,start/action,powerOff/action}`,
`Microsoft.Compute/disks/read`, `Microsoft.Compute/snapshots/write`,
`Microsoft.Network/networkInterfaces/read`, `Microsoft.Network/publicIPAddresses/read`.

### AAP credentials

- **Azure** credential type (service principal or managed identity).
- **Machine** credential (SSH key for Linux **or** WinRM user for Windows).
- **Source Control** credential for the Git repository where artifacts are
  committed (only required if step 7 is run).

---

## How to run

### 1. Dry run (always start here)

```yaml
# extra_vars
dry_run: true
confirm_destructive_action: false
```

Steps 1, 2, 3, 5, 6 still execute and produce artifacts. Step 4 prints the
exact actions it *would* perform and exits without mutating Azure.

### 2. Real rebuild

After reviewing the dry-run output and the snapshots:

```yaml
dry_run: false
confirm_destructive_action: true
```

`confirm_destructive_action: true` is the explicit gate before
`04_rebuild_vm_from_existing_os_disk.yml` deletes the VM resource.

See [`sample_extra_vars.yml`](sample_extra_vars.yml) for the full set of
inputs.

---

## Risks and limitations

- **Destructive step.** Step 4 deletes the VM resource. Although the OS disk,
  NIC and Public IP are preserved, a misconfiguration in step 4 can produce a
  VM that does not boot. Always rely on the snapshots from step 2 to roll back.
- **OS disk mode only.** This flow only supports VMs whose OS disk is a
  managed disk. VMs with unmanaged (page-blob) OS disks are rejected.
- **Public IP must be Static.** Dynamic Public IPs may be released when the
  VM is deallocated. Step 3 fails fast if the IP is not Static.
- **NIC preservation.** The default behaviour is to preserve the existing NIC
  and reattach it to the rebuilt VM. NIC recreation is intentionally **not**
  parameterised in this first version.
- **Generated Terraform is a baseline.** The output of step 6 is marked
  `# generated baseline` and **must** be reviewed before being applied to any
  production environment. It does not include secrets and may be missing
  fields that depend on tenant policy (lock, diagnostics workspace, custom
  extensions, etc.).
- **No customer data is read.** The OS-fact gathering in step 1 deliberately
  avoids reading user data, application logs or database contents.

---

## Rollback strategy

The snapshots produced in step 2 are the rollback boundary.

1. If step 4 fails after the VM resource was deleted but before the new VM is
   created, recreate the VM manually from the existing OS disk (still present
   in the resource group). The snapshots are an additional safety net in case
   the disk itself is corrupted.
2. If step 5 reports validation failures, you can either:
   - Roll back by creating a new managed disk from the OS disk snapshot and
     a new VM that attaches it.
   - Or fix the misconfiguration in place (e.g. NSG, extension) and re-run
     `05_post_rebuild_validation.yml`.

A formal rollback playbook is intentionally out of scope for this first
iteration — see the *Future work* section in `docs/`.

---

## Artifact versioning

Step 7 commits the artifacts produced by all previous steps to the Git
repository at:

```
inventory/managed-servers/<managed_server_id>-<azure_vm_name>/
```

This path is structured to match the SamurAI Shield Managed Server detail
view, so the application can fetch evidence directly from Git.

---

## Security notes

- Secrets (Git tokens, Azure SPN secrets, SSH/WinRM passwords) are never
  written to JSON, Markdown, Terraform or stdout. They must be supplied via
  AAP credentials or environment variables.
- The generated Terraform omits all sensitive fields and uses comments to
  flag values that the operator must review (`admin_username`, `subnet_id`,
  `image_reference`, etc.).
- `no_log: true` is set on every task that handles tokens or passwords.

---

## See also

- [`workflow.yml`](workflow.yml) — declarative description of the AAP
  Workflow Template.
- [`docs/azure_legacy_to_immutable_rebuild_workflow.md`](docs/azure_legacy_to_immutable_rebuild_workflow.md)
  — operational documentation, decision tree, integration with SamurAI Shield.
- [`sample_extra_vars.yml`](sample_extra_vars.yml) — minimum extra-vars
  contract.
