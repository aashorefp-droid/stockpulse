@echo off
setlocal enabledelayedexpansion
set ROOT=C:\Users\malla\git\streamlit
set PYTHON=%ROOT%\.venv\Scripts\python.exe

echo.
echo ============================================================
echo  StockPulse -- Bounce (kill + restart backend + frontend)
echo ============================================================
echo.

:: ── Kill backend (port 8000) ─────────────────────────────────
echo [1/5] Stopping backend (port 8000)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING" 2^>nul') do (
    echo       Killing PID %%p
    taskkill /PID %%p /F >nul 2>&1
)

:: ── Kill frontend (ports 3000-3003) ─────────────────────────
echo [2/5] Stopping frontend (ports 3000-3003)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3000 " ^| findstr "LISTENING" 2^>nul') do (
    echo       Killing PID %%p ^(port 3000^)
    taskkill /PID %%p /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3001 " ^| findstr "LISTENING" 2^>nul') do (
    echo       Killing PID %%p ^(port 3001^)
    taskkill /PID %%p /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3002 " ^| findstr "LISTENING" 2^>nul') do (
    echo       Killing PID %%p ^(port 3002^)
    taskkill /PID %%p /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3003 " ^| findstr "LISTENING" 2^>nul') do (
    echo       Killing PID %%p ^(port 3003^)
    taskkill /PID %%p /F >nul 2>&1
)

:: Brief pause to let ports free up
timeout /t 2 /nobreak >nul

:: ── Clear Next.js webpack cache (prevents stale module errors) ──
echo [3/5] Clearing Next.js build cache...
if exist "%ROOT%\frontend\.next" (
    rmdir /s /q "%ROOT%\frontend\.next" >nul 2>&1
    echo       .next cache cleared.
) else (
    echo       No cache found, skipping.
)

:: ── Start backend in new window ──────────────────────────────
echo [4/5] Starting backend on port 8000...
start "StockPulse Backend" cmd /k "cd /d %ROOT% && %PYTHON% -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"

:: Brief pause so backend starts before frontend
timeout /t 2 /nobreak >nul

:: ── Start frontend in new window ─────────────────────────────
echo [5/5] Starting frontend (Next.js)...
start "StockPulse Frontend" cmd /k "cd /d %ROOT%\frontend && npm run dev"

echo.
echo Done! Two new windows opened.
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:3000  (first compile ~15s)
echo.
