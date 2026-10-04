import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const installer = fileURLToPath(new URL("./install.ps1", import.meta.url));
const quote = (value: string) => "'" + value.replaceAll("'", "''") + "'";

// Each scenario runs install.ps1 under PowerShell with a stand-in download and 7-Zip, so no real
// archive is fetched. A refused scenario names the message it must fail with.
const scenarios: Record<string, string | null> = {
  valid: null,
  checksum: "SHA-256",
  extract: "extraction failed",
  missing: "no x64/opengl32.dll",
  "no-7zip": "7-Zip is required",
  existing: "already exists",
};

for (const [scenario, refusal] of Object.entries(scenarios)) {
  test(`Mesa provisioning: ${scenario}`, (t) => {
    const dir = mkdtempSync(join(tmpdir(), "x64 [mesa control] "));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    const control = join(dir, "control.ps1");
    writeFileSync(
      control,
      `
$ErrorActionPreference='Stop'
$scenario=${quote(scenario)}
function global:Invoke-WebRequest { param($Uri,$OutFile,$MaximumRetryCount,$RetryIntervalSec)
  [IO.File]::WriteAllText($OutFile,'synthetic archive')
}
if ($scenario -eq 'no-7zip') { $env:PATH=''; $env:ProgramFiles=(Join-Path ${quote(dir)} 'no-program-files') }
else {
  function global:7z {
    if ($args -notcontains 'x64') { throw 'Extraction is not limited to x64.' }
    $out=($args | Where-Object { $_ -like '-o*' }).Substring(2)
    if ($scenario -eq 'extract') { $global:LASTEXITCODE=7; return }
    New-Item -ItemType Directory -Path (Join-Path $out 'x64/[sub]') -Force | Out-Null
    if ($scenario -ne 'missing') {
      [IO.File]::WriteAllText((Join-Path $out 'x64/opengl32.dll'),'synthetic driver')
      [IO.File]::WriteAllText((Join-Path $out 'x64/libgallium_wgl.dll'),'synthetic dependency')
      [IO.File]::WriteAllText((Join-Path $out 'x64/[sub]/nested.dll'),'synthetic nested library')
    }
    $global:LASTEXITCODE=0
  }
}
$hash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes('synthetic archive')))
if ($scenario -eq 'checksum') { $hash='0'*64 }
$destination=Join-Path ${quote(dir)} 'driver'
if ($scenario -eq 'existing') { New-Item -ItemType Directory -Path $destination | Out-Null }
$failure=$null
try { $installed=& ${quote(installer)} -DestinationPath $destination -ExpectedSha256 $hash }
catch { $failure=$_.Exception.Message }
if ($scenario -eq 'valid') {
  if ($failure) { throw $failure }
  if ($installed -ne (Join-Path $destination 'opengl32.dll')) { throw "Unexpected output path: $installed" }
  if (-not (Test-Path -LiteralPath (Join-Path $destination 'libgallium_wgl.dll'))) { throw 'Driver dependencies were lost.' }
  if (-not (Test-Path -LiteralPath (Join-Path $destination '[sub]/nested.dll'))) { throw 'Nested libraries were lost.' }
} else {
  if ($failure -notmatch ${quote(refusal ?? "")}) { throw "Wrong failure: $failure" }
  $leftover=Get-ChildItem -LiteralPath ${quote(dir)} -Directory -Filter 'driver'
  if ($scenario -eq 'existing') { if (-not $leftover) { throw 'The existing destination was removed.' } }
  elseif ($leftover) { throw 'Rejected archive created install output.' }
}
if (Get-ChildItem -LiteralPath ${quote(dir)} -Directory -Filter 'mesa-*') { throw 'Download staging leaked.' }
`,
    );
    const result = spawnSync(process.env["PWSH"] ?? "pwsh", ["-NoProfile", "-File", control], {
      encoding: "utf8",
      timeout: 60_000,
      env: { ...process.env, TMPDIR: dir, TEMP: dir, TMP: dir },
    });
    assert.equal(result.status, 0, result.error?.message ?? result.stdout + result.stderr);
  });
}
