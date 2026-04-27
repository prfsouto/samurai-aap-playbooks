# SamurAI Shield - AAP Playbooks

This repository contains Ansible Automation Platform playbooks used by SamurAI Shield remediation workflows.

## Playbooks

### linux_yum_update_with_evidence.yml

Performs controlled yum/dnf update operations on Red Hat-like Linux systems with pre-checks, post-checks, evidence collection and structured JSON output.

Supported actions:

- `Check`: validates the host and lists available updates without changing packages.
- `Apply`: applies yum/dnf updates and collects before/after evidence.

## AAP Project Configuration

Use this repository as a Git project in Ansible Automation Platform.

Source Control URL:

```text
https://github.com/prfsouto/samurai-aap-playbooks.git
