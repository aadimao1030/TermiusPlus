// TermiusPlus.app：启动本机服务（launcher/serve.py），在原生窗口中打开界面；关闭窗口或 Cmd+Q 时停止服务。
import Cocoa
import WebKit

let defaultPort = 8765
let terminalStates: Set<String> = ["completed", "failed", "cancelled", "interrupted"]
let supportDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/TermiusPlus")
let storageURL = supportDir.appendingPathComponent("storage.json")
let logURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/TermiusPlus/server.log")

struct LaunchError: Error { let message: String }

// MARK: - 小工具

func run(_ path: String, _ args: [String]) -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: path)
    p.arguments = args
    let out = Pipe()
    p.standardOutput = out
    p.standardError = FileHandle.nullDevice
    do { try p.run() } catch { return "" }
    let data = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return String(decoding: data, as: UTF8.self)
}

func portListening(_ port: Int) -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return false }
    defer { close(fd) }
    var addr = sockaddr_in()
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = in_port_t(UInt16(port).bigEndian)
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    return withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0 }
    }
}

func alive(_ pid: pid_t) -> Bool { pid > 0 && kill(pid, 0) == 0 }

func randomToken() -> String {
    var bytes = [UInt8](repeating: 0, count: 32)
    _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
    return Data(bytes).base64EncodedString()
        .replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
}

/// 调用服务的 /api/status，返回仍在本机运行的传输数量；无法访问时返回 nil。
func activeLocalTransfers(port: Int, token: String) -> Int? {
    var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/status")!, timeoutInterval: 3)
    request.httpMethod = "POST"
    request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("http://127.0.0.1:\(port)", forHTTPHeaderField: "Origin")
    request.httpBody = Data("{}".utf8)
    let done = DispatchSemaphore(value: 0)
    var result: Int?
    URLSession.shared.dataTask(with: request) { data, response, _ in
        defer { done.signal() }
        guard (response as? HTTPURLResponse)?.statusCode == 200, let data = data,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let jobs = object["jobs"] as? [[String: Any]] else { return }
        // 远程独立会话（direction=remote）在服务器 tmux 中运行，不受本机退出影响。
        result = jobs.filter { !terminalStates.contains($0["state"] as? String ?? "") && ($0["direction"] as? String) != "remote" }.count
    }.resume()
    _ = done.wait(timeout: .now() + 4)
    return result
}

// MARK: - 服务进程

final class Server {
    let project: String
    var pid: pid_t = 0
    var port = defaultPort
    var token = ""
    var isChild = false
    var pipeWrite: Int32 = -1

    init(project: String) { self.project = project }

    /// 返回占用端口的 TermiusPlus 服务 PID（本项目目录下的 app.py / serve.py），否则 nil。
    func existingServer(on port: Int) -> pid_t? {
        let pids = run("/usr/sbin/lsof", ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN", "-t"]).split(separator: "\n").compactMap { pid_t($0) }
        for pid in pids {
            let command = run("/bin/ps", ["-o", "command=", "-p", "\(pid)"])
            guard command.contains("app.py") || command.contains("serve.py") else { continue }
            let cwd = run("/usr/sbin/lsof", ["-a", "-p", "\(pid)", "-d", "cwd", "-Fn"]).split(separator: "\n").first { $0.hasPrefix("n") }.map { String($0.dropFirst()) }
            if cwd == project { return pid }
        }
        return nil
    }

    func tokenOf(_ pid: pid_t) -> String? {
        for part in run("/bin/ps", ["-E", "-ww", "-o", "command=", "-p", "\(pid)"]).split(separator: " ") where part.hasPrefix("TERMIUSPLUS_TOKEN=") {
            return String(part.dropFirst("TERMIUSPLUS_TOKEN=".count))
        }
        return nil
    }

    /// 旧服务无法访问时，读取传输记录判断是否仍有本机传输。
    func recordedActiveTransfers() -> Int {
        let url = URL(fileURLWithPath: project).appendingPathComponent(".termiusplus-state/transfers.json")
        guard let data = try? Data(contentsOf: url), let list = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return 0 }
        return list.compactMap { $0["snapshot"] as? [String: Any] }
            .filter { !terminalStates.contains($0["state"] as? String ?? "") && ($0["direction"] as? String) != "remote" }.count
    }

