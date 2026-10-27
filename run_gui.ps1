# 저장소의 src/gui.py로 데스크톱 화면을 실행합니다.
$ErrorActionPreference = 'Stop'
# PowerShell 7에서도 Python의 실패 종료 코드를 직접 전달합니다.
$PSNativeCommandUseErrorActionPreference = $false

$exitCode = 1
$entryPoint = Join-Path $PSScriptRoot 'src\gui.py'
try {
    if ($env:VIRTUAL_ENV) {
        $venvDir = $env:VIRTUAL_ENV
    }
    else {
        $venvDir = Join-Path $PSScriptRoot '.venv'
        $activate = Join-Path $venvDir 'Scripts\Activate.ps1'
        $localPython = Join-Path $venvDir 'Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $activate -PathType Leaf) -or
            -not (Test-Path -LiteralPath $localPython -PathType Leaf)) {
            throw @'
저장소의 .venv 가상환경이 없습니다. 먼저 저장소 루트에서 설치하세요:
  py -3 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
'@
        }
        . $activate
    }

    $python = Join-Path $venvDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "활성 가상환경의 Python을 찾을 수 없습니다: $python. 가상환경을 다시 활성화하거나 비활성화한 후 실행하세요."
    }

    & $python $entryPoint
    $exitCode = $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine("오류: $($_.Exception.Message)")
}
exit $exitCode
