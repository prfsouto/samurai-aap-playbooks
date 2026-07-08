# SamurAI Shield - AAP Playbooks

This repository contains Ansible Automation Platform playbooks used by SamurAI Shield remediation workflows.

## Playbooks

### linux_yum_update_with_evidence.yml

Performs controlled yum/dnf update operations on Red Hat-like Linux systems with pre-checks, post-checks, evidence collection and structured JSON output.

Supported actions:

- `Check`: validates the host and lists available updates without changing packages.
- `Apply`: applies yum/dnf updates and collects before/after evidence.

### oracle_rac_patch_with_evidence.yml

Rolling patch of Oracle Grid Infrastructure (GI) + all Oracle Database homes on a
Real Application Cluster (RAC), using `opatchauto` + `datapatch`. Nodes are patched
**one at a time** (`serial: 1`) so the cluster stays available, and every node is
returned to the exact state it was found in.

Safety model ("deliver the environment exactly as it was found"):

1. **Health gate** — refuses to start if the cluster is not fully healthy, so a
   degraded cluster is never turned into an outage.
2. **Baseline capture** — records every database instance, listener and service
   that is ONLINE on the local node before patching.
3. **Patch** — stages/unzips the patch, always runs `opatchauto apply -analyze`
   (read-only prereq/conflict check), and in Apply mode runs `opatchauto apply`
   (RAC-aware: stops the local node's resources, patches GI + all DB homes, and
   restarts them).
4. **datapatch** — on the last node of the rolling sequence by default, applies
   the SQL payload of the RU to each database (idempotent, run once per DB).
5. **Restore + re-validate** — brings back anything from the baseline that did
   not come back automatically, then re-checks. If the node cannot be returned
   to its baseline, the play fails **before** touching the next node
   (`any_errors_fatal: true`), so a bad node never cascades cluster-wide.

Supported actions:

- `Check`: stage + `opatchauto apply -analyze` only. Read-only, no downtime.
- `Apply`: full rolling patch, `datapatch`, and baseline restore/validation.

The patch mechanism assumes: privilege escalation configured (opatchauto runs as
root, crsctl/srvctl as the GI owner, datapatch as each DB home owner), `unzip`
available on the nodes, a rolling-installable RU/RUR, and OPatch already meeting
the patch's minimum version. See
[oracle_rac_patch.sample_extra_vars.yml](playbooks/oracle_rac_patch.sample_extra_vars.yml)
for the full extra-vars contract, and the header of the playbook for details.

## AAP Project Configuration

Use this repository as a Git project in Ansible Automation Platform.

Source Control URL:

```text
https://github.com/prfsouto/samurai-aap-playbooks.git
