# verify.ps1 — comprobación end-to-end del proyecto Realtor AI.
#
# Qué hace, en este orden:
#   1. Comprueba pre-requisitos (Python 3.12+, Node.js 22.13+, Docker, pip).
#   2. Usa un venv existente; solo lo crea con -BootstrapDependencies.
#   3. Solo instala/actualiza dependencias con -BootstrapDependencies.
#   4. (Opcional y explícito) levanta servicios locales con Docker.
#   5. Aplica migraciones solo con -ApplyMigrations; una DB remota exige
#      además -AllowRemoteDatabaseMutation.
#   6. Corre las pruebas de producto.
#   7. Verifica panel web y app movil (tipos, build y auth segura).
#   8. Lanza uvicorn en background y hace un health check.
#
# Cualquier paso que falle se reporta con un [FAIL] claro y aborta. Los pasos
# opcionales (Docker, uvicorn) se saltan con [SKIP] si no aplican.
#
# Uso:
#   pwsh -ExecutionPolicy Bypass -File .\verify.ps1
#   pwsh -File .\verify.ps1 -BootstrapDependencies -WithLocalServices -ApplyMigrations
# o doble click en el archivo si ya tienes PowerShell asociado.

param(
    [switch]$BootstrapDependencies,
    [switch]$WithLocalServices,
    [switch]$ApplyMigrations,
    [switch]$AllowRemoteDatabaseMutation
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Write-Section($title) {
    Write-Host ""
    Write-Host "==== $title ====" -ForegroundColor Cyan
}
function Write-Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "[SKIP] $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }

if ($AllowRemoteDatabaseMutation -and -not $ApplyMigrations) {
    Write-Fail "-AllowRemoteDatabaseMutation solo tiene sentido junto a -ApplyMigrations"
    exit 2
}

# ----------------------------------------------------------------------------
# 1. Pre-requisitos
# ----------------------------------------------------------------------------
Write-Section "1. Pre-requisitos"

$pythonExe = $null
$pythonArgs = @()
$existingVenvPython = "$root\backend\.venv\Scripts\python.exe"
$pythonCandidates = @()
if (Test-Path $existingVenvPython) {
    $pythonCandidates += @{ Exe = $existingVenvPython; Args = @() }
}
foreach ($candidateName in @("python3.12", "python3", "python")) {
    $candidateCommand = Get-Command $candidateName -ErrorAction SilentlyContinue
    if ($candidateCommand) {
        $pythonCandidates += @{ Exe = $candidateCommand.Source; Args = @() }
    }
}
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $pythonCandidates += @{ Exe = $pyLauncher.Source; Args = @("-3.12") }
}
foreach ($candidate in $pythonCandidates) {
    try {
        $v = & $candidate.Exe @($candidate.Args) --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(1[2-9]|[2-9]\d)") {
            $pythonExe = $candidate.Exe
            $pythonArgs = @($candidate.Args)
            Write-Ok "$pythonExe → $v"
            break
        }
    } catch {}
}
if (-not $pythonExe) {
    Write-Fail "No encontré Python 3.12+. Instala desde https://www.python.org/downloads/"
    exit 1
}

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$bundledNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$nodeExe = if ($nodeCommand) {
    $nodeCommand.Source
} elseif (Test-Path $bundledNode) {
    $bundledNode
} else {
    $null
}
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) {
    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
}
if (-not $nodeExe) {
    Write-Fail "No encontré Node.js 22.13+; es necesario para Expo 57 y el panel web"
    exit 1
}
$npmExe = if ($npmCommand) { $npmCommand.Source } else { $null }
$nodeVersionText = & $nodeExe --version 2>&1
if ($LASTEXITCODE -ne 0 -or $nodeVersionText -notmatch '^v(\d+)\.(\d+)\.') {
    Write-Fail "No pude determinar la versión de Node.js"
    exit 1
}
$nodeMajor = [int]$matches[1]
$nodeMinor = [int]$matches[2]
if ($nodeMajor -lt 22 -or ($nodeMajor -eq 22 -and $nodeMinor -lt 13)) {
    Write-Fail "Node.js 22.13+ es obligatorio; encontré $nodeVersionText"
    exit 1
}
if ($npmExe) {
    Write-Ok "Node.js $nodeVersionText + npm disponibles"
} else {
    $localFrontendDeps =
        (Test-Path "$root\dashboard\node_modules") -and
        (Test-Path "$root\mobile\node_modules")
    if (-not $localFrontendDeps) {
        Write-Fail "npm no está disponible y faltan dependencias locales de panel o app movil"
        exit 1
    }
    Write-Ok "Node.js $nodeVersionText disponible; se usarán dependencias frontend ya instaladas"
}

