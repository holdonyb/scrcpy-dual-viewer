"""Fetch pinned official tools into an ignored, project-local build directory."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
VENDOR = BUILD / "vendor"


def fetch(url, path, digest=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size and (not digest or hashlib.sha256(path.read_bytes()).hexdigest() == digest):
        return path
    partial = path.with_suffix(path.suffix + ".download")
    for attempt in range(4):
        try:
            curl = shutil.which("curl.exe" if sys.platform == "win32" else "curl")
            if curl:
                subprocess.run([curl, "-sSL", "--fail", "--retry", "3", "--max-time", "600", "-o", str(partial), url], check=True)
            else:
                request = urllib.request.Request(url, headers={"User-Agent": "MultiScreenRecorder-build"})
                with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as output:
                    shutil.copyfileobj(response, output)
            if digest and hashlib.sha256(partial.read_bytes()).hexdigest() != digest:
                raise ValueError(f"SHA-256 mismatch: {path.name}")
            partial.replace(path)
            return path
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))


def unpack(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for name in source.namelist():
                if not (destination / name).resolve().is_relative_to(destination.resolve()):
                    raise ValueError("Archive path escapes build directory")
            source.extractall(destination)
    else:
        with tarfile.open(archive) as source:
            if hasattr(tarfile, "data_filter"):
                source.extractall(destination, filter="data")
            else:
                for member in source.getmembers():
                    if member.issym() or member.islnk() or not (destination / member.name).resolve().is_relative_to(destination.resolve()):
                        raise ValueError("Unsafe archive member; use current Python 3.11")
                source.extractall(destination)


def main():
    platform = "windows" if sys.platform == "win32" else "linux"
    lock = json.loads((ROOT / "packaging/tools-lock.json").read_text())
    VENDOR.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if platform == "windows" else ""
    for tool, entry in lock[platform].items():
        extension = ".zip" if platform == "windows" else (".tar.gz" if tool == "scrcpy" else ".tar.xz")
        archive = fetch(entry["url"], BUILD / "downloads" / f"{tool}-{platform}{extension}", entry["sha256"])
        extracted = BUILD / "extracted" / f"{tool}-{platform}"
        unpack(archive, extracted)
        executable = next(extracted.rglob(tool + suffix))
        target = VENDOR / tool
        target.mkdir(exist_ok=True)
        if tool == "scrcpy":
            shutil.copytree(executable.parent, target, dirs_exist_ok=True)
        else:
            for name in ("ffmpeg", "ffprobe"):
                shutil.copy2(executable.with_name(name + suffix), target / (name + suffix))
            for file in extracted.rglob("*"):
                if file.is_file() and file.name.lower().startswith(("license", "copying", "readme")):
                    shutil.copy2(file, target / file.name)
        print(f"Verified {tool}: {entry['sha256']}")
    font = fetch(lock["font"]["url"], BUILD / "downloads/NotoSansCJKsc-Regular.otf", lock["font"]["sha256"])
    (VENDOR / "fonts").mkdir(exist_ok=True)
    shutil.copy2(font, VENDOR / "fonts" / font.name)
    license_file = fetch("https://raw.githubusercontent.com/notofonts/noto-cjk/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/LICENSE",
                         BUILD / "downloads/Noto-LICENSE.txt")
    shutil.copy2(license_file, VENDOR / "fonts/OFL.txt")


if __name__ == "__main__":
    main()
