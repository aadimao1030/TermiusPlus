# TermiusPlus

TermiusPlus 是给在远程服务器上和 Codex、DeepSeek 这类编程 agent 一起工作时用的远程连接、文件树和高速传输工具。它不像 VS Code Remote 那样要在远程机器上下载并安装服务组件，而是直接走本机已有的 SSH，因此连接快，并且带有文件预览、终端和多线路 rsync 续传；这种不装远程组件、又把浏览和传输放在一起的用法目前少见，是专为 agent 协作设计的。

界面在本机浏览器里打开，Python 只用标准库。macOS、Linux 和 Windows 10/11 都可以从源码运行。界面截图在 `docs/images/`。

### 特点

- 直连 SSH。远程机器只需要它自己的 OpenSSH、Python 3.6+ 和 rsync 3.x，不用安装 TermiusPlus 或任何远程服务组件。
- 本地和远程双栏文件树，拖放后用 rsync 3.x 做差量上传、下载和续传。
- 同一台服务器可以保存多条线路（直连、不同 IP、跳板）。工具先测速再选用较快的一条，持续低速时可以换线路并接着传。
- 文件预览（文本和常见图片）以及本机、远程终端。
- 远程到远程可以在执行端的 tmux 里单独跑。本机关闭或断网之后，已经启动的会话继续。
- 服务只监听 `127.0.0.1`。每次启动的访问凭证放在链接的 `#` 后面。

远程认证使用 SSH 密钥或 agent，界面里不能输入 SSH 密码。先在终端用同样的配置连一次，确认主机指纹；工具不绕过主机指纹检查。

## 目录

仓库根目录的 `python3 app.py`（Windows 上 `python app.py` 或 `py -3 app.py`）仍然是启动命令。各目录的用途：

| 路径 | 用途 |
| --- | --- |
| `app.py` | 根目录薄入口，转调 `termiusplus.server`，命令行参数不变 |
| `termiusplus/server.py` | HTTP 服务、目录浏览、预览、rsync 传输 |
| `termiusplus/platform_compat.py` | Windows 与 POSIX 的进程、路径和 rsync 查找 |
| `termiusplus/terminal_sessions.py` | 本地终端和 SSH 终端会话 |
| `termiusplus/terminal_worker.py` | POSIX 上为 SSH 准备控制终端的子进程 |
| `termiusplus/transfer_history.py` | 传输记录和续传 |
| `termiusplus/remote_tasks.py` | 远程 tmux 独立会话的控制端 |
| `termiusplus/remote_worker.py` | 发到执行端运行的任务脚本 |
| `termiusplus/file_server.py` | 旧服务还在时，单独提供新版文件操作 |
| `termiusplus/terminal_server.py` | 旧服务还在时，单独提供终端 |
| `termiusplus/remote_server.py` | 旧服务还在时，单独提供远程会话 |
| `termiusplus/setup_remote_access.py` | 可选：在执行端生成专用密钥 |
| `web/index.html` | 文件树和传输页面 |
| `web/terminal.html` | 终端页面 |
| `web/static/vendor/` | 内置 xterm.js，不访问外网 |
| `platforms/windows/conpty.py` | 用标准库调用 ConPTY，打开 Windows 本地终端 |
| `platforms/windows/launcher.py` | 启动服务，并用 Edge 或 Chrome 的应用窗口打开界面 |
| `launcher/main.swift` | macOS App：窗口、启停服务、保存连接 |
| `launcher/serve.py` | macOS App 使用的服务入口 |
| `launcher/build.sh` | 编译并安装 `TermiusPlus.app` |
| `launcher/icon.swift` | 生成 App 图标 |
| `scripts/TermiusPlus.vbs` | Windows 双击入口，没有控制台窗口 |
| `scripts/TermiusPlus.bat` | 同样的入口，保留控制台，方便看报错 |
| `tests/` | 单元测试 |
| `docs/images/` | 界面截图 |
| `.github/workflows/ci.yml` | 在 Ubuntu、macOS、Windows 上跑测试，并编译 macOS App |