$dockerAvailable = $false
try {
    docker --version | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $dockerAvailable = $true
        Write-Ok "Docker disponible"
    }
} catch {
    Write-Skip "Docker no disponible — skip docker-compose"
}

# ----------------------------------------------------------------------------
# 2. Venv
# ----------------------------------------------------------------------------
Write-Section "2. Virtualenv en backend\.venv"

Set-Location "$root\backend"
if (-not (Test-Path ".venv")) {
    if (-not $BootstrapDependencies) {
        Write-Fail "falta backend\.venv; créalo explícitamente con -BootstrapDependencies"
        exit 1
    }
    & $pythonExe @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Fail "venv creation failed"; exit 1 }
    Write-Ok ".venv creado"
} else {
    Write-Ok ".venv ya existe"
}
$venvPy = "$root\backend\.venv\Scripts\python.exe"

# ----------------------------------------------------------------------------
# 3. Instalar deps
# ----------------------------------------------------------------------------
Write-Section "3. Dependencias"

if ($BootstrapDependencies) {
    Write-Host "[MUTATION] Se instalarán/actualizarán dependencias locales." -ForegroundColor Yellow
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install -e ".[dev]" --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install falló — revisa el error arriba"
        exit 1
    }
    Write-Ok "dependencias backend instaladas"
} else {
    & $venvPy -c "import app, pytest" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "el venv no está preparado; usa -BootstrapDependencies"
        exit 1
    }
    Write-Ok "dependencias backend existentes (sin cambios)"
}

# ----------------------------------------------------------------------------
# 4. Docker compose (postgres + redis)
# ----------------------------------------------------------------------------
Write-Section "4. Servicios (docker compose)"

$composePath = "$root\infra\docker-compose.yml"
if ($WithLocalServices -and $dockerAvailable -and (Test-Path $composePath)) {
    # Solo levantamos postgres y redis — backend/worker/beat los corremos
    # localmente (más rápido para iterar).
    docker compose -f $composePath up -d postgres redis 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "docker compose up postgres redis falló"
        exit 1
    }
    Write-Ok "postgres + redis arriba. Espero 5s a que Postgres esté listo..."
    Start-Sleep -Seconds 5
} elseif ($WithLocalServices -and -not $dockerAvailable) {
    Write-Fail "Se pidió -WithLocalServices pero Docker no está disponible"
    exit 1
} else {
    Write-Skip "sin cambios en Docker (usa -WithLocalServices para arrancar Postgres y Redis locales)"
}

# ----------------------------------------------------------------------------
# 5. Alembic migraciones
# ----------------------------------------------------------------------------
Write-Section "5. Alembic upgrade head"

