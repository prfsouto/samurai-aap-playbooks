[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$CommandJson)

$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
$command = $CommandJson | ConvertFrom-Json
$org = [int]$command.organization_id
$provider = [string]$command.provider
$instanceId = [string]$command.instance_id
$action = [string]$command.action
$bindings = @($command.bindings)
if ($org -le 0 -or $provider -notin @('aws', 'azure') -or
    [string]::IsNullOrWhiteSpace($instanceId) -or
    $action -notin @('prepare', 'write', 'protect', 'observe') -or $bindings.Count -eq 0) {
    throw 'Candidate disk command lacks governed organization, VM, action or bindings.'
}
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Candidate disk isolation requires an elevated Administrator token.'
}
$os = Get-CimInstance Win32_OperatingSystem
if ([int]$os.ProductType -ne 3 -or [int]$os.BuildNumber -lt 20348) {
    throw 'Candidate disk isolation requires a supported Windows Server guest.'
}

function Read-Metadata([string]$Uri, [string]$Method, [hashtable]$Headers) {
    $request = [Net.WebRequest]::Create($Uri)
    $request.Proxy = $null
    $request.Method = $Method
    $request.Timeout = 5000
    foreach ($name in $Headers.Keys) { $request.Headers.Add($name, [string]$Headers[$name]) }
    $response = $request.GetResponse()
    try {
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        try {
            $buffer = [char[]]::new(1048577)
            $count = $reader.ReadBlock($buffer, 0, $buffer.Length)
            if ($count -gt 1048576) { throw 'Cloud metadata exceeded its size bound.' }
            return [string]::new($buffer, 0, $count)
        } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}

$metadata = $null
if ($provider -eq 'aws') {
    $token = Read-Metadata 'http://169.254.169.254/latest/api/token' 'PUT' @{ 'X-aws-ec2-metadata-token-ttl-seconds' = '60' }
    $observedInstance = Read-Metadata 'http://169.254.169.254/latest/meta-data/instance-id' 'GET' @{ 'X-aws-ec2-metadata-token' = $token }
    if ($observedInstance -cne $instanceId) { throw 'Candidate AWS VM differs from the governed instance.' }
} else {
    $metadata = Read-Metadata 'http://169.254.169.254/metadata/instance?api-version=2021-02-01' 'GET' @{ Metadata = 'true' } | ConvertFrom-Json
    if ([string]$metadata.compute.resourceId -ine $instanceId) {
        throw 'Candidate Azure VM differs from the governed resource.'
    }
}
if ($action -eq 'prepare') {
    Set-StorageSetting -NewDiskPolicy OfflineAll -ErrorAction Stop
    if ((Get-StorageSetting).NewDiskPolicy -ne 'OfflineAll') {
        throw 'New candidate disks are not kept offline.'
    }
}
$disks = @(Get-Disk)
$physical = @(Get-CimInstance -ClassName Win32_DiskDrive)
$seen = @{}
$observed = @(foreach ($binding in $bindings) {
    $volumeId = [string]$binding.destination_volume_stable_id
    if ([string]::IsNullOrWhiteSpace($volumeId) -or
        [string]$binding.candidate_instance_id -ine $instanceId -or
        [string]$binding.source_volume_stable_id -ieq $volumeId -or
        [long]$binding.size_bytes -le 0) {
        throw 'Candidate volume binding is incomplete or points back to the source.'
    }
    $lun = $null
    if ($provider -eq 'aws') {
        $wanted = $volumeId.Replace('-', '').ToLowerInvariant()
        $matches = @($disks | Where-Object {
            ([string]$_.SerialNumber).Trim().Replace('-', '').ToLowerInvariant() -eq $wanted
        })
    } else {
        $profile = @($metadata.compute.storageProfile.dataDisks | Where-Object {
            [string]$_.managedDisk.id -ieq $volumeId
        })
        if ($profile.Count -ne 1 -or [int]$profile[0].lun -ne [int]$binding.attached_lun) {
            throw 'Azure candidate volume does not match its frozen VM attachment.'
        }
        $lun = [int]$profile[0].lun
        $indices = @($physical | Where-Object { [int]$_.SCSILogicalUnit -eq $lun } | ForEach-Object { [int]$_.Index })
        $matches = @($disks | Where-Object { $indices -contains [int]$_.Number })
    }
    if ($matches.Count -ne 1 -or $matches[0].IsBoot -or $matches[0].IsSystem -or
        $seen.ContainsKey([int]$matches[0].Number)) {
        throw 'Candidate volume is ambiguous, reused or belongs to the system.'
    }
    $disk = $matches[0]
    $seen[[int]$disk.Number] = $true
    if ([long]$disk.Size -ne [long]$binding.size_bytes) {
        throw 'Candidate raw disk capacity differs from the frozen destination.'
    }
    if (-not $disk.IsOffline -and $disk.PartitionStyle -ne 'RAW') {
        $parts = @(Get-Partition -DiskNumber $disk.Number -ErrorAction Stop)
        if (@($parts | Where-Object { @($_.AccessPaths | Where-Object { $_ }).Count -gt 0 }).Count -gt 0) {
            throw 'Candidate disk has an accessible filesystem before the governed transfer.'
        }
    }
    if ($action -eq 'prepare') {
        if (-not $disk.IsOffline) { Set-Disk -Number $disk.Number -IsOffline $true -ErrorAction Stop }
        Set-Disk -Number $disk.Number -IsReadOnly $true -ErrorAction Stop
    } elseif ($action -eq 'write') {
        if (-not $disk.IsOffline) { throw 'Raw copy requires an offline destination disk.' }
        if ($disk.IsReadOnly) { Set-Disk -Number $disk.Number -IsReadOnly $false -ErrorAction Stop }
    } elseif ($action -eq 'protect') {
        if (-not $disk.IsOffline) { throw 'Raw copy cleanup cannot protect a mounted destination.' }
        if (-not $disk.IsReadOnly) { Set-Disk -Number $disk.Number -IsReadOnly $true -ErrorAction Stop }
    }
    $disk = Get-Disk -Number $disk.Number
    if (-not $disk.IsOffline -or ($action -in @('prepare', 'protect', 'observe') -and -not $disk.IsReadOnly) -or
        ($action -eq 'write' -and $disk.IsReadOnly)) {
        throw 'Candidate raw disk isolation could not be confirmed.'
    }
    [ordered]@{ name = "\\.\PhysicalDrive$($disk.Number)"; serial = ([string]$disk.SerialNumber).Trim();
        type = 'disk'; size = [long]$disk.Size; mountpoints = @(); children = @();
        disk_number = [int]$disk.Number; lun = $lun; offline = [bool]$disk.IsOffline;
        readonly = [bool]$disk.IsReadOnly }
})
if ($observed.Count -ne $bindings.Count) { throw 'Candidate observation omitted a REQUIRED disk.' }
$hostUuid = if ($provider -eq 'azure') { [string](Get-CimInstance Win32_ComputerSystemProduct).UUID } else { '' }
$cloud = @{ provider = $provider; platform = 'windows_server'; instance_id = $instanceId }
if ($provider -eq 'azure') {
    $cloud.vm_id = [string]$metadata.compute.vmId
    $cloud.storage_profile = $metadata.compute.storageProfile
}
$Ansible.Result = @{ observation = @{ organization_id = $org; cloud = $cloud;
    host = @{ product_uuid = $hostUuid }; blockdevices = $observed;
    observed_at = [DateTime]::UtcNow.ToString('o') } }
$Ansible.Changed = $action -ne 'observe'
