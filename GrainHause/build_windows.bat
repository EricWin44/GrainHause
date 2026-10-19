@echo off
REM Builds GrainHause.exe for Windows 10/11. Needs Python 3.10+ from python.org ("Add to PATH" ticked).
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --windowed --name GrainHause --icon assets\icon.ico --add-data "assets;assets" ^
  --collect-all rawpy --collect-all imagecodecs --collect-all pillow_heif ^
  run.py
echo.
echo Done: dist\GrainHause\GrainHause.exe
echo Move the whole dist\GrainHause folder somewhere permanent, e.g. C:\Program Files\GrainHause
pause
