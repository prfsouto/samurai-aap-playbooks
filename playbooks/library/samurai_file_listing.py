#!/usr/bin/python
"""Lista um volume de origem com caminho, tamanho e sha256 por arquivo. Só lê.

Embrulho do listador compartilhado para a releitura da origem. Não altera o host.
"""
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.file_listing import (
    MAX_ENTRIES,
    MAX_READ_BYTES,
    MAX_SERIALIZED_BYTES,
    FileListingError,
    list_volume,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            mount_point=dict(type='str', required=True),
            max_entries=dict(type='int', default=MAX_ENTRIES),
            max_serialized_bytes=dict(type='int', default=MAX_SERIALIZED_BYTES),
            max_read_bytes=dict(type='int', default=MAX_READ_BYTES),
        ),
        supports_check_mode=True,
    )
    mount_point = module.params['mount_point']
    try:
        listing = list_volume(
            mount_point,
            max_entries=module.params['max_entries'],
            max_serialized_bytes=module.params['max_serialized_bytes'],
            max_read_bytes=module.params['max_read_bytes'],
        )
    except (FileListingError, OSError) as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(changed=False, listing=dict(listing, mount_point=mount_point))


if __name__ == '__main__':
    main()
