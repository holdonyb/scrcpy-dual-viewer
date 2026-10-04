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
