# ⚠ 本文件必须存成 UTF-8 with BOM。powershell.exe 5.1 会按 GBK 读无 BOM 的文件，
#   中文注释里的引号会被吃掉，脚本直接语法错、双击没反应（2026-11-19 踩过）。
#
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

# 端口不写死：服务在 8787 被占时会自动往后找，并把实际端口写进 data\port.txt。
# 启动器要跟着它，否则窗口会对着一个没人的端口报 Failed to fetch。
$dataDir = if ($env:NOVEL_DATA_DIR) { $env:NOVEL_DATA_DIR } else { Join-Path $root 'data' }
$portFile = Join-Path $dataDir 'port.txt'
$port = 8787
$url = "http://127.0.0.1:$port"
$log = Join-Path $root 'app.log'
$profileDir = Join-Path $root '.appdata\edge-profile'

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function Update-Port {
    if (-not (Test-Path $portFile)) { return }
    $saved = (Get-Content $portFile -Raw -ErrorAction SilentlyContinue)
    if ($null -eq $saved) { return }
    $saved = $saved.Trim()
    if ($saved -match '^\d+$' -and [int]$saved -ne $port) {
        $script:port = [int]$saved
        $script:url = "http://127.0.0.1:$($script:port)"
        Log "端口变成 $($script:port)（读自 port.txt）"
    }
}

function Test-Up {
    Update-Port
    try {
        $r = Invoke-WebRequest -Uri $script:url -UseBasicParsing -TimeoutSec 2
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

# 这个 profile 下还活着的浏览器进程。用来判断"窗口到底关没关"——
# 不能只看 Start-Process 给的句柄：Edge 遇到同 profile 的旧实例会直接复用，
# 新句柄一秒就退出，脚本会误以为窗口关了、把服务杀掉（2026-11-19 踩过）。
function Get-ProfileProcs {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '^(msedge|chrome)\.exe$' -and
            $_.CommandLine -and $_.CommandLine -like "*$profileDir*"
        }
}

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

$before = @(Get-ProfileProcs).Count
$browser = Start-Process -FilePath $browserExe `
    -ArgumentList "--app=$url", "--user-data-dir=$profileDir", '--window-size=1320,900' `
    -PassThru

Log "开了窗口，browser pid=$($browser.Id)（开窗前该 profile 已有进程 $before 个）"

# 窗口关掉 = 这个 profile 下的浏览器进程清空
$closed = $false
$deadline = (Get-Date).AddHours(8)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if (@(Get-ProfileProcs).Count -eq 0) { $closed = $true; break }
}
if ($closed) { Log '窗口关了' } else { Log '等满 8 小时没等到窗口关闭，按关处理' }

# 收尾：只杀自己起的服务，不动别人起的
if ($server) {
    Start-Sleep -Milliseconds 300
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    Log "已停服务 pid=$($server.Id)"
} else {
    Log '服务不是本次启动的，留着没动'
}

Log '--- 结束 ---'
