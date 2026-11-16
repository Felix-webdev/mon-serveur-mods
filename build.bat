@echo off
echo Installation des dependances...
pip install minecraft-launcher-lib requests customtkinter pillow certifi pyinstaller

echo Creation du launcher (app-Windows.exe)...
pyinstaller --onefile --windowed --name app-Windows --collect-all minecraft_launcher_lib --collect-all customtkinter launcher.py

echo Creation du demarreur (MonLauncher-Windows.exe)...
pyinstaller --onefile --windowed --name MonLauncher-Windows --collect-all certifi bootstrap.py

echo.
echo Termine ! Les fichiers sont dans le dossier "dist".
echo (Pour un test local : lance MonLauncher-Windows.exe, il telechargera le launcher depuis GitHub.)
pause
