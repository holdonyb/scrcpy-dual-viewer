"""Independent headless capture; all blocking work runs outside the Qt thread."""
import ctypes
from collections import deque
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Signal
from recording_export import export_recording, media_info
from app_runtime import external_environment


def hidden_kwargs():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def find_media_tools(config, roots=()):
    ffmpeg = config.get("ffmpeg_path")
    if not ffmpeg and not getattr(sys, "frozen", False):
        ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        for root in roots:
            ffmpeg = next((str(p) for p in (root / "ffmpeg.exe", root / "ffmpeg/bin/ffmpeg.exe",
                                          root / "ffmpeg/ffmpeg.exe", root / "ffmpeg/bin/ffmpeg",
                                          root / "ffmpeg/ffmpeg", root / "ffmpeg") if p.is_file()), None)
            if ffmpeg:
                break
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    ffprobe = None
    if ffmpeg:
        for name in ("ffprobe.exe", "ffprobe"):
            candidate = Path(ffmpeg).resolve().with_name(name)
            if candidate.is_file():
                ffprobe = str(candidate)
                break
    return ffmpeg, ffprobe or shutil.which("ffprobe")


def parse_microphones(stderr):
    """Keep DirectShow alternative IDs, including devices with identical labels."""
    devices = []
    current = None
    for line in stderr.splitlines():
        source = re.search(r'"(.*)" \(([^)]*)\)\s*$', line)
        if source:
            current = None
            if "audio" in source[2].split(", "):
                current = {"name": source[1], "id": source[1]}
                devices.append(current)
        else:
            alternative = re.search(r'Alternative name "([^"\r\n]+)"', line)
            if current is not None and alternative:
                current["id"] = alternative[1]
    return devices


def resolve_microphone(selected, devices):
    matches = [d for d in devices if d["id"] == selected]
    if not matches and not selected.startswith("@"):
        matches = [d for d in devices if d["name"] == selected]
    if len(matches) != 1:
        available = "；".join(d["name"] for d in devices) or "未检测到可用麦克风"
        raise ValueError("找不到所选麦克风或名称重复，请重新查找并选择麦克风。\n当前检测到：" + available)
    return matches[0]["id"]


def list_microphones(ffmpeg):
    if sys.platform != "win32":
        return []
    result = subprocess.run([ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
                            env=external_environment(), **hidden_kwargs())
    return parse_microphones(result.stderr)


def signal_private_console(pid):
    """Run ONLY in an ephemeral helper, never detach the GUI's own console."""
    if sys.platform != "win32":
        return 1
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole()
    kernel.AttachConsole.argtypes = [ctypes.c_uint32]
    if not kernel.AttachConsole(int(pid)):
        return 2
    handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint32)
    handler = handler_type(lambda event: True)
    kernel.SetConsoleCtrlHandler.argtypes = [handler_type, ctypes.c_bool]
    kernel.SetConsoleCtrlHandler(handler, True)
    result = kernel.GenerateConsoleCtrlEvent(1, 0)  # CTRL_BREAK, private console only
    time.sleep(0.15)
    kernel.FreeConsole()
    return 0 if result else 3


def request_capture_stop(process, main_script):
    if sys.platform == "win32":
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command += [str(main_script)]
        subprocess.run(command + ["--signal-recording", str(process.pid)],
                       timeout=15, **hidden_kwargs())
    else:
        process.send_signal(signal.SIGINT)


def audio_options(sdk, enabled):
    if not enabled or sdk < 30:
        return ["--no-audio"]
    if sdk >= 33:
        return ["--audio-source=playback", "--audio-dup", "--audio-codec=aac"]
    return ["--audio-source=output", "--audio-codec=aac"]


