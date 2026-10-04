"""Real FFmpeg export and hidden-console lifecycle; no real device or mic capture."""
import array
import json
import math
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
from PySide6.QtCore import QSettings, QEvent
from PySide6.QtWidgets import QApplication
import dual_scrcpy_qt as m
import recording_capture as capture
from recording_export import export_recording, media_info

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
DEVICES = [m.AdbDevice(f"SYNTHETIC_{i}", "device", f"Synthetic {i}") for i in range(3)]


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/ffprobe required")
class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hicool-recording-test-")
        self.base = Path(self.temp.name)
        self.windows = []
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.finish()
        wait_until(lambda: all(not r.busy for r in self.recorders), 30)
        for win in self.windows:
            win.quit_after_recording()
        APP.processEvents()
        for win in self.windows:
            win.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def window(self):
        settings = QSettings(str(self.base / "settings.ini"), QSettings.IniFormat)
        with patch.object(m.AdbHelper, "list_devices", return_value=DEVICES):
            win = m.MainWindow(settings)
        self.windows.append(win)
        return win

    def make_source(self, name, size, frequency=None, duration=0.65):
        output = self.base / name
        args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size={size}:rate=15"]
        if frequency:
            args += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000", "-c:a", "aac"]
        args += ["-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(output)]
        subprocess.run(args, check=True, capture_output=True, **capture.hidden_kwargs())
        return str(output)

    def test_one_two_three_sources_modes_and_audio_switches(self):
        sources = [self.make_source(f"source_{i}.mp4", size, 440 + i*220)
                   for i, size in enumerate(("240x420", "640x360", "400x300"))]
        for count, mode in ((1, "separate"), (2, "combined"), (3, "both")):
            with self.subTest(count=count, mode=mode):
                folder = self.base / f"output_{count}"
                devices = [{"slot": "ABC"[i], "title": ["手机", "眼镜", "设备 3"][i], "audio": i != 1}
                           for i in range(count)]
                plan = {"folder": str(folder), "devices": devices,
                        "segments": [{"ABC"[i]: sources[i] for i in range(count)}],
                        "mode": mode, "layout": "presentation", "title": "测试 · 合成演示", "date": "2026.10.04"}
                result = export_recording(plan, FFMPEG, FFPROBE)
                expected = count if mode == "separate" else 1 if mode == "combined" else count + 1
                self.assertEqual(len(result["outputs"]), expected)
                for path in result["outputs"]:
                    info = media_info(FFPROBE, path)
                    self.assertEqual(info["video"]["codec_name"], "h264")
                    self.assertAlmostEqual(info["duration"], result["duration"], delta=0.15)
                    if Path(path).name == "合成视频.mp4":
                        self.assertEqual((info["video"]["width"], info["video"]["height"]), (1920, 1080))
                        self.assertTrue(info["audio"])
                    elif Path(path).name.startswith("02_"):
                        self.assertFalse(info["audio"], "unchecked device audio must be absent")

    def test_pause_segments_join_and_narration_mixed_once(self):
        a = self.make_source("a.mp4", "320x240", 440)
        b = self.make_source("b.mp4", "320x240", 660)
        mic = self.base / "mic.m4a"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=880:sample_rate=48000", "-t", "0.65", "-c:a", "aac", str(mic)],
                       check=True, capture_output=True, **capture.hidden_kwargs())
        plan = {"folder": str(self.base / "paused"), "devices": [
            {"slot": "A", "title": "phone", "audio": True}, {"slot": "B", "title": "glasses", "audio": False}],
            "segments": [{"A": a, "B": b, "mic": str(mic)}, {"A": a, "B": b, "mic": str(mic)}],
            "mode": "both", "layout": "equal", "title": "Synthetic recording"}
        result = export_recording(plan, FFMPEG, FFPROBE)
        self.assertLess(result["duration"], 1.6, "pause time must not be inserted")
        mixed = result["outputs"][-1]
        raw = subprocess.run([FFMPEG, "-v", "error", "-i", mixed, "-ss", "0.1", "-t", "0.25", "-vn",
                              "-ar", "48000", "-ac", "1", "-f", "f32le", "-"], capture_output=True,
                             check=True, **capture.hidden_kwargs()).stdout
        samples = array.array("f", raw)
        def amplitude(hz):
            n = len(samples)
            sine = sum(v*math.sin(2*math.pi*hz*i/48000) for i, v in enumerate(samples))*2/n
            cosine = sum(v*math.cos(2*math.pi*hz*i/48000) for i, v in enumerate(samples))*2/n
            return math.hypot(sine, cosine)
        self.assertGreater(amplitude(440), 0.015)
        self.assertGreater(amplitude(880), 0.03)
        self.assertLess(amplitude(660), 0.004, "unchecked glasses audio leaked")
        self.assertAlmostEqual(amplitude(440)/amplitude(880), 0.65, delta=0.12)
        self.assertTrue(all(media_info(FFPROBE, p)["audio"] for p in result["outputs"]))

    def test_missing_device_audio_is_reported_and_export_failure_retains_inputs(self):
        source = self.make_source("silent.mp4", "320x240")
        plan = {"folder": str(self.base / "missing_audio"), "devices": [{"slot": "A", "title": "phone", "audio": True}],
                "segments": [{"A": source}], "mode": "separate"}
        result = export_recording(plan, FFMPEG, FFPROBE)
        self.assertIn("没有提供设备声音", result["notes"][0])
        with patch("recording_export.subprocess.run", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                export_recording(plan, FFMPEG, FFPROBE)
        self.assertTrue(Path(source).is_file())

    def test_silent_combined_video_and_rotation_metadata(self):
        source = self.make_source("landscape.mp4", "320x180", 440)
        rotated = self.base / "rotated.mp4"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-display_rotation:v:0", "90", "-i", source,
                        "-c", "copy", str(rotated)],
                       check=True, capture_output=True, **capture.hidden_kwargs())
        plan = {"folder": str(self.base / "silent_rotated"), "devices": [{"slot": "A", "title": "phone", "audio": False}],
                "segments": [{"A": str(rotated)}], "mode": "both", "title": "Silent rotation"}
        self.assertTrue(media_info(FFPROBE, rotated)["video"].get("side_data_list"), "rotation fixture is missing metadata")
        result = export_recording(plan, FFMPEG, FFPROBE)
        individual = media_info(FFPROBE, result["outputs"][0])
        self.assertEqual((individual["video"]["width"], individual["video"]["height"]), (180, 320))
        for output in result["outputs"]:
            self.assertFalse(media_info(FFPROBE, output)["audio"])

    def test_ui_device_count_and_recording_source_selection(self):
        win = self.window()
        with patch.object(m.AdbHelper, "list_devices", return_value=DEVICES):
            win.device_count.setCurrentIndex(2)
        self.assertEqual([p.current_serial() for p in win.active_panels], [d.serial for d in DEVICES])
        win.panel_b.device_audio.setChecked(False)
        with patch.object(win.recording.recorder, "start") as start:
            win.recording.start()
        self.assertTrue(start.called)
        plan = start.call_args.args[0]
        self.assertEqual(len(plan["devices"]), 3)
        self.assertEqual([d["audio"] for d in plan["devices"]], [True, False, True])
        self.assertIsNone(plan["microphone"], "mic must stay off unless selected")
        win.recording.mic.setChecked(True)
        win.recording.microphones.addItem("SYNTHETIC microphone", "SYNTHETIC microphone")
        win.recording.microphones.setCurrentIndex(1)
        with patch.object(win.recording.recorder, "start") as start:
            win.recording.start()
        self.assertEqual(start.call_args.args[0]["microphone"], "SYNTHETIC microphone")

    def test_audio_options_are_playback_not_device_mic(self):
        self.assertEqual(capture.audio_options(29, True), ["--no-audio"])
        self.assertEqual(capture.audio_options(34, False), ["--no-audio"])
        self.assertIn("--audio-source=output", capture.audio_options(31, True))
        self.assertIn("--audio-source=playback", capture.audio_options(34, True))
        self.assertIn("--audio-dup", capture.audio_options(34, True))

    @unittest.skipUnless(sys.platform == "win32", "Windows console signal integration")
    def test_headless_pause_resume_background_and_mp4_finalization(self):
        win = self.window()
        # CI desktops need not provide a tray; explicitly exercise the tray branch.
        if win.tray is None:
            from unittest.mock import Mock
            win.tray = Mock()
        recorder = win.recording.recorder
        self.recorders.append(recorder)
        errors, results, commands = [], [], []
        recorder.failed.connect(errors.append)
        recorder.saved.connect(results.append)
        original_spawn = subprocess.Popen
        original_run = subprocess.run
        def run(argv, **kwargs):
            if argv[0] == "SYNTHETIC_ADB":
                return subprocess.CompletedProcess(argv, 0, "34\n", "")
            return original_run(argv, **kwargs)
        def spawn(argv, **kwargs):
            if argv[0] == "SYNTHETIC_SCRCPY":
                commands.append(argv)
                argv = [sys.executable, str(ROOT / "tests/console_capture_child.py")] + argv[1:]
            return original_spawn(argv, **kwargs)
        plan = {"folder": str(self.base / "headless"), "devices": [
            {"slot": "A", "serial": "SYNTHETIC_PHONE", "title": "phone", "audio": True}],
            "microphone": None, "mode": "separate", "layout": "presentation", "title": "test"}
        win.show()
        with patch.object(capture.subprocess, "Popen", side_effect=spawn), \
             patch.object(capture.subprocess, "run", side_effect=run):
            recorder.start(plan, m.default_config(), "SYNTHETIC_SCRCPY", "SYNTHETIC_ADB", FFMPEG, FFPROBE, ROOT / "dual_scrcpy_qt.py")
            self.assertTrue(wait_until(lambda: recorder.state == "recording" or errors))
            self.assertFalse(errors)
            self.assertTrue(wait_until(lambda: (self.base / "headless/.parts/000_A.console.txt").exists()))
            self.assertEqual((self.base / "headless/.parts/000_A.console.txt").read_text(), "False")
            win.stop_all()
            self.assertTrue(recorder.busy)
            win.close()  # tray hides; recording continues
            self.assertTrue(recorder.busy)
            self.assertFalse(win._closing)
            wait_until(lambda: False, 1.2)
            recorder.pause()
            self.assertTrue(wait_until(lambda: (recorder.state == "paused" and win.recording.status.text().startswith("已暂停")) or errors))
            self.assertFalse(errors)
            elapsed = win.recording._seconds
            wait_until(lambda: False, 0.5)
            self.assertAlmostEqual(win.recording._seconds, elapsed, delta=0.1)
            recorder.resume()
            self.assertTrue(wait_until(lambda: recorder.state == "recording" or errors))
            wait_until(lambda: False, 1.2)
            recorder.finish()
            self.assertTrue(wait_until(lambda: not recorder.busy, 30))
            APP.processEvents()
        self.assertFalse(errors, errors)
        self.assertEqual(len(commands), 2)
        self.assertTrue(all("--no-window" in argv and "--audio-source=playback" in argv for argv in commands))
        self.assertEqual(len(results), 1)
        info = media_info(FFPROBE, results[0]["outputs"][0])
        self.assertTrue(info["video"] and info["audio"])
        self.assertTrue((self.base / "headless/.parts/000_A.closed.txt").is_file())
        self.assertTrue((self.base / "headless/.parts/001_A.closed.txt").is_file())
        public = json.loads((self.base / "headless/recording.json").read_text(encoding="utf-8"))
        self.assertNotIn("serial", public["devices"][0])
        self.assertEqual(len(public["segments"]), 2)

    @unittest.skipUnless(sys.platform == "win32", "Windows capture integration")
    def test_three_headless_devices_and_synthetic_microphone_finish_on_exit(self):
        win = self.window()
        recorder = win.recording.recorder
        self.recorders.append(recorder)
        errors, results, mic_commands = [], [], []
        recorder.failed.connect(errors.append)
        recorder.saved.connect(results.append)
        original_spawn, original_run = subprocess.Popen, subprocess.run
        def run(argv, **kwargs):
            if argv[0] == "SYNTHETIC_ADB":
                return subprocess.CompletedProcess(argv, 0, "34\n", "")
            return original_run(argv, **kwargs)
        def spawn(argv, **kwargs):
            if argv[0] == "SYNTHETIC_SCRCPY":
                argv = [sys.executable, str(ROOT / "tests/console_capture_child.py")] + argv[1:]
            elif "dshow" in argv:
                mic_commands.append(argv)
                self.assertEqual(argv[argv.index("-i")+1], "audio=SYNTHETIC microphone")
                # Replace only the input; exercise the actual FFmpeg microphone stop/export path.
                start = argv.index("-f")
                end = argv.index("-vn")
                argv = argv[:start] + ["-re", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000"] + argv[end:]
            return original_spawn(argv, **kwargs)
        plan = {"folder": str(self.base / "three_headless"), "devices": [
            {"slot": slot, "serial": f"SYNTHETIC_{slot}", "title": slot, "audio": slot != "B"} for slot in "ABC"],
            "microphone": "SYNTHETIC microphone", "mode": "separate", "title": "Synthetic"}
        win.show()
        with patch.object(capture.subprocess, "Popen", side_effect=spawn), \
             patch.object(capture.subprocess, "run", side_effect=run):
            recorder.start(plan, m.default_config(), "SYNTHETIC_SCRCPY", "SYNTHETIC_ADB", FFMPEG, FFPROBE, ROOT / "dual_scrcpy_qt.py")
            self.assertTrue(wait_until(lambda: recorder.state == "recording" or errors))
            wait_until(lambda: False, 1.5)
            win.quit_after_recording()
            self.assertTrue(wait_until(lambda: not recorder.busy, 30))
            APP.processEvents()
        self.assertFalse(errors, errors)
        self.assertEqual(len(results[0]["outputs"]), 3)
        self.assertEqual(len(mic_commands), 1)
        for output in results[0]["outputs"]:
            info = media_info(FFPROBE, output)
            self.assertTrue(info["video"] and info["audio"])
        self.assertFalse(win.isVisible())

    def test_preparation_cancel_does_not_launch_capture(self):
        recorder = capture.Recorder()
        self.recorders.append(recorder)
        plan = {"folder": str(self.base / "cancelled"), "devices": [
            {"slot": "A", "serial": "synthetic", "title": "test", "audio": True}], "microphone": "synthetic"}
        def run(argv, **kwargs):
            time.sleep(0.2)
            return subprocess.CompletedProcess(argv, 0, "34\n", "")
        with patch.object(capture.subprocess, "run", side_effect=run), \
             patch.object(capture.subprocess, "Popen") as spawn:
            recorder.start(plan, m.default_config(), "synthetic", "synthetic", FFMPEG, FFPROBE, ROOT / "dual_scrcpy_qt.py")
            recorder.finish()
            self.assertTrue(wait_until(lambda: not recorder.busy))
        spawn.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows disconnect integration")
    def test_capture_disconnect_reports_failure_and_keeps_recoverable_segments(self):
        recorder = capture.Recorder()
        self.recorders.append(recorder)
        errors, results = [], []
        recorder.failed.connect(errors.append)
        recorder.saved.connect(results.append)
        original_spawn, original_run = subprocess.Popen, subprocess.run
        def run(argv, **kwargs):
            if argv[0] == "SYNTHETIC_ADB":
                return subprocess.CompletedProcess(argv, 0, "34\n", "")
            return original_run(argv, **kwargs)
        def spawn(argv, **kwargs):
            if argv[0] == "SYNTHETIC_SCRCPY":
                argv = [sys.executable, str(ROOT / "tests/console_capture_child.py"), "--exit-after", "0.7"] + argv[1:]
            return original_spawn(argv, **kwargs)
        plan = {"folder": str(self.base / "disconnect"), "devices": [
            {"slot": "A", "serial": "synthetic", "title": "test", "audio": True}], "mode": "separate"}
        with patch.object(capture.subprocess, "Popen", side_effect=spawn), \
             patch.object(capture.subprocess, "run", side_effect=run):
            recorder.start(plan, m.default_config(), "SYNTHETIC_SCRCPY", "SYNTHETIC_ADB", FFMPEG, FFPROBE, ROOT / "dual_scrcpy_qt.py")
            self.assertTrue(wait_until(lambda: not recorder.busy))
            APP.processEvents()
        self.assertEqual(len(errors), 1)
        self.assertIn("退出代码 7", errors[0])
        self.assertFalse(results)
        self.assertTrue(media_info(FFPROBE, self.base / "disconnect/.parts/000_A.mp4")["video"])
        self.assertEqual(len(plan["segments"]), 1)


if __name__ == "__main__":
    unittest.main()
