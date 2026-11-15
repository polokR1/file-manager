[CmdletBinding()]
param(
    [string] $Python = 'python',
    [string] $MakeNsis = 'makensis',
    [switch] $SmokeTest
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildDir = Join-Path $projectRoot ('.build\windows\' + [guid]::NewGuid().ToString('N'))
$distDir = Join-Path $projectRoot 'dist'
$bundleDir = Join-Path $buildDir 'bundle'
$appDir = Join-Path $bundleDir 'FileOrganizer'
$payloadManifest = Join-Path $buildDir 'uninstall-payload.nsh'
$setupScript = Join-Path $projectRoot 'installer\setup.nsi'
$appScript = Join-Path $projectRoot 'organizer.py'
$appIcon = Join-Path $projectRoot 'assets\file-organizer.ico'

$pythonExe = (Get-Command $Python -CommandType Application).Source
$makeNsisExe = (Get-Command $MakeNsis -CommandType Application).Source
$metadata = & $pythonExe -c 'import runpy, sys; meta = runpy.run_path(sys.argv[1]); print(meta["APP_VERSION"]); print(meta["APP_NAME"])' (Join-Path $projectRoot 'app_version.py')
if ($LASTEXITCODE -ne 0 -or $metadata.Count -ne 2 -or $metadata[0] -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Could not read the application version and name.'
}
$appVersion, $appName = $metadata
$installerName = "FileOrganizer-Setup-x64-$appVersion.exe"
if ($SmokeTest) {
    $installerName = "FileOrganizer-Setup-x64-$appVersion-SmokeTest.exe"
}
$platform = & $pythonExe -c 'import sysconfig; print(sysconfig.get_platform())'
if ($LASTEXITCODE -ne 0 -or $platform -ne 'win-amd64') {
    throw 'The installer requires an x64 Windows Python interpreter.'
}
& $pythonExe -c 'from PIL import Image, ExifTags'
if ($LASTEXITCODE -ne 0) {
    throw 'Install the build dependencies with: python -m pip install -r requirements-build.txt'
}

New-Item -ItemType Directory -Path $buildDir, $distDir -Force | Out-Null
& $pythonExe -m PyInstaller --noconfirm --onedir --windowed --name FileOrganizer `
    --icon $appIcon --add-data "${appIcon}:assets" --specpath $buildDir `
    --workpath (Join-Path $buildDir 'work') --distpath $bundleDir $appScript
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller failed. Install PyInstaller in the selected Python environment and retry.'
}

# Keep the runtime notices supplied by the Windows CPython distribution with the installed program.
$pythonBase = & $pythonExe -c 'import sys; print(sys.base_prefix)'
Copy-Item -LiteralPath (Join-Path $pythonBase 'LICENSE.txt') -Destination (Join-Path $appDir 'Licencja-Python-Tcl-Tk.txt')
$pillowNotice = & $pythonExe -c 'from importlib.metadata import distribution; d = distribution("Pillow"); print(next(d.locate_file(p) for p in d.files if p.name == "LICENSE"))'
if ($LASTEXITCODE -ne 0) {
    throw 'Could not locate the Pillow distribution license.'
}
Copy-Item -LiteralPath $pillowNotice -Destination (Join-Path $appDir 'Licencja-Pillow.txt')

# Generate exact uninstall paths from the bundle. NSIS removes only the files installed by this build.
$files = @(Get-ChildItem -LiteralPath $appDir -Recurse -File -Force)
$dirs = @(Get-ChildItem -LiteralPath $appDir -Recurse -Directory -Force |
    Sort-Object -Property { $_.FullName.Length } -Descending)
$manifest = @(
    '!macro DeletePayloadFiles'
    foreach ($file in $files) {
        $relativePath = $file.FullName.Substring($appDir.Length + 1).Replace('$', '$$')
        '  Delete "$INSTDIR\{0}"' -f $relativePath
    }
    '!macroend'
    '!macro RemovePayloadDirectories'
    foreach ($dir in $dirs) {
        $relativePath = $dir.FullName.Substring($appDir.Length + 1).Replace('$', '$$')
        '  RMDir "$INSTDIR\{0}"' -f $relativePath
    }
    '!macroend'
)
[System.IO.File]::WriteAllLines($payloadManifest, [string[]] $manifest, [System.Text.UTF8Encoding]::new($true))
$sizeInKiB = [long] [math]::Ceiling(($files | Measure-Object -Property Length -Sum).Sum / 1KB)

$installerPath = Join-Path $buildDir $installerName
$compilerArgs = @(
    '/V2'
    '/WX'
    '/INPUTCHARSET'
    'UTF8'
    "/DPAYLOAD_DIR=$appDir"
    "/DPAYLOAD_MANIFEST=$payloadManifest"
    "/DSETUP_OUTPUT=$installerPath"
    "/DPAYLOAD_KIB=$sizeInKiB"
    "/DAPP_ICON=$appIcon"
    "/DAPP_VERSION=$appVersion"
    "/DAPP_NAME=$appName"
)
if ($SmokeTest) {
    $compilerArgs += '/DSMOKE_TEST'
}
& $makeNsisExe @compilerArgs $setupScript
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw 'NSIS failed to build the installer.'
}

$outputPath = Join-Path $distDir $installerName
Move-Item -LiteralPath $installerPath -Destination $outputPath -Force
Write-Output $outputPath
