@echo off
chcp 65001 >nul
rem Збірка AudioConverter.exe (PyInstaller) / Build AudioConverter.exe with PyInstaller
rem Потрібно / Requires: Python 3.8+ (python.org), інтернет для pip / internet for pip.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
copy /y audio_converter.pyw AudioConverter.py >nul
python -m PyInstaller --noconfirm --clean --windowed --onedir --name AudioConverter ^
  --icon AudioConverter.ico --add-data "AudioConverter.ico;." ^
  --exclude-module unittest --exclude-module pydoc_data AudioConverter.py || goto :err
del AudioConverter.py
echo.
echo Готово: dist\AudioConverter\AudioConverter.exe
echo Покладіть ffmpeg.exe, ffprobe.exe, ffplay.exe (+ DLL, якщо збірка «shared»)
echo у dist\AudioConverter\ffmpeg\bin
echo Done. Put ffmpeg.exe, ffprobe.exe, ffplay.exe into dist\AudioConverter\ffmpeg\bin
pause
exit /b 0
:err
echo Помилка збірки / Build failed
pause
exit /b 1
