# . .\venv.ps1로 읽어들이면 현재 셸을 활성화하고 직접 실행하면 활성화된 새 셸을 연다.
function Invoke-ProumanVenv {
    param([string]$Root, [bool]$Direct, [ref]$Status)
    $venvDir = Join-Path $Root '.venv'
    $activate = Join-Path $venvDir 'Scripts\Activate.ps1'
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $activate -PathType Leaf) -or
        -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $message = @"
오류: 가상환경이 없습니다: $venvDir
생성: py -3 -m venv "$venvDir"
설치: & "$venvPython" -m pip install -r "$(Join-Path $Root 'requirements.txt')"
"@
        [Console]::Error.WriteLine($message.Trim())
        $Status.Value = 1
        return
    }
    if ($Direct) {
        Write-Output '가상환경이 활성화된 새 셸을 엽니다. 종료하려면 exit를 입력하세요.'
        $shellName = 'powershell.exe'
        if ($PSVersionTable.PSVersion.Major -ge 6) {
            $shellName = 'pwsh.exe'
            if (-not $IsWindows) {
                $shellName = 'pwsh'
            }
        }
        $shellExe = Join-Path $PSHOME $shellName
        $quoted = "'" + ($activate -replace "'", "''") + "'"
        & $shellExe -NoExit -Command ". $quoted"
        return
    }
    $currentPython = Get-Command python -ErrorAction SilentlyContinue
    if ($env:VIRTUAL_ENV -eq $venvDir -and $null -ne $currentPython -and $currentPython.Source -eq $venvPython) {
        Write-Output "이미 활성화됨: $venvDir"
        return
    }
    . $activate
    if (-not $?) {
        $Status.Value = 1
        return
    }
    Write-Output "가상환경 활성화: $venvDir"
}

$proumanVenvStatus = 0
Invoke-ProumanVenv -Root $PSScriptRoot -Direct ($MyInvocation.InvocationName -ne '.') -Status ([ref]$proumanVenvStatus)
Remove-Item Function:\Invoke-ProumanVenv
if ($MyInvocation.InvocationName -ne '.') {
    exit $proumanVenvStatus
}
if ($proumanVenvStatus -ne 0) {
    $global:LASTEXITCODE = $proumanVenvStatus
}
Remove-Variable proumanVenvStatus
