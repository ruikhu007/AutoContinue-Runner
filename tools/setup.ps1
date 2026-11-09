# 续推运行器安装程序：准备独立的 Python 运行环境、安装依赖、创建桌面快捷方式。
# 由「安装.bat」调用。可以重复运行：已经装好的部分会跳过，缺的会补上。
param(
    [switch]$NoShortcut,
    [string]$ShortcutName = "续推运行器",
    [switch]$Quiet
)

# 用 Continue：PowerShell 5.1 在 Stop 模式下会把外部程序写到 stderr 的普通提示当成错误
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot
$Rt   = Join-Path $Root "runtime"
$Py   = Join-Path $Rt "python.exe"
$PyVer = "3.12.10"
$PySha = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
$PyUrls = @(
    "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip",
    "https://registry.npmmirror.com/-/binary/python/$PyVer/python-$PyVer-embed-amd64.zip",
    "https://mirrors.huaweicloud.com/python/$PyVer/python-$PyVer-embed-amd64.zip"
)
$Indexes = @(
    "https://pypi.org/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple"
)

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Fail($msg) {
    Say ""
    Say "安装没有完成：$msg" "Red"
    Say "可以再双击一次「安装.bat」重试；仍不行，请把这个窗口的内容截图发给云知。" "Yellow"
    exit 1
}

Say "续推运行器 安装程序" "Cyan"
Say "安装位置：$Root"
Say ""

# ---------------------------------------------------------------- 1. Python 运行环境
Say "[1/4] 准备 Python 运行环境（只放在本程序目录里，不影响电脑上已有的 Python）" "Cyan"
$ok = $false
if (Test-Path $Py) {
    & $Py -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" 2>$null
    $ok = ($LASTEXITCODE -eq 0)
}
if ($ok) {
    Say "      已存在，跳过。"
} else {
    $zip = Join-Path $env:TEMP "acr-python-$PyVer.zip"
    $got = $false
    foreach ($u in $PyUrls) {
        try {
            Say "      下载 $u"
            Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing -TimeoutSec 180 -ErrorAction Stop
            $h = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
            if ($h -eq $PySha) { $got = $true; break }
            Say "      文件校验不一致，换下一个下载源。" "Yellow"
        } catch {
            Say "      这个下载源不可用：$($_.Exception.Message)" "Yellow"
        }
    }
    if (-not $got) { Fail "Python 运行环境下载失败，请检查网络。" }
    try {
        if (Test-Path $Rt) { Remove-Item $Rt -Recurse -Force -ErrorAction Stop }
        Expand-Archive -Path $zip -DestinationPath $Rt -Force -ErrorAction Stop
    } catch { Fail "解压 Python 运行环境失败：$($_.Exception.Message)" }
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    # 打开 site-packages，并把程序目录（runtime 的上一级）加入模块搜索路径
    $pth = Join-Path $Rt "python312._pth"
    [IO.File]::WriteAllText($pth, "python312.zip`r`n.`r`n..`r`nimport site`r`n", [Text.Encoding]::ASCII)
    Say "      完成。"
}

# ---------------------------------------------------------------- 2. pip
Say "[2/4] 准备安装工具 pip" "Cyan"
& $Py -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    $whl = Get-ChildItem (Join-Path $PSScriptRoot "pip-*.whl") | Select-Object -First 1
    if (-not $whl) { Fail "安装包不完整：tools 目录里缺少 pip。" }
    # pip 本身是纯 Python 的 wheel（zip 包），直接解压进 site-packages 即可使用
    $sp = Join-Path $Rt "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $sp | Out-Null
    & $Py -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$($whl.FullName)" "$sp"
    & $Py -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) { Fail "pip 安装失败。" }
}
Say "      完成。"

# ---------------------------------------------------------------- 3. 依赖
Say "[3/4] 安装运行所需的组件（首次约 60MB，视网速需要 1~5 分钟）" "Cyan"
$req = Join-Path $Root "requirements-lock.txt"
if (-not (Test-Path $req)) { $req = Join-Path $Root "requirements.txt" }
$done = $false
foreach ($idx in $Indexes) {
    Say "      从 $idx 安装…"
    & $Py -m pip install --no-warn-script-location --disable-pip-version-check --prefer-binary --timeout 30 --retries 2 -q -r "$req" -i $idx
    if ($LASTEXITCODE -eq 0) { $done = $true; break }
    Say "      这个源没装成功，换下一个。" "Yellow"
}
if (-not $done) { Fail "组件安装失败，请检查网络。" }
& $Py -c "import autocontinue, playwright, fastapi, uvicorn, yaml, mcp, pydantic; import uiautomation, mss; print('      v' + autocontinue.__version__)"
if ($LASTEXITCODE -ne 0) { Fail "组件自检没有通过。" }
Say "      预编译，让第一次启动更快……"
& $Py -m compileall -q -j 0 "$Rt\Lib\site-packages" "$Root\autocontinue" *> $null
Say "      完成。"

# ---------------------------------------------------------------- 4. 快捷方式
Say "[4/4] 创建快捷方式" "Cyan"
if ($NoShortcut) {
    Say "      按要求跳过。"
} else {
  try {
    $sh = New-Object -ComObject WScript.Shell -ErrorAction Stop
    $targets = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
    foreach ($dir in $targets) {
        $lnk = $sh.CreateShortcut((Join-Path $dir "$ShortcutName.lnk"))
        $lnk.TargetPath = Join-Path $Root "启动续推运行器.bat"
        $lnk.WorkingDirectory = $Root
        $lnk.IconLocation = (Join-Path $PSScriptRoot "icon.ico") + ",0"
        $lnk.WindowStyle = 7
        $lnk.Description = "续推运行器：自动推进大模型长程任务"
        $lnk.Save()
    }
    Say "      已在桌面和开始菜单创建「$ShortcutName」。"
  } catch { Say "      快捷方式没建成（$($_.Exception.Message)），可以直接双击程序目录里的「启动续推运行器.bat」。" "Yellow" }
}

Say ""
Say "安装完成。" "Green"
Say "以后双击桌面上的「$ShortcutName」即可使用。第一次会打开一个专用浏览器，请在里面登录要用的大模型网站（ChatGPT、Claude 等）。"
if (-not $Quiet) {
    $ans = Read-Host "现在就启动吗？(Y/n)"
    if ($ans -eq "" -or $ans -match "^[Yy]") {
        Start-Process -FilePath (Join-Path $Root "启动续推运行器.bat") -WorkingDirectory $Root -WindowStyle Minimized
    }
}
exit 0
