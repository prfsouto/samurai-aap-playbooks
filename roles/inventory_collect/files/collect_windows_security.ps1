# Native observations only. Failed reads terminate instead of publishing absence.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
if ($null -eq $cv.UBR -or -not $cv.CurrentBuild -or -not $cv.InstallationType) {
    throw 'Windows build, UBR or installation type was not observed'
}
function Get-RebootObservation {
    $markers = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootInProgress',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\PackagesPending',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    )
    $present = @($markers | Where-Object { Test-Path -LiteralPath $_ })
    $session = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager'
    $renamePending = $null -ne $session.PendingFileRenameOperations
    [pscustomobject]@{ pending = ($present.Count -gt 0 -or $renamePending) }
}
$rebootBefore = Get-RebootObservation
$bootBefore = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
$hotfixes = @(Get-HotFix | ForEach-Object { $_.HotFixID } | Sort-Object)
function Get-RuntimeVersions([string]$Name) {
    $versions = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $base) { continue }
        $path = Join-Path $base "dotnet\shared\$Name"
        if (Test-Path -LiteralPath $path) {
            $versions += @(Get-ChildItem -LiteralPath $path -Directory | ForEach-Object { $_.Name })
        }
    }
    return @($versions | Sort-Object -Unique)
}
$edge = $null
foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
    if (-not $base) { continue }
    $path = Join-Path $base 'Microsoft\Edge\Application\msedge.exe'
    if (Test-Path -LiteralPath $path) {
        $version = (Get-Item -LiteralPath $path).VersionInfo.ProductVersion
        if (-not $version) { throw 'Edge executable version was not observed' }
        if ($edge -and $edge -ne $version) { throw 'Distinct Edge executable versions require reconciliation' }
        $edge = $version
    }
}
$framework = $null
$frameworkPath = 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full'
if (Test-Path -LiteralPath $frameworkPath) {
    $value = Get-ItemProperty -LiteralPath $frameworkPath
    $framework = [pscustomobject]@{ release = $value.Release; version = [string]$value.Version }
}
$rebootAfter = Get-RebootObservation
$bootAfter = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
$hotfixesAfter = @(Get-HotFix | ForEach-Object { $_.HotFixID } | Sort-Object)
if ($rebootBefore.pending -ne $rebootAfter.pending -or $bootBefore -ne $bootAfter -or
    ($hotfixes -join ',') -ne ($hotfixesAfter -join ',')) {
    throw 'Windows patch/reboot observation changed during collection'
}
[pscustomobject]@{
    schema = 'samurai.windows_security_inventory/v1'
    status = 'observed'
    current_build = [string]$cv.CurrentBuild
    ubr = $cv.UBR
    installation_type = [string]$cv.InstallationType
    hotfixes = $hotfixes
    reboot_pending = $rebootAfter.pending
    last_boot_at = $bootAfter
    patch_observation_stable = $true
    edge = $edge
    dotnet = @(Get-RuntimeVersions 'Microsoft.NETCore.App')
    aspnetcore = @(Get-RuntimeVersions 'Microsoft.AspNetCore.App')
    windowsdesktop = @(Get-RuntimeVersions 'Microsoft.WindowsDesktop.App')
    dotnet_framework = $framework
} | ConvertTo-Json -Depth 5 -Compress
