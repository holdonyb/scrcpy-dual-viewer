"""Synthetic headless scrcpy stand-in; handles CTRL_BREAK and closes FFmpeg."""
import ctypes
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

output = Path(sys.argv[sys.argv.index("--record") + 1])
output.parent.mkdir(parents=True, exist_ok=True)
event = threading.Event()
callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint32)
callback = callback_type(lambda signal: (event.set() or True) if signal == 1 else False)
kernel = ctypes.WinDLL("kernel32")
kernel.SetConsoleCtrlHandler.argtypes = [callback_type, ctypes.c_bool]
if not kernel.SetConsoleCtrlHandler(callback, True):
    sys.exit(3)
kernel.GetConsoleWindow.restype = ctypes.c_void_p
user = ctypes.WinDLL("user32")
user.IsWindowVisible.argtypes = [ctypes.c_void_p]
output.with_suffix(".console.txt").write_text(str(bool(user.IsWindowVisible(kernel.GetConsoleWindow()))))
command = [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-re", "-f", "lavfi",
           "-i", "testsrc2=size=320x240:rate=15"]
if "--no-audio" not in sys.argv:
    command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-c:a", "aac"]
command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(output)]
child = subprocess.Popen(command, stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
deadline = time.monotonic() + float(sys.argv[sys.argv.index("--exit-after")+1]) if "--exit-after" in sys.argv else None
unexpected = False
while not event.wait(0.03):
    if deadline and time.monotonic() >= deadline:
        unexpected = True
        break
    if child.poll() is not None:
        sys.exit(4)
child.stdin.write(b"q\n")
child.stdin.flush()
child.wait(timeout=8)
output.with_suffix(".closed.txt").write_text("CTRL_BREAK")
sys.exit(7 if unexpected else child.returncode)
