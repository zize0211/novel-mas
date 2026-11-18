# novel-mas 桌面启动器（关窗口 = 服务跟着退）
#
# 关键点：
#   1. 浏览器用**独立 profile**（--user-data-dir）开。
#      Edge 默认是单实例的，复用主进程的话「关掉这个窗口」主进程不会退，
#      Wait-Process 会永远等不到，服务也就永远不停。
#   2. 拿到浏览器进程句柄，阻塞等它退出，退了就杀 python 服务。
#   3. 所有输出写日志，不弹黑框（bat 那边用 -WindowStyle Hidden 调）。

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = 'http://127.0.0.1:8787'
$log = Join-Path $root 'app.log'
$profileDir = Join-Path $root '.appdata\edge-profile'

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function Test-Up {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

Log '--- 启动 ---'

$server = $null
if (Test-Up) {
    Log '服务已在跑（不是我起的），不接管它的生命周期'
} else {
    $pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pythonw) { $pythonw = 'python' }

    $env:PYTHONPATH = Join-Path $root 'src'
    $env:PYTHONIOENCODING = 'utf-8'

    $server = Start-Process -FilePath $pythonw `
        -ArgumentList '-m', 'novel_mas.web' `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -PassThru

    Log "起了服务，pid=$($server.Id)"

    $up = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Up) { $up = $true; break }
    }
    if (-not $up) {
        Log '服务没起来，退出'
        if ($server) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
        exit 1
    }
    Log '服务就绪'
}

# 独立 profile：开一个跟主浏览器无关的实例，关窗口就是真退
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

$edge   = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'

$browserExe = $null
if (Test-Path $edge) { $browserExe = $edge }
elseif (Test-Path $chrome) { $browserExe = $chrome }

if (-not $browserExe) {
    Log '没找到 Edge/Chrome，用默认浏览器打开，服务不跟窗口退'
    Start-Process $url
    exit 0
}

$browser = Start-Process -FilePath $browserExe `
    -ArgumentList "--app=$url", "--user-data-dir=$profileDir", '--window-size=1320,900' `
    -PassThru

Log "开了窗口，browser pid=$($browser.Id)"

# 等浏览器退出。关窗口 -> 进程结束 -> 这里返回
try {
    Wait-Process -Id $browser.Id -ErrorAction Stop
    Log '窗口关了'
} catch {
    Log "等待出错（可能进程已经没了）：$($_.Exception.Message)"
}

# 收尾：只杀自己起的服务，不动别人起的
if ($server) {
    Start-Sleep -Milliseconds 300
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    Log "已停服务 pid=$($server.Id)"
} else {
    Log '服务不是本次启动的，留着没动'
}

Log '--- 结束 ---'
