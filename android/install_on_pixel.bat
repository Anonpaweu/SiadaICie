@echo off
setlocal
set "ADB=C:\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"

echo Sprawdzanie polaczenia z telefonem (adb devices)...
"%ADB%" devices
echo.
set "APK=%~dp0app\build\outputs\apk\release\app-release.apk"
if not exist "%APK%" set "APK=%~dp0app\build\outputs\apk\debug\app-debug.apk"
if not exist "%APK%" (
    echo [BLAD] Brak pliku APK. Zbuduj najpierw przez: gradlew.bat assembleRelease
    exit /b 1
)

echo Instalacja aktualizacji (%APK%) bez kasowania poprzedniej wersji...
"%ADB%" install -r "%APK%"
if errorlevel 1 (
    echo [BLAD] Instalacja nie powiodla sie. Upewnij sie, ze Pixel ma ponad 1 GB wolnego miejsca i wlaczone Debugowanie USB lub bezprzewodowe.
    exit /b 1
)
echo [OK] Aplikacja zainstalowana i zaktualizowana na Pixelu!
endlocal
