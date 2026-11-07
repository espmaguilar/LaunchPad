# Re-run this script any time your credentials expire or you open a new terminal.
# Usage: .\set_aws_creds.ps1

Write-Host "Paste your AWS credential lines from Workshop Studio, then press Enter on a blank line:" -ForegroundColor Cyan

$lines = @()
while ($true) {
    $line = Read-Host
    if ($line -eq "") { break }
    $lines += $line
}

# Parse credentials from export / $Env: / plain KEY=VALUE lines
$creds = @{}
foreach ($line in $lines) {
    $cleaned = $line.Trim() -replace '^export\s+', '' -replace '^set\s+', '' -replace '^\$Env:', ''
    if ($cleaned -match '^(AWS_[^=\s]+)\s*=\s*"?([^"]+?)"?\s*$') {
        $creds[$Matches[1]] = $Matches[2]
    }
}

$keyId     = $creds["AWS_ACCESS_KEY_ID"]
$secretKey = $creds["AWS_SECRET_ACCESS_KEY"]
$token     = $creds["AWS_SESSION_TOKEN"]
$region    = "us-east-1"

if (-not $keyId -or -not $secretKey -or -not $token) {
    Write-Host "`nERROR: Could not find all three credentials in what you pasted." -ForegroundColor Red
    Write-Host "Expected lines containing: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN" -ForegroundColor Yellow
    exit 1
}

# Apply to the current terminal session
$Env:AWS_ACCESS_KEY_ID     = $keyId
$Env:AWS_SECRET_ACCESS_KEY = $secretKey
$Env:AWS_SESSION_TOKEN     = $token
$Env:AWS_DEFAULT_REGION    = $region

# Persist via Windows registry (new standalone PowerShell/CMD windows)
[System.Environment]::SetEnvironmentVariable("AWS_ACCESS_KEY_ID",     $keyId,     "User")
[System.Environment]::SetEnvironmentVariable("AWS_SECRET_ACCESS_KEY", $secretKey, "User")
[System.Environment]::SetEnvironmentVariable("AWS_SESSION_TOKEN",     $token,     "User")
[System.Environment]::SetEnvironmentVariable("AWS_DEFAULT_REGION",    $region,    "User")

# Persist via PowerShell profile (new terminals inside VS Code)
$awsEnvFile = "$HOME\.aws_workshop_env.ps1"
@"
`$Env:AWS_ACCESS_KEY_ID     = "$keyId"
`$Env:AWS_SECRET_ACCESS_KEY = "$secretKey"
`$Env:AWS_SESSION_TOKEN     = "$token"
`$Env:AWS_DEFAULT_REGION    = "$region"
"@ | Set-Content -Path $awsEnvFile -Encoding UTF8

if (-not (Test-Path $PROFILE)) {
    New-Item -ItemType File -Path $PROFILE -Force | Out-Null
}
$profileHasSource = (Select-String -Path $PROFILE -Pattern "aws_workshop_env" -Quiet -ErrorAction SilentlyContinue)
if (-not $profileHasSource) {
    Add-Content -Path $PROFILE -Value "`nif (Test-Path `"$awsEnvFile`") { . `"$awsEnvFile`" }"
}

Write-Host "`nCredentials saved. Verifying with AWS..." -ForegroundColor Green
aws sts get-caller-identity
