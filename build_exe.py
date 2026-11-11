"""Build 专注时间助手.exe (PyInstaller, one-folder, no console) into 程序/ and add a shortcut.

Run from the project folder, with the program closed (quit from the tray first):
    .venv\\Scripts\\python.exe build_exe.py

One-folder instead of one-file: a one-file EXE unpacks itself to a temp folder and runs
as two processes on every start, which is slower and uses more memory.
Build caches stay in 90_过程文件/2026-11-10_打包/. data/ is never copied into the build.
"""
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
NAME = "专注时间助手"
WORK = ROOT/"90_过程文件"/"2026-11-10_打包"
OUTPUT = ROOT/"程序"
VERSION = (3, 1, 0, 0)


def make_icon(path):
    """Multi-size .ico from the app's own vector logo (PNG-compressed entries)."""
    sys.path.insert(0, str(ROOT/"app"))
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
    app = QGuiApplication.instance() or QGuiApplication([])
    from theme import ACCENT
    images = []
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        image = QImage(size, size, QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.scale(size/24, size/24)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(ACCENT)); p.drawRoundedRect(QRectF(1, 1, 22, 22), 6, 6)
        p.setPen(QPen(QColor("white"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush); p.drawEllipse(QRectF(5.5, 5.5, 13, 13)); p.drawLine(12, 8.5, 12, 12); p.drawLine(12, 12, 14.8, 13.6)
        p.end()
        data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer, "PNG")
        images.append((size, bytes(data)))
    header = struct.pack("<HHH", 0, 1, len(images)); offset = 6+16*len(images); entries = b""; blobs = b""
    for size, blob in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset+len(blobs)); blobs += blob
    path.write_bytes(header+entries+blobs)


def make_version(path):
    numbers = ", ".join(map(str, VERSION)); dotted = ".".join(map(str, VERSION))
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numbers}), prodvers=({numbers}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404b0', [
      StringStruct('FileDescription', '{NAME}'), StringStruct('ProductName', '{NAME}'),
      StringStruct('FileVersion', '{dotted}'), StringStruct('ProductVersion', '{dotted}'),
      StringStruct('OriginalFilename', '{NAME}.exe'), StringStruct('LegalCopyright', 'Local use')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])])
""", encoding="utf-8")


def shortcut(target, link):
    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
              f"$s.TargetPath='{target}';$s.WorkingDirectory='{target.parent}';"
              f"$s.IconLocation='{target},0';$s.Description='{NAME}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    icon, version = WORK/"icon.ico", WORK/"version.txt"
    make_icon(icon); make_version(version)
    dist = WORK/"dist"
    command = [sys.executable, "-m", "PyInstaller", str(ROOT/"app"/"main.py"), "--name", NAME, "--windowed", "--noconfirm", "--clean",
               "--icon", str(icon), "--version-file", str(version), "--paths", str(ROOT/"app"),
               "--add-data", f"{ROOT/'app'/'assets'};assets", "--contents-directory", "运行库",
               "--distpath", str(dist), "--workpath", str(WORK/"build"), "--specpath", str(WORK),
               "--exclude-module", "tkinter", "--exclude-module", "unittest", "--exclude-module", "pydoc",
               "--exclude-module", "ssl", "--exclude-module", "PySide6.QtNetwork"]
    subprocess.run(command, check=True)
    built = dist/NAME
    # Qt files this program never loads: software OpenGL and Qt networking (the browser
    # bridge is Python's own loopback HTTP server). Removing them only saves disk space.
    runtime = built/"运行库"/"PySide6"
    for unused in ["opengl32sw.dll", "Qt6Network.dll", "libcrypto-3-x64.dll", "libssl-3-x64.dll",
                   "plugins/tls", "plugins/networkinformation", "plugins/generic"]:
        target = runtime/unused
        if target.is_dir(): shutil.rmtree(target)
        elif target.exists(): target.unlink()
    print(f"built {built}")
    install()


def install():
    """Replace only 程序/ with the fresh build; data/, settings and the extension stay where they are."""
    built = WORK/"dist"/NAME
    if not (built/f"{NAME}.exe").exists():
        sys.exit("no build found; run build_exe.py first")
    if OUTPUT.exists():
        # Renaming fails while the program (or a shell) still uses the folder, so nothing gets half-deleted.
        parked = OUTPUT.with_name(OUTPUT.name+"_旧")
        try:
            if parked.exists(): shutil.rmtree(parked)
            OUTPUT.rename(parked)
        except OSError:
            print(f"程序正在运行，新版本已构建在 {built}。请从托盘退出后运行：\n    .venv\\Scripts\\python.exe build_exe.py --install")
            sys.exit(2)
        shutil.rmtree(parked, ignore_errors=True)
    shutil.copytree(built, OUTPUT)
    shortcut(OUTPUT/f"{NAME}.exe", ROOT/f"{NAME}.lnk")
    size = sum(f.stat().st_size for f in OUTPUT.rglob("*") if f.is_file())/2**20
    print(f"installed {OUTPUT/(NAME+'.exe')}  ({size:.0f} MB)")


if __name__ == "__main__":
    install() if "--install" in sys.argv else main()
