[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $InputPng,

    [Parameter(Position = 1)]
    [string] $OutputDirectory,

    [string[]] $Language = @("jpn", "eng"),

    [ValidateRange(1.0, 1200.0)]
    [double] $Dpi = 144.0,

    [switch] $NoOpen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Stop-Demo {
    param([string] $Message, [int] $ExitCode)

    [Console]::Error.WriteLine("Aiteqno demo: $Message")
    exit $ExitCode
}

function Find-CompatiblePython {
    $versionProbe = "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}'); raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)"
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        foreach ($minor in @(14, 13, 12, 11)) {
            try {
                $version = & $launcher.Source "-3.$minor" -c $versionProbe 2>$null
                if ($LASTEXITCODE -eq 0) {
                    return @($launcher.Source, "-3.$minor", $version)
                }
            }
            catch {
                continue
            }
        }
    }

    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        try {
            $version = & $python.Source -c $versionProbe 2>$null
            if ($LASTEXITCODE -eq 0) {
                return @($python.Source, "", $version)
            }
        }
        catch {
            return $null
        }
    }

    return $null
}

function Invoke-BasePython {
    param([string[]] $Arguments)

    if ([string]::IsNullOrEmpty($script:PythonPrefix)) {
        & $script:PythonExecutable @Arguments
    }
    else {
        & $script:PythonExecutable $script:PythonPrefix @Arguments
    }
}

function Get-Sha256Hex {
    param([string] $Path)

    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $algorithm = [System.Security.Cryptography.SHA256]::Create()
        try {
            return [System.BitConverter]::ToString(
                $algorithm.ComputeHash($stream)
            ).Replace("-", "").ToLowerInvariant()
        }
        finally {
            $algorithm.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Find-Tesseract {
    if (-not [string]::IsNullOrWhiteSpace($env:AITEQNO_TESSERACT_EXECUTABLE)) {
        if (-not (Test-Path -LiteralPath $env:AITEQNO_TESSERACT_EXECUTABLE -PathType Leaf)) {
            Stop-Demo "AITEQNO_TESSERACT_EXECUTABLE points to a missing file: $env:AITEQNO_TESSERACT_EXECUTABLE" 5
        }
        return (Resolve-Path -LiteralPath $env:AITEQNO_TESSERACT_EXECUTABLE).Path
    }

    $command = Get-Command "tesseract.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $commonPath = Join-Path $env:ProgramFiles "Tesseract-OCR\tesseract.exe"
        if (Test-Path -LiteralPath $commonPath -PathType Leaf) {
            return $commonPath
        }
    }

    Stop-Demo "Tesseract 5.x or newer was not found. Install Tesseract with jpn and eng language data, then retry." 5
}

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$inputPath = [System.IO.Path]::GetFullPath($InputPng)
if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
    Stop-Demo "Input PNG does not exist: $inputPath" 3
}
if ([System.IO.Path]::GetExtension($inputPath) -ine ".png") {
    Stop-Demo "Only a single-page .png input is supported: $inputPath" 3
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $inputDirectory = Split-Path -Parent $inputPath
    $inputName = [System.IO.Path]::GetFileNameWithoutExtension($inputPath)
    $OutputDirectory = Join-Path $inputDirectory "$inputName-aiteqno-output"
}
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputPath) {
    Stop-Demo "Output already exists and will not be overwritten: $outputPath" 4
}

$wheelDirectory = Join-Path $packageRoot "wheel"
$wheels = @(Get-ChildItem -LiteralPath $wheelDirectory -Filter "aiteqno-*.whl" -File)
if ($wheels.Count -ne 1) {
    Stop-Demo "The demo package must contain exactly one Aiteqno wheel." 5
}
$wheel = $wheels[0]
$wheelHash = Get-Sha256Hex -Path $wheel.FullName

$pythonSelection = Find-CompatiblePython
if ($null -eq $pythonSelection -or $pythonSelection.Count -ne 3) {
    Stop-Demo "Python 3.11, 3.12, 3.13, or 3.14 was not found." 5
}
$script:PythonExecutable = $pythonSelection[0]
$script:PythonPrefix = $pythonSelection[1]
$pythonVersion = $pythonSelection[2]

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Stop-Demo "LOCALAPPDATA is unavailable; a per-user runtime cannot be created." 5
}
$runtimeKey = "$pythonVersion-$($wheelHash.Substring(0, 16))"
$runtimeParent = Join-Path $env:LOCALAPPDATA "Aiteqno\demo-runtimes"
$runtimePath = Join-Path $runtimeParent $runtimeKey
$runtimePython = Join-Path $runtimePath "Scripts\python.exe"
$readyMarker = Join-Path $runtimePath "aiteqno-demo.ready"

