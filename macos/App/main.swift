// NOTORYX Ops for Mac: a native window around the station's dashboard.
//
// On launch it sets up a Python environment from the bundled wheel (first run or a new
// version), starts the station in the background, and shows the dashboard in its own
// window. Quitting the app stops the station it started.

import AppKit
import WebKit

let appName = "NOTORYX Ops"
let supportDir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    .appendingPathComponent(appName, isDirectory: true)
let venvDir = supportDir.appendingPathComponent("venv", isDirectory: true)
let logURL = supportDir.appendingPathComponent("station.log")
let configURL = supportDir.appendingPathComponent("config.toml")

func findPython() -> String? {
    let candidates = [
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
        "/usr/bin/python3",
    ]
    for c in candidates where FileManager.default.isExecutableFile(atPath: c) {
        if run(c, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"]).status == 0 {
            return c
        }
    }
    return nil
}

@discardableResult
func run(_ exe: String, _ args: [String], log: FileHandle? = nil) -> (status: Int32, out: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: exe)
    p.arguments = args
    let pipe = Pipe()
    p.standardOutput = log ?? pipe
    p.standardError = log ?? pipe
    do { try p.run() } catch { return (-1, "\(error)") }
    p.waitUntilExit()
    let data = log == nil ? pipe.fileHandleForReading.readDataToEndOfFile() : Data()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

func openLog() -> FileHandle? {
    try? FileManager.default.createDirectory(at: supportDir, withIntermediateDirectories: true)
    if !FileManager.default.fileExists(atPath: logURL.path) {
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
    }
    let h = try? FileHandle(forWritingTo: logURL)
    h?.seekToEndOfFile()
    return h
}

