"""Observe the approved image and materialize its governed account access."""
import base64
import secrets

from ansible.module_utils.azure_candidate_transport import AzureCandidateError
from ansible.module_utils.azure_candidate_resources import COMPUTE


def image_profile(client, spec):
    gallery = len(spec.image[1:].split('/')) == 12
    image = client.get(spec.image, '2024-03-03' if gallery else COMPUTE,
                       expand='ReplicationStatus' if gallery else None)
    properties = image.get('properties') or {}
    if properties.get('provisioningState') != 'Succeeded':
        raise AzureCandidateError('Approved destination image is not ready')
    storage = properties.get('storageProfile') or {}
    if gallery:
        definition = client.get('/'.join(spec.image.split('/')[:-2]), '2024-03-03').get('properties') or {}
        regions = (properties.get('replicationStatus') or {}).get('summary')
        if not isinstance(regions, list) or not any(
            str(r.get('region', '')).replace(' ', '').lower() == spec.region and r.get('state') == 'Completed' for r in regions):
            raise AzureCandidateError('Approved gallery image has not completed replication to the destination region')
        os_type, os_state = definition.get('osType'), definition.get('osState')
        minimum = (storage.get('osDiskImage') or {}).get('sizeInGB')
        data = storage.get('dataDiskImages')
    else:
        root = storage.get('osDisk') or {}
        os_type, os_state, minimum = root.get('osType'), root.get('osState'), root.get('diskSizeGB')
        data = storage.get('dataDisks')
        if image.get('location') != spec.region:
            raise AzureCandidateError('Managed image is outside the destination region')
    dialect = {'Linux': 'posix_shell', 'Windows': 'windows_powershell'}.get(os_type)
    root_slot = next(s for s in spec.slots if s['role'] == 'ROOT')
    if (dialect != spec.block['bootstrap'] or os_state not in {'Generalized', 'Specialized'}
            or type(minimum) is not int or minimum <= 0 or root_slot['size_gb'] < minimum or data):
        raise AzureCandidateError('Approved image platform or root geometry is incompatible with the frozen slots')
    return os_type, os_state, root_slot


def os_profile(spec, os_type, os_state):
    if os_state == 'Specialized':
        return None
    user = spec.block['ssh_username']
    if user.lower() in {'root', 'administrator', 'admin'}:
        raise AzureCandidateError('Generalized Azure image requires an Azure-compatible platform login policy')
    profile = {'computerName': spec.name[:15] if os_type == 'Windows' else spec.block['hostname'],
               'adminUsername': user}
    if os_type == 'Linux':
        profile['linuxConfiguration'] = {'disablePasswordAuthentication': True, 'provisionVMAgent': True,
            'ssh': {'publicKeys': [{'path': f'/home/{user}/.ssh/authorized_keys', 'keyData': spec.block['ssh_public_key']}]}}
    else:
        profile['adminPassword'] = 'Ss9!' + secrets.token_urlsafe(32)
        profile['windowsConfiguration'] = {'provisionVMAgent': True}
    return profile


def access_extension(spec, os_type):
    user, key = spec.block['ssh_username'], spec.block['ssh_public_key']
    if os_type == 'Linux':
        script = f'''#!/bin/sh
set -eu
u='{user}'
h="$(getent passwd "$u" | cut -d: -f6)"
test -n "$h"
install -d -m700 -o "$(id -u "$u")" -g "$(id -g "$u")" "$h/.ssh"
touch "$h/.ssh/authorized_keys"
chown "$u" "$h/.ssh/authorized_keys"
chmod 600 "$h/.ssh/authorized_keys"
grep -Fqx -- '{key}' "$h/.ssh/authorized_keys" || printf '%s\\n' '{key}' >> "$h/.ssh/authorized_keys"
'''
        return {'publisher': 'Microsoft.Azure.Extensions', 'type': 'CustomScript', 'typeHandlerVersion': '2.1',
                'autoUpgradeMinorVersion': True, 'settings': {'script': base64.b64encode(script.encode()).decode()}}
    script = f'''$ErrorActionPreference = 'Stop'
Set-StorageSetting -NewDiskPolicy OfflineAll -ErrorAction Stop
$u = Get-LocalUser -Name '{user}'
$admin = Get-LocalGroup -SID 'S-1-5-32-544'
$members = Get-LocalGroupMember -Group $admin
$isAdmin = @($members | Where-Object {{ $_.SID -eq $u.SID }}).Count -gt 0
$target = if ($isAdmin) {{ "$env:ProgramData\\ssh\\administrators_authorized_keys" }} else {{ "C:\\Users\\{user}\\.ssh\\authorized_keys" }}
New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
if (-not (Test-Path $target)) {{ New-Item -ItemType File -Path $target | Out-Null }}
if (-not (Select-String -Path $target -SimpleMatch -Pattern '{key}' -Quiet)) {{
  [IO.File]::AppendAllText($target, '{key}' + "`n", (New-Object Text.UTF8Encoding $false))
}}
if ($isAdmin) {{ icacls.exe $target /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null }}
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {{ throw 'Candidate key ACL failed' }}
'''
    encoded = base64.b64encode(script.encode('utf-16-le')).decode()
    return {'publisher': 'Microsoft.Compute', 'type': 'CustomScriptExtension', 'typeHandlerVersion': '1.10',
            'autoUpgradeMinorVersion': True, 'settings': {'commandToExecute': 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded}}