## macOS App

App 是一个原生窗口。它在后台启动本仓库的服务，自己带上访问凭证，关掉窗口就停止这次由它启动的服务。

### 1. 准备编译环境

需要 macOS 11 或更高版本，以及 Xcode 命令行工具。仓库里还要有 Python 3（系统自带的 `/usr/bin/python3` 即可，App 用它启动服务）。传输功能另需 rsync 3.x，安装方式见下一节；没装 rsync 时仍可浏览和开终端。

```sh
xcode-select --install
```

如果已经装过，这条命令会提示已安装，可以忽略。

### 2. 编译并安装

在仓库根目录执行。默认安装到 `/Applications/TermiusPlus.app`：

```sh
bash launcher/build.sh
```

装到其他目录时，把目录作为第一个参数，例如：

```sh
bash launcher/build.sh "$HOME/Applications"
```

脚本会把当时的仓库绝对路径写进 App 的 `Info.plist`（键 `TermiusPlusProjectDir`）。之后移动或改名仓库，要重新运行 `launcher/build.sh`，否则 App 找不到 `app.py`。只改 `termiusplus/` 或 `web/` 里的服务和页面时，不用重新编译，退出 App 再打开即可。

### 3. 第一次打开

在启动台、程序坞或访达里打开 `/Applications/TermiusPlus.app`。本机用 `build.sh` 编译出来的 App 是临时签名，通常可以直接打开。

如果系统提示来自身份不明的开发者，或提示已损坏、无法打开：

1. 在访达中按住 Control 点按 `TermiusPlus.app`，选择打开，再在对话框里点打开。
2. 或打开 系统设置 > 隐私与安全性，在页面下方点仍要打开。
3. 如果 App 是从浏览器下载的压缩包里拷出来的，隔离属性可能拦住它。在终端执行 `xattr -dr com.apple.quarantine /Applications/TermiusPlus.app` 后再打开。路径按实际安装位置改。

窗口出现后不需要复制链接。默认端口是 8765；如果这个端口已被别的程序占用，App 会自动改用后面的空闲端口，最多再试 50 个。

### 4. 关闭、日志和已保存的连接

点窗口左上角的关闭按钮，或按 Cmd+Q。App 会停止它自己启动的服务和本机传输。有本机传输正在进行时，退出前会先确认。App 异常退出时，服务也会停。停止后未完成的本机传输记为中断，下次打开可在记录里点续传。已经在服务器 tmux 里启动的远程独立会话不受影响。

如果这个仓库里已经有手动运行的 `python3 app.py`，并且端口和凭证能对上，App 直接使用它，窗口标题会注明。关掉 App 时不会停掉这个手动服务。

- 服务日志：`~/Library/Logs/TermiusPlus/server.log`
- 连接配置：`~/Library/Application Support/TermiusPlus/storage.json`。这是 App 自己的副本，和浏览器里的 localStorage 分开，换端口也不会丢。浏览器里已经保存的连接可以用菜单 文件 > 从浏览器导入连接配置 迁过来。

## 从源码运行：macOS 与 Linux

在仓库根目录执行 `python3 app.py`。服务只监听 `127.0.0.1`。按 Ctrl+C 停止服务和本机传输。

### 1. 安装 Python 3.9+

```sh
python3 --version
```

需要 3.9 或更高。macOS 可用系统自带的 `python3`，也可以用 Homebrew 的 Python。Linux 用发行版的 `python3` 包。

### 2. 安装 OpenSSH 客户端

macOS 已自带 `ssh`。Linux 示例（Debian/Ubuntu）：

```sh
sudo apt-get update && sudo apt-get install -y openssh-client
ssh -V
```

### 3. 安装 rsync 3.x

浏览目录和终端不需要 rsync。开始传输时才需要 **rsync 3.x**。没找到时，界面会说明原因，服务本身不退出。

macOS 的系统 openrsync 不能用。用 Homebrew 安装真正的 rsync 3.x：

