$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$runtimeDirectory = Join-Path $projectRoot 'data'
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$config = Get-Content -LiteralPath (Join-Path $runtimeDirectory 'runtime-config.json') -Raw | ConvertFrom-Json
$secretPath = Join-Path $runtimeDirectory 'runtime-secrets.json'
$protectedValues = @{}
if (Test-Path -LiteralPath $secretPath) {
    $protectedValues = Get-Content -LiteralPath $secretPath -Raw | ConvertFrom-Json -AsHashtable
}
foreach ($name in @('API_PROXY_TOKEN', 'AGENT_RUNTIME_TOKEN', 'PROVIDER_KEY_ENCRYPTION_KEY', 'OPENROUTER_API_KEY')) {
    if ($protectedValues.ContainsKey($name)) {
        $secureValue = ConvertTo-SecureString $protectedValues[$name]
        $value = [System.Net.NetworkCredential]::new('', $secureValue).Password
    } else {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($value)) {
            if ($name -eq 'OPENROUTER_API_KEY') { throw 'Configure OPENROUTER_API_KEY before starting rubai.' }
            $randomBytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
            $value = [Convert]::ToBase64String($randomBytes).Replace('+', '-').Replace('/', '_')
        }
        $protectedValues[$name] = ConvertFrom-SecureString (ConvertTo-SecureString $value -AsPlainText -Force)
    }
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}
# Windows DPAPI protects these values for this Windows account; Git ignores data/.
$protectedValues | ConvertTo-Json | Set-Content -LiteralPath $secretPath -Encoding utf8
$env:PUBLIC_ORIGIN = $config.public_origin
$env:APP_ENV = 'production'
$env:REDIS_URL = 'redis://127.0.0.1:6379/0'
$env:AGENT_RUNTIME_URL = 'http://127.0.0.1:8090'
$env:PLATFORM_API_BASE = 'http://127.0.0.1:8080/v1'
$env:HERMES_AGENT_DIR = Join-Path $env:LOCALAPPDATA 'hermes/hermes-agent'
$env:HERMES_PYTHON = Join-Path $env:HERMES_AGENT_DIR 'venv/Scripts/python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$pythonPath = Join-Path $projectRoot 'apps/api/.venv/Scripts/python.exe'
Push-Location (Join-Path $projectRoot 'apps/api')
try {
    $env:RUBAI_POSTGRES_DATA = Join-Path $runtimeDirectory 'postgres'
    $env:DATABASE_URL = & $pythonPath -c "import os; from app.local_postgres import start_postgres; print(start_postgres(os.environ['RUBAI_POSTGRES_DATA']).get_uri().replace('postgresql://','postgresql+asyncpg://',1))"
    if ($LASTEXITCODE -ne 0) { throw 'Could not start local PostgreSQL.' }
} finally { Pop-Location }
