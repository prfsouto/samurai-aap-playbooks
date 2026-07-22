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

### windows_update_with_evidence.yml

Controlled Windows Update run on Windows Server targets, reached over WinRM/PSRP.
Same shape as the Linux update playbook: pre-checks, optional install, post-checks,
evidence files under `C:\ProgramData\samurai-shield\windows-update\<host>\<timestamp>`
and a consolidated `samurai_shield_result` JSON block on stdout.

Supported actions:

- `Check`: searches Windows Update and lists what is applicable. Nothing is
  downloaded or installed.
- `Apply`: installs the updates found, re-checks the host (hotfix diff, pending
  reboot, remaining updates, stopped automatic services) and optionally reboots.

Safety model:

1. **Gates before touching anything** — refuses to run when the OS is not
   Windows, when the Windows Update service is missing/disabled, or when the
   system drive has less than `MIN_FREE_GB` (default 5 GB) free.
2. **Reboot is the playbook's decision, never the module's** — `win_updates` is
   always called with `reboot: false`. The host is rebooted only when reboot is
   allowed *and* it actually reports a pending reboot; otherwise it is left
   pending and `reboot_required_after=yes` is reported in the JSON. The "allowed"
   flag has two sources: the SamurAI engine injects a lowercase `allow_reboot`
   from the governed job, which **takes precedence**; the uppercase `ALLOW_REBOOT`
   is the survey/manual knob (and the fallback default `false`). `summary.json`
   records which source won as `allow_reboot_source` (`governed` | `survey` |
   `default`). The coalescing keys on "defined", not "truthy", so a governed
   `allow_reboot=false` is honoured as an explicit *no* rather than falling
   through to the survey default.
3. **Drivers are opt-in** — the default categories are
   `SecurityUpdates,CriticalUpdates,UpdateRollups,Updates`. Drivers, Feature
   Packs and Service Packs are only installed if `WU_CATEGORIES` asks for them.
4. **Serial by default** (`SERIAL_BATCH=1`) so a wave never takes a whole fleet
   down at once.

The normal remediation call only needs `HOSTNAMES` + `ACTION`. Everything else
(`ALLOW_REBOOT`, `WU_CATEGORIES`, `EXCLUDE_KB`, `WU_SERVER_SELECTION`,
`BECOME_SYSTEM`, `LOG_LEVEL`, connection overrides, …) is optional with a working
default — see
[windows_update.sample_extra_vars.yml](playbooks/windows_update.sample_extra_vars.yml)
for the full contract and the header of the playbook for details.

#### Job Template survey

[windows_update.survey_spec.json](playbooks/windows_update.survey_spec.json) is the
survey definition for the Job Template, kept in git so it is versioned and
reviewable instead of living only in the AAP UI. Apply it with:

```bash
curl -k -X POST \
  -H "Authorization: Bearer $AAP_TOKEN" -H "Content-Type: application/json" \
  -d @playbooks/windows_update.survey_spec.json \
  https://<aap-host>/api/v2/job_templates/<ID>/survey_spec/
```

Then enable **Prompt on launch → Survey** on the Job Template.

Two reasons the survey matters beyond convenience:

- `HOSTNAMES` is recognized by SamurAI Shield as a target-entry field, so it is
  **hidden from the operator form and auto-filled from the governed asset** — it
  is never typed by hand. The remediation engine also re-asserts it server-side,
  so a value set anywhere else is ignored.
- A survey takes precedence over the dashboard's heuristic playbook parse, which
  otherwise scrapes internal `set_fact` names out of the YAML and sends them as
  extra-vars — and extra-vars outrank `set_fact`, which breaks the run.

Because AAP sends a blank optional survey answer as an **empty string** (not as
an absent variable), every optional var is resolved with
`(X | default('') | trim) or <default>` rather than a plain `| default()`.

#### AAP setup checklist

Suggested order: check the Execution Environment first, since it is the only
step that can require building an image. Then validate with `ACTION=Check`
against a single host before allowing `Apply`.

**1. Execution Environment.** The `winrm` connection plugin needs the **`pywinrm`**
Python library, and `ansible-galaxy` cannot install Python dependencies — so
`requirements.yml` does not cover this. If the EE lacks it, no Job Template
setting can compensate; a custom EE is required.