/// Puts text from the dashboard's Copy buttons on the Mac clipboard.
final class CopyHandler: NSObject, WKScriptMessageHandler {
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let text = message.body as? String, !text.isEmpty, text.count <= 20_000 else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var status: NSTextField!
    var spinner: NSProgressIndicator!
    var station: Process?
    var port = 8484
    var quitting = false

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        buildWindow()
        DispatchQueue.global(qos: .userInitiated).async { self.startStation() }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { window.makeKeyAndOrderFront(nil) }
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ note: Notification) {
        quitting = true
        guard let p = station, p.isRunning else { return }
        p.interrupt()
        let deadline = Date().addingTimeInterval(6)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
        if p.isRunning { p.terminate() }
    }

    // MARK: window

    func buildWindow() {
        let frame = NSRect(x: 0, y: 0, width: 1280, height: 820)
        window = NSWindow(contentRect: frame, styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = appName
        window.minSize = NSSize(width: 720, height: 480)
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        window.setFrameAutosaveName("MainWindow")

        let cfg = WKWebViewConfiguration()
        cfg.applicationNameForUserAgent = "NOTORYXOpsApp/1"
        // The web view has no clipboard access of its own; pages copy through this handler.
        cfg.userContentController.add(CopyHandler(), name: "notoryxCopy")
        web = WKWebView(frame: frame, configuration: cfg)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.autoresizingMask = [.width, .height]
        web.isHidden = true

        let content = NSView(frame: frame)
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor(red: 0.055, green: 0.047, blue: 0.114, alpha: 1).cgColor
        content.addSubview(web)

        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.translatesAutoresizingMaskIntoConstraints = false
        spinner.startAnimation(nil)
        status = NSTextField(labelWithString: "Starting the station…")
        status.textColor = .secondaryLabelColor
        status.alignment = .center
        status.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(spinner)
        content.addSubview(status)
        NSLayoutConstraint.activate([
            spinner.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            spinner.centerYAnchor.constraint(equalTo: content.centerYAnchor, constant: -16),
            status.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            status.topAnchor.constraint(equalTo: spinner.bottomAnchor, constant: 12),
            status.widthAnchor.constraint(lessThanOrEqualTo: content.widthAnchor, constant: -40),
        ])
        window.contentView = content
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func setStatus(_ text: String, spinning: Bool = true) {
        DispatchQueue.main.async {
            self.status.stringValue = text
            self.status.isHidden = false
            self.spinner.isHidden = !spinning
            self.web.isHidden = true
        }
    }

    // MARK: station

    func startStation() {
        let log = openLog()
        guard let wheel = Bundle.main.urls(forResourcesWithExtension: "whl", subdirectory: nil)?.first else {
            return fail("The app is missing its station package. Download it again.")
        }
        guard let python = findPython() else {
            return fail("NOTORYX Ops needs Python 3.10 or newer. Install it from python.org, then open NOTORYX Ops again.",
                        link: "https://www.python.org/downloads/macos/")
        }
        let stamp = venvDir.appendingPathComponent(".installed-\(wheel.lastPathComponent)")
        if !FileManager.default.fileExists(atPath: stamp.path) {
            let pip = venvDir.appendingPathComponent("bin/pip").path
            let pipArgs = ["install", "--quiet", "--disable-pip-version-check", "--upgrade", wheel.path]
            // An update upgrades the existing environment in place, so add-ons installed in it
            // (such as Meshtastic) are kept. A fresh or broken environment is built from scratch.
            var ready = false
            if FileManager.default.isExecutableFile(atPath: pip) {
                setStatus("Updating NOTORYX Ops…")
                ready = run(pip, pipArgs, log: log).status == 0
            }
            if !ready {
                setStatus("Setting up NOTORYX Ops. The first start takes about a minute…")
                try? FileManager.default.removeItem(at: venvDir)
                guard run(python, ["-m", "venv", venvDir.path], log: log).status == 0,
                      run(pip, pipArgs, log: log).status == 0
                else {
                    return fail("Setting up the station failed. Details are in the log.", showLog: true)
                }
            }
            // Forget older version stamps, then mark this one as installed.
            for name in (try? FileManager.default.contentsOfDirectory(atPath: venvDir.path)) ?? []
            where name.hasPrefix(".installed-") {
                try? FileManager.default.removeItem(at: venvDir.appendingPathComponent(name))
            }
            FileManager.default.createFile(atPath: stamp.path, contents: nil)
            // A new version: drop cached pages so the dashboard can't show the old screens.
            // Saved looks and other local settings are kept.
            DispatchQueue.main.async {
                WKWebsiteDataStore.default().removeData(
                    ofTypes: [WKWebsiteDataTypeDiskCache, WKWebsiteDataTypeMemoryCache],
                    modifiedSince: .distantPast) {}
            }
        }

        let py = venvDir.appendingPathComponent("bin/python").path
        let portOut = run(py, ["-c", "from notoryx_ops import config; print(config.load().port)"])
        port = Int(portOut.out.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 8484

        if !stationAnswers() {
            setStatus("Starting the station…")
            let p = Process()
            p.executableURL = venvDir.appendingPathComponent("bin/notoryx-ops")
            p.arguments = ["--config", configURL.path, "run"]
            p.standardOutput = log
            p.standardError = log
            p.terminationHandler = { [weak self] proc in
                guard let self = self, !self.quitting else { return }
                if proc.terminationStatus == 0 {
                    self.stopped()  // Stop station on the Station page, or a clean exit
                } else {
                    self.fail("The station stopped unexpectedly (code \(proc.terminationStatus)). Details are in the log.",
                              showLog: true, restart: true)
                }
            }
            do { try p.run() } catch { return fail("Couldn't start the station: \(error.localizedDescription)") }
            station = p
            for _ in 0..<120 {
                if stationAnswers() { break }
                if !p.isRunning { return }
                Thread.sleep(forTimeInterval: 0.5)
            }
        }
        guard stationAnswers() else { return fail("The station didn't answer. Details are in the log.", showLog: true, restart: true) }
        DispatchQueue.main.async { self.loadDashboard() }
    }

    func stationAnswers() -> Bool {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/station") else { return false }
        var req = URLRequest(url: url)
        req.timeoutInterval = 1.5
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            ok = (resp as? HTTPURLResponse)?.statusCode == 200
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 2)
        return ok
    }

    func loadDashboard() {
        guard let url = URL(string: "http://127.0.0.1:\(port)/") else { return }
        web.load(URLRequest(url: url))
    }

    func fail(_ message: String, link: String? = nil, showLog: Bool = false, restart: Bool = false) {
        DispatchQueue.main.async {
            self.setStatus(message, spinning: false)
            let a = NSAlert()
            a.messageText = appName
            a.informativeText = message
            a.addButton(withTitle: restart ? "Restart" : "OK")
            if showLog { a.addButton(withTitle: "Show Log") }
            if link != nil { a.addButton(withTitle: "Get Python") }
            let r = a.runModal()
            if r == .alertSecondButtonReturn {
                if showLog { NSWorkspace.shared.activateFileViewerSelecting([logURL]) }
                if let l = link, let u = URL(string: l) { NSWorkspace.shared.open(u) }
            } else if r == .alertFirstButtonReturn && restart {
                self.restartStation(nil)
            }
        }
    }

    func stopped() {
        DispatchQueue.main.async {
            self.setStatus("The station is stopped.", spinning: false)
            let a = NSAlert()
            a.messageText = "The station is stopped"
            a.informativeText = "Packets and fleet polls are paused until you start it again."
            a.addButton(withTitle: "Start Station")
            a.addButton(withTitle: "Quit \(appName)")
            a.addButton(withTitle: "Leave Stopped")
            switch a.runModal() {
            case .alertFirstButtonReturn: self.restartStation(nil)
            case .alertSecondButtonReturn: NSApp.terminate(nil)
            default: break
            }
        }
    }

    @objc func restartStation(_ sender: Any?) {
        quitting = true
        if let p = station, p.isRunning {
            p.interrupt()
            p.waitUntilExit()
        }
        station = nil
        quitting = false
        setStatus("Restarting the station…")
        DispatchQueue.global(qos: .userInitiated).async { self.startStation() }
    }

    // MARK: web view

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        web.isHidden = false
        status.isHidden = true
        spinner.isHidden = true
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let host = url.host, host != "127.0.0.1" && host != "localhost",
           action.navigationType == .linkActivated {
            NSWorkspace.shared.open(url)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = appName
        a.informativeText = message
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = appName
        a.informativeText = message
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert()
        a.messageText = appName
        a.informativeText = prompt
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Cancel")
        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        a.accessoryView = field
        a.window.initialFirstResponder = field
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    // MARK: menu actions

    @objc func reload(_ sender: Any?) { if web.url == nil { loadDashboard() } else { web.reload() } }
    @objc func openInBrowser(_ sender: Any?) {
        if let u = URL(string: "http://127.0.0.1:\(port)/") { NSWorkspace.shared.open(u) }
    }
    @objc func showSettings(_ sender: Any?) { NSWorkspace.shared.activateFileViewerSelecting([configURL]) }
    @objc func editSettings(_ sender: Any?) {
        window.makeKeyAndOrderFront(nil)
        if let u = URL(string: "http://127.0.0.1:\(port)/settings") { web.load(URLRequest(url: u)) }
    }
    @objc func showLog(_ sender: Any?) { NSWorkspace.shared.activateFileViewerSelecting([logURL]) }
    @objc func showWindow(_ sender: Any?) { window.makeKeyAndOrderFront(nil) }
    @objc func zoomIn(_ sender: Any?) { web.pageZoom = min(web.pageZoom + 0.1, 2) }
    @objc func zoomOut(_ sender: Any?) { web.pageZoom = max(web.pageZoom - 0.1, 0.6) }
    @objc func zoomReset(_ sender: Any?) { web.pageZoom = 1 }
    @objc func go(_ sender: NSMenuItem) {
        if let path = sender.representedObject as? String, let u = URL(string: "http://127.0.0.1:\(port)\(path)") {
            web.load(URLRequest(url: u))
        }
    }

    func buildMenu() {
        let main = NSMenu()
        func item(_ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = [.command],
                  target: AnyObject? = nil) -> NSMenuItem {
            let i = NSMenuItem(title: title, action: action, keyEquivalent: key)
            i.keyEquivalentModifierMask = mods
            i.target = target
            return i
        }
        func sub(_ title: String, _ items: [NSMenuItem]) {
            let top = NSMenuItem()
            let m = NSMenu(title: title)
            items.forEach { m.addItem($0) }
            top.submenu = m
            main.addItem(top)
        }
        sub(appName, [
            item("About \(appName)", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), "", [], target: NSApp),
            .separator(),
            item("Settings…", #selector(editSettings(_:)), ",", target: self),
            item("Show Settings File in Finder", #selector(showSettings(_:)), target: self),
            item("Show Station Log", #selector(showLog(_:)), target: self),
            item("Start or Restart Station", #selector(restartStation(_:)), target: self),
            .separator(),
            item("Hide \(appName)", #selector(NSApplication.hide(_:)), "h", target: NSApp),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option], target: NSApp),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:)), target: NSApp),
            .separator(),
            item("Quit \(appName)", #selector(NSApplication.terminate(_:)), "q", target: NSApp),
        ])
        sub("Edit", [
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        let pages: [(String, String, String)] = [
            ("Live Map", "/", "1"), ("Packets", "/packets", "2"), ("Channels", "/channels", "3"),
            ("My Fleet", "/fleet", "4"), ("Observers", "/observers", "5"), ("Coverage", "/coverage", "6"),
            ("Analytics", "/analytics", "7"), ("Alerts", "/alerts", "8"), ("Station", "/station", "9"),
        ]
        sub("Go", pages.map { p in
            let i = item(p.0, #selector(go(_:)), p.2, target: self)
            i.representedObject = p.1
            return i
        })
        sub("View", [
            item("Reload", #selector(reload(_:)), "r", target: self),
            .separator(),
            item("Actual Size", #selector(zoomReset(_:)), "0", target: self),
            item("Zoom In", #selector(zoomIn(_:)), "+", target: self),
            item("Zoom Out", #selector(zoomOut(_:)), "-", target: self),
            .separator(),
            item("Open in Browser", #selector(openInBrowser(_:)), "o", [.command, .shift], target: self),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        let windowMenu: [NSMenuItem] = [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Close", #selector(NSWindow.performClose(_:)), "w"),
            .separator(),
            item("Dashboard", #selector(showWindow(_:)), "0", [.command, .option], target: self),
        ]
        sub("Window", windowMenu)
        NSApp.mainMenu = main
        NSApp.windowsMenu = main.items.last?.submenu
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
