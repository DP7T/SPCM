# SPCM installer - started by "Install SPCM.bat". Safe to run again at any time.
# 1. finds or installs Python 3.12
# 2. installs the Python libraries
# 3. installs the Thorlabs camera package if ThorCam is installed
# 4. creates "SPCM" and "SPCM (simulation)" icons on the Desktop and in the Start menu

$ErrorActionPreference = "Stop"
$App = Split-Path -Parent $PSScriptRoot
Set-Location $App

function Say($msg, $color = "Cyan") { Write-Host ""; Write-Host "== $msg" -ForegroundColor $color }

function Find-Python {
    foreach ($v in "312", "313", "311", "310") {
        foreach ($root in "$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles") {
            $c = Join-Path $root "Python$v\python.exe"
            if (Test-Path $c) { return $c }
        }
    }
    try {
        $p = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) {
            $p = "$p".Trim()
            $ok = & $p -c "import sys; print(sys.version_info >= (3, 10) and sys.maxsize > 2**32)"
            if ("$ok".Trim() -eq "True") { return $p }
        }
    } catch {}
    return $null
}

# ---------------------------------------------------------------- Python
Say "Looking for Python"
$Py = Find-Python
if (-not $Py) {
    Say "Python not found - installing Python 3.12 (this takes a few minutes)"
    $done = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Python.Python.3.12 --scope user --silent `
                --accept-package-agreements --accept-source-agreements
            $done = $true
        } catch { $done = $false }
    }
    $Py = Find-Python
    if (-not $Py) {
        Say "Downloading the Python installer from python.org"
        $inst = Join-Path $env:TEMP "python-3.12.7-amd64.exe"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe" -OutFile $inst
        Start-Process $inst -Wait -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0"
        $Py = Find-Python
    }
    if (-not $Py) { throw "Python could not be installed automatically. Install Python 3.12 (64-bit) from python.org, tick 'Add python.exe to PATH', then run Install SPCM again." }
}
Write-Host "Using $Py"

# ---------------------------------------------------------------- libraries
Say "Installing the Python libraries"
& $Py -m pip install --upgrade pip --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip failed (no internet?)" }
& $Py -m pip install -r (Join-Path $App "requirements.txt") --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "Installing the libraries failed (no internet?)" }

# ---------------------------------------------------------------- Thorlabs camera SDK
Say "Looking for the Thorlabs camera package (ThorCam)"
# ThorCam ships the SDK as "Scientific_Camera_Interfaces.zip" (older versions:
# "Scientific Camera Interfaces.zip"), or already unpacked. Look for all of them.
$roots = @("$env:ProgramFiles\Thorlabs", "${env:ProgramFiles(x86)}\Thorlabs") | Where-Object { $_ -and (Test-Path $_) }
$pkg = $null
foreach ($root in $roots) {
    $pkg = Get-ChildItem $root -Recurse -Filter "thorlabs_tsi_camera_python_sdk_package*.zip" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pkg) { break }
}
$dest = Join-Path $env:LOCALAPPDATA "SPCM\ThorlabsSDK"
if (-not $pkg) {
    $sci = $null
    foreach ($root in $roots) {
        $sci = Get-ChildItem $root -Recurse -Include "*Camera*Interfaces*.zip" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($sci) { break }
    }
    if ($sci) {
        Write-Host "Found $($sci.FullName)"
        Expand-Archive $sci.FullName -DestinationPath $dest -Force
        $pkg = Get-ChildItem $dest -Recurse -Filter "thorlabs_tsi_camera_python_sdk_package*.zip" | Select-Object -First 1
    }
} else {
    Write-Host "Found $($pkg.FullName)"
    # also unpack the folder it sits in, for the DLLs next to it
    $sdk = Split-Path (Split-Path $pkg.FullName)
    if (Test-Path (Join-Path $sdk "Native Toolkit")) {
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Copy-Item (Join-Path $sdk "Native Toolkit") $dest -Recurse -Force
    }
}
if ($pkg) {
    & $Py -m pip install $pkg.FullName --disable-pip-version-check
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Camera package installed." -ForegroundColor Green
    } else {
        Write-Host "Installing the camera package failed (see above)." -ForegroundColor Red
    }
} else {
    Write-Host "Camera package not found under $($roots -join ', ')." -ForegroundColor Yellow
    Write-Host "Install ThorCam (with 'Scientific Camera Interfaces'), then run Install SPCM again." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- shortcuts
Say "Creating the SPCM icons"
$Pyw = Join-Path (Split-Path $Py) "pythonw.exe"
if (-not (Test-Path $Pyw)) { $Pyw = $Py }
$Main = Join-Path $App "main.py"
$Icon = Join-Path $App "spcm.ico"
$Shell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
$StartDir = Join-Path ([Environment]::GetFolderPath("Programs")) "SPCM"
New-Item -ItemType Directory -Force -Path $StartDir | Out-Null

function Make-Link($path, $extra) {
    $s = $Shell.CreateShortcut($path)
    $s.TargetPath = $Pyw
    $s.Arguments = "`"$Main`" $extra".Trim()
    $s.WorkingDirectory = $App
    $s.IconLocation = $Icon
    $s.Save()
}
Make-Link (Join-Path $Desktop "SPCM.lnk") ""
Make-Link (Join-Path $Desktop "SPCM (simulation).lnk") "--sim"
Make-Link (Join-Path $StartDir "SPCM.lnk") ""
Make-Link (Join-Path $StartDir "SPCM (simulation).lnk") "--sim"

Say "Done! Start SPCM from the icon on your Desktop." "Green"
