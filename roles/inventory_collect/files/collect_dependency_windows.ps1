$ErrorActionPreference = 'Stop'

function ConvertFrom-DependencyRequestJson([string]$Json) {
    $request = ConvertFrom-Json -InputObject $Json
    if ($request.policy.valid_from -isnot [string] -or $request.policy.valid_until -isnot [string]) {
        # PowerShell date coercion must not change the frozen policy's JSON content.
        $text = [IO.StringReader]::new($Json)
        $reader = [Newtonsoft.Json.JsonTextReader]::new($text)
        $reader.DateParseHandling = [Newtonsoft.Json.DateParseHandling]::None
        try {
            $original = [Newtonsoft.Json.Linq.JObject]::Load($reader)
            $request.policy.valid_from = [string]$original['policy']['valid_from']
            $request.policy.valid_until = [string]$original['policy']['valid_until']
        } finally {
            $reader.Dispose()
            $text.Dispose()
        }
    }
    return $request
}

function ConvertTo-StableJson($Value) {
    $json = ConvertTo-Json -InputObject $Value -Depth 20 -Compress
    $builder = New-Object System.Text.StringBuilder
    foreach ($character in $json.ToCharArray()) {
        if ([int]$character -gt 127) {
            [void]$builder.Append(('\u{0:x4}' -f [int]$character))
        } else {
            [void]$builder.Append($character)
        }
    }
    return $builder.ToString()
}

function Get-RecordDigest($Records) {
    $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-StableJson @($Records)))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return (($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join '') }
    finally { $sha.Dispose() }
}

function Assert-DependencyBudget {
    if ([DateTime]::UtcNow -ge $script:Deadline) { throw 'duration_limit' }
    if ($script:BytesRead -ge $script:Limits.max_bytes) { throw 'byte_limit' }
}

function Start-DependencyRead {
    Assert-DependencyBudget
    if ($script:Calls -ge $script:Limits.max_calls) { throw 'call_limit' }
    $script:Calls++
}

function Add-DependencyRecord($Record, $Source, $Records) {
    Assert-DependencyBudget
    if ($Records.Count -ge $script:Limits.max_records_per_source) { throw 'record_limit' }
    $projected = [ordered]@{}
    foreach ($field in ($Source.fields | Sort-Object)) {
        $value = $Record[$field]
        if ($null -eq $value -or ([string]$value).Length -eq 0) { throw 'source_record_invalid' }
        if ([Text.Encoding]::UTF8.GetByteCount([string]$value) -gt $script:Limits.max_field_bytes) { throw 'field_limit' }
        if ($value -is [string] -and $value -match '[\x00-\x1f]') { throw 'source_record_invalid' }
        $projected[$field] = $value
    }
    $size = [Text.Encoding]::UTF8.GetByteCount((ConvertTo-StableJson $projected))
    if ($script:BytesRead + $size -gt $script:Limits.max_bytes) { throw 'byte_limit' }
    $script:BytesRead += $size
    [void]$Records.Add($projected)
}

function Get-DependencyServiceIdentity([string]$Name) {
    if ($Name -cmatch '[^\x00-\x7f]') { throw 'service_identity_unresolved' }
    return $Name.ToLowerInvariant()
}

