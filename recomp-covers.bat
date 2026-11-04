@echo off
rem Recomp Covers launcher (Windows): bootstraps a local venv with
rem PySide6 + Pillow; checks the imports, not just the venv, so a
rem half-made venv heals itself.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv
)
.venv\Scripts\python -c "import PySide6, PIL" 2>nul
if errorlevel 1 .venv\Scripts\pip install --quiet PySide6 Pillow
.venv\Scripts\python app.py %*
