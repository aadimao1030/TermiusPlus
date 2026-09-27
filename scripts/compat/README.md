# 旧版本兼容入口

正常运行只需 `python3 app.py`；下列服务仅用于升级代码但不能中断已有传输的场景。

- `file_server.py`：在 8769 提供新版文件操作和终端，其他请求转发到 8765；设置 `TERMIUSPLUS_FILE_TOKEN` 为原服务访问凭证。
- `terminal_server.py`：在 8767 提供独立终端；设置 `TERMIUSPLUS_TERMINAL_TOKEN`。
- `remote_server.py`：在 8768 提供独立远程任务管理；设置 `TERMIUSPLUS_REMOTE_TOKEN`，并将 `TERMIUSPLUS_STATE_DIR` 指向 `.termiusplus-state/remote-controller`。

每个服务都只绑定 127.0.0.1。原服务仍运行的任务不自动迁移；完成后正常重启主程序即可不再使用兼容入口。