if ($ApplyMigrations) {
    # Cargar .env únicamente para el paso explícitamente mutante. La
    # verificación normal nunca importa silenciosamente credenciales de prod.
    $migrationEnvOriginals = @{}
    if (Test-Path "$root\backend\.env") {
        Get-Content "$root\backend\.env" | ForEach-Object {
            if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+)\s*$') {
                $envName = $matches[1]
                if (-not $migrationEnvOriginals.ContainsKey($envName)) {
                    $migrationEnvOriginals[$envName] = [Environment]::GetEnvironmentVariable($envName, "Process")
                }
                [Environment]::SetEnvironmentVariable($envName, $matches[2], "Process")
            }
        }
        Write-Ok ".env cargado para la migración solicitada"
    }

    $databaseUrl = if ($env:DATABASE_URL) {
        $env:DATABASE_URL
    } else {
        "postgresql+asyncpg://realtor:realtor@localhost:5432/realtor"
    }
    try {
        $databaseHost = ([Uri]$databaseUrl).DnsSafeHost
    } catch {
        Write-Fail "DATABASE_URL no es una URL válida; no se ha ejecutado ninguna migración"
        exit 1
    }
    $localDatabaseHosts = @("localhost", "127.0.0.1", "::1")
    if ($databaseHost -notin $localDatabaseHosts -and -not $AllowRemoteDatabaseMutation) {
        Write-Fail "Migración remota bloqueada para '$databaseHost'. Revisa el destino y repite con -AllowRemoteDatabaseMutation solo si es intencional."
        exit 2
    }
    if ($databaseHost -notin $localDatabaseHosts) {
        Write-Host "[MUTATION] Se aplicarán migraciones a la base REMOTA '$databaseHost'." -ForegroundColor Magenta
    } else {
        Write-Host "[MUTATION] Se aplicarán migraciones a la base local '$databaseHost'." -ForegroundColor Yellow
    }

    Set-Location "$root\backend"
    & $venvPy -m alembic upgrade head 2>&1 | Out-Host
    $migrationExit = $LASTEXITCODE
    foreach ($entry in $migrationEnvOriginals.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
    if ($migrationExit -ne 0) {
        Write-Fail "alembic upgrade falló — comprueba la DB y DATABASE_URL"
        exit 1
    }
    Write-Ok "migraciones aplicadas al destino confirmado"
} else {
    Write-Skip "migraciones no ejecutadas (usa -ApplyMigrations de forma explícita)"
}

# ----------------------------------------------------------------------------
# 6. pytest
# ----------------------------------------------------------------------------
Write-Section "6. pytest"

$previousAppEnv = $env:APP_ENV
$env:APP_ENV = "development"
Set-Location $root
& $venvPy -m pytest "$root\backend\tests" -x 2>&1 | Out-Host
$testExit = $LASTEXITCODE
if ($testExit -ne 0) {
    Write-Fail "pytest falló (exit=$testExit). Pega el output arriba para que lo revisemos."
    exit $testExit
}

if ($null -eq $previousAppEnv) {
    Remove-Item Env:APP_ENV -ErrorAction SilentlyContinue
} else {
    $env:APP_ENV = $previousAppEnv
}
Write-Ok "tests de producto pasaron"

# ----------------------------------------------------------------------------
# 7. Panel web y app movil
# ----------------------------------------------------------------------------
Write-Section "7. Panel web y app movil"

foreach ($frontend in @("dashboard", "mobile")) {
    $frontendPath = Join-Path $root $frontend
    if (-not (Test-Path (Join-Path $frontendPath "package-lock.json"))) {
        Write-Fail "$frontend no contiene package-lock.json"
        exit 1
    }
    Set-Location $frontendPath
    if (-not $BootstrapDependencies -and -not (Test-Path (Join-Path $frontendPath "node_modules"))) {
        Write-Fail "faltan dependencias de $frontend; usa -BootstrapDependencies"
        exit 1
    }
    if ($npmExe -and $BootstrapDependencies) {
        Write-Host "[MUTATION] npm ci en $frontend." -ForegroundColor Yellow
        & $npmExe ci --silent
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "npm ci falló en $frontend"
            exit 1
        }
    }
    if ($npmExe) {
        & $npmExe run typecheck
    } else {
        & $nodeExe ".\node_modules\typescript\bin\tsc" --noEmit
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "typecheck falló en $frontend"
        exit 1
    }
}

Set-Location "$root\dashboard"
if ($npmExe) {
    & $npmExe run lint
} else {
    & $nodeExe ".\node_modules\eslint\bin\eslint.js" . --max-warnings=0
}
if ($LASTEXITCODE -ne 0) {
    Write-Fail "lint del dashboard falló"
    exit 1
}
if ($npmExe) {
    & $npmExe run build
} else {
    & $nodeExe ".\node_modules\next\dist\bin\next" build
}
if ($LASTEXITCODE -ne 0) {
    Write-Fail "build de dashboard falló"
    exit 1
}

