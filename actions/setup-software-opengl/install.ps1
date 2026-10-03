<#
.SYNOPSIS
Installs a checksum-verified x64 Mesa driver outside the game package.
.DESCRIPTION
Downloads an exact mesa-dist-win archive, verifies it before extraction, and copies the
x64 driver's directory, including dependent libraries. The caller owns the destination.
Returns the absolute opengl32.dll path. Requires 7-Zip and leaves no download staging.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $DestinationPath,
    [ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version = '26.2.0',
    [ValidatePattern('^[A-Fa-f0-9]{64}$')][string] $ExpectedSha256 = 'DCB2719EF346DAB5B609FCB193A5F13CFC4B0502E3F4DE1AD43D349477402F47'
)
$ErrorActionPreference = 'Stop'
$destination = [IO.Path]::GetFullPath($DestinationPath)
$staging = Join-Path ([IO.Path]::GetTempPath()) ('mesa-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    $name = "mesa3d-$Version-release-msvc.7z"
    $archive = Join-Path $staging $name
    Invoke-WebRequest -Uri "https://github.com/pal1000/mesa-dist-win/releases/download/$Version/$name" `
        -OutFile $archive -MaximumRetryCount 4 -RetryIntervalSec 5
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $ExpectedSha256) {
        throw "Unexpected SHA-256 for $name."
    }
    $zipper = Get-Command 7z -ErrorAction SilentlyContinue
    $zipCommand = if ($zipper.Source) { $zipper.Source } else { $zipper.Name }
    if (-not $zipCommand -and $env:ProgramFiles) { $zipCommand = Join-Path $env:ProgramFiles '7-Zip/7z.exe' }
    if (-not $zipCommand) { throw '7-Zip is required to expand the Mesa archive.' }
    $extracted = Join-Path $staging 'extracted'
    & $zipCommand x $archive "-o$extracted" -y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Mesa archive extraction failed.' }
    $drivers = @(Get-ChildItem -LiteralPath $extracted -Recurse -File -Filter 'opengl32.dll' |
        Where-Object { $_.FullName -match '(\\|/)x64(\\|/)' })
    if ($drivers.Count -ne 1) { throw 'Expected exactly one x64 OpenGL driver in the archive.' }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -Path (Join-Path $drivers[0].DirectoryName '*') -Destination $destination -Recurse -Force
    $installed = Join-Path $destination 'opengl32.dll'
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw 'Mesa driver installation failed.' }
    $installed
}
finally { Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue }
