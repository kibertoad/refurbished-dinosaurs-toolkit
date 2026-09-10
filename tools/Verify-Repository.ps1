[CmdletBinding()]
param(
    [string] $RepositoryRoot,
    [string] $PolicyPath
)

$ErrorActionPreference = 'Stop'
if (-not $RepositoryRoot) { $RepositoryRoot = Split-Path -Parent $PSScriptRoot }
$root = (Resolve-Path -LiteralPath $RepositoryRoot).Path
if (-not $PolicyPath) { $PolicyPath = Join-Path $root 'tools/repository-policy.json' }
$policy = Get-Content -LiteralPath $PolicyPath -Raw | ConvertFrom-Json
$safeRoot = $root.Replace('\', '/')

function Normalize-RepositoryPath([string] $Path) {
    $normalized = $Path.Replace('\', '/')
    if ($normalized.StartsWith('./', [StringComparison]::Ordinal)) { return $normalized.Substring(2) }
    return $normalized
}

function Starts-WithRepositoryRoot([string] $Path, [string[]] $Roots) {
    foreach ($candidate in $Roots) {
        $normalized = (Normalize-RepositoryPath $candidate).TrimStart('/')
        if ($Path.StartsWith($normalized, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

# Include untracked, non-ignored files so the check protects the next commit too.
$output = @(& git -c "safe.directory=$safeRoot" -c core.quotepath=false -C $root `
    ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) { throw "Unable to enumerate repository files under '$root'." }
$paths = @($output | Where-Object { $_ } | ForEach-Object { Normalize-RepositoryPath $_ } |
    Sort-Object -Unique)
$violations = [Collections.Generic.List[string]]::new()
$restricted = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($extension in $policy.restrictedExtensions) { [void] $restricted.Add([string] $extension) }

foreach ($path in $paths) {
    if (Starts-WithRepositoryRoot $path $policy.deniedRoots) {
        $violations.Add("local/imported content is eligible to be committed: $path")
        continue
    }
    $approvedRestrictedPath = Starts-WithRepositoryRoot $path $policy.approvedRestrictedRoots
    if ($restricted.Contains([IO.Path]::GetExtension($path)) -and -not $approvedRestrictedPath) {
        $violations.Add("restricted media outside an approved clean-room/synthetic root: $path")
    }
    $absolutePath = Join-Path $root $path
    if (-not [IO.File]::Exists($absolutePath)) { continue }
    $length = [IO.FileInfo]::new($absolutePath).Length
    if ($length -gt $policy.maximumTrackedFileBytes -and $policy.approvedLargeFiles -notcontains $path) {
        $violations.Add("unreviewed large file ($length bytes): $path")
    }
}

if ($violations.Count -gt 0) {
    Write-Error ("Repository policy failed:`n - " + ($violations -join "`n - "))
    exit 1
}
Write-Host "Repository policy passed for $($paths.Count) tracked and candidate files."