Set-Location "$root\mobile"
if ($npmExe) {
    & $npmExe run validate:auth-config
} else {
    & $nodeExe ".\scripts\validate-auth-config.cjs"
}
if ($LASTEXITCODE -ne 0) {
    Write-Fail "validación de auth móvil falló"
    exit 1
}
Write-Ok "panel web y app móvil verificados"

# ----------------------------------------------------------------------------
# 8. Smoke test del API
# ----------------------------------------------------------------------------
Write-Section "8. Smoke test (liveness y readiness local opcional)"

$smokePreviousAppEnv = $env:APP_ENV
$smokePreviousDatabaseUrl = $env:DATABASE_URL
$smokePreviousRedisUrl = $env:REDIS_URL
$env:APP_ENV = "development"
if ($WithLocalServices) {
    $env:DATABASE_URL = "postgresql+asyncpg://realtor:realtor@localhost:5432/realtor"
    $env:REDIS_URL = "redis://localhost:6379/0"
}
$uvi = Start-Process -FilePath $venvPy -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8765" -WorkingDirectory "$root\backend" -PassThru -WindowStyle Hidden -RedirectStandardOutput "$root\backend\.uvicorn.log" -RedirectStandardError "$root\backend\.uvicorn.err.log"
if ($null -eq $smokePreviousAppEnv) { Remove-Item Env:APP_ENV -ErrorAction SilentlyContinue } else { $env:APP_ENV = $smokePreviousAppEnv }
if ($null -eq $smokePreviousDatabaseUrl) { Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue } else { $env:DATABASE_URL = $smokePreviousDatabaseUrl }
if ($null -eq $smokePreviousRedisUrl) { Remove-Item Env:REDIS_URL -ErrorAction SilentlyContinue } else { $env:REDIS_URL = $smokePreviousRedisUrl }
Start-Sleep -Seconds 4
$smokeFailed = $false
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8765/healthz" -UseBasicParsing -TimeoutSec 5
    if ($resp.StatusCode -eq 200) {
        Write-Ok "liveness /healthz responde 200"
    } else {
        Write-Fail "/healthz devolvió $($resp.StatusCode)"
        $smokeFailed = $true
    }
    if ($WithLocalServices) {
        $ready = Invoke-WebRequest -Uri "http://127.0.0.1:8765/readyz" -UseBasicParsing -TimeoutSec 8
        $readyBody = $ready.Content | ConvertFrom-Json
        if ($ready.StatusCode -eq 200 -and $readyBody.postgres -and $readyBody.redis) {
            Write-Ok "readiness /readyz confirma Postgres y Redis"
        } else {
            Write-Fail "/readyz no confirmó las dependencias locales"
            $smokeFailed = $true
        }
    } else {
        Write-Skip "readiness con dependencias no probada (usa -WithLocalServices)"
    }
} catch {
    Write-Fail "uvicorn no respondió. Logs:"
    Get-Content "$root\backend\.uvicorn.log" -ErrorAction SilentlyContinue | Out-Host
    Get-Content "$root\backend\.uvicorn.err.log" -ErrorAction SilentlyContinue | Out-Host
    $smokeFailed = $true
} finally {
    if ($uvi -and -not $uvi.HasExited) {
        Stop-Process -Id $uvi.Id -Force -ErrorAction SilentlyContinue
    }
}
if ($smokeFailed) { exit 1 }

Write-Section "Resumen"
Write-Host "Si llegaste hasta aquí sin [FAIL], el proyecto está funcional end-to-end:" -ForegroundColor Green
Write-Host "  - Dependencias comprobadas (instaladas solo con -BootstrapDependencies)"
Write-Host "  - Postgres/Redis locales: solo si pediste -WithLocalServices"
Write-Host "  - Schema migrado: solo si pediste -ApplyMigrations"
Write-Host "  - Tests de producto verdes"
Write-Host "  - Panel web compilado y app móvil comprobada"
Write-Host "  - API responde a /healthz"
Write-Host ""
Write-Host "Para arrancar el dev server normalmente:"
Write-Host "  cd backend"
Write-Host "  .venv\Scripts\Activate.ps1"
Write-Host "  uvicorn app.main:app --reload"
