# Debian 12 Brotli security update

This lab candidate upgrades the installed `libbrotli1:amd64` package on one
Debian 12 host. Debian fixed CVE-2026-111497 in `1.0.9-2+deb12u1`.
The repository's APT playbook upgrades a whole host or security pocket. This
candidate uses one vendor artifact and refuses a broader transaction.
[Debian advisory](https://lists.debian.org/debian-lts-announce/2026/10/msg00049.html)

- `ACTION=Check` is the default. It reads the package database and compares
  the Debian version with the advisory floor. `--check` also avoids application.
- `ACTION=Apply` requires one approved, disposable target in the
  `samurai_remediation_lab` inventory group, with privilege escalation for the
  transaction. Run it with exclusive use of APT and dpkg. A held package below
  the floor requires a separate decision. A package marked for reinstallation
  fails the observation, even at the version floor.
- Stage the official package at
  `/var/tmp/samurai-advisory-assets/libbrotli1_1.0.9-2+deb12u1_amd64.deb`.
  Use a root-owned `0700` directory and a root-owned `0600` or `0400`
  regular file. Verify the Debian security archive signature and package index
  before staging. The playbook checks the pinned SHA-256. No binary is bundled.
  [Debian package and checksum](https://packages.debian.org/bookworm/amd64/libbrotli1/download)
- The simulation must show an upgrade and configuration of exactly the
  requested library. The simulation and Apply are separate APT invocations.
  Apply uses the staged file with repository sources and package removals
  disabled and preserves the package's APT installation marker.
  A second Apply at or above the
  floor skips the transaction. In a partial failure, reconcile the package
  database and logs before retrying. Recovery relies on a separately approved
  and tested snapshot. A downgrade restores the vulnerable version.

Evidence reports package versions before and after. Loaded libraries in
long-running workloads require a separate process check and a governed
restart when applicable. The playbook defers the `needrestart` hook and
leaves runtime verification pending. Bundled or statically linked decoders,
other architectures and releases, and container images are outside scope.
The candidate has no production Job Template or catalog entry.

The [manifest](provenance.yml) records the adapted MIT component, changes,
artifact and test boundaries. The local test is a package update and a Brotli
round trip in a fresh process on disposable Debian. AAP and live-workload
validation remain pending.
