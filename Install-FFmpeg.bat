@echo off
chcp 65001 >nul
rem Встановлення FFmpeg через winget / Install FFmpeg via winget
echo Встановлення FFmpeg (Gyan.FFmpeg) через winget...
echo Installing FFmpeg (Gyan.FFmpeg) via winget...
echo.
where winget >nul 2>nul || goto :nowinget
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :fail
echo.
echo Готово. Перезапустіть Аудіоконвертер — він знайде FFmpeg автоматично.
echo Done. Restart Car Audio Converter — it will find FFmpeg automatically.
pause
exit /b 0
:nowinget
echo winget не знайдено (потрібна Windows 10/11 з «Інсталятором програм» з Microsoft Store).
echo winget was not found (Windows 10/11 with "App Installer" from Microsoft Store is required).
:fail
echo.
echo Встановіть FFmpeg вручну: завантажте ffmpeg-release-essentials.zip з
echo https://www.gyan.dev/ffmpeg/builds/ і покладіть ffmpeg.exe, ffprobe.exe, ffplay.exe
echo у папку ffmpeg\bin поруч із програмою.
echo Manual install: download ffmpeg-release-essentials.zip from the link above and put
echo ffmpeg.exe, ffprobe.exe, ffplay.exe into the ffmpeg\bin folder next to the program.
pause
exit /b 1
