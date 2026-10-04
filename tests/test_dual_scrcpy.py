"""Regression checks for device selection, portable tools and native shutdown.

No Android device is required or controlled. Windows integration checks use
separate synthetic Qt windows; MP4 checks use a real FFmpeg writer.
"""
import ctypes
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QSettings, QTimer, QEvent
from PySide6.QtWidgets import QApplication, QDialog
import dual_scrcpy_qt as m

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
APP.setStyle("Fusion")
APP.setStyleSheet(m.DARK_QSS)
CHILD = Path(__file__).with_name("native_test_child.py")
DEVICES = [m.AdbDevice("TEST_PHONE", "device", "Test Phone"),
           m.AdbDevice("TEST_GLASSES", "device", "Test Glasses")]


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dual-viewer-regression-")
        self.base = Path(self.tmp.name).resolve()
        self.windows = []
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            if session.proc is not None:
                session.stop(timeout_s=0.2)
        for win in self.windows:
            win.close()
        wait_until(lambda: all(s.proc is None for s in self.sessions), timeout=3)
        for session in self.sessions:
            if session.proc is not None:
                session.proc.kill()
                session.proc.wait(timeout=5)
                session._poll()
        APP.processEvents()
        for win in self.windows:
            win.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.tmp.cleanup()

    def window(self, devices=DEVICES):
        settings = QSettings(str(self.base / "settings.ini"), QSettings.IniFormat)
        with patch.object(m.AdbHelper, "list_devices", return_value=list(devices)):
            win = m.MainWindow(settings)
        self.windows.append(win)
        self.sessions.extend([win.panel_a.session, win.panel_b.session])
        return win

    def launch_native(self, win, *, record=False, delay=0, ignore=False, exit_code=0):
        win.cfg["record"] = record
        win.cfg["record_dir"] = str(self.base / "recordings")
        win.scrcpy_path = "SCRCPY_TEST_EXECUTABLE"
        original = subprocess.Popen
        markers = []
        environments = []

        def spawn(argv, **kwargs):
            if argv[0] != "SCRCPY_TEST_EXECUTABLE":
                return original(argv, **kwargs)
            marker = self.base / f"close-{len(markers)}"
            markers.append(marker)
            environments.append(kwargs.get("env", {}))
            child_args = [sys.executable, str(CHILD), "--title", argv[argv.index("--window-title") + 1],
                          "--marker", str(marker), "--close-delay", str(delay), "--exit-code", str(exit_code)]
            if "--record" in argv:
                child_args += ["--record", argv[argv.index("--record") + 1]]
            if ignore:
                child_args += ["--ignore-close"]
            return original(child_args, **kwargs)

        win.show()
        with patch.object(m.subprocess, "Popen", side_effect=spawn):
            win.start_all()
        panels = [p for p in (win.panel_a, win.panel_b) if p.session.proc is not None]
        self.assertTrue(wait_until(lambda: all(p._container is not None for p in panels)), "windows did not embed")
        self.assertTrue(panels)
        return panels, markers, environments

    def test_portable_flat_and_nested_tools_without_path(self):
        bundle = self.base / "scrcpy"
        bundle.mkdir()
        exe, adb = bundle / "scrcpy.exe", bundle / "adb.exe"
        exe.touch()
        adb.touch()
        with patch.object(m, "resource_dirs", return_value=[self.base]), \
             patch.object(m.shutil, "which", return_value=None), \
             patch.object(m.Path, "home", return_value=self.base):
            self.assertEqual(m.ScrcpyFinder.find(), str(exe))
            self.assertEqual(Path(m.AdbHelper().adb_path), adb)
            nested = bundle / "scrcpy-win64-test"
            nested.mkdir()
            exe.unlink()
            adb.unlink()
            (nested / "scrcpy.exe").touch()
            (nested / "adb.exe").touch()
            self.assertEqual(m.ScrcpyFinder.find(), str(nested / "scrcpy.exe"))
            self.assertEqual(Path(m.AdbHelper().adb_path), nested / "adb.exe")

    def test_frozen_resource_roots(self):
        internal = self.base / "_internal"
        internal.mkdir()
        with patch.object(m, "__file__", str(internal / "dual_scrcpy_qt.py")), \
             patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "_MEIPASS", str(internal), create=True), \
             patch.object(sys, "executable", str(self.base / "dual_mirror.exe")):
            self.assertEqual(set(m.resource_dirs()), {self.base, internal})

    def test_adb_statuses_and_error_are_visible(self):
        output = "List of devices attached\nREADY device model:Test_Phone\nU unauthorized usb:1\nO offline usb:2\n"
        with patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            devices = m.AdbHelper("adb.exe").list_devices()
        self.assertEqual([d.status for d in devices], ["device", "unauthorized", "offline"])
        win = self.window(devices)
        win.panel_b.select_serial("U")
        self.assertIn("允许 USB 调试", win.panel_b.status.text())
        self.assertFalse(win.panel_b.btn_toggle.isEnabled())
        self.assertIn("未授权", win.panel_b.combo.currentText())
        with patch.object(win.panel_b.session, "start") as start:
            win.panel_b.start()
        start.assert_not_called()
        with patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "adb failure")):
            with self.assertRaisesRegex(RuntimeError, "adb failure"):
                m.AdbHelper("adb.exe").list_devices()
        helper = m.AdbHelper("adb.exe")
        helper.adb_path = None
        with self.assertRaisesRegex(RuntimeError, "找不到 adb"):
            helper.list_devices()

    def test_refresh_preserves_swapped_selection(self):
        win = self.window()
        win.panel_a.select_serial("TEST_GLASSES")
        win.panel_b.select_serial("TEST_PHONE")
        with patch.object(win.adb, "list_devices", return_value=list(reversed(DEVICES))):
            win.refresh_devices()
        self.assertEqual([win.panel_a.current_serial(), win.panel_b.current_serial()], ["TEST_GLASSES", "TEST_PHONE"])

    def test_one_device_start_all_only_starts_a(self):
        win = self.window(DEVICES[:1])
        self.assertEqual(win.panel_a.current_serial(), "TEST_PHONE")
        self.assertIsNone(win.panel_b.current_serial())
        with patch.object(win.panel_a, "start") as a, patch.object(win.panel_b, "start") as b, \
             patch.object(m.QMessageBox, "warning") as warning:
            win.start_all()
        a.assert_called_once()
        b.assert_not_called()
        warning.assert_not_called()

    def test_invalid_settings_cannot_be_accepted_or_mutate_config(self):
        cfg = m.default_config()
        before = dict(cfg)
        dialog = m.SettingsDialog(cfg)
        for field, value in ((dialog.max_size, "abc"), (dialog.bitrate, "-8"), (dialog.fps, "1001")):
            dialog.max_size.setText("1280")
            dialog.bitrate.setText("8")
            dialog.fps.setText("0")
            field.setText(value)
            with self.assertRaises(ValueError):
                dialog.apply()
            with patch.object(m.QMessageBox, "warning") as warning:
                dialog.accept()
            warning.assert_called_once()
            self.assertEqual(cfg, before)
            self.assertEqual(dialog.result(), QDialog.Rejected)
        dialog.close()

    def test_settings_and_manual_tools_survive_restart(self):
        win = self.window()
        adb = self.base / "adb.exe"
        scrcpy = self.base / "scrcpy.exe"
        adb.touch()
        scrcpy.touch()
        dialog = m.SettingsDialog(win.cfg)
        dialog.max_size.setText("1920")
        dialog.fps.setText("30")
        dialog.adb_path.setText(str(adb))
        dialog.scrcpy_path.setText(str(scrcpy))
        dialog.accept()
        with patch.object(m, "SettingsDialog", return_value=dialog), \
             patch.object(dialog, "exec", return_value=QDialog.Accepted), \
             patch.object(m.AdbHelper, "list_devices", return_value=[]):
            win.open_settings()
        reopened = self.window([])
        self.assertEqual(reopened.cfg["max_size"], "1920")
        self.assertEqual(reopened.cfg["fps"], "30")
        self.assertEqual(Path(reopened.adb.adb_path), adb)
        self.assertEqual(Path(reopened.scrcpy_path), scrcpy)

    def test_wifi_recording_is_a_regular_unique_file(self):
        win = self.window([m.AdbDevice("192.0.2.1:5555", "device")])
        win.cfg.update(record=True, record_dir=str(self.base / "recordings"))
        win.scrcpy_path = "scrcpy.exe"
        captured = []
        with patch.object(win.panel_a.session, "start", side_effect=lambda argv, title, **kw: captured.append((argv, kw))):
            win.panel_a.start()
            win.panel_a.stop()
            win.panel_a.start()
        paths = [Path(argv[argv.index("--record") + 1]) for argv, _ in captured]
        self.assertEqual(len(paths), 2)
        self.assertNotEqual(paths[0], paths[1])
        for path in paths:
            self.assertNotIn(":", path.name)
            path.write_bytes(b"test")
            self.assertIn(path.name, [p.name for p in path.parent.iterdir()])
        self.assertEqual(captured[0][1]["env"]["ADB"], win.adb.adb_path)
        win.panel_a.stop()

    def test_invalid_record_directory_recovers_without_starting(self):
        win = self.window(DEVICES[:1])
        blocked = self.base / "is-a-file"
        blocked.write_text("test", encoding="utf-8")
        win.cfg.update(record=True, record_dir=str(blocked))
        win.scrcpy_path = "scrcpy.exe"
        with patch.object(win.panel_a.session, "start") as start:
            win.panel_a.start()
        start.assert_not_called()
        self.assertTrue(win.panel_a.btn_toggle.isEnabled())
        self.assertIn("录屏目录", win.panel_a.status.text())

    def test_stderr_is_drained_before_a_window_exists(self):
        session = m.ScrcpySession("ERR")
        self.sessions.append(session)
        errors = []
        session.failed.connect(errors.append)
        session.start([sys.executable, "-c", "import sys; sys.stderr.write('x'*1048576+'DRAIN_DONE'); sys.exit(9)"], "UNIQUE-NO-WINDOW")
        self.assertTrue(wait_until(lambda: bool(errors), timeout=3))
        self.assertIn("DRAIN_DONE", errors[0])
        self.assertIsNone(session.proc)

    @unittest.skipUnless(sys.platform == "win32", "Windows native integration")
    def test_native_embedding_click_refresh_and_async_stop(self):
        win = self.window()
        panels, markers, environments = self.launch_native(win, delay=400)
        win.resize(1100, 700)
        APP.processEvents()
        for panel, marker in zip(panels, markers):
            self.assertTrue(m.user32.PostMessageW(panel.session.hwnd, 0x201, 1, (40 << 16) | 40))
            self.assertTrue(m.user32.PostMessageW(panel.session.hwnd, 0x202, 0, (40 << 16) | 40))
        self.assertTrue(wait_until(lambda: all(Path(str(marker) + ".click").exists() for marker in markers)))
        with patch.object(win.adb, "list_devices", return_value=list(reversed(DEVICES))):
            win.refresh_devices()
        self.assertEqual([p.current_serial() for p in panels], [d.serial for d in DEVICES])
        self.assertTrue(all(p._active_serial in p.status.text() for p in panels))
        with patch.object(win.adb, "list_devices", return_value=[]):
            win.refresh_devices()
        self.assertEqual([p.current_serial() for p in panels], [d.serial for d in DEVICES])
        stopped = []
        for panel in panels:
            panel.session.stopped.connect(stopped.append)
        ticks = []
        QTimer.singleShot(20, lambda: ticks.append(True))
        start = time.monotonic()
        win.stop_all()
        self.assertLess(time.monotonic() - start, 0.2)
        self.assertTrue(all(p._container is not None for p in panels))
        self.assertTrue(wait_until(lambda: all(p.session.proc is None for p in panels)))
        self.assertTrue(ticks, "the UI event loop must remain responsive during shutdown")
        self.assertEqual(stopped, [False, False])
        self.assertTrue(all(marker.exists() for marker in markers))
        self.assertTrue(all(p._container is None for p in panels))
        self.assertTrue(all(env["ADB"] == win.adb.adb_path for env in environments))

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("ffmpeg") and shutil.which("ffprobe"), "Windows/FFmpeg integration")
    def test_stop_all_and_close_window_finalize_mp4(self):
        for close_window in (False, True):
            with self.subTest(close_window=close_window):
                win = self.window()
                panels, markers, _ = self.launch_native(win, record=True, delay=200)
                paths = [p._record_path for p in panels]
                self.assertTrue(wait_until(lambda: all(p.exists() and p.stat().st_size > 40 for p in paths)))
                # Allow at least one second of real frames, pumping the parent UI.
                until = time.monotonic() + 1
                wait_until(lambda: time.monotonic() >= until, timeout=2)
                if close_window:
                    win.close()
                    self.assertTrue(win.isVisible(), "parent must stay alive while recording finalizes")
                else:
                    win.stop_all()
                self.assertTrue(wait_until(lambda: all(p.session.proc is None for p in panels)))
                if close_window:
                    self.assertTrue(wait_until(lambda: not win.isVisible()))
                self.assertTrue(all(marker.exists() for marker in markers))
                for path in paths:
                    result = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                                             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertGreater(float(result.stdout.strip()), 0)
                self.assertNotIn("强制", win.log_view.toPlainText())
                win.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows native integration")
    def test_force_stop_warns_recording_may_be_incomplete(self):
        win = self.window(DEVICES[:1])
        panels, markers, _ = self.launch_native(win, record=True, ignore=True)
        panel = panels[0]
        stopped = []
        panel.session.stopped.connect(stopped.append)
        panel.session.stop(timeout_s=0.2)
        self.assertTrue(wait_until(lambda: panel.session.proc is None))
        self.assertEqual(stopped, [True])
        self.assertTrue(markers[0].exists())
        self.assertIn("录屏可能不完整", win.log_view.toPlainText())
        self.assertIsNone(panel._container)

    @unittest.skipUnless(sys.platform == "win32", "Windows SDL integration")
    def test_scrcpy_sdl_window_accepts_graceful_close_after_embedding(self):
        scrcpy = m.ScrcpyFinder.find()
        dll = Path(scrcpy).parent / "SDL3.dll" if scrcpy else None
        if not dll or not dll.is_file():
            self.skipTest("scrcpy's bundled SDL3.dll is unavailable")
        win = self.window(DEVICES[:1])
        win.show()
        panel = win.panel_a
        panel._active_serial = panel.current_serial()
        title = f"SDL-REGRESSION-{time.time_ns()}"
        marker = self.base / "sdl-quit"
        stopped = []
        panel.session.stopped.connect(stopped.append)
        panel.session.start([sys.executable, str(CHILD.with_name("sdl_test_child.py")),
                             "--dll", str(dll), "--title", title, "--marker", str(marker)], title)
        self.assertTrue(wait_until(lambda: panel._container is not None))
        panel.stop()
        self.assertTrue(wait_until(lambda: panel.session.proc is None))
        self.assertEqual(stopped, [False])
        self.assertEqual(marker.read_text(encoding="utf-8"), "SDL_QUIT received")
        self.assertIsNone(panel._container)

    @unittest.skipUnless(sys.platform == "win32", "Windows native integration")
    def test_nonzero_shutdown_is_reported_as_failure(self):
        win = self.window(DEVICES[:1])
        panels, markers, _ = self.launch_native(win, exit_code=7)
        win.stop_all()
        self.assertTrue(wait_until(lambda: panels[0].session.proc is None))
        self.assertTrue(markers[0].exists())
        self.assertIn("镜像未能正常关闭", win.log_view.toPlainText())
        self.assertNotIn("已正常停止", win.log_view.toPlainText())


if __name__ == "__main__":
    unittest.main(verbosity=2)