function Assert-DependencyRequest($Request) {
    $keys = @($Request.PSObject.Properties.Name | Sort-Object)
    if (($keys -join ',') -ne 'collector,limits,policy,sources') { throw 'explicit_request_required' }
    if ($Request.collector.profile -ne 'windows-local-dependencies' -or
        $Request.collector.version -ne '1' -or $Request.collector.platform -ne 'windows') { throw 'unsupported_profile' }
    foreach ($key in @('max_hosts','max_calls','max_bytes','max_raw_bytes','max_duration_seconds',
                       'max_records_per_source','max_field_bytes','retention_seconds')) {
        $value = $Request.limits.$key
        if (($value -isnot [int] -and $value -isnot [long]) -or $value -le 0) { throw 'explicit_limits_required' }
    }
    if ($Request.limits.max_hosts -ne 1 -or -not $Request.limits.budget_ref) { throw 'explicit_budget_required' }
    foreach ($key in @('policy_id','policy_version','catalog_sha256','decision_3_ref','decision_6_ref')) {
        if (-not $Request.policy.$key) { throw 'approved_policy_required' }
    }
    $from = [DateTimeOffset]::Parse($Request.policy.valid_from)
    $until = [DateTimeOffset]::Parse($Request.policy.valid_until)
    $now = [DateTimeOffset]::UtcNow
    if ($from -gt $now -or $until -le $now) { throw 'policy_not_valid' }
    $allowed = @{
        processes = @('name','pid'); services = @('name','pid','state')
        listening = @('local_address','local_port','pid','protocol','state')
        connections = @('local_address','local_port','pid','protocol','remote_address','remote_port','state')
        configuration = @('name','startup_mode')
    }
    $kinds = @{ processes = 'windows_process'; services = 'windows_cim_service'; listening = 'windows_net_tcp'
                connections = 'windows_net_tcp'; configuration = 'windows_cim_service_properties' }
    $seen = @{}
    if (@($Request.sources).Count -eq 0) { throw 'explicit_sources_required' }
    foreach ($source in $Request.sources) {
        $dimension = $source.dimension
        if (-not $allowed.ContainsKey($dimension) -or $seen.ContainsKey($dimension) -or
            $source.source_kind -ne $kinds[$dimension]) { throw 'unsupported_source' }
        $seen[$dimension] = $true
        $fields = @($source.fields)
        if (@($fields | Sort-Object -Unique).Count -ne $fields.Count -or
            @($fields | Where-Object { $_ -notin $allowed[$dimension] }).Count -gt 0) { throw 'unsafe_projection' }
        $required = @($allowed[$dimension] | Where-Object { $_ -ne 'pid' })
        if ($dimension -eq 'processes') { $required = @('name','pid') }
        if (@($required | Where-Object { $_ -notin $fields }).Count -gt 0) { throw 'incomplete_projection' }
        $scope = if ($dimension -eq 'processes') { 'visible_processes' }
                 elseif ($dimension -in @('services','configuration')) { 'named_services' } else { 'local_network_stack' }
        if ($source.scope -ne $scope) { throw 'unsupported_scope' }
        if ($scope -eq 'named_services') {
            if (@($source.service_names).Count -eq 0) { throw 'exact_service_allowlist_required' }
            $names = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($name in $source.service_names) {
                if ($name -cnotmatch '\A[A-Za-z0-9][A-Za-z0-9_.@:-]*\z' -or
                    -not $names.Add((Get-DependencyServiceIdentity $name))) {
                    throw 'exact_service_allowlist_required'
                }
            }
        } elseif (@($source.service_names).Count -ne 0) { throw 'invalid_service_selector' }
        $validity = $Request.limits.signal_validity_seconds.$dimension
        if (($validity -isnot [int] -and $validity -isnot [long]) -or $validity -le 0) { throw 'explicit_validity_required' }
    }
}

