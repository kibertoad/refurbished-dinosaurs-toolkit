import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const installer = fileURLToPath(new URL('./install.ps1', import.meta.url));
const quote = value => "'" + value.replaceAll("'", "''") + "'";
for (const scenario of ['valid', 'checksum', 'extract', 'missing', 'ambiguous']) {
  test(`Mesa provisioning: ${scenario}`, t => {
    const dir = mkdtempSync(join(tmpdir(), 'mesa-control-'));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    const driver = join(dir, 'control.ps1');
    writeFileSync(driver, `
$ErrorActionPreference='Stop'
function global:Invoke-WebRequest { param($Uri,$OutFile,$MaximumRetryCount,$RetryIntervalSec)
  [IO.File]::WriteAllText($OutFile,'synthetic archive')
}
function global:7z {
  $out=($args | Where-Object { $_ -like '-o*' }).Substring(2)
  if ('${scenario}' -eq 'extract') { $global:LASTEXITCODE=7; return }
  New-Item -ItemType Directory -Path (Join-Path $out 'x64') -Force | Out-Null
  if ('${scenario}' -ne 'missing') {
    [IO.File]::WriteAllText((Join-Path $out 'x64/opengl32.dll'),'synthetic driver')
    [IO.File]::WriteAllText((Join-Path $out 'x64/gallium.dll'),'synthetic dependency')
  }
  if ('${scenario}' -eq 'ambiguous') {
    New-Item -ItemType Directory -Path (Join-Path $out 'other/x64') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $out 'other/x64/opengl32.dll'),'ambiguous driver')
  }
  $global:LASTEXITCODE=0
}
$bytes=[Text.Encoding]::UTF8.GetBytes('synthetic archive')
$hash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
if ('${scenario}' -eq 'checksum') { $hash='0'*64 }
$destination=Join-Path ${quote(dir)} 'driver'
$failure=$null
try { $installed=& ${quote(installer)} -DestinationPath $destination -ExpectedSha256 $hash }
catch { $failure=$_.Exception.Message }
if ('${scenario}' -eq 'valid') {
  if ($failure) { throw $failure }
  if ($installed -ne (Join-Path $destination 'opengl32.dll')) { throw 'Unexpected output path' }
  if (-not (Test-Path (Join-Path $destination 'gallium.dll'))) { throw 'Driver dependencies were lost' }
} else {
  $expected=@{checksum='SHA-256';extract='extraction failed';missing='exactly one';ambiguous='exactly one'}['${scenario}']
  if ($failure -notmatch $expected) { throw "Wrong failure: $failure" }
  if (Test-Path $destination) { throw 'Rejected archive created install output' }
}
if (Get-ChildItem ${quote(dir)} -Directory -Filter 'mesa-*') { throw 'Download staging leaked' }
`);
    const result = spawnSync(process.env.PWSH || 'pwsh', ['-NoProfile', '-File', driver], {
      encoding: 'utf8', timeout: 30000, env: { ...process.env, TMPDIR: dir, TEMP: dir, TMP: dir }
    });
    assert.equal(result.status, 0, result.error?.message || result.stdout + result.stderr);
  });
}