if (-not (Test-Path -LiteralPath $readyMarker -PathType Leaf)) {
    if (Test-Path -LiteralPath $runtimePath) {
        Remove-Item -LiteralPath $runtimePath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $runtimeParent -Force | Out-Null
    $stagingPath = Join-Path $runtimeParent "$runtimeKey.installing-$PID"
    if (Test-Path -LiteralPath $stagingPath) {
        Remove-Item -LiteralPath $stagingPath -Recurse -Force
    }
    try {
        Invoke-BasePython -Arguments @("-m", "venv", $stagingPath)
        if ($LASTEXITCODE -ne 0) {
            throw "Python failed to create the demo runtime."
        }
        $stagingPython = Join-Path $stagingPath "Scripts\python.exe"
        & $stagingPython -m pip install --disable-pip-version-check $wheel.FullName
        if ($LASTEXITCODE -ne 0) {
            throw "pip failed to install Aiteqno and its dependencies. Internet access is required on the first run."
        }
        Set-Content -LiteralPath (Join-Path $stagingPath "aiteqno-demo.ready") -Value $wheelHash -Encoding ASCII
        Move-Item -LiteralPath $stagingPath -Destination $runtimePath
    }
    catch {
        if (Test-Path -LiteralPath $stagingPath) {
            Remove-Item -LiteralPath $stagingPath -Recurse -Force
        }
        Stop-Demo $_.Exception.Message 5
    }
}
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    Stop-Demo "The cached Python runtime is incomplete: $runtimePath" 5
}

$tesseract = Find-Tesseract
$versionOutput = @(& $tesseract --version 2>&1)
if ($LASTEXITCODE -ne 0 -or $versionOutput.Count -eq 0 -or $versionOutput[0] -notmatch "tesseract\s+v?([0-9]+)\.") {
    Stop-Demo "Tesseract version could not be determined: $tesseract" 5
}
if ([int] $Matches[1] -lt 5) {
    Stop-Demo "Tesseract 5.x or newer is required: $($versionOutput[0])" 5
}
$env:AITEQNO_TESSERACT_EXECUTABLE = $tesseract

if ([string]::IsNullOrWhiteSpace($env:AITEQNO_TESSDATA_PREFIX)) {
    $bundledTessdata = Join-Path (Split-Path -Parent $tesseract) "tessdata"
    if (Test-Path -LiteralPath $bundledTessdata -PathType Container) {
        $env:AITEQNO_TESSDATA_PREFIX = $bundledTessdata
    }
}
if (-not [string]::IsNullOrWhiteSpace($env:AITEQNO_TESSDATA_PREFIX)) {
    $env:TESSDATA_PREFIX = $env:AITEQNO_TESSDATA_PREFIX
}
$availableLanguages = @(& $tesseract --list-langs 2>&1)
if ($LASTEXITCODE -ne 0) {
    Stop-Demo "Tesseract language data could not be inspected." 5
}
foreach ($selectedLanguage in $Language) {
    if ($availableLanguages -notcontains $selectedLanguage) {
        Stop-Demo "Tesseract language '$selectedLanguage' is missing. Available: $($availableLanguages -join ', ')" 5
    }
}

$runner = Join-Path $packageRoot "runner\demo_runner.py"
$schema = Join-Path $packageRoot "schema\document-ir-v0.1.schema.json"
$runnerArguments = @(
    $runner,
    $inputPath,
    "--output", $outputPath,
    "--schema", $schema,
    "--dpi", $Dpi.ToString([System.Globalization.CultureInfo]::InvariantCulture)
)
foreach ($selectedLanguage in $Language) {
    $runnerArguments += @("--language", $selectedLanguage)
}

& $runtimePython @runnerArguments
$demoExitCode = $LASTEXITCODE
if ($demoExitCode -ne 0) {
    exit $demoExitCode
}

Write-Host ""
Write-Host "Aiteqno demo completed: $outputPath"
if (-not $NoOpen) {
    Start-Process -FilePath "explorer.exe" -ArgumentList @("/e,", "`"$outputPath`"")
}
exit 0