```bash
podman run --rm <ee-image> python3 -c "import winrm; print(winrm.__version__)"
podman run --rm <ee-image> ansible-galaxy collection list ansible.windows
```

`awx-ee` normally ships both; `ee-minimal` ships neither. `WIN_CONNECTION=psrp`
needs `pypsrp` instead, and Kerberos auth needs `requests-kerberos`.

**2. Project.** Sync the project after merging (or enable *Update Revision on
Launch*). The root `requirements.yml` IS honoured — the controller stats
`roles/requirements.yml`, `collections/requirements.yml` **and** the root
`requirements.yml`, installing the last one with `ansible-galaxy install -r`,
which handles roles and collections alike. It only runs when
**Settings → Jobs → Enable Collection(s) Download** is on.

**3. Credential.** A Machine credential whose user belongs to the local
Administrators group on the targets. It injects `ansible_user` /
`ansible_password`; the playbook's `add_host` pins only the transport, never
credentials.

**4. Job Template.**

| Field | Value |
| --- | --- |
| Playbook | `playbooks/windows_update_with_evidence.yml` |
| Inventory | any (e.g. `samurai_managed`) — required by AAP, but the playbook builds its own inventory and the targets need not be in it |
| Credentials | the Windows Machine credential |
| `ask_variables_on_launch` | **`true`** |
| `survey_enabled` | `true`, plus the survey spec above |
| Timeout | `0`, or well above a full update run |
| `job_slice_count` | **`1`** |

```bash
curl -k -X PATCH \
  -H "Authorization: Bearer $AAP_TOKEN" -H "Content-Type: application/json" \
  -d '{"ask_variables_on_launch": true, "survey_enabled": true, "timeout": 0, "job_slice_count": 1}' \
  https://<aap-host>/api/v2/job_templates/<ID>/
```

Two settings there are load-bearing:

- **`ask_variables_on_launch: true`** — the remediation engine launches with
  `{"extra_vars": json.dumps(...)}`. Without this flag the Controller *silently
  discards* the extra-vars and reports them under `ignored_fields` — no error.
  `HOSTNAMES` would never arrive and the job would end with "no hosts to target".
- **`job_slice_count: 1`** — slicing partitions the *Job Template inventory*,
  which this playbook ignores: play 1 runs on `localhost` and re-adds every host
  from `HOSTNAMES` in **every** slice. With 3 slices, all 3 would patch the whole
  fleet concurrently. Use the playbook's `SERIAL_BATCH` for batching instead.

**Do NOT enable "Privilege Escalation" on the template.** It adds a global
`--become`; combined with a Windows inventory that carries
`ansible_become_method=runas`, the `runas` become plugin then loads on the
`localhost` bootstrap play and fails with *"required ... become plugin: runas
setting: become_user"*. The playbook declares `become` per task (`runas` /
SYSTEM) and defensively pins `become: false` on every play, so an accidental
global become no longer breaks it — but leaving the checkbox off is still the
correct configuration.

**5. Windows targets** (outside AAP). A WinRM listener reachable from the
execution node on the port the playbook connects to. The default is **HTTPS
5986**; `winrm quickconfig` only creates the **HTTP 5985** listener and opens
5985 — an HTTPS listener plus a firewall rule for 5986 must be set up
explicitly (see the header of `windows_update.sample_extra_vars.yml` for the
`WIN_PORT` / `WIN_TRANSPORT` overrides if you must fall back to 5985/HTTP).
Because the target hosts are built with `add_host`, they do NOT inherit
connection vars from the AAP inventory — any `WIN_PORT` override must arrive as
an extra-var (JT extra-vars or the mapping's `extra_vars_template`), not via the
inventory. Also required: `wuauserv` not disabled (the playbook refuses to run
otherwise) and enough free space on the system drive.

**6. SamurAI Shield.** Point the playbook mapping at the new Job Template id. Do
**not** put `HOSTNAMES` in the mapping's `extra_vars_template`: the engine always
derives it from the governed asset and the merge only fills keys it has not already
set, so the value is silently discarded.

## AAP Project Configuration

Use this repository as a Git project in Ansible Automation Platform.

Source Control URL:

```text
https://github.com/prfsouto/samurai-aap-playbooks.git
