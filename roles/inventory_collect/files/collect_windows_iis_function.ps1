$ErrorActionPreference = 'Stop'
$result = [ordered]@{schema_version='samurai.windows_iis_function_observation/v1';observed_at=[DateTimeOffset]::UtcNow.ToString('o');scope='IIS default local endpoint and service configuration';complete_application_function=$false;configuration_writes=0;service_mutations=0;services=@();http=[ordered]@{measured=$false;uri='http://127.0.0.1/';status=$null;body_sha256=$null;error=$null}}
try {
 $feature = Get-WindowsFeature -Name 'Web-Server' -ErrorAction Stop
 $result.feature_installed = [bool]$feature.Installed
 if ($feature.Installed) {
  $configPath = Join-Path $env:windir 'System32\inetsrv\config\applicationHost.config'
  $config = [xml](Get-Content -LiteralPath $configPath -Raw -ErrorAction Stop)
  $result.configuration_sha256=(Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
  $result.sites=@($config.configuration.'system.applicationHost'.sites.site | ForEach-Object {
   [ordered]@{name=$_.name;id=$_.id;bindings=@($_.bindings.binding | ForEach-Object {[ordered]@{protocol=$_.protocol;bindingInformation=$_.bindingInformation}});paths=@($_.application.virtualDirectory | ForEach-Object {$_.physicalPath})}
  })
  $result.services = @(Get-CimInstance Win32_Service -Filter "Name='W3SVC' OR Name='WAS'" -ErrorAction Stop | Select-Object Name,State,StartMode)
  try {
   $response = Invoke-WebRequest -Uri 'http://127.0.0.1/' -UseBasicParsing -TimeoutSec 10 -MaximumRedirection 0 -ErrorAction Stop
   $result.http.measured=$true;$result.http.status=[int]$response.StatusCode
   $sha=[System.Security.Cryptography.SHA256]::Create()
   try {$result.http.body_sha256=([BitConverter]::ToString($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes([string]$response.Content)))).Replace('-','').ToLowerInvariant()} finally {$sha.Dispose()}
  } catch {
   if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {$result.http.measured=$true;$result.http.status=[int]$_.Exception.Response.StatusCode}
   $result.http.error=$_.Exception.GetType().Name
  }
 }
 $result.observation_state='observed'
} catch {$result.observation_state='unavailable';$result.error=$_.Exception.GetType().Name}
$result | ConvertTo-Json -Depth 5 -Compress
