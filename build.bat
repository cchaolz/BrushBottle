py -3.13 -m PyInstaller --onedir --noconfirm --contents-directory _internal -i icon.png -w --add-data "icon.png;." main.py
xcopy /E /I /Y templates dist\main\templates
pause