    func stopPid(_ target: pid_t, timeout: TimeInterval = 10) {
        guard alive(target) else { return }
        kill(target, SIGTERM)
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if isChild && target == pid { var status: Int32 = 0; if waitpid(target, &status, WNOHANG) == target { return } }
            if !alive(target) { return }
            usleep(100_000)
        }
        killpg(target, SIGKILL)
        kill(target, SIGKILL)
        if isChild && target == pid { var status: Int32 = 0; waitpid(target, &status, 0) }
    }

    /// 准备服务：必要时接管或停止旧服务，然后启动新服务并等待端口就绪。
    /// askForceStop 在主线程询问是否强制停止无法接管的旧服务。
    func prepare(askForceStop: (pid_t, Int) -> Bool) throws {
        guard FileManager.default.fileExists(atPath: project + "/app.py") else {
            throw LaunchError(message: "找不到 \(project)/app.py。项目移动后请重新运行 launcher/build.sh。")
        }
        port = ProcessInfo.processInfo.environment["TERMIUSPLUS_PORT"].flatMap { Int($0) } ?? defaultPort
        if portListening(port) {
            if let old = existingServer(on: port) {
                if let oldToken = tokenOf(old) {
                    // 已有本项目服务（例如在终端或编辑器里手动启动）：直接使用，不打断；退出 App 时也不停止它。
                    pid = old; token = oldToken; isChild = false
                    return
                }
                let active = recordedActiveTransfers()
                if active > 0 && !askForceStop(old, active) { throw LaunchError(message: "") }
                stopPid(old)
                for _ in 0..<50 where portListening(port) { usleep(100_000) }
            }
            // 端口被其他程序占用时换用空闲端口；连接配置由 App 单独保存，不受端口影响。
            while portListening(port) && port < defaultPort + 50 { port += 1 }
        }
        token = randomToken()
        try spawn()
        let deadline = Date().addingTimeInterval(30)
        while Date() < deadline {
            var status: Int32 = 0
            if waitpid(pid, &status, WNOHANG) == pid {
                pid = 0
                let log = (try? String(contentsOf: logURL, encoding: .utf8)) ?? ""
                throw LaunchError(message: "服务启动失败：\n" + String(log.suffix(1500)))
            }
            if portListening(port) { return }
            usleep(100_000)
        }
        stop()
        throw LaunchError(message: "服务 30 秒内未就绪，日志见 \(logURL.path)")
    }

    func spawn() throws {
        try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        var fds: [Int32] = [0, 0]
        guard pipe(&fds) == 0 else { throw LaunchError(message: "无法创建管道") }
        pipeWrite = fds[1]
        _ = fcntl(pipeWrite, F_SETFD, FD_CLOEXEC)
        let logFd = open(logURL.path, O_WRONLY | O_CREAT | O_TRUNC, 0o600)

        var env = ProcessInfo.processInfo.environment
        env["TERMIUSPLUS_TOKEN"] = token
        env["PYTHONUNBUFFERED"] = "1"
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        if env["LANG"] == nil { env["LANG"] = "en_US.UTF-8" }
        if env["SHELL"] == nil, let pw = getpwuid(getuid()), let shell = pw.pointee.pw_shell { env["SHELL"] = String(cString: shell) }

        let args = ["/usr/bin/python3", "-u", project + "/launcher/serve.py", "--port", "\(port)"]
        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        posix_spawn_file_actions_adddup2(&actions, fds[0], 0)
        posix_spawn_file_actions_adddup2(&actions, logFd, 1)
        posix_spawn_file_actions_adddup2(&actions, logFd, 2)
        posix_spawn_file_actions_addchdir_np(&actions, project)
        var attr: posix_spawnattr_t?
        posix_spawnattr_init(&attr)
        // 后台线程会屏蔽信号，必须为子进程清空信号屏蔽并恢复默认处理，否则 SIGTERM 无法正常停止服务。
        posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF))
        posix_spawnattr_setpgroup(&attr, 0)
        var noSignals = sigset_t(), allSignals = sigset_t()
        sigemptyset(&noSignals); sigfillset(&allSignals)
        posix_spawnattr_setsigmask(&attr, &noSignals)
        posix_spawnattr_setsigdefault(&attr, &allSignals)

        let cArgs = args.map { strdup($0) } + [nil]
        let cEnv = env.map { strdup("\($0.key)=\($0.value)") } + [nil]
        var child: pid_t = 0
        let rc = posix_spawn(&child, args[0], &actions, &attr, cArgs, cEnv)
        cArgs.forEach { free($0) }; cEnv.forEach { free($0) }
        posix_spawn_file_actions_destroy(&actions); posix_spawnattr_destroy(&attr)
        close(fds[0]); close(logFd)
        guard rc == 0 else { throw LaunchError(message: "无法启动 python3：\(String(cString: strerror(rc)))") }
        pid = child
        isChild = true
    }

    /// 只停止 App 自己启动的服务。
    func stop() {
        if pid > 0 && isChild { stopPid(pid) }
        pid = 0
        if pipeWrite >= 0 { close(pipeWrite); pipeWrite = -1 }
    }
}

// MARK: - 连接配置存储（不随端口变化，页面 localStorage 中 termiusplus.* 的副本）

