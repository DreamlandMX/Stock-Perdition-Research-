param(
    [Parameter(Mandatory = $true)]
    [string]$CredentialFile
)

$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $CredentialFile).Path
try {
    $payload = Get-Content -LiteralPath $resolved -Raw | ConvertFrom-Json
    $key = [string]$payload.key
    $secret = [string]$payload.secret
    if ($key -notmatch '^PK[A-Z0-9]{20,40}$') { throw "alpaca_key_format_invalid" }
    if ($secret -notmatch '^[A-Za-z0-9]{40,60}$') { throw "alpaca_secret_format_invalid" }

    [Environment]::SetEnvironmentVariable("APCA_API_KEY_ID", $key, "User")
    [Environment]::SetEnvironmentVariable("APCA_API_SECRET_KEY", $secret, "User")

    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $keyHash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($key))
        $keyFingerprint = ([BitConverter]::ToString($keyHash)).Replace("-", "").Substring(0, 8)
        $secretHash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($secret))
        $secretFingerprint = ([BitConverter]::ToString($secretHash)).Replace("-", "").Substring(0, 8)
    }
    finally { $sha.Dispose() }

    Write-Output "credentials_configured key_length=$($key.Length) key_fp=$keyFingerprint secret_length=$($secret.Length) secret_fp=$secretFingerprint"
}
finally {
    Remove-Item -LiteralPath $resolved -Force -ErrorAction SilentlyContinue
    $key = $null
    $secret = $null
    $payload = $null
}
