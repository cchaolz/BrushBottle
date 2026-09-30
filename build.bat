@echo off
setlocal

py -3.13 -m PyInstaller --onedir --noconfirm --contents-directory _internal -i icon.png -w --add-data "icon.png;." main.py
if errorlevel 1 goto :failed

if not exist "dist\main\main.exe" goto :failed

xcopy /E /I /Y templates dist\main\templates >nul
if not exist "dist\main\templates" goto :failed

echo.
echo BUILD OK  --^>  dist\main\main.exe
pause
exit /b 0

:failed
echo.
echo BUILD FAILED  --  PyInstaller 失败或产物缺失, 已中止
pause
exit /b 1
