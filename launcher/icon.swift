// 生成 App 图标 PNG：swift icon.swift 输出路径.png
import Cocoa

let size: CGFloat = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
let inset: CGFloat = 100
let rect = NSRect(x: inset, y: inset, width: size - 2 * inset, height: size - 2 * inset)
let path = NSBezierPath(roundedRect: rect, xRadius: 180, yRadius: 180)
NSGradient(starting: NSColor(calibratedRed: 0.13, green: 0.16, blue: 0.24, alpha: 1), ending: NSColor(calibratedRed: 0.05, green: 0.07, blue: 0.11, alpha: 1))!.draw(in: path, angle: -90)
let attributes: [NSAttributedString.Key: Any] = [
    .font: NSFont.monospacedSystemFont(ofSize: 330, weight: .bold),
    .foregroundColor: NSColor(calibratedRed: 0.35, green: 0.85, blue: 0.62, alpha: 1),
]
let text = NSAttributedString(string: ">_", attributes: attributes)
text.draw(at: NSPoint(x: rect.minX + 110, y: rect.midY - 120))
let plus = NSAttributedString(string: "+", attributes: [.font: NSFont.systemFont(ofSize: 260, weight: .heavy), .foregroundColor: NSColor(calibratedRed: 0.40, green: 0.65, blue: 1, alpha: 1)])
plus.draw(at: NSPoint(x: rect.maxX - 250, y: rect.maxY - 330))
image.unlockFocus()
let rep = NSBitmapImageRep(data: image.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
