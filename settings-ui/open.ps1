# Open the aivis-speech settings window (starts the local settings server if needed).
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = 'http://127.0.0.1:18890/'
function Test-Server { try { $null = (New-Object System.Net.WebClient).DownloadString($url + 'api/ping'); $true } catch { $false } }
if (-not (Test-Server)) {
  $py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
  if (-not $py) { $py = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
  if (-not $py) { Write-Error 'Python was not found. Install Python 3.10+ and run /aivis setup.'; exit 1 }
  Start-Process -FilePath $py -ArgumentList @((Join-Path $here 'server.py')) -WindowStyle Hidden
  for ($i = 0; $i -lt 30 -and -not (Test-Server); $i++) { Start-Sleep -Milliseconds 200 }
}
$chrome = @('C:\Program Files\Google\Chrome\Application\chrome.exe', 'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) { Start-Process -FilePath $chrome -ArgumentList @("--app=$url", '--window-size=560,820') } else { Start-Process $url }
