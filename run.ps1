# 실행한 작업 디렉터리는 유지하고, 저장소의 src/main.py를 호출합니다.
$ErrorActionPreference = 'Stop'
# PowerShell 7에서도 Python의 실패 종료 코드를 직접 전달합니다.
$PSNativeCommandUseErrorActionPreference = $false
$programArgs = @($args)

if ($programArgs.Count -eq 0) {
    Write-Output @'
사용법: .\run.ps1 <사람 YAML 파일> [--person 이름 | --project 사업약칭 | --all-people | --all-projects | --all] [--excel 파일.xlsx]

예시 (저장소 루트에서 실행):
  .\run.ps1 people\people_2027.yaml
  .\run.ps1 people\people_2027.yaml --person "홍길동"
  .\run.ps1 people\people_2027.yaml --project "사업1"
  .\run.ps1 people\people_2027.yaml --all-people    # 사람 전체 상세
  .\run.ps1 people\people_2027.yaml --all-projects  # 사업 전체 상세
  .\run.ps1 people\people_2027.yaml --all
  .\run.ps1 people\people_2027.yaml --excel 결과.xlsx  # 전체 결과를 엑셀로 저장

템플릿 실행:
  cd templates\01_basic
  ..\..\run.ps1 people\people_2027.yaml --all

현재 작업 디렉터리의 projects.yaml을 읽습니다.
활성 가상환경을 사용하며, 없으면 저장소의 .venv를 활성화합니다.
가상환경 설치 방법은 README.md를 참고하세요. 전체 옵션: .\run.ps1 --help
'@
    exit 0
}

$exitCode = 1
$entryPoint = Join-Path $PSScriptRoot 'src\main.py'
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
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
'@
        }
        . $activate
    }

    $python = Join-Path $venvDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "활성 가상환경의 Python을 찾을 수 없습니다: $python. 가상환경을 다시 활성화하거나 비활성화한 후 실행하세요."
    }

    & $python $entryPoint @programArgs
    $exitCode = $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine("오류: $($_.Exception.Message)")
}
exit $exitCode
