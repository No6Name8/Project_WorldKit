# Runs every WorldKit test suite and prints one summary line per suite (Windows PowerShell 5.1+).
#   powershell -ExecutionPolicy Bypass -File scripts\test_all.ps1          # offline suites
#   powershell -ExecutionPolicy Bypass -File scripts\test_all.ps1 -Live    # also the real-Supabase test and the live-site check
# Needs: pipeline\.venv (Python 3.10) and `npm install` in viewer\tests and web. Three browser suites (viewer, integration, sam3d)
# make one real Muse Spark call each and need MODEL_API_KEY in pipeline\.env; without it they stop at that step.
param([switch]$Live)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root "pipeline\.venv\Scripts\python.exe"
$results = @()

function Run-Suite($name, $workdir, $exe, $argList) {
  Write-Host "== $name"
  Push-Location $workdir
  $out = & $exe @argList 2>&1 | Out-String
  $code = $LASTEXITCODE
  Pop-Location
  $summary = ($out -split "`r?`n" | Where-Object { $_ -match "passed|checks passed|^# (pass|fail) " } | Select-Object -Last 2) -join "; "
  if ($code -ne 0) { Write-Host ($out -split "`r?`n" | Where-Object { $_ -match "^FAIL|CRASH|Error" } | Select-Object -First 5 | Out-String) }
  $script:results += [pscustomobject]@{ Suite = $name; Result = $(if ($code -eq 0) { "PASS" } else { "FAIL" }); Summary = $summary.Trim() }
}

Run-Suite "pytest" $Root $Py @("-m", "pytest", "pipeline\tests", "-q", "-p", "no:cacheprovider")

# viewer server for the browser suites (port 5050)
$server = Start-Process -FilePath $Py -ArgumentList "viewer\server.py" -WorkingDirectory $Root -PassThru -WindowStyle Hidden
for ($i = 0; $i -lt 30; $i++) { try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5050/ -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 } }
try {
  foreach ($t in "browser_test", "integration_test", "sam3d_browser_test", "scene_physics_browser_test", "demo_readiness_test", "ai_edit_browser_test", "map_scene_browser_test", "parts_browser_test", "replay_browser_test", "source_browser_test") {
    Run-Suite "viewer/$t" (Join-Path $Root "viewer\tests") "node" @("$t.js")
  }
} finally { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }

Run-Suite "web unit" (Join-Path $Root "web") "node" @("--test", "tests/lib.test.js")
Run-Suite "web e2e (mocked Supabase)" $Root "node" @("web/tests/e2e_mock.mjs")
if ($Live) {
  Run-Suite "web e2e (real Supabase)" $Root "node" @("web/tests/e2e_real.mjs")
  Run-Suite "live site (check_live)" $Root "node" @("scripts/check_live.mjs")
}

$results | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
if ($results | Where-Object { $_.Result -ne "PASS" }) { exit 1 }
