[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$CommandJson)

$ErrorActionPreference = 'Stop'
$Ansible.Changed = $false
$command = $CommandJson | ConvertFrom-Json
$scope = $command.scope
$volumes = @($command.parameters.volumes)
$action = [string]$scope.action
if ($action -notin @('acquire', 'inspect_candidate', 'activate_candidate', 'release_abort') -or
    [int]$scope.organization_id -le 0 -or [int]$scope.generation -le 0 -or
    $volumes.Count -eq 0) {
    throw 'Windows consistency command has no governed source scope.'
}
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Source disk observation requires an elevated Windows Administrator token.'
}
$os = Get-CimInstance Win32_OperatingSystem
if ([int]$os.ProductType -ne 3 -or [int]$os.BuildNumber -lt 20348) {
    throw 'Windows consistency requires a supported Windows Server guest.'
}

function Read-InstanceMetadata([string]$Uri, [string]$Method, [hashtable]$Headers) {
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
            if ($count -gt 1048576) { throw 'Cloud metadata exceeded the permitted bound.' }
            return [string]::new($buffer, 0, $count)
        } finally { $reader.Dispose() }
    } finally { $response.Dispose() }
}

$provider = [string]$command.parameters.provider
if ($provider -eq 'aws') {
    $token = Read-InstanceMetadata 'http://169.254.169.254/latest/api/token' 'PUT' @{ 'X-aws-ec2-metadata-token-ttl-seconds' = '60' }
    $instance = Read-InstanceMetadata 'http://169.254.169.254/latest/meta-data/instance-id' 'GET' @{ 'X-aws-ec2-metadata-token' = $token }
    if ($instance -cne [string]$scope.source_instance_id) { throw 'Guest AWS instance differs from the frozen source.' }
} elseif ($provider -eq 'azure') {
    $metadata = Read-InstanceMetadata 'http://169.254.169.254/metadata/instance?api-version=2021-02-01' 'GET' @{ Metadata = 'true' } | ConvertFrom-Json
    if ([string]$metadata.compute.resourceId -ine [string]$scope.source_instance_id) {
        throw 'Guest Azure VM differs from the frozen source.'
    }
} else {
    throw 'Source provider is not supported for Windows consistency.'
}