```sh
brew install rsync
"$(brew --prefix)/bin/rsync" --version
```

`--version` 的第一行应包含 `rsync  version 3`。Apple 芯片上 `brew --prefix` 一般是 `/opt/homebrew`，所以程序在 `/opt/homebrew/bin/rsync`。Intel Mac 一般是 `/usr/local/bin/rsync`。工具会先找这两个路径，再找 PATH，因此只要装在上述位置，就不必改 PATH，也不会误用系统 openrsync。

如果 rsync 装在别的路径（例如 Linuxbrew），启动前指定：

```sh
export TERMIUSPLUS_RSYNC="$(brew --prefix)/bin/rsync"
python3 app.py
```

Linux 用发行版的 rsync 3.x，例如：

```sh
sudo apt-get install -y rsync
rsync --version
```

### 4. 启动并打开完整链接

```sh
cd /path/to/TermiusPlus
python3 app.py
```

终端会打印两行，类似：

```text
TermiusPlus 已启动，请在浏览器打开：
http://127.0.0.1:8765/#一长串字符
```

把第二行整行复制到浏览器地址栏，包括 `#` 和它后面的凭证。凭证只在这次启动有效。只打开 `http://127.0.0.1:8765/`、少了 `#` 后面的部分，或用了上一次启动的旧链接，页面请求会失败，提示：请使用启动时显示的完整链接打开。

换端口（8765 已被占用时）：

```sh
python3 app.py --port 8766
```

打开的链接端口要和打印出来的一致，例如 `http://127.0.0.1:8766/#...`。

希望每次重启都用同一个凭证（方便把链接做成书签）时，先设置 `TERMIUSPLUS_TOKEN`，再启动。不设置时，每次启动都会生成新的随机凭证。

```sh
export TERMIUSPLUS_TOKEN='请换成一段足够长的随机字符串'
python3 app.py
```

这时完整链接是 `http://127.0.0.1:8765/#` 再加上这个环境变量的值。凭证出现在本机终端输出里，不要发到远程机器上。

## 从源码运行：Windows

在 Windows 10 或 Windows 11 上，可以从 PowerShell 运行源码，也可以双击 `scripts\TermiusPlus.vbs`。两条路都使用本机已安装的 Python，不需要打包成 exe。

### 1. 安装 Python 3.9+