function Invoke-DependencyCollection($Request) {
    Assert-DependencyRequest $Request
    $started = [DateTime]::UtcNow.ToString('o')
    $script:Limits = $Request.limits
    $script:Deadline = [DateTime]::UtcNow.AddSeconds($script:Limits.max_duration_seconds)
    $script:Calls = 0
    $script:BytesRead = 0
    $results = New-Object 'System.Collections.Generic.List[object]'
    foreach ($dimension in @('processes','services','listening','connections','configuration')) {
        $source = @($Request.sources | Where-Object { $_.dimension -eq $dimension }) | Select-Object -First 1
        $records = New-Object 'System.Collections.Generic.List[object]'
        $errors = @()
        $state = 'not_collected'; $reason = 'not_requested'; $truncated = $false
        if ($null -ne $source) {
            try {
                if ($dimension -eq 'processes') {
                    Start-DependencyRead
                    Get-Process -ErrorAction Stop | ForEach-Object {
                        Add-DependencyRecord @{ name = [string]$_.ProcessName; pid = [int]$_.Id } $source $records
                    }
                } elseif ($dimension -in @('services','configuration')) {
                    $seenServices = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
                    foreach ($name in $source.service_names) {
                        $expectedIdentity = Get-DependencyServiceIdentity $name
                        Start-DependencyRead
                        Get-CimInstance -ClassName Win32_Service -Filter "Name='$name'" -Property Name,State,ProcessId,StartMode -ErrorAction Stop |
                            ForEach-Object {
                                $identity = Get-DependencyServiceIdentity ([string]$_.Name)
                                if ($identity -cne $expectedIdentity) { throw 'service_identity_unresolved' }
                                if (-not $seenServices.Add($identity)) { throw 'service_identity_repeated' }
                                $record = if ($dimension -eq 'services') {
                                    @{ name = [string]$_.Name; pid = [int]$_.ProcessId; state = ([string]$_.State).ToLowerInvariant() }
                                } else { @{ name = [string]$_.Name; startup_mode = [string]$_.StartMode } }
                                Add-DependencyRecord $record $source $records
                            }
                    }
                } else {
                    Start-DependencyRead
                    $tcpState = if ($dimension -eq 'listening') { 'Listen' } else { 'Established' }
                    Get-NetTCPConnection -State $tcpState -ErrorAction Stop | ForEach-Object {
                        $record = @{ protocol = 'tcp'; local_address = [string]$_.LocalAddress; local_port = [int]$_.LocalPort
                                     pid = [int]$_.OwningProcess; state = if ($dimension -eq 'listening') { 'listening' } else { 'established' } }
                        if ($dimension -eq 'connections') {
                            $record.remote_address = [string]$_.RemoteAddress; $record.remote_port = [int]$_.RemotePort
                        }
                        Add-DependencyRecord $record $source $records
                    }
                }
                Assert-DependencyBudget
                $state = if ($records.Count -gt 0) { 'complete_for_scope' } else { 'empty_confirmed' }
                $reason = ''
            } catch {
                $code = [string]$_.Exception.Message
                if ($code -ceq 'service_identity_repeated') { throw }
                if ($code -in @('duration_limit','byte_limit','call_limit','record_limit','field_limit')) {
                    $state = 'partial'; $reason = $code; $truncated = $true
                } elseif ($_.Exception -is [UnauthorizedAccessException] -or $_.CategoryInfo.Category -eq 'PermissionDenied') {
                    $state = if ($records.Count -gt 0) { 'partial' } else { 'permission_denied' }; $reason = 'permission_denied'
                } elseif ($dimension -in @('listening','connections') -and $records.Count -eq 0 -and
                          $_.CategoryInfo.Category -eq 'ObjectNotFound' -and
                          $_.FullyQualifiedErrorId -eq 'CmdletizationQuery_NotFound,Get-NetTCPConnection') {
                    $state = 'empty_confirmed'; $reason = ''
                } else {
                    $state = if ($records.Count -gt 0) { 'partial' } else { 'unavailable' }; $reason = 'source_read_failed'
                }
                if ($reason) { $errors = @($reason) }
            }
        }
        $orderedRecords = @($records | Sort-Object { ConvertTo-StableJson $_ })
        [void]$results.Add([ordered]@{ dimension = $dimension; requested = $source; state = $state; reason = $reason
                                    truncated = $truncated; records = $orderedRecords
                                    records_sha256 = (Get-RecordDigest $orderedRecords); errors = $errors })
    }
    return [ordered]@{ schema = 'samurai.dependency_collection/v1'; collector = $Request.collector
                      policy = $Request.policy; limits = $Request.limits; observed_from = $started
                      observed_to = [DateTime]::UtcNow.ToString('o'); sources = $results.ToArray()
                      usage = @{ calls = $script:Calls; bytes_read = $script:BytesRead; byte_measurement = 'projected_records' } }
}