func loadStorage() -> [String: String] {
    guard let data = try? Data(contentsOf: storageURL), let object = try? JSONSerialization.jsonObject(with: data) as? [String: String] else { return [:] }
    return object
}

func saveStorage(_ values: [String: String]) {
    try? FileManager.default.createDirectory(at: supportDir, withIntermediateDirectories: true)
    if let data = try? JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys]) {
        try? data.write(to: storageURL, options: .atomic)
    }
}

func storageScript(_ values: [String: String]) -> WKUserScript {
    let seed = String(decoding: (try? JSONSerialization.data(withJSONObject: values)) ?? Data("{}".utf8), as: UTF8.self)
    let source = """
    (function(){
      var seed = \(seed), prefix = 'termiusplus.';
      try { if (window.top === window) for (var k in seed) localStorage.setItem(k, seed[k]); } catch (e) {}
      function sync() {
        try {
          var out = {};
          for (var i = 0; i < localStorage.length; i++) { var key = localStorage.key(i); if (key.indexOf(prefix) === 0) out[key] = localStorage.getItem(key); }
          window.webkit.messageHandlers.tpStorage.postMessage(JSON.stringify(out));
        } catch (e) {}
      }
      var P = Storage.prototype, set = P.setItem, remove = P.removeItem, clear = P.clear;
      P.setItem = function(k, v) { set.call(this, k, v); if (this === localStorage && String(k).indexOf(prefix) === 0) sync(); };
      P.removeItem = function(k) { remove.call(this, k); if (this === localStorage && String(k).indexOf(prefix) === 0) sync(); };
      P.clear = function() { clear.call(this); if (this === localStorage) sync(); };
    })();
    """
    return WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: false)
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Server!
    var ready = false
    var stopping = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let project = Bundle.main.object(forInfoDictionaryKey: "TermiusPlusProjectDir") as? String ?? ""
        server = Server(project: project)
        buildMenu()

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController.add(self, name: "tpStorage")
        config.userContentController.addUserScript(storageScript(loadStorage()))
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        webView.loadHTMLString("<html><body style='font:15px -apple-system;color:#888;display:flex;height:100vh;margin:0;align-items:center;justify-content:center'>正在启动 TermiusPlus…</body></html>", baseURL: nil)

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 860), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "TermiusPlus"
        window.contentView = webView
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("TermiusPlusMain")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        DispatchQueue.global().async {
            do {
                try self.server.prepare(askForceStop: { pid, count in
                    DispatchQueue.main.sync {
                        let alert = NSAlert()
                        alert.messageText = "已有 TermiusPlus 服务正在传输"
                        alert.informativeText = "进程 \(pid) 有 \(count) 个本机传输仍在运行，且无法取得它的访问凭证。强制停止会中断这些传输，之后可在传输记录中续传。"
                        alert.addButton(withTitle: "退出")
                        alert.addButton(withTitle: "停止旧服务并继续")
                        return alert.runModal() == .alertSecondButtonReturn
                    }
                })
                DispatchQueue.main.async {
                    self.ready = true
                    if !self.server.isChild { self.window.title = "TermiusPlus（使用已运行的服务 \(self.server.pid)）" }
                    self.webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.server.port)/#\(self.server.token)")!))
                }
            } catch let error as LaunchError {
                DispatchQueue.main.async {
                    if !error.message.isEmpty {
                        let alert = NSAlert()
                        alert.messageText = "TermiusPlus 无法启动"
                        alert.informativeText = error.message
                        alert.runModal()
                    }
                    self.stopping = true
                    NSApp.terminate(nil)
                }
            } catch {
                DispatchQueue.main.async { self.stopping = true; NSApp.terminate(nil) }
            }
        }
    }

    func buildMenu() {
        let main = NSMenu()
        func submenu(_ title: String, _ items: [NSMenuItem]) {
            let holder = NSMenuItem(); let menu = NSMenu(title: title)
            items.forEach { menu.addItem($0) }
            holder.submenu = menu; main.addItem(holder)
        }
        func item(_ title: String, _ action: Selector?, _ key: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: title, action: action, keyEquivalent: key); i.keyEquivalentModifierMask = mods; return i
        }
        submenu("TermiusPlus", [
            item("关于 TermiusPlus", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""),
            .separator(),
            item("隐藏 TermiusPlus", #selector(NSApplication.hide(_:)), "h"),
            item("隐藏其他", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            .separator(),
            item("退出 TermiusPlus", #selector(NSApplication.terminate(_:)), "q"),
        ])
        submenu("文件", [
            item("从浏览器导入连接配置…", #selector(importFromBrowser), ""),
            item("重新载入页面", #selector(reloadPage), "r", [.command, .shift]),
            .separator(),
            item("关闭窗口", #selector(NSWindow.performClose(_:)), "w"),
        ])
        submenu("编辑", [
            item("撤销", Selector(("undo:")), "z"),
            item("重做", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("剪切", #selector(NSText.cut(_:)), "x"),
            item("复制", #selector(NSText.copy(_:)), "c"),
            item("粘贴", #selector(NSText.paste(_:)), "v"),
            item("全选", #selector(NSText.selectAll(_:)), "a"),
        ])
        submenu("显示", [
            item("实际大小", #selector(zoomReset), "0"),
            item("放大", #selector(zoomIn), "="),
            item("缩小", #selector(zoomOut), "-"),
            .separator(),
            item("进入全屏幕", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        submenu("窗口", [
            item("最小化", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("缩放", #selector(NSWindow.performZoom(_:)), ""),
        ])
        NSApp.mainMenu = main
    }

    @objc func zoomReset() { webView.pageZoom = 1 }
    @objc func zoomIn() { webView.pageZoom = min(webView.pageZoom + 0.1, 3) }
    @objc func zoomOut() { webView.pageZoom = max(webView.pageZoom - 0.1, 0.5) }
    @objc func reloadPage() { if ready { webView.reload() } }

    @objc func importFromBrowser() {
        let snippet = "copy(JSON.stringify(Object.fromEntries(Object.entries(localStorage).filter(([k])=>k.startsWith('termiusplus.')))))"
        let alert = NSAlert()
        alert.messageText = "从浏览器导入连接配置"
        alert.informativeText = "1. 点击“复制命令”。\n2. 在原浏览器中打开旧的 TermiusPlus 页面（地址以 http://127.0.0.1:8765 开头），按 Cmd+Option+J 打开控制台，粘贴并回车（Chrome/Edge 首次粘贴可能要求先输入 allow pasting）。命令会把连接配置复制到剪贴板。\n3. 回到这里再次打开本菜单，点击“从剪贴板导入”。导入后页面会重新载入，已打开的终端会断开。"
        alert.addButton(withTitle: "从剪贴板导入")
        alert.addButton(withTitle: "复制命令")
        alert.addButton(withTitle: "取消")
        switch alert.runModal() {
        case .alertSecondButtonReturn:
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(snippet, forType: .string)
        case .alertFirstButtonReturn:
            let text = NSPasteboard.general.string(forType: .string) ?? ""
            guard let object = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] else {
                let fail = NSAlert(); fail.messageText = "剪贴板中不是导出的连接配置"; fail.runModal(); return
            }
            var values = loadStorage()
            for (key, value) in object where key.hasPrefix("termiusplus.") { if let s = value as? String { values[key] = s } }
            saveStorage(values)
            installStorageScript(values)
            reloadPage()
        default: break
        }
    }

    func installStorageScript(_ values: [String: String]) {
        let controller = webView.configuration.userContentController
        controller.removeAllUserScripts()
        controller.addUserScript(storageScript(values))
    }

    // 页面修改 termiusplus.* 时保存副本，并更新下次载入时注入的内容。
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let text = message.body as? String, let values = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: String] else { return }
        saveStorage(values)
        installStorageScript(values)
    }

    // 非本服务的链接（例如旧版远程会话页面）用默认浏览器打开。
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.allow) }
        let local = url.host == "127.0.0.1" && url.port == server.port
        if local || url.scheme == "about" || url.scheme == "data" || !ready { return decisionHandler(.allow) }
        if url.scheme == "file" { return decisionHandler(.cancel) }  // 拖到空白处的文件不跳转
        if action.targetFrame?.isMainFrame ?? true { NSWorkspace.shared.open(url); return decisionHandler(.cancel) }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.runModal(); completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = message
        alert.addButton(withTitle: "确定"); alert.addButton(withTitle: "取消")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert(); alert.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24)); field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "确定"); alert.addButton(withTitle: "取消")
        completionHandler(alert.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { reloadPage() }

    // 关闭窗口 = 退出 App（可在确认框中取消）。
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if stopping || server.pid == 0 || !server.isChild { return .terminateNow }
        // 注销、重启、关机时不弹确认框。
        let event = NSAppleEventManager.shared().currentAppleEvent
        let systemQuit = event?.attributeDescriptor(forKeyword: AEKeyword(0x77687920 /* 'why?' */)) != nil
        if !systemQuit, ready, let count = activeLocalTransfers(port: server.port, token: server.token), count > 0 {
            let alert = NSAlert()
            alert.messageText = "有 \(count) 个传输正在进行"
            alert.informativeText = "退出会中断本机传输，之后可在传输记录中点击续传。远程独立会话不受影响。"
            alert.addButton(withTitle: "取消")
            alert.addButton(withTitle: "退出")
            if alert.runModal() != .alertSecondButtonReturn { return .terminateCancel }
        }
        stopping = true
        window.orderOut(nil)
        DispatchQueue.global().async {
            self.server.stop()
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) { server?.stop() }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
