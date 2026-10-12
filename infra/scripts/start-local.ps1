$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'local-env.ps1')
$pythonPath = Join-Path $projectRoot 'apps/api/.venv/Scripts/python.exe'
$runtimeProcesses = @{}
$processFile = Join-Path $runtimeDirectory 'runtime-processes.json'
if (Test-Path -LiteralPath $processFile) {
    $runtimeProcesses = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json -AsHashtable
}
foreach ($name in @('api', 'agents')) {
    if ($runtimeProcesses.ContainsKey($name) -and (Get-Process -Id $runtimeProcesses[$name] -ErrorAction SilentlyContinue)) {
        throw "$name is already running. Stop it before restarting."
    }
}
$redisDirectory = Join-Path $projectRoot $config.redis_directory
$redisCli = Join-Path $redisDirectory 'redis-cli.exe'
$redisExecutable = Join-Path $redisDirectory 'redis-server.exe'
if (-not (Test-Path -LiteralPath $redisCli) -or -not (Test-Path -LiteralPath $redisExecutable)) {
    throw 'Set redis_directory in data/runtime-config.json to the installed Redis directory.'
}
$redisPing = & $redisCli -h 127.0.0.1 -p 6379 PING 2>$null
if ($redisPing -ne 'PONG') {
    $redisProcess = Start-Process -FilePath $redisExecutable -ArgumentList 'redis.conf' -WorkingDirectory (Join-Path $runtimeDirectory 'redis') -WindowStyle Hidden -PassThru
    $runtimeProcesses.redis = $redisProcess.Id
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        Start-Sleep -Milliseconds 300
        $redisPing = & $redisCli -h 127.0.0.1 -p 6379 PING 2>$null
        if ($redisPing -eq 'PONG') { break }
    }
    if ($redisPing -ne 'PONG') { throw 'Redis did not become ready.' }
}
Push-Location (Join-Path $projectRoot 'apps/api')
try {
    & $pythonPath -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
} finally { Pop-Location }
$apiProcess = Start-Process -FilePath $pythonPath -ArgumentList '-m uvicorn app.main:app --host 127.0.0.1 --port 8080 --no-access-log' -WorkingDirectory (Join-Path $projectRoot 'apps/api') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDirectory 'api.log') -RedirectStandardError (Join-Path $runtimeDirectory 'api-error.log') -PassThru
$agentProcess = Start-Process -FilePath $pythonPath -ArgumentList '-m uvicorn app:app --host 127.0.0.1 --port 8090 --no-access-log' -WorkingDirectory (Join-Path $projectRoot 'apps/agents') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDirectory 'agents.log') -RedirectStandardError (Join-Path $runtimeDirectory 'agents-error.log') -PassThru
$runtimeProcesses.api = $apiProcess.Id
$runtimeProcesses.agents = $agentProcess.Id
$runtimeProcesses | ConvertTo-Json | Set-Content -LiteralPath $processFile -Encoding utf8
Write-Output 'rubai API and agent runtime started on loopback. Check /readyz before using the site.'
