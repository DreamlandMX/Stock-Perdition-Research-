[CmdletBinding()]
param(
    [string]$RepositoryRoot
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent $PSScriptRoot
}
$root = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$excludedSegments = @('.git', '.venv', 'node_modules', 'var')
$missing = New-Object System.Collections.Generic.List[object]
$checked = 0
$pattern = '!?(?:\[[^\]]*\])\((?<target>[^)]+)\)'

$markdownFiles = Get-ChildItem -LiteralPath $root -Filter '*.md' -File -Recurse |
    Where-Object {
        $relative = $_.FullName.Substring($root.Length).TrimStart('\', '/')
        -not ($excludedSegments | Where-Object {
            $relative -eq $_ -or $relative.StartsWith("$_\", [StringComparison]::OrdinalIgnoreCase)
        })
    }

foreach ($file in $markdownFiles) {
    $lineNumber = 0
    $inFence = $false
    foreach ($line in Get-Content -LiteralPath $file.FullName) {
        $lineNumber += 1
        if ($line -match '^\s*(?:```|~~~)') {
            $inFence = -not $inFence
            continue
        }
        if ($inFence) {
            continue
        }
        foreach ($match in [regex]::Matches($line, $pattern)) {
            $target = $match.Groups['target'].Value.Trim()
            if ($target.StartsWith('<') -and $target.EndsWith('>')) {
                $target = $target.Substring(1, $target.Length - 2)
            }
            elseif ($target -match '^(?<path>\S+)(?:\s+["''].*)?$') {
                $target = $Matches['path']
            }

            if ($target -match '^(?:https?://|mailto:|data:|#)') {
                continue
            }

            $pathOnly = ($target -split '[?#]', 2)[0]
            if ([string]::IsNullOrWhiteSpace($pathOnly)) {
                continue
            }

            $checked += 1
            $decoded = [Uri]::UnescapeDataString($pathOnly).Replace('/', [IO.Path]::DirectorySeparatorChar)
            $candidate = [IO.Path]::GetFullPath((Join-Path $file.DirectoryName $decoded))
            if (-not (Test-Path -LiteralPath $candidate)) {
                $missing.Add([pscustomobject]@{
                    file = $file.FullName.Substring($root.Length).TrimStart('\', '/')
                    line = $lineNumber
                    target = $target
                })
            }
        }
    }
}

[pscustomobject]@{
    status = if ($missing.Count -eq 0) { 'ok' } else { 'failed' }
    checked_links = $checked
    missing = $missing
} | ConvertTo-Json -Depth 4

if ($missing.Count -gt 0) {
    exit 1
}
