# Contributing

## Set up local validation

From the repository root, use Python 3.14 and install the same direct
test dependencies as GitHub Actions.

```bash
python3.14 -m venv ~/venvs/samurai-aap-playbooks
source ~/venvs/samurai-aap-playbooks/bin/activate
python -m pip install -r tests/requirements.txt
export ANSIBLE_COLLECTIONS_PATH="$VIRTUAL_ENV/collections"
ansible-galaxy collection install -r requirements.yml -p "$ANSIBLE_COLLECTIONS_PATH"
```

Use a localhost inventory to check playbook syntax without running tasks
or connecting to target machines. Host-pattern warnings for groups
populated at runtime are expected with this inventory.

```bash
ansible-playbook -i 'localhost,' --syntax-check \
  playbooks/linux_yum_update_with_evidence.yml
python -m pytest -q -n 0 tests/test_vulnerability_validation_package_collector.py
```

Replace the paths with the playbook and tests relevant to your change. The
CI workflow runs the full test suite. Tests that call Ansible exercise
local fixtures. Operational validation uses the Job Template's actual
execution environment and an approved test host.

## Open a pull request

1. Branch from `main` and keep the change focused. For paths mirrored
   from SamurAI Shield, include the source change and commit reference.
2. When changing an input, update its survey or example variables, where
   present. Describe any changes to evidence or AAP Job Template settings.
3. Include the commands you ran and their results. For changes to
   execution behavior, include approved test-job evidence when available.
   Remove credentials, private URLs, and host details from shared logs.
