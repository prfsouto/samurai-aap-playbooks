# SamurAI Shield - AAP Playbooks

Ansible Automation Platform (AAP) runs these playbooks for SamurAI Shield.
They cover remediation, vulnerability validation, tenant provisioning, and
immutable infrastructure workflows.

## Playbooks

| Use case | AAP playbook | Inputs and evidence |
| --- | --- | --- |
| Update RHEL-family hosts with yum/dnf | [linux_yum_update_with_evidence.yml](playbooks/linux_yum_update_with_evidence.yml) | <a name="linux_yum_update_with_evidenceyml"></a>[Reference](docs/remediation.md#linux_yum_update_with_evidenceyml) |
| Update specific RPM packages | [linux_yum_targeted_package_update_with_evidence.yml](playbooks/linux_yum_targeted_package_update_with_evidence.yml) | <a name="linux_yum_targeted_package_update_with_evidenceyml"></a>[Reference](docs/remediation.md#linux_yum_targeted_package_update_with_evidenceyml) |
| Update Debian or Ubuntu hosts | [linux_apt_update_with_evidence.yml](playbooks/linux_apt_update_with_evidence.yml) | <a name="linux_apt_update_with_evidenceyml"></a>[Reference](docs/remediation.md#linux_apt_update_with_evidenceyml) |
| Apply an Oracle RAC rolling patch | [oracle_rac_patch_with_evidence.yml](playbooks/oracle_rac_patch_with_evidence.yml) | <a name="oracle_rac_patch_with_evidenceyml"></a>[Reference](docs/remediation.md#oracle_rac_patch_with_evidenceyml) |
| Run Windows Update | [windows_update_with_evidence.yml](playbooks/windows_update_with_evidence.yml) | <a name="windows_update_with_evidenceyml"></a>[Reference](docs/remediation.md#windows_update_with_evidenceyml) |
| Upgrade the Tomcat server component | [tomcat_update_with_evidence.yml](playbooks/tomcat_update_with_evidence.yml) | <a name="tomcat_update_with_evidenceyml"></a>[Reference](docs/remediation.md#tomcat_update_with_evidenceyml) |
| Replace a vulnerable Java library | [java_library_patch_with_evidence.yml](playbooks/java_library_patch_with_evidence.yml) | <a name="java_library_patch_with_evidenceyml"></a>[Reference](docs/remediation.md#java_library_patch_with_evidenceyml) |
| Collect installed RPM package evidence | [linux_rpm_package.yml](playbooks/vulnerability_validation/linux_rpm_package.yml) | <a name="vulnerability_validationlinux_rpm_packageyml"></a>[Reference](docs/remediation.md#vulnerability_validationlinux_rpm_packageyml) |

<a name="job-template-survey"></a><a name="aap-setup-checklist"></a>
For the Windows Job Template, see the [survey](docs/remediation.md#job-template-survey)
and [setup checklist](docs/remediation.md#aap-setup-checklist).

When a playbook supports `ACTION=Check`, use it first on one approved test
host. Read the playbook reference for side effects. For example, the apt
check refreshes the package index and the Oracle RAC check stages the patch.

For tenant provisioning, start with the [tenant playbook](playbooks/tenant_provision/provision.yml).
For immutable infrastructure, see the [image build](playbooks/image_build.yml),
[image validation](playbooks/image_validate.yml), and
[AWS replacement](playbooks/aws_replacement.yml) entry points. The
[Azure demonstration](immutable/azure/legacy-to-immutable-rebuild/README.md)
has its own setup and workflow guide.

## Repository layout

| Path | Purpose |
| --- | --- |
| [`playbooks/`](playbooks/) | Job entry points, surveys, example variables, task includes, and custom Ansible modules and filters. |
| [`roles/`](roles/) | Reusable tasks, variables, templates, and files loaded by playbooks. |
| [`workflow_templates/`](workflow_templates/) | Definitions for AAP templates and the campaign callback credential type. |
| [`packer/`](packer/) and [`shared/`](shared/) | Image-build template and the helper that fingerprints image definitions. |
| [`immutable/`](immutable/) and [`examples/`](examples/azure-legacy-to-immutable/) | Azure rebuild demonstration and synthetic evidence examples. |
| [`rulebooks/`](rulebooks/) | Placeholder for event-driven automation. |
| [`tests/`](tests/) | Python and local Ansible tests for playbook and role behavior. |

## AAP Project Configuration

The SamurAI Shield AAP project uses this Git repository and the `main`
branch. Each Job Template points to a playbook path relative to the root.
The Job Template also selects the execution environment, inventory, and
credentials required for its target. [`ansible.cfg`](ansible.cfg) defines
where Ansible finds roles and collections.

For files mirrored from the SamurAI Shield application repository, follow
its mirror manifest when making changes. Coordinate any path change with
the manifest and update the AAP Job Template when its playbook path moves.

## Validate and contribute

The [contribution guide](CONTRIBUTING.md) shows how to set up an isolated
environment, check playbook syntax, and run targeted tests. The [GitHub
Actions workflow](.github/workflows/validate.yml) runs the repository tests
and checks the remediation and RPM evidence playbooks on pull requests.
