"""Build on the target OS. Windows emits a single EXE; Linux an extracted app."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_runtime import VERSION


def main():
    vendor = ROOT / "build/vendor"
    windows = sys.platform == "win32"
    name = f"MultiScreenRecorder-{VERSION}-windows-x64" if windows else "MultiScreenRecorder"
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--noupx", "--windowed",
            "--onefile" if windows else "--onedir", "--name", name]
    for source, target in ((vendor / "scrcpy", "scrcpy"), (vendor / "ffmpeg", "ffmpeg"),
                           (vendor / "fonts", "fonts"), (ROOT / "packaging/assets", "packaging/assets"),
                           (ROOT / "packaging/licenses", "licenses"),
                           (ROOT / "THIRD_PARTY_NOTICES.md", ".")):
        args += ["--add-data", str(source) + (";" if windows else ":") + target]
    if windows:
        args += ["--icon", str(ROOT / "packaging/assets/icon.ico")]
    args += ["--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
             "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick",
             "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build/pyinstaller"),
             "--specpath", str(ROOT / "build"), str(ROOT / "dual_scrcpy_qt.py")]
    env = os.environ.copy()
    if windows:
        # Avoid collecting unrelated ICU/UCRT DLLs from developer-tool PATHs.
        # Windows 10/11 provide ICU and UCRT themselves; Qt ships its VC runtime.
        system = Path(os.environ.get("SystemRoot", "C:/Windows"))
        env["PATH"] = os.pathsep.join(map(str, (Path(sys.executable).parent, Path(sys.base_prefix),
                                               Path(sys.base_prefix) / "DLLs", system / "System32", system)))
    subprocess.run(args, cwd=ROOT, check=True, env=env)
    output = ROOT / "dist/release"
    output.mkdir(parents=True, exist_ok=True)
    if windows:
        shutil.copy2(ROOT / "dist" / (name + ".exe"), output / (name + ".exe"))
        compiler = shutil.which("ISCC") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        subprocess.run([compiler, f"/DAppVersion={VERSION}", f"/DBinary={output / (name + '.exe')}",
                        f"/DOutputDir={output}", str(ROOT / "packaging/windows.iss")], check=True)
    else:
        app = ROOT / "dist" / name
        shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", app)
        (app / "START-HERE.txt").write_text(
            "多屏录制 " + VERSION + "\n\nUbuntu 22.04+ / x86_64 / X11 桌面。\n运行 ./MultiScreenRecorder\n"
            "已包含 Python、Qt、scrcpy、adb、FFmpeg 和中文字体。\n"
            "设备需开启 USB 调试并授权；Linux USB 权限设置见项目 README。\n"
            "电脑麦克风目前仅支持 Windows，Linux 可录设备播放声音。\n", encoding="utf-8")
        with tarfile.open(output / f"MultiScreenRecorder-{VERSION}-linux-x64.tar.gz", "w:gz") as archive:
            archive.add(app, arcname=name)
    assets = sorted(p for p in output.iterdir() if p.suffix == ".exe" or p.name.endswith(".tar.gz"))
    (output / ("SHA256SUMS-windows.txt" if windows else "SHA256SUMS-linux.txt")).write_text(
        "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n" for p in assets), encoding="utf-8")


if __name__ == "__main__":
    main()
