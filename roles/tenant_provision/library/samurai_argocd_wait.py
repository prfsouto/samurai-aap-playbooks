#!/usr/bin/python
from __future__ import annotations

DOCUMENTATION = r"""
module: samurai_argocd_wait
short_description: Wait, read-only, for the tenant ArgoCD Application to be Synced and Healthy
options:
  server: {type: str, required: true}
  token: {type: str, required: true}
  name: {type: str, required: true}
  timeout_seconds: {type: int, default: 600}
  interval_seconds: {type: int, default: 10}
  project: {type: str, default: tenants}
  ca_file: {type: path}
"""

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.samurai_tenant_provision import ProvisionError, http_for, wait_for_application


def main():
    module = AnsibleModule(
        argument_spec=dict(
            server=dict(type="str", required=True),
            token=dict(type="str", required=True, no_log=True),
            name=dict(type="str", required=True),
            timeout_seconds=dict(type="int", default=600),
            interval_seconds=dict(type="int", default=10),
            project=dict(type="str", default="tenants"),
            ca_file=dict(type="path"),
        ),
        supports_check_mode=True,
    )
    p = module.params
    if not 1 <= p["timeout_seconds"] <= 3600 or not 1 <= p["interval_seconds"] <= 300:
        module.fail_json(msg="timeout_seconds must be 1-3600 and interval_seconds 1-300")
    if module.check_mode:
        module.exit_json(changed=False, skipped=True)
    try:
        result = wait_for_application(
            http_for(p["ca_file"]), server=p["server"], token=p["token"], name=p["name"],
            timeout_seconds=p["timeout_seconds"], interval_seconds=p["interval_seconds"], project=p["project"],
        )
    except ProvisionError as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
