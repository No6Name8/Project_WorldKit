# Resets the live demo: a clean copy of the mug run at v0, the edit server and the scene viewer started, the browser opened.
#   powershell -ExecutionPolicy Bypass -File scripts\demo_reset.ps1 [-Source pipeline\runs\mug_grounded] [-NoBrowser]
# Object + AI Edit:  http://127.0.0.1:5070/?demo=1   (worldkit.serve on runs\demo_live; needs MODEL_API_KEY in pipeline\.env)
# Map scenes:        http://127.0.0.1:5050/?demo=1   (viewer\server.py; Map > Demo run, Developer tools hidden in demo mode)
param([string]$Source = "", [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root "pipeline\.venv\Scripts\python.exe"
if (-not $Source) {
  $Source = Join-Path $Root "pipeline\runs\mug_grounded"
  if (-not (Test-Path (Join-Path $Source "model_print.stl"))) { $Source = Join-Path $Root "viewer\assets\demo\run_mug" }  # the tracked copy of the same run
}
$Live = Join-Path $Root "pipeline\runs\demo_live"

# stop servers left from an earlier take (by command line: a stale server keeps serving old files)
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "python.exe" -and ($_.CommandLine -match "worldkit\.serve" -or $_.CommandLine -match "viewer[\\/]server\.py") } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# clean copy at v0: no edit history
if (Test-Path $Live) { Remove-Item -Recurse -Force $Live }
New-Item -ItemType Directory -Force $Live | Out-Null
foreach ($f in "model.glb", "model_print.stl", "report.json") { Copy-Item (Join-Path $Source $f) $Live }
if (Test-Path (Join-Path $Source "parts")) { Copy-Item -Recurse (Join-Path $Source "parts") $Live }
Write-Host "demo_live reset to v0 from $Source"

Start-Process -FilePath $Py -ArgumentList "-m", "worldkit.serve", $Live, "--port", "5070" -WorkingDirectory (Join-Path $Root "pipeline") -WindowStyle Minimized
Start-Process -FilePath $Py -ArgumentList (Join-Path $Root "viewer\server.py") -WorkingDirectory $Root -WindowStyle Minimized
foreach ($port in 5070, 5050) {
  for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/api/health" -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 } }
}
Write-Host "edit server http://127.0.0.1:5070/?demo=1   scene viewer http://127.0.0.1:5050/?demo=1"
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:5070/?demo=1" }
