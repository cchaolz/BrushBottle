pyinstaller --onedir --noconfirm -i icon.png -w main.py
xcopy /E /I /Y templates dist\main\templates
xcopy /Y icon.png dist\main\
pause