class Recorder(QObject):
    changed = Signal(str)
    elapsed = Signal(float)
    log = Signal(str)
    progress = Signal(int, str)
    saved = Signal(object)
    failed = Signal(str)
    warning = Signal(str)
    idle = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.state = "idle"
        self._commands = queue.Queue()
        self._thread = None
        self.last_result = None

    def _set_state(self, state):
        self.state = state
        self.changed.emit(state)

    def start(self, plan, config, scrcpy, adb, ffmpeg, ffprobe, main_script):
        if self.busy:
            return
        self.busy = True
        self._commands = queue.Queue()
        self.last_result = None
        self._set_state("preparing")
        self._thread = threading.Thread(target=self._run, args=(plan, dict(config), scrcpy, adb, ffmpeg,
                                                              ffprobe, main_script), daemon=True)
        self._thread.start()

    def pause(self):
        if self.state == "recording":
            self._set_state("pausing")
            self._commands.put("pause")

    def resume(self):
        if self.state == "paused":
            self._set_state("starting")
            self._commands.put("resume")

    def finish(self):
        if self.busy and self.state not in ("exporting", "stopping"):
            self._set_state("stopping")
            self._commands.put("finish")

    def _run(self, plan, config, scrcpy, adb, ffmpeg, ffprobe, main_script):
        children = []
        segment = None
        total = 0
        segment_started = None
        notes = []

        def persist():
            public = dict(plan)
            public["devices"] = [{k: v for k, v in d.items() if k != "serial"} for d in plan["devices"]]
            public["notes"] = notes
            (Path(plan["folder"]) / "recording.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")

        def spawn(command, kind):
            env = external_environment()
            env["ADB"] = adb
            kwargs = hidden_kwargs()
            if kind == "device" and sys.platform == "win32":
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
                kwargs = {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startup}
            proc = subprocess.Popen(command, stdin=subprocess.PIPE if kind == "mic" else subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env, **kwargs)
            tail = deque(maxlen=50)
            def drain():
                for line in iter(proc.stderr.readline, b""):
                    tail.append(line.decode("utf-8", errors="replace").strip())
                proc.stderr.close()
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            children.append((proc, kind, tail, reader))

        def disable_microphone(reason):
            # A failed optional input must never stop the Android captures.
            plan["microphone"] = None
            if segment and segment.get("mic"):
                try:
                    info = media_info(ffprobe, segment["mic"])
                    if not info["audio"] or info["duration"] <= 0:
                        segment.pop("mic")
                except (ValueError, OSError, subprocess.SubprocessError):
                    segment.pop("mic", None)
            message = "电脑麦克风不可用，设备录制继续；本次不再采集电脑讲解。\n" + reason
            notes.append(message)
            persist()
            self.warning.emit(message)

        def check_microphone():
            for child in list(children):
                proc, kind, tail, reader = child
                if kind != "mic" or proc.poll() is None:
                    continue
                reader.join(timeout=1)
                if proc.stdin:
                    proc.stdin.close()
                children.remove(child)
                disable_microphone(f"麦克风采集退出（代码 {proc.returncode}）：" + "\n".join(tail)[-900:])

        def start_segment():
            nonlocal segment, segment_started
            if plan.get("microphone"):
                try:
                    available = list_microphones(ffmpeg)
                    plan["microphone"] = resolve_microphone(plan["microphone"], available)
                    name = next(d["name"] for d in available if d["id"] == plan["microphone"])
                    self.log.emit("电脑麦克风：" + name)
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    disable_microphone(str(error))
            if not self._commands.empty():
                self.log.emit("已取消录制准备。")
                return False
            segment = {}
            index = len(plan["segments"])
            segment_started = time.monotonic()
            for device in plan["devices"]:
                slot = device["slot"]
                output = Path(plan["folder"]) / ".parts" / f"{index:03d}_{slot}.mp4"
                segment[slot] = str(output)
                command = [scrcpy, "--serial", device["serial"], "--no-window", "--no-control",
                           "--no-audio-playback", "--record", str(output)]
                command += audio_options(device["sdk"], device["audio"])
                if config.get("max_size") != "0":
                    command += ["--max-size", str(config["max_size"])]
                if config.get("bitrate") != "0":
                    command += ["--video-bit-rate", f"{config['bitrate']}M"]
                command += ["--max-fps", str(config.get("fps") or 30) if config.get("fps") != "0" else "30"]
                spawn(command, "device")
            if plan.get("microphone"):
                output = Path(plan["folder"]) / ".parts" / f"{index:03d}_mic.m4a"
                segment["mic"] = str(output)
                try:
                    spawn([ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", "-f", "dshow",
                           "-i", f"audio={plan['microphone']}", "-vn", "-c:a", "aac", "-b:a", "192k",
                           "-ar", "48000", "-ac", "2", str(output)], "mic")
                except OSError as error:
                    disable_microphone(str(error))
            self._set_state("recording")
            return True

        def stop_segment():
            nonlocal segment, segment_started, total
            check_microphone()
            errors = []
            mic_errors = []
            # Signal all inputs first; wait only afterwards to keep stop times close.
            signal_threads = []
            def signal_child(proc, kind):
                try:
                    if kind == "mic":
                        proc.stdin.write(b"q\n")
                        proc.stdin.flush()
                    else:
                        request_capture_stop(proc, main_script)
                except (OSError, subprocess.SubprocessError) as error:
                    (mic_errors if kind == "mic" else errors).append(f"停止采集失败：{error}")
            for proc, kind, tail, reader in children:
                if proc.poll() is not None:
                    (mic_errors if kind == "mic" else errors).append("采集意外退出：" + "\n".join(tail)[-700:])
                    continue
                worker = threading.Thread(target=signal_child, args=(proc, kind), daemon=True)
                worker.start()
                signal_threads.append(worker)
            for worker in signal_threads:
                worker.join(timeout=16)
            for proc, kind, tail, reader in children:
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    (mic_errors if kind == "mic" else errors).append("采集关闭超时，片段可能不完整")
                reader.join(timeout=1)
                if proc.stdin:
                    proc.stdin.close()
                if proc.returncode:
                    (mic_errors if kind == "mic" else errors).append("采集返回错误：" + "\n".join(tail)[-700:])
            children.clear()
            if mic_errors:
                disable_microphone("\n".join(dict.fromkeys(mic_errors)))
            if segment is not None:
                plan["segments"].append(segment)
                total += max(0, time.monotonic() - segment_started)
                segment = None
                segment_started = None
                persist()
                self.elapsed.emit(total)
            if errors:
                raise RuntimeError("\n".join(dict.fromkeys(errors)))

        try:
            folder = Path(plan["folder"])
            (folder / ".parts").mkdir(parents=True, exist_ok=False)
            plan["segments"] = []
            persist()
            for device in plan["devices"]:
                result = subprocess.run([adb, "-s", device["serial"], "shell", "getprop", "ro.build.version.sdk"],
                                        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
                                        env=external_environment(), **hidden_kwargs())
                sdk = int(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip().isdigit() else 0
                device["sdk"] = sdk
                if device["audio"]:
                    if sdk < 30:
                        notes.append(f"{device['title']}：系统不支持设备声音，本次仅录画面")
                    elif sdk < 33:
                        notes.append(f"{device['title']}：录设备声音期间，设备自身播放可能静音；Android 11 请先解锁屏幕")
                    else:
                        notes.append(f"{device['title']}：已选择播放声音；应用若禁止音频捕获，相应声音无法录入")
            for note in notes:
                self.log.emit(note)
            persist()
            # A finish during preparation must not start capturing a microphone.
            if not self._commands.empty():
                self.log.emit("已取消录制准备。")
                return
            if not start_segment():
                return
            while True:
                try:
                    command = self._commands.get(timeout=0.1)
                except queue.Empty:
                    command = None
                check_microphone()
                if command == "pause":
                    stop_segment()
                    self._set_state("paused")
                elif command == "resume":
                    start_segment()
                elif command == "finish":
                    if children:
                        stop_segment()
                    self._set_state("exporting")
                    result = export_recording(plan, ffmpeg, ffprobe, self.progress.emit)
                    result["notes"] = notes + result["notes"]
                    self.last_result = result
                    self.saved.emit(result)
                    break
                if children:
                    exited = next((child for child in children if child[0].poll() is not None), None)
                    if exited:
                        if exited[1] == "mic":
                            check_microphone()
                        else:
                            exited[3].join(timeout=1)
                            raise RuntimeError(f"设备采集已断开（退出代码 {exited[0].returncode}）：" + "\n".join(exited[2])[-900:])
                    self.elapsed.emit(total + time.monotonic() - segment_started)
        except Exception as error:
            self._set_state("stopping")
            if children:
                try:
                    stop_segment()
                except Exception:
                    pass
            self.failed.emit(f"{error}\n已录片段保留在：{plan['folder']}")
        finally:
            self.busy = False
            self._set_state("idle")
            self.idle.emit()
