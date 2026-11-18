# Build an offline Android APK using an existing Android SDK (no Gradle downloads).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
  $sdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
  $tools = Join-Path $sdk "build-tools\36.1.0"
  $androidJar = Join-Path $sdk "platforms\android-36.1\android.jar"
  if (-not (Test-Path $androidJar)) { throw "Android platform API not found: $androidJar" }
  New-Item -ItemType Directory -Path "build\classes","build\dex" -Force | Out-Null
  & "$tools\aapt2.exe" compile --dir res -o build\compiled-res.zip
  if ($LASTEXITCODE -ne 0) { throw "AAPT2 compile failed" }
  & "$tools\aapt2.exe" link --auto-add-overlay -o build\unsigned.apk --manifest AndroidManifest.xml -I $androidJar --min-sdk-version 26 --target-sdk-version 35 --version-code 1 --version-name 0.1.0 -R build\compiled-res.zip
  if ($LASTEXITCODE -ne 0) { throw "AAPT2 link failed" }
  $sources = @(Get-ChildItem src -Filter "*.java" -Recurse | ForEach-Object { $_.FullName })
  & javac -encoding UTF-8 -source 8 -target 8 -cp $androidJar -d build\classes $sources
  if ($LASTEXITCODE -ne 0) { throw "javac failed" }
  & jar cf build\classes.jar -C build\classes .
  if ($LASTEXITCODE -ne 0) { throw "jar failed" }
  & "$tools\d8.bat" --min-api 26 --lib $androidJar --output build\dex build\classes.jar
  if ($LASTEXITCODE -ne 0) { throw "D8 failed" }
  & jar uf build\unsigned.apk -C build\dex classes.dex
  if ($LASTEXITCODE -ne 0) { throw "dex packaging failed" }
  & "$tools\zipalign.exe" -f 4 build\unsigned.apk build\aligned.apk
  if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }
  $keystore = Join-Path $env:USERPROFILE ".android\debug.keystore"
  if (-not (Test-Path $keystore)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $keystore) -Force | Out-Null
    & keytool -genkeypair -alias androiddebugkey -keystore $keystore -storepass android -keypass android -dname "CN=Android Debug,O=Local Dev,C=JP" -keyalg RSA -keysize 2048 -validity 10000 -noprompt
    if ($LASTEXITCODE -ne 0) { throw "debug keystore creation failed" }
  }
  & "$tools\apksigner.bat" sign --ks $keystore --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out build\MuseAivis.apk build\aligned.apk
  if ($LASTEXITCODE -ne 0) { throw "APK signing failed" }
  & "$tools\apksigner.bat" verify --verbose build\MuseAivis.apk
  if ($LASTEXITCODE -ne 0) { throw "APK signature verification failed" }
  & "$tools\aapt2.exe" dump badging build\MuseAivis.apk | Select-Object -First 5
  Write-Output "OUTPUT=$((Resolve-Path build\MuseAivis.apk).Path)"
} finally {
  Pop-Location
}
