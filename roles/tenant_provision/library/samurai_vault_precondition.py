#!/usr/bin/python
from __future__ import annotations

DOCUMENTATION = r"""
module: samurai_vault_precondition
short_description: Check, read-only, that the tenant Vault secret, policy and role exist
description: Reads metadata only; never a secret value, never a write.
options:
  addr: {type: str, required: true}
  token: {type: str, required: true}
  tenant_internal_id: {type: str, required: true}
  ca_file: {type: path}
"""

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.samurai_tenant_provision import ProvisionError, check_vault_precondition, http_for


def main():
    module = AnsibleModule(
        argument_spec=dict(
            addr=dict(type="str", required=True),
            token=dict(type="str", required=True, no_log=True),
            tenant_internal_id=dict(type="str", required=True),
            ca_file=dict(type="path"),
        ),
        supports_check_mode=True,
    )
    p = module.params
    try:
        result = check_vault_precondition(http_for(p["ca_file"]), addr=p["addr"], token=p["token"], internal_id=p["tenant_internal_id"])
    except ProvisionError as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