$disks = @(Get-Disk)
$physical = @(Get-CimInstance -ClassName Win32_DiskDrive)
$observed = @(foreach ($item in $volumes) {
    if ([string]$item.filesystem -ine 'ntfs' -or [string]::IsNullOrWhiteSpace([string]$item.partition_guid) -or
        [string]::IsNullOrWhiteSpace([string]$item.mount_point)) {
        throw 'Frozen Windows data volume lacks NTFS, GPT or mount identity.'
    }
    if ($provider -eq 'aws') {
        $wanted = ([string]$item.stable_id).Replace('-', '').ToLowerInvariant()
        $matches = @($disks | Where-Object {
            $serial = ([string]$_.SerialNumber).Trim() -replace '_00000001\.$', ''
            $serial.Replace('-', '').ToLowerInvariant() -eq $wanted
        })
    } else {
        $profile = @($metadata.compute.storageProfile.dataDisks | Where-Object {
            [string]$_.managedDisk.id -ieq [string]$item.stable_id
        })
        if ($profile.Count -ne 1) { throw 'Azure disk is not uniquely attached to the frozen VM.' }
        $lun = [int]$profile[0].lun
        $indices = @($physical | Where-Object { [int]$_.SCSILogicalUnit -eq $lun } | ForEach-Object { [int]$_.Index })
        $matches = @($disks | Where-Object { $indices -contains [int]$_.Number })
    }
    if ($matches.Count -ne 1 -or $matches[0].IsBoot -or $matches[0].IsSystem) {
        throw 'Windows data disk is ambiguous or system-owned.'
    }
    if ($action -in @('acquire', 'release_abort') -and ($matches[0].IsOffline -or $matches[0].IsReadOnly)) {
        throw 'Source data disk must be online and writable.'
    }
    if ($action -eq 'inspect_candidate' -and (-not $matches[0].IsOffline -or -not $matches[0].IsReadOnly)) {
        throw 'Candidate data disk is not offline and readonly before transfer.'
    }
    if ($action -eq 'activate_candidate' -and ($matches[0].IsOffline -ne $matches[0].IsReadOnly)) {
        throw 'Candidate data disk is neither isolated nor already activated.'
    }
    $disk = $matches[0]
    if ([long]$item.size_bytes -le 0 -or [long]$disk.Size -ne [long]$item.size_bytes) {
        throw 'Source disk size differs from the frozen replication plan.'
    }
    if ([string]$disk.PartitionStyle -ne 'GPT') { throw 'Windows data disk is not GPT.' }
    $parts = @(Get-Partition -DiskNumber $disk.Number -ErrorAction Stop | Where-Object {
        ([string]$_.Guid).Trim('{}') -ieq ([string]$item.partition_guid).Trim('{}')
    })
    if ($parts.Count -ne 1) { throw 'Windows GPT partition differs from the frozen plan.' }
    if ($action -eq 'activate_candidate') {
        if ($disk.IsOffline) {
            Set-Disk -Number $disk.Number -IsReadOnly $false -ErrorAction Stop
            Set-Disk -Number $disk.Number -IsOffline $false -ErrorAction Stop
        }
        $disk = Get-Disk -Number $disk.Number
        if ($disk.IsOffline -or $disk.IsReadOnly) { throw 'Candidate data disk did not gain write authority.' }
    }
    if ($action -ne 'inspect_candidate') {
        $parts = @(Get-Partition -DiskNumber $disk.Number -ErrorAction Stop | Where-Object {
            ([string]$_.Guid).Trim('{}') -ieq ([string]$item.partition_guid).Trim('{}')
        })
        if ($parts.Count -ne 1 -or -not (@($parts[0].AccessPaths) -icontains [string]$item.mount_point)) {
            throw 'Windows GPT partition or mount differs from the frozen plan.'
        }
        $volume = @($parts[0] | Get-Volume)
        if ($volume.Count -ne 1 -or [string]$volume[0].FileSystemType -ine 'ntfs') {
            throw 'Windows partition is not one observed NTFS filesystem.'
        }
    }
    $observedGuid = ([string]$parts[0].Guid).Trim('{}')
    $writesResumed = $false
    if ($action -eq 'release_abort') {
        $probeDirectory = Join-Path ([string]$item.mount_point) '.samurai-consistency'
        $directoryExisted = Test-Path -LiteralPath $probeDirectory
        New-Item -ItemType Directory -Force -Path $probeDirectory -ErrorAction Stop | Out-Null
        $probePath = Join-Path $probeDirectory ('abort-' + [Guid]::NewGuid().ToString('N') + '.bin')
        $payload = [Guid]::NewGuid().ToByteArray()
        try {
            $stream = [IO.FileStream]::new($probePath, [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write, [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough)
            try {
                $stream.Write($payload, 0, $payload.Length)
                $stream.Flush($true)
            } finally { $stream.Dispose() }
            $readBack = [IO.File]::ReadAllBytes($probePath)
            if ([BitConverter]::ToString($payload) -cne [BitConverter]::ToString($readBack)) {
                throw 'Source write probe did not survive an independent reread.'
            }
            $writesResumed = $true
        } finally {
            if (Test-Path -LiteralPath $probePath) { Remove-Item -LiteralPath $probePath -Force -ErrorAction Stop }
            if (-not $directoryExisted) { Remove-Item -LiteralPath $probeDirectory -ErrorAction Stop }
        }
    }
    [ordered]@{ stable_id = [string]$item.stable_id; disk_number = [int]$disk.Number;
        disk_serial = ([string]$disk.SerialNumber).Trim(); disk_size_bytes = [long]$disk.Size;
        partition_guid = $observedGuid; mount_point = [string]$item.mount_point;
        offline = [bool]$disk.IsOffline; readonly = [bool]$disk.IsReadOnly;
        writes_resumed = $writesResumed }
})
if ($observed.Count -ne $volumes.Count) { throw 'Required Windows volumes were not observed.' }
$resultState = @{ acquire = 'PREPARED'; inspect_candidate = 'CANDIDATE_READ_ONLY';
    activate_candidate = 'CANDIDATE_WRITABLE'; release_abort = 'WRITES_RESUMED' }[$action]
$Ansible.Result = @{ consistency_observation = @{ scope = $scope; state = $resultState;
    observed_volumes = $observed; observed_at = [DateTime]::UtcNow.ToString('o') } }
$Ansible.Changed = $action -in @('activate_candidate', 'release_abort')
