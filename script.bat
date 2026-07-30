@echo off
REM ============================================================================
REM  ElevenMetric - arranque de front + back para el dashboard
REM
REM  El backend (FastAPI/uvicorn) sirve tambien el frontend estatico, asi que
REM  un unico servidor en http://localhost:8000 levanta todo el sistema.
REM
REM  Este script:
REM    1. Crea/activa un entorno virtual en backend\.venv
REM    2. Instala las dependencias (solo la primera vez)
REM    3. Siembra la base de datos demo (solo la primera vez)
REM    4. Arranca uvicorn y abre el dashboard en el navegador
REM ============================================================================

setlocal
cd /d "%~dp0backend"

REM --- 1. Entorno virtual --------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [ElevenMetric] Creando entorno virtual...
    python -m venv .venv
    if errorlevel 1 (
        echo [ElevenMetric] ERROR: no se pudo crear el entorno virtual. Instala Python 3.
        pause
        exit /b 1
    )
)
call ".venv\Scripts\activate.bat"

REM --- 2. Dependencias (marcador para no reinstalar cada vez) ---------------
if not exist ".venv\.deps_installed" (
    echo [ElevenMetric] Instalando dependencias...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ElevenMetric] ERROR: fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
    echo ok> ".venv\.deps_installed"
)

REM --- 3. Semilla de la base de datos demo (solo la primera vez) ------------
if not exist ".venv\.db_seeded" (
    echo [ElevenMetric] Sembrando base de datos demo...
    python -m app.db.seed --reset
    if errorlevel 1 (
        echo [ElevenMetric] ERROR: fallo la siembra de la base de datos.
        pause
        exit /b 1
    )
    echo ok> ".venv\.db_seeded"
)

REM --- 4. Arranque ---------------------------------------------------------
echo.
echo [ElevenMetric] Dashboard en:  http://localhost:8000
echo [ElevenMetric] Login demo:    owner@demo.fc  /  elevenmetric
echo [ElevenMetric] API docs:      http://localhost:8000/docs
echo [ElevenMetric] (Ctrl+C para detener el servidor)
echo.

REM Abre el navegador tras un breve margen para que el servidor este listo
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000"

python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

endlocal