从 [python.org](https://www.python.org/downloads/windows/) 安装。安装程序里勾选 py launcher，并勾选 Add python.exe to PATH。装完后新开一个 PowerShell：

```powershell
py -3 --version
```

应显示 3.9 或更高。`python --version` 也可以。双击脚本优先用 `pyw -3`，找不到再用 `pythonw`。

### 2. 启用 OpenSSH 客户端

打开 设置 > 应用 > 可选功能 > 添加可选功能，安装 OpenSSH 客户端。装完后：

```powershell
ssh -V
```

工具先在 PATH 里找 `ssh`，再找 `C:\Windows\System32\OpenSSH\ssh.exe`。认证仍是密钥或 agent，不在界面里输入密码。先在 PowerShell 里用同样的配置连一次目标机器，确认主机指纹。

### 3. 安装 rsync 3.x

Windows 没有自带可用的 rsync。浏览和终端可以先用；一点传输，就会提示需要 rsync 3.x。三选一即可。

**MSYS2（推荐）。** 从 [msys2.org](https://www.msys2.org/) 安装。打开 MSYS2 终端执行：

```sh
pacman -S rsync
```

默认安装时，程序在 `C:\msys64\usr\bin\rsync.exe`。这是 msys 路径风格（`/c/Users/...`），也是工具的默认风格，不要再设置 `TERMIUSPLUS_RSYNC_PATH_STYLE`。

**Git for Windows。** 官方安装包不一定带 rsync。只有当这个文件真实存在时才能用：`C:\Program Files\Git\usr\bin\rsync.exe`。它同样使用 msys 路径，不用设置 `TERMIUSPLUS_RSYNC_PATH_STYLE`。

**cwRsync。** 使用 Cygwin 路径（`/cygdrive/c/Users/...`），必须同时设置 `TERMIUSPLUS_RSYNC_PATH_STYLE=cygwin`。路径以实际安装目录为准，常见位置是 `C:\Program Files\cwRsync\bin\rsync.exe`。

在 PowerShell 里为当前窗口指定 MSYS2 的 rsync，然后启动（把路径换成你机器上 `rsync.exe` 的位置）：

```powershell
cd C:\path\to\TermiusPlus
$env:TERMIUSPLUS_RSYNC = 'C:\msys64\usr\bin\rsync.exe'
py -3 app.py
```

cwRsync 则是：

```powershell
$env:TERMIUSPLUS_RSYNC = 'C:\Program Files\cwRsync\bin\rsync.exe'
$env:TERMIUSPLUS_RSYNC_PATH_STYLE = 'cygwin'
py -3 app.py
```

`$env:` 只对当前 PowerShell 窗口有效。要给以后新开的窗口使用，可以用 `setx`，然后关掉窗口再开一个：

```powershell
setx TERMIUSPLUS_RSYNC "C:\msys64\usr\bin\rsync.exe"
```

cwRsync 再加一条：`setx TERMIUSPLUS_RSYNC_PATH_STYLE cygwin`。不要把 `TERMIUSPLUS_RSYNC_PATH_STYLE` 设成 `windows` 或其他值；盘符路径会被 rsync 当成远程主机名。

未设置环境变量时，工具还会在 PATH、`C:\msys64\usr\bin`、Git 的 `usr\bin` 和常见 cwRsync 目录里查找。调用 rsync 时会设置 `MSYS_NO_PATHCONV=1`，避免 MSYS 再改写参数。

### 4. 在 PowerShell 里从源码启动

```powershell
cd C:\path\to\TermiusPlus
py -3 app.py
```

`python app.py` 等价。换端口：`py -3 app.py --port 8766`。

和 macOS/Linux 一样，把终端打印的整行链接复制到浏览器，包括 `#` 后面的凭证。固定凭证：

```powershell
$env:TERMIUSPLUS_TOKEN = '请换成一段足够长的随机字符串'
py -3 app.py
```

按 Ctrl+C 停止。本地栏显示 `C:/Users/...` 这种正斜杠路径。从磁盘根目录再向上会进入此电脑，列出已连接的盘符。不能把此电脑本身当作传输目录。

本地终端使用系统自带的 ConPTY（Windows 10 1809 和 Windows 11），不需要 pywinpty。默认打开 PowerShell，并把控制台输入输出设为 UTF-8；找不到 PowerShell 时使用 `COMSPEC`（一般是 cmd）。可以用 `TERMIUSPLUS_SHELL` 指定 `powershell.exe`、`pwsh.exe` 或 `cmd.exe` 的完整路径。更老的 Windows 上打开本地终端会提示失败，文件浏览不受影响。

和 Unix 不完全一样、但会给出说明而不是直接崩溃的部分：重命名用不会覆盖同名文件的 `os.rename`；取消传输用 `taskkill /F /T` 结束进程树；chmod 只按 Windows 能表达的方式处理；创建符号链接可能需要开发者模式或管理员权限。远程独立会话的执行端仍然必须是带 tmux、Python 3.6+ 和 rsync 3.x 的服务器，Windows 只作为控制端。

### 5. 双击 Windows 窗口

先完成上面的 Python 安装。rsync 只影响传输，不影响把窗口打开。

1. 在资源管理器中进入仓库的 `scripts` 文件夹。
2. 双击 `TermiusPlus.vbs`。
3. 脚本用 `pyw -3` 运行 `platforms\windows\launcher.py`，没有黑色控制台。它在 `127.0.0.1` 上启动 `app.py`（默认端口 8765，被占用时改用后面的空闲端口），并用 Microsoft Edge 或 Google Chrome 的 `--app` 模式打开独立窗口。浏览器使用单独的配置目录 `%LOCALAPPDATA%\TermiusPlus\browser-profile`，不会打开你日常使用的窗口。
4. 关掉这个窗口后，本次由启动器拉起的服务会停止。

补充：

- 再次双击时，如果上次由启动器拉起的服务还在，会继续用它再开一个窗口。后开的窗口关掉不会停止服务，先开的那个关掉时才会停。
- 在 PowerShell 里手动执行 `py -3 app.py` 时，启动器认不出那个进程的凭证，会改用下一个端口，并且不会把它关掉。两边共用仓库里的 `.termiusplus-state`，不要同时跑两个传输。
- 服务日志：`%LOCALAPPDATA%\TermiusPlus\server.log`。带凭证的链接也会写在这个日志里。
- 没有安装 Edge 和 Chrome 时，会尝试用默认浏览器打开，并弹出说明。点确定后，本次启动的服务会停止。也可以设置 `TERMIUSPLUS_BROWSER` 为 `msedge.exe` 或 `chrome.exe` 的完整路径。
- 双击没有反应时，改双击 `scripts\TermiusPlus.bat`。它是同一个入口，但保留控制台，方便看报错。
- 这不取代源码运行。仍然可以直接 `py -3 app.py`，自己用浏览器打开终端里打印的链接。修改 `termiusplus` 或 `web` 后，重新双击即可，不用重新打包。

## 常见问题

**请使用启动时显示的完整链接打开。** 浏览器地址和当前这次服务的凭证不一致。`#` 后面的字符串必须和这次启动时终端打印的完全相同。关掉旧标签，从终端重新复制整行，包括 `http://127.0.0.1:`、端口、`#` 和后面的全部字符。如果设置了 `TERMIUSPLUS_TOKEN`，链接里的凭证就是这个环境变量的值。macOS App 和 `TermiusPlus.vbs` 会自己带上凭证，不要再手改地址栏里 `#` 后面的内容。

**端口被占用。** 直接运行 `python3 app.py` 或 `py -3 app.py` 时，默认绑定 8765。如果终端出现 Address already in use，或 Windows 提示只允许使用一次，说明已有一个 TermiusPlus 或别的程序占着这个端口。换一个端口再启动，例如 `python3 app.py --port 8766`，并打开新打印的链接。macOS App 和 Windows 的 VBS 启动器会自己往后找空闲端口；源码启动不会自动换端口。

**需要 rsync 3.x，或提示系统 openrsync 不可用。** 浏览和终端仍可用，只有传输会停在这一步。按上面对应系统安装 rsync 3.x，或把 `TERMIUSPLUS_RSYNC` 设成那个 `rsync` 程序的完整路径，然后重新启动服务。macOS 上先运行 `"$(brew --prefix)/bin/rsync" --version`，确认是 3.x。Windows 上确认 `rsync.exe` 的路径写对了；用 cwRsync 时还要有 `TERMIUSPLUS_RSYNC_PATH_STYLE=cygwin`。

**Windows 双击后没有窗口。** 用 `scripts\TermiusPlus.bat` 看控制台输出，并打开 `%LOCALAPPDATA%\TermiusPlus\server.log`。先确认 `py -3 --version` 能运行。没有 Edge 和 Chrome 时，启动器会说明这一点并停掉它刚启动的服务。

**Windows 本地终端打不开。** ConPTY 需要 Windows 10 1809 或 Windows 11。更老的系统会看到明确的失败提示，文件浏览不受影响。

## 使用

1. 添加直连、不同 IP 或跳板线路。主机可填 `~/.ssh/config` 中的别名，例如 `server-direct`、`server-jump`。别名的非默认端口也需要填写到界面中。跳板也可直接填 `user@bastion:22`。密钥留空时使用 SSH 配置/agent。
2. 点击任一栏顶部的位置标题，选择“本地”或任一已保存的远程连接。两栏独立，可本地↔远程、本地↔本地或远程↔远程。点击圆形箭头刷新当前目录；眼睛按钮切换隐藏文件显示，默认不显示，并分别记住每栏设置。此设置只影响浏览，传输整个目录时仍包含隐藏文件。
3. 点击三角展开多个层级；双击文件夹进入。点击行选中，Cmd/Ctrl 点击可多选。把文件或文件夹拖到另一栏即可确认传输，拖到文件夹上则以该文件夹为目标。也可直接把访达或资源管理器中的文件或文件夹拖入任意一栏。浏览器会先将外部拖放内容暂存到本机，再使用 rsync 传输，需要额外本地磁盘空间。远程↔远程通过本机临时目录分两步中转，同样需要本地空间。

   选中后点击“打包”或按 **Cmd/Ctrl+Shift+P**，在源目录生成 tar.gz，保留原文件；传输确认框也可勾选“先打包再传输”。**Cmd/Ctrl+Enter** 从当前栏传到另一栏，**Cmd/Ctrl+R** 刷新当前栏。未选中项目时，传输的是源目录的内容；会更新目标同名文件，不会删除目标多余文件。指向文件夹的软链接可点击三角展开、双击或按 Enter 进入目标目录，并保留 ↗ 标记。展开时只读取下一层，不自动递归链接。直接传输或打包链接本身仍保留链接；进入目标目录后可传输其中的内容。

4. 多条线路会先核对远程机器和目录身份，再各做一次 4 MiB SSH 下载测速，选择较快线路。测速超时不会淘汰已核对可用的线路，仍会尝试实际传输；备用线路只允许指向所选服务器和同一目录。指向不同机器或目录的线路被排除。Linux 使用 machine-id；其他平台使用主机名，结合规范路径、设备号与 inode 校验，因此主机名重复的平台仍需用户确保确实是同一服务器。
5. 开启自动切换时，传输持续低于阈值约 20 秒，会重新比较线路；备用线路速度须超过当前探测速度的 1.5 倍且至少快 128 KiB/s，才会停止当前连接并通过备用线路续传。切换至少间隔 60 秒，每任务最多 6 次低速切换。网络错误另有最多 3 次重试；关闭自动切换时只在当前线路重试。

测速是下载方向的小样本，并包含连接开销，不是准确的长期吞吐量预测；上传与下载可能不对称。备用探测会短暂占用带宽。rsync 的进度速度也不是独立网卡实时测速，因此自动切换属于保守启发式策略。文件列表扫描期间不因无进度而切换。SSH 不支持把正在传的连接无缝迁移到另一路线，切换有短暂中断。

使用 `.termiusplus-partial` 保存未完成数据，重试时作为 rsync 差量校验的基础；不使用危险的 `--append` 或 `--inplace`。阶段速度、已处理字节及进度来自 rsync。远程到远程的中转任务分别显示下载/上传阶段进度和两阶段总进度（下载占 0–50%，上传占 50–100%）。线路探测速度包含 SSH 建连开销，与实际传输速度分别显示。目录内容应尽量在传输期间保持稳定。

线路配置保存在该浏览器的 localStorage，仅包含名称、主机、端口、跳板、密钥路径；不会读取或存储私钥内容。任务记录和日志保存在本机 `.termiusplus-state/transfers.json`，重启后恢复。工具每次只运行一个传输任务，防止任务同时写入同一目录。

可通过 `TERMIUSPLUS_RSYNC` 指定 rsync 3.x。macOS/Linux 示例：`TERMIUSPLUS_RSYNC=/自定义路径/rsync python3 app.py`。Windows 的设置方式见上面的从源码运行：Windows。

## 取消与续传

取消、失败或服务中断后，记录里显示“续传”。点击后创建新任务，使用原连接、源目录、目标目录和文件选择；不会同时启动两个写入任务。再次提交相同源、目标和文件选择也会自动复用上一条未完成任务。普通上传/下载复用目标端 `.termiusplus-partial`；远程到远程复用原中转目录，并重新核对、同步源内容后继续上传。rsync 使用已下载的数据作差量校验基础，不盲目追加；开始时有校验过程，进度可能重新从 0 显示，并非重新发送全部字节。

记录与 Finder 暂存、中转副本会保留到工具目录的 `.termiusplus-state/`，服务重启后仍可继续。完成的中转、Finder 暂存会自动清理；未完成数据会占用磁盘空间。已完成的打包结果会复用，不重复打包。中断在打包尚未完成时，会重新完成打包。旧版记录缺少连接参数时，先在两栏打开原源和目标目录，再点“续传”并核对确认框；仅能复用仍然存在的未完成数据。

## 远程独立会话：本机断联仍继续

两栏选择远程 A 和 B 后，在传输确认框选择“远程会话（Mac 断联后继续）”。可在 A 推送，也可在 B 拉取；填写执行端独立登录另一端所用的 SSH 地址、端口，以及执行端自身的密钥路径（可选）。本机 SSH 别名或密钥路径不一定适用于执行端。两端需要 Python 3.6+、rsync 3.x，执行端需要 tmux，且必须能独立使用密钥登录另一端、已确认主机指纹。不会复制本机私钥或使用本机的 agent 转发。Windows 作为控制端时同样适用：本机断网或关掉窗口后，已经在服务器上启动的会话继续运行。

确认框会记住同一执行端与对端的 SSH 地址、端口和远程密钥路径。已验证的连接默认值也可保存在 `.termiusplus-state/remote-links.json`；其中只记录路径，不保存私钥。修改执行端或对端后会选取对应设置。

工具在执行端的独立 tmux 服务（`tmux -L termiusplus`）创建 `tp-任务ID` 会话，任务脚本、配置与状态保存在 `~/.cache/termiusplus/jobs/任务ID/`。A 与 B 直接传输数据，不经过 Mac。Mac 断网、合盖、关闭网页或退出本机工具后，已确认启动的远程会话继续运行；本机重连或重开后重新读取同一会话的进度和日志，不重复启动。可手动用 `tmux -L termiusplus attach -t tp-任务ID` 查看运行中的会话。

远程网络错误最多重试 3 次；该模式使用执行端到对端的一条 SSH 线路，本机中转的多线路测速与切换不适用。取消必须成功联系执行端；本机离线时不能声称已取消。取消或失败后可从记录续传，以新会话复用对端未完成数据。服务器重启、tmux 服务被终止不能靠会话保持；记录仍可用于重新续传。完成后会话退出，状态与日志仍保留在远程。

旧传输服务运行时，可用 `python3 -m termiusplus.remote_server` 在 8768 端口提供此功能，状态目录设为 `.termiusplus-state/remote-controller`；下一次正常启动会合并这些记录并继续监控。独立服务拒绝在原文件服务仍有任务时启动远程传输，避免两个任务同时写入。旧版经 Mac 中转的记录继续用原方式续传，不能将 Mac 缓存自动变成不依赖 Mac 的远程任务。

## 终端

点击顶部“终端”，或远程文件栏顶部的 `>_` 按钮，打开交互式终端。标签旁的“＋”第一项固定是“本地 · 这台电脑”。macOS 和 Linux 用本机的登录 shell（`$SHELL`，取不到时用 `/bin/sh`）；Windows 用 PowerShell，找不到时用 cmd，也可以用 `TERMIUSPLUS_SHELL` 指定程序。本地终端在选定的本地目录打开；本地文件栏顶部的 `>_` 按钮直接打开该目录的本地终端。选择服务器则按原方式打开 SSH 终端；从远程文件栏打开时使用当前远程目录。本地会话与远程会话可同时存在于同一终端页面，互不影响。

支持方向键历史、Tab 补全、Ctrl+C、终端大小调整、选择复制与多标签切换，同一终端页面可同时连接最多 8 个会话：点击“＋”选择本地或某台服务器，点击标签切换；切换时后台会话继续运行并接收输出，终端内容、输入与目录互相独立。也可为同一位置打开多个会话。标签上的 × 关闭该会话；已结束的标签可点击重新连接。本地终端与远程终端共用同一套限制：同一位置再次打开会切回已有标签，除非先关闭它。顶部不再常驻连接、目录设置与操作工具栏。Ctrl+C 中断、Ctrl+L 清屏、⌘/Ctrl+Shift+C 复制仍可直接使用。远程认证沿用已有密钥、agent 和跳板配置，仍要求预先确认主机指纹。终端不自动切换线路，以免丢失交互会话；长时间命令可在远程使用 tmux。

本地终端在本机以当前用户身份运行，权限与直接在终端里输入命令相同；它同样受本工具的访问凭证保护，服务默认只监听 `127.0.0.1`。

正常启动 `python3 app.py` 会同时提供文件和终端功能，前端使用本地内置的 xterm.js 5.5.0 与 fit 插件（许可证见 `web/static/vendor`），无需安装 Python 依赖或访问 CDN。文件与终端视图切换时保留原页面、会话、输出和目录位置；关闭整个工作区页面会断开会话；无法发送关闭请求时，停止轮询 5 分钟后回收连接。终端输出缓存限制为 1 MiB。当前保留旧传输服务运行时，可设置 `TERMIUSPLUS_TERMINAL_TOKEN` 为原访问凭证并运行 `python3 -m termiusplus.terminal_server --port 8767`，独立提供终端，不影响传输。下一次正常启动无需独立服务。

## 文件预览

选中一个文件点击“预览”，或双击文件 / 按 Enter 打开；Alt+P 预览当前选中的文件。支持本地、远程和软链接目标，只读显示，不修改原文件。文本支持 UTF-8 和带 BOM 的 UTF-16/32，仅显示前 256 KiB；PNG、JPEG、GIF、WebP 图片最大 8 MiB。HTML、SVG、代码均按文本显示，不执行。PDF、压缩包及其他二进制格式暂不支持。远程预览通过当前连接读取所需内容。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

Windows 上对应的命令是 `py -3 -m unittest discover -s tests -v`。GitHub Actions 会在 Ubuntu、macOS 和 Windows 上运行这套测试，并在 macOS 上编译 `TermiusPlus.app`。

测试覆盖初始线路择优、异机线路排除、网络故障切换、参数校验、特殊路径处理、不删除目标文件、续传设置、线路切换阈值、真实本地 rsync 传输、中断续传与取消（真实 rsync 测试在未安装 3.x 时跳过）。远程连接、跳板、真实网络择优需要你自己的服务器环境验证。

## 现成的免费选择

- [RsyncUI](https://github.com/rsyncOSX/RsyncUI)：macOS 的免费开源 rsync 图形工具，可上传下载，要求免密 SSH；适合目录同步任务，不是 Cursor 风格文件浏览器。
- [VS Code Remote SSH](https://code.visualstudio.com/docs/remote/ssh)：远程多级目录树与终端；搭配 RsyncUI 可满足浏览和 rsync 传输。会在远程安装 VS Code Server。
- [Rsync Upload](https://marketplace.visualstudio.com/items?itemName=Dri.rsync-upload)：MIT 扩展，可给 VS Code Remote SSH 加上 rsync 上传。其文档侧重上传，不能据此承诺完整下载功能。

上述工具的已查阅文档没有明确承诺持续低速时自动在多个 IP/跳板线路间择优续传。

文件与终端使用相同位置的顶部导航；传输、预览和打包操作集中在顶部。文件列表与传输记录各有独立滚动条，整页也能滚动。拖动两栏之间的分隔条可调整左右宽度，拖动三个框底部的把手可分别调整高度；尺寸自动保存，双击把手恢复默认尺寸。点击任一文件栏的连接名称，可切换位置；连接旁的“编辑”可修改名称、主机、端口、跳板和密钥路径，或删除保存的连接。编辑只更新后续连接参数，已经运行的终端和传输继续使用启动时的参数。
