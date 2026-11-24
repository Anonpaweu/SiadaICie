@echo off
setlocal
set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=C:\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"

echo Sprawdzanie polaczenia z telefonem (adb devices)...
"%ADB%" devices
echo.
set "APK=%~dp0app\build\outputs\apk\debug\app-debug.apk"
if not exist "%APK%" (
    echo [BLAD] Brak pliku %APK%. Zbuduj najpierw APK przez: gradlew.bat assembleDebug
    exit /b 1
)

echo Instalacja aktualizacji bez kasowania poprzedniej wersji...
"%ADB%" install -r "%APK%"
if errorlevel 1 (
    echo [BLAD] Instalacja nie powiodla sie. Upewnij sie, ze Pixel ma wlaczone Debugowanie USB lub bezprzewodowe.
    exit /b 1
)
echo [OK] Aplikacja zainstalowana i zaktualizowana na Pixelu!
endlocal
