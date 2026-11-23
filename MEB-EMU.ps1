param(
    [ValidateSet('help','setup','doctor','verify','test','import','plan','start','package')][string]$Action='help',
    [string]$Bundle,
    [string]$Profile
)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
Set-Location -LiteralPath $root
$python=Join-Path $root '.venv/Scripts/python.exe'
if($Action -eq 'help'){
    Write-Output 'MEB EMU 0.1.0-preview.1'
    Write-Output 'setup | doctor | verify | test | import -Bundle PATH | plan -Profile PATH | start -Profile PATH | package'
    exit 0
}
if($Action -eq 'setup'){
    if(-not(Test-Path -LiteralPath $python)){
        if(Get-Command py -ErrorAction SilentlyContinue){& py -3.14 -m venv (Join-Path $root '.venv')}
        else{& python -m venv (Join-Path $root '.venv')}
        if($LASTEXITCODE -ne 0){throw 'Python 3.14 is required'}
    }
    & $python -c 'import sys;assert sys.version_info>=(3,14)'
    if($LASTEXITCODE -ne 0){throw 'Python 3.14 is required'}
    & $python -m pip install -r (Join-Path $root 'requirements.txt')
    if($LASTEXITCODE -ne 0){throw 'Python dependency installation failed'}
    & $python -X utf8 -B (Join-Path $root 'tools/dependencies.py')
    exit $LASTEXITCODE
}
if(-not(Test-Path -LiteralPath $python)){throw 'Run ./MEB-EMU.ps1 setup first'}
$arguments=@('-X','utf8','-B',(Join-Path $root 'tools/emu.py'),$Action)
if($Bundle){$arguments+=@('--bundle',$Bundle)}
if($Profile){$arguments+=@('--profile',$Profile)}
& $python @arguments
exit $LASTEXITCODE
