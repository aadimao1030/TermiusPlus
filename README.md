# TermiusPlus

**自动选择最快可用线路，低速时自动切换；传输中断后断点续传。**

TermiusPlus 是一个基于 rsync 的 SSH 文件与终端工作区。为同一服务器配置多个 IP、直连或跳板线路，工具会测速择优；传输持续低速时重新测速，切换到明显更快的线路，并复用已传数据继续传输。取消、断网或程序退出后，也可从记录续传。

![文件工作区](docs/assets/workspace.png)

## 亮点

| 功能 | 带来的便利 |
| --- | --- |
| **自动选择与切换更快线路** | 多个 IP / 直连 / 跳板先测速，选择测速最快的可用线路；持续低速时自动比较并切换，网络错误时尝试可用备用线路 |
| **rsync 断点续传** | 切换线路、取消、断网或程序退出后，复用未完成数据继续传；再次传同一文件也会自动复用未完成任务 |
| **远程 A → B 独立会话** | 两端直接传输，不经过 Mac；本机断网、合盖后仍继续 |
| **可展开的双栏目录树** | 本地与远程自由组合，拖放传输，面板可调整大小 |
| **文件预览与整理** | 预览、快捷打包、右键重命名及可找回的删除 |
| **本地 + SSH 多标签终端** | 最多 8 个会话，切换文件视图时保留终端 |

后端仅用 Python 标准库，前端资源随项目提供，无需 CDN 或云端账户。

## 架构

```mermaid
flowchart LR
    UI[浏览器 / macOS 窗口] --> API[本机 Python 服务]
    API --> FILES[文件浏览 / 预览 / 打包]
    API --> PTY[本地 PTY / SSH 终端]
    API --> PROBE[多线路测速 · 择优与低速切换]
    PROBE --> RSYNC[rsync · 差量传输与断点续传]
    API --> STATE[本机任务记录]
    RSYNC <--> MAC[本机文件]
    RSYNC <-->|SSH| A[远程 A]
    RSYNC <-->|SSH| B[远程 B]
    API -.启动与读取状态.-> TMUX[执行端 A 或 B · tmux]
    TMUX <-->|rsync over SSH · 数据直传| PEER[另一台远程服务器]
```

## 开始使用

本机需要 **Python 3.9+、OpenSSH、rsync 3.x**；支持 macOS / Linux。远程文件服务需要 Python 3.6+ 与 rsync 3.x。

```bash
# macOS；系统自带的 rsync/openrsync 不满足要求
brew install rsync

git clone https://github.com/aadimao1030/TermiusPlus.git
cd TermiusPlus
python3 app.py
```

1. 打开启动时输出的**完整链接**（包含 `#` 后的访问凭证）。在该终端按 **Ctrl+C** 关闭服务。
2. 新建 SSH 连接；先通过系统 SSH 确认主机指纹，使用已有密钥或 agent 登录。
3. 在两栏顶部选择位置。**双击文件夹展开/收起，三击进入**；选中文件后拖到另一栏，或点击传输按钮。
4. 文件右键可重命名、删除；删除移入所在目录的 `.termiusplus-trash`。打包快捷键为 **⌘/Ctrl+Shift+P**。
5. 打开“终端”，点击标签旁的 **＋**，选择“本地”或已保存的远程连接。

**Mac 断联仍传：**两栏选远程 A/B，在确认框选择“远程会话”。执行端须有 tmux，并能独立通过 SSH 密钥登录另一端。此模式使用执行端的固定线路；多线路测速与切换适用于本机控制的传输。本地传输与经 Mac 中转的传输仍需要本机在线。

**macOS 窗口版：**运行 `bash launcher/build.sh` 编译安装，之后打开 `TermiusPlus.app`；退出自启动的窗口版会停止本机服务。

## 开发

```text
app.py          启动入口
termiusplus/    服务、SSH、文件操作、终端与任务管理
web/            页面、样式、浏览器脚本
static/vendor/  内置 xterm.js 与许可
launcher/       macOS 原生窗口
scripts/        远程密钥配置与旧服务兼容工具
tests/         回归测试
```

运行 `python3 -m unittest discover -s tests -v`（前端测试还需要 Node.js；tmux 测试需要本机 tmux）。更多配置与行为见 [使用说明](docs/advanced.md)。

项目为独立实现，与 Termius 无关联。MIT 许可；第三方组件遵循各自许可。
