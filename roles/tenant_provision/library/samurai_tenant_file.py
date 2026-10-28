#!/usr/bin/python
from __future__ import annotations

DOCUMENTATION = r"""
module: samurai_tenant_file
short_description: Create the tenant file in the tenants repository (GitOps), idempotently
description:
  - Commits tenants/<environment>/<tenant_internal_id>.yaml with a GitHub App installation token restricted to one repository.
  - Never overwrites a file that belongs to another identity; a rerun commits nothing.
options:
  tenant_internal_id: {type: str, required: true}
  tenant_slug: {type: str, required: true}
  tenant_environment: {type: str, required: true}
  tenant_name: {type: str, required: true}
  admin_email: {type: str, required: true}
  admin_full_name: {type: str, required: true}
  repository: {type: str, required: true}
  branch: {type: str, default: main}
  api_url: {type: str, default: "https://api.github.com"}
  app_id: {type: str, required: true}
  installation_id: {type: str, required: true}
  private_key_file: {type: path, required: true}
  commit_message: {type: str, required: true}
"""

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.samurai_tenant_provision import (
    ConflictError, Http, ProvisionError, github_app_jwt, installation_token, tenant_file_path, upsert_tenant_file, validate_tenant,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            tenant_internal_id=dict(type="str", required=True),
            tenant_slug=dict(type="str", required=True),
            tenant_environment=dict(type="str", required=True),
            tenant_name=dict(type="str", required=True),
            admin_email=dict(type="str", required=True, no_log=True),
            admin_full_name=dict(type="str", required=True, no_log=True),
            repository=dict(type="str", required=True),
            branch=dict(type="str", default="main"),
            api_url=dict(type="str", default="https://api.github.com"),
            app_id=dict(type="str", required=True),
            installation_id=dict(type="str", required=True),
            private_key_file=dict(type="path", required=True),
            commit_message=dict(type="str", required=True),
        ),
        supports_check_mode=True,
    )
    p = module.params
    try:
        document = validate_tenant(
            internal_id=p["tenant_internal_id"], slug=p["tenant_slug"], environment=p["tenant_environment"],
            name=p["tenant_name"], admin_email=p["admin_email"], admin_full_name=p["admin_full_name"],
        )
        path = tenant_file_path(p["tenant_environment"], p["tenant_internal_id"])
        try:
            with open(p["private_key_file"], "rb") as handle:
                pem = handle.read()
        except OSError:
            raise ProvisionError("the GitHub App private key file is not readable") from None
        http = Http()
        token = installation_token(http, p["api_url"], github_app_jwt(p["app_id"], pem), p["installation_id"], p["repository"])
        result = upsert_tenant_file(
            http, api_url=p["api_url"], token=token, repository=p["repository"], branch=p["branch"], path=path,
            document=document, message=p["commit_message"], check_mode=module.check_mode,
        )
    except ConflictError as exc:
        module.fail_json(msg=str(exc), conflict=True)
    except ProvisionError as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
