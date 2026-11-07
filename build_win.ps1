# 저장소 루트의 .venv에서 Windows GUI 단일 실행파일을 만든다.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
$entryPoint = Join-Path $repoRoot 'src\gui.py'
$distPath = Join-Path $repoRoot 'publish\win'
$buildPath = Join-Path $repoRoot 'build\pyinstaller'

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "가상환경이 없습니다: $pythonExe`n먼저 requirements-build.txt를 설치하세요."
}

New-Item -ItemType Directory -Force -Path $distPath, $buildPath | Out-Null
& $pythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name Prouman `
    --distpath $distPath `
    --workpath (Join-Path $buildPath 'work') `
    --specpath $buildPath `
    --paths (Join-Path $repoRoot 'src') `
    $entryPoint
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 빌드 실패 (종료 코드 $LASTEXITCODE)"
}

$executable = Join-Path $distPath 'Prouman.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "빌드 결과가 없습니다: $executable"
}
Get-Item -LiteralPath $executable | Select-Object FullName, Length, LastWriteTime
