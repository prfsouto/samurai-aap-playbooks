$ErrorActionPreference = 'Stop'
$disks = @(Get-Disk)
$physicalDisks = @(Get-CimInstance -ClassName Win32_DiskDrive)
if ($disks.Count -eq 0 -or $physicalDisks.Count -eq 0) {
    throw 'No disk inventory was observed.'
}
if (Compare-Object @($physicalDisks.Index | Sort-Object) @($disks.Number | Sort-Object)) {
    throw 'Disk enumeration is incomplete; dynamic or unsupported storage requires review.'
}
$partitions = @(Get-Partition)
$volumes = @(Get-Volume)
$observed = @(
    foreach ($disk in $disks) {
        if ([string]::IsNullOrWhiteSpace($disk.Path)) {
            throw 'An observed disk has no native device path.'
        }
        [pscustomobject]@{
            path = [string]$disk.Path
            serial_number = ([string]$disk.SerialNumber).Trim()
            size = [long]$disk.Size
            number = [int]$disk.Number
            partition_style = [string]$disk.PartitionStyle
            bus_type = [string]$disk.BusType
            clustered = [bool]$disk.IsClustered
            partitions = @(
                foreach ($partition in @($partitions | Where-Object DiskNumber -eq $disk.Number)) {
                    $accessPaths = @($partition.AccessPaths | Where-Object { $_ })
                    [pscustomobject]@{
                        number = [int]$partition.PartitionNumber
                        guid = [string]$partition.Guid
                        size = [long]$partition.Size
                        access_paths = $accessPaths
                        volumes = @(
                            foreach ($volume in @($volumes | Where-Object { $accessPaths -contains $_.Path })) {
                                [pscustomobject]@{
                                    path = [string]$volume.Path
                                    type = [string]$volume.FileSystemType
                                    size = [long]$volume.Size
                                }
                            }
                        )
                    }
                }
            )
        }
    }
)
ConvertTo-Json -InputObject @{ schema_version = 1; disks = $observed } -Depth 8 -Compress
