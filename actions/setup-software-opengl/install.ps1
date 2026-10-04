#Requires -Version 7
<#
.SYNOPSIS
Installs a checksum-verified x64 Mesa driver outside the game package.
.DESCRIPTION
Downloads an exact mesa-dist-win archive, verifies it before extraction, and copies the archive's
x64 directory: opengl32.dll and the libraries it loads, such as libgallium_wgl.dll. The destination
must not exist yet; it is created only once the driver is in place and removed if copying fails.
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
if (Test-Path -LiteralPath $destination) { throw "Destination $destination already exists." }
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
    $zipCommand = if ($zipper) { if ($zipper.Source) { $zipper.Source } else { $zipper.Name } }
    elseif ($env:ProgramFiles -and (Test-Path -LiteralPath (Join-Path $env:ProgramFiles '7-Zip/7z.exe') -PathType Leaf)) {
        Join-Path $env:ProgramFiles '7-Zip/7z.exe'
    }
    if (-not $zipCommand) { throw '7-Zip is required to expand the Mesa archive.' }
    $extracted = Join-Path $staging 'extracted'
    # Only the x64 directory is needed; the archive also holds the x86 build.
    & $zipCommand x $archive "-o$extracted" -y x64 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Mesa archive extraction failed.' }
    $driverDirectory = Join-Path $extracted 'x64'
    if (-not (Test-Path -LiteralPath (Join-Path $driverDirectory 'opengl32.dll') -PathType Leaf)) {
        throw 'The archive has no x64/opengl32.dll.'
    }
    try {
        New-Item -ItemType Directory -Path $destination | Out-Null
        Get-ChildItem -LiteralPath $driverDirectory -Force |
            Copy-Item -Destination $destination -Recurse -Force
    }
    catch {
        Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
        throw
    }
    Join-Path $destination 'opengl32.dll'
}
finally { Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue }
