"""Packaged-binary checks using synthetic media; never opens devices or a mic."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

from app_runtime import VERSION, external_environment, initialize_desktop
from recording_capture import find_media_tools, hidden_kwargs, request_capture_stop
from recording_export import export_recording, media_info, find_font


def own_command():
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).with_name("dual_scrcpy_qt.py")))
    return command


def window_child(args):
    from PySide6.QtWidgets import QApplication, QPushButton
    app = QApplication([])
    initialize_desktop(app)
    class Child(QPushButton):
        def closeEvent(self, event):
            Path(args[1]).write_text("normal close", encoding="utf-8")
            super().closeEvent(event)
    win = Child("Synthetic window")
    win.setWindowTitle(args[0])
    win.resize(320, 240)
    win.show()
    code = app.exec()
    import shiboken6
    shiboken6.delete(win)
    shiboken6.delete(app)
    return code


def microphone_failure_test(win, base, ffmpeg, ffprobe, wait_for):
    """Exercise the packaged Recorder with two real synthetic video processes."""
    from unittest.mock import patch
    import recording_capture as capture
    original_spawn, original_run = subprocess.Popen, subprocess.run
    device_processes = []
    mic_commands = []
    warnings, errors, results = [], [], []
    recorder = capture.Recorder(win)
    recorder.warning.connect(warnings.append)
    recorder.failed.connect(errors.append)
    recorder.saved.connect(results.append)

    class SyntheticDevice:
        # FFmpeg uses 255 for a normal SIGINT/CTRL_BREAK stop; scrcpy uses 0.
        def __init__(self, process):
            self.process = process

        @property
        def returncode(self):
            return 0 if self.process.returncode == 255 else self.process.returncode

        def poll(self):
            self.process.poll()
            return self.returncode

        def wait(self, **kwargs):
            self.process.wait(**kwargs)
            return self.returncode

        def __getattr__(self, name):
            return getattr(self.process, name)

    def run(argv, **kwargs):
        if argv[0] == "SYNTHETIC_ADB":
            return subprocess.CompletedProcess(argv, 0, "34\n", "")
        return original_run(argv, **kwargs)

    def spawn(argv, **kwargs):
        if argv[0] == "SYNTHETIC_SCRCPY":
            output = argv[argv.index("--record") + 1]
            argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-re", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=15", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", output]
            child = SyntheticDevice(original_spawn(argv, **kwargs))
            device_processes.append(child)
            return child
        if "dshow" in argv:
            mic_commands.append(argv)
            # Deliberately invalid synthetic input: never enumerate/open real microphones.
            argv = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "nonexistent_synthetic_microphone", argv[-1]]
        return original_spawn(argv, **kwargs)

    plan = {"folder": str(base / "optional-mic"), "devices": [
        {"slot": slot, "serial": "SYNTHETIC_" + slot, "title": "模拟设备 " + slot, "audio": True} for slot in "AB"],
        "microphone": "@SYNTHETIC_MIC", "mode": "both", "title": "麦克风故障测试"}
    with patch.object(capture.subprocess, "run", side_effect=run), \
         patch.object(capture.subprocess, "Popen", side_effect=spawn), \
         patch.object(capture, "list_microphones", return_value=[{"name": "Synthetic", "id": "@SYNTHETIC_MIC"}]):
        try:
            recorder.start(plan, {"max_size": "0", "bitrate": "0", "fps": "30"},
                           "SYNTHETIC_SCRCPY", "SYNTHETIC_ADB", ffmpeg, ffprobe,
                           Path(__file__).with_name("dual_scrcpy_qt.py"))
            assert wait_for(lambda: warnings or errors), "Missing microphone warning"
            assert not errors and recorder.busy and recorder.state == "recording", errors
            assert len(device_processes) == 2 and all(p.poll() is None for p in device_processes)
            wait_for(lambda: False, 1.2)
            recorder.finish()
            assert wait_for(lambda: not recorder.busy, 60), "Recorder did not finish"
            assert not errors and len(results) == 1, errors
            assert len(mic_commands) == 1 and len(warnings) == 1
            assert warnings[0] in results[0]["notes"]
            assert len(results[0]["outputs"]) == 3
            for output in results[0]["outputs"]:
                info = media_info(ffprobe, output)
                assert info["video"] and info["audio"] and info["duration"] > 0.5
        finally:
            recorder.finish()
            wait_for(lambda: not recorder.busy, 30)
            for process in device_processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)


def self_test(report_path):
    report = {"version": VERSION, "platform": sys.platform, "frozen": bool(getattr(sys, "frozen", False)),
              "checks": {}, "passed": False}
    win = None
    app = None
    proc = None
    try:
        from PySide6.QtCore import QSettings, QEvent
        from PySide6.QtWidgets import QApplication
        import dual_scrcpy_qt as m
        app = QApplication([])
        initialize_desktop(app)
        app.setStyle("Fusion")
        app.setStyleSheet(m.DARK_QSS)
        roots = m.resource_dirs()
        scrcpy = m.ScrcpyFinder.find()
        adb = m.find_adb(scrcpy)
        ffmpeg, ffprobe = find_media_tools({}, roots)
        for name, tool, flag in (("scrcpy", scrcpy, "--version"), ("adb", adb, "version"),
                                 ("ffmpeg", ffmpeg, "-version"), ("ffprobe", ffprobe, "-version")):
            assert tool, f"Missing bundled {name}"
            if getattr(sys, "frozen", False):
                assert any(Path(tool).resolve().is_relative_to(p.resolve()) for p in roots), name
            result = subprocess.run([tool, flag], capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=30, env=external_environment(), **hidden_kwargs())
            assert result.returncode == 0, f"{name}: {result.stderr[-300:]}"
            report["checks"][name] = (result.stdout or result.stderr).splitlines()[0]
        assert find_font().is_file()
        report["checks"]["font"] = find_font().name
        with tempfile.TemporaryDirectory(prefix="multiscreen-smoke-") as temporary:
            base = Path(temporary)
            original = m.AdbHelper.list_devices
            m.AdbHelper.list_devices = lambda self: []
            try:
                win = m.MainWindow(QSettings(str(base / "settings.ini"), QSettings.IniFormat))
            finally:
                m.AdbHelper.list_devices = original
            win.show()
            def wait_for(condition, seconds=25):
                until = time.monotonic() + seconds
                while time.monotonic() < until:
                    app.processEvents()
                    if condition():
                        return True
                    time.sleep(0.02)
                return False
            assert wait_for(lambda: win.isVisible())
            report_path.parent.mkdir(parents=True, exist_ok=True)
            assert win.grab().save(str(report_path.with_suffix(".png")))
            report["checks"]["gui"] = True
            title = f"RELEASE-SMOKE-{time.time_ns()}"
            marker = base / "window.closed"
            panel = win.panel_a
            stopped = []
            panel.session.stopped.connect(stopped.append)
            panel.session.start(own_command() + ["--test-window", title, str(marker)], title)
            assert wait_for(lambda: panel._container is not None), "Native window embedding failed"
            panel.stop()
            assert wait_for(lambda: panel.session.proc is None), "Native child did not stop"
            assert stopped == [False] and marker.read_text() == "normal close"
            report["checks"]["native_embed_and_close"] = True

            # Same external-console shutdown path used by headless scrcpy capture.
            capture = base / "graceful.mp4"
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-re", "-f", "lavfi",
                       "-i", "testsrc2=size=320x240:rate=15", "-c:v", "libx264", "-preset", "ultrafast", str(capture)]
            kwargs = {}
            if sys.platform == "win32":
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
                kwargs = {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startup}
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, env=external_environment(), **kwargs)
            assert wait_for(lambda: capture.is_file() and capture.stat().st_size > 32)
            wait_for(lambda: False, 1.2)
            request_capture_stop(proc, Path(m.__file__))
            proc.wait(timeout=15)
            assert media_info(ffprobe, capture)["duration"] > 0.5
            report["checks"]["headless_normal_finalize"] = True
            proc = None

            microphone_failure_test(win, base, ffmpeg, ffprobe, wait_for)
            report["checks"]["optional_microphone_failure"] = True

            sources = []
            for index in range(3):
                source = base / f"source-{index}.mp4"
                result = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=15", "-f", "lavfi", "-i",
                    f"sine=frequency={440+index*220}:sample_rate=48000", "-t", "0.6", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(source)], capture_output=True,
                    env=external_environment(), **hidden_kwargs())
                assert result.returncode == 0, result.stderr.decode(errors="replace")
                sources.append(str(source))
            for count in range(1, 4):
                devices = [{"slot": str(i), "title": f"模拟设备 {i+1}", "audio": True} for i in range(count)]
                segment = {str(i): sources[i] for i in range(count)}
                plan = {"folder": str(base / f"export-{count}"), "devices": devices,
                        "segments": [segment, segment], "mode": "both", "layout": "presentation", "title": "多屏录制测试"}
                result = export_recording(plan, ffmpeg, ffprobe)
                assert len(result["outputs"]) == count + 1
                for path in result["outputs"]:
                    info = media_info(ffprobe, path)
                    assert info["video"]["codec_name"] == "h264" and info["audio"]
                    assert info["duration"] > 1
                info = media_info(ffprobe, result["outputs"][-1])
                assert (info["video"]["width"], info["video"]["height"]) == (1920, 1080)
                report["checks"][f"export_{count}_sources"] = True
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        if win:
            win._closing = True
            win.close()
            if win.panel_a.session.proc:
                win.panel_a.session.proc.kill()
                win.panel_a.session.proc.wait(timeout=10)
            win.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
        if app:
            import shiboken6
            shiboken6.delete(app)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1
