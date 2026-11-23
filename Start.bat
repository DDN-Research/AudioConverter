@echo off
chcp 65001 >nul
rem Запуск Аудіоконвертера з вихідного коду / Run Car Audio Converter from source
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0audio_converter.pyw" & exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw "%~dp0audio_converter.pyw" & exit /b 0)
where python >nul 2>nul && (start "" python "%~dp0audio_converter.pyw" & exit /b 0)
echo Python не знайдено. Встановіть Python 3.8+ з https://www.python.org/downloads/
echo (поставте галочку "Add python.exe to PATH"), потім запустіть цей файл знову.
echo.
echo Python was not found. Install Python 3.8+ from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH") and run this file again.
pause
exit /b 1
