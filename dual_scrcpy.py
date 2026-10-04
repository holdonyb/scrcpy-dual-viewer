#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同时开启两个 scrcpy 窗口，用于镜像两台 Android 设备（如手机 + AR 眼镜）。
依赖：Python 3、adb、scrcpy（Windows 下需要 .exe）
"""

import os
import re
import sys
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class AdbDevice:
    serial: str
    status: str
    product: str = ""
    model: str = ""
    device: str = ""
    transport_id: str = ""

    def display_name(self) -> str:
        parts = [self.serial, self.status]
        if self.model:
            parts.append(self.model)
        elif self.product:
            parts.append(self.product)
        if self.device:
            parts.append(f"({self.device})")
        return " ".join(parts)


class ScrcpyFinder:
    """在常见位置查找 scrcpy 可执行文件。"""

    @staticmethod
    def find() -> Optional[str]:
        # 1. PATH
        exe = shutil.which("scrcpy") or shutil.which("scrcpy.exe")
        if exe:
            return exe

        # 2. 脚本/exe 同目录下的便携版 scrcpy（也检查 PyInstaller onedir 的 _internal）
        script_dir = Path(__file__).resolve().parent
        bundled = sorted((script_dir / "scrcpy").glob("scrcpy-win*/scrcpy.exe"))
        if not bundled:
            bundled = sorted((script_dir / "_internal" / "scrcpy").glob("scrcpy-win*/scrcpy.exe"))

        # 3. 常见安装路径
        home = Path.home()
        candidates = [
            *bundled,
            home / "scoop" / "shims" / "scrcpy.exe",
            home / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages" / "Genymobile.scrcpy",
            Path("C:/") / "Program Files" / "scrcpy" / "scrcpy.exe",
            Path("C:/") / "ProgramData" / "scoop" / "apps" / "scrcpy" / "current" / "scrcpy.exe",
            Path("C:/") / "tools" / "scrcpy" / "scrcpy.exe",
            Path("C:/") / "scrcpy" / "scrcpy.exe",
        ]
        # Winget 安装目录会带版本号，递归找一下
        winget_base = home / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
        if winget_base.exists():
            for p in winget_base.rglob("scrcpy.exe"):
                candidates.append(p)

        for p in candidates:
            if isinstance(p, str):
                p = Path(p)
            if p.exists() and p.is_file():
                return str(p)
        return None


class AdbHelper:
    def __init__(self, adb_path: Optional[str] = None):
        self.adb_path = adb_path or (shutil.which("adb") or shutil.which("adb.exe"))

    def run(self, args: List[str], timeout: int = 10) -> subprocess.CompletedProcess:
        if not self.adb_path:
            raise RuntimeError("找不到 adb，请确认 Android SDK platform-tools 已安装并在 PATH 中。")
        cmd = [self.adb_path] + args
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")

    def list_devices(self) -> List[AdbDevice]:
        result = self.run(["devices", "-l"])
        devices: List[AdbDevice] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            status = parts[1]
            info = {"status": status}
            for item in parts[2:]:
                if ":" in item:
                    k, v = item.split(":", 1)
                    info[k] = v
            devices.append(AdbDevice(
                serial=serial,
                status=status,
                product=info.get("product", ""),
                model=info.get("model", ""),
                device=info.get("device", ""),
                transport_id=info.get("transport_id", ""),
            ))
        return devices


class DualScrcpyApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("双设备 scrcpy 镜像")
        self.root.geometry("680x520" if sys.platform == "win32" else "620x520")
        self.root.minsize(620, 480)

        self.adb = AdbHelper()
        self.scrcpy_path = ScrcpyFinder.find()
        self.processes: List[subprocess.Popen] = []
        self.devices: List[AdbDevice] = []

        self._build_ui()
        self.refresh_devices()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # 工具路径
        path_frame = ttk.LabelFrame(self.root, text="工具路径")
        path_frame.pack(fill="x", **pad)

        ttk.Label(path_frame, text="adb:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self.adb_var = tk.StringVar(value=self.adb.adb_path or "")
        ttk.Entry(path_frame, textvariable=self.adb_var, width=50).grid(row=0, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(path_frame, text="浏览", command=self._browse_adb).grid(row=0, column=2, padx=5, pady=3)

        ttk.Label(path_frame, text="scrcpy:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        self.scrcpy_var = tk.StringVar(value=self.scrcpy_path or "")
        ttk.Entry(path_frame, textvariable=self.scrcpy_var, width=50).grid(row=1, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(path_frame, text="浏览", command=self._browse_scrcpy).grid(row=1, column=2, padx=5, pady=3)

        path_frame.columnconfigure(1, weight=1)

        # 设备选择
        dev_frame = ttk.LabelFrame(self.root, text="设备选择")
        dev_frame.pack(fill="x", **pad)

        ttk.Label(dev_frame, text="设备 A（左/上）:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self.dev_a_var = tk.StringVar()
        self.combo_a = ttk.Combobox(dev_frame, textvariable=self.dev_a_var, state="readonly", width=45)
        self.combo_a.grid(row=0, column=1, sticky="ew", padx=5, pady=3)

        ttk.Label(dev_frame, text="设备 B（右/下）:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        self.dev_b_var = tk.StringVar()
        self.combo_b = ttk.Combobox(dev_frame, textvariable=self.dev_b_var, state="readonly", width=45)
        self.combo_b.grid(row=1, column=1, sticky="ew", padx=5, pady=3)

        ttk.Button(dev_frame, text="刷新设备列表", command=self.refresh_devices).grid(row=0, column=2, rowspan=2, sticky="ns", padx=5, pady=3)
        dev_frame.columnconfigure(1, weight=1)

        # 参数配置
        cfg_frame = ttk.LabelFrame(self.root, text="启动参数")
        cfg_frame.pack(fill="x", **pad)

        ttk.Label(cfg_frame, text="最大尺寸（短边 px, 0=原尺寸）:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self.max_size_var = tk.StringVar(value="1280")
        ttk.Entry(cfg_frame, textvariable=self.max_size_var, width=10).grid(row=0, column=1, sticky="w", padx=5, pady=3)

        ttk.Label(cfg_frame, text="视频码率（Mbps）:").grid(row=0, column=2, sticky="w", padx=5, pady=3)
        self.bitrate_var = tk.StringVar(value="8")
        ttk.Entry(cfg_frame, textvariable=self.bitrate_var, width=10).grid(row=0, column=3, sticky="w", padx=5, pady=3)

        ttk.Label(cfg_frame, text="最大帧率（fps, 0=不限制）:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        self.fps_var = tk.StringVar(value="0")
        ttk.Entry(cfg_frame, textvariable=self.fps_var, width=10).grid(row=1, column=1, sticky="w", padx=5, pady=3)

        self.turn_off_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg_frame, text="启动后关闭设备屏幕", variable=self.turn_off_var).grid(row=1, column=2, columnspan=2, sticky="w", padx=5, pady=3)

        ttk.Label(cfg_frame, text="窗口标题前缀 A:").grid(row=2, column=0, sticky="w", padx=5, pady=3)
        self.title_a_var = tk.StringVar(value="设备A")
        ttk.Entry(cfg_frame, textvariable=self.title_a_var, width=12).grid(row=2, column=1, sticky="w", padx=5, pady=3)

        ttk.Label(cfg_frame, text="窗口标题前缀 B:").grid(row=2, column=2, sticky="w", padx=5, pady=3)
        self.title_b_var = tk.StringVar(value="设备B")
        ttk.Entry(cfg_frame, textvariable=self.title_b_var, width=12).grid(row=2, column=3, sticky="w", padx=5, pady=3)

        self.record_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg_frame, text="同时录屏到文件", variable=self.record_var, command=self._toggle_record_dir).\
            grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=3)
        self.record_dir_var = tk.StringVar(value=str(Path.home() / "Videos"))
        self.record_dir_entry = ttk.Entry(cfg_frame, textvariable=self.record_dir_var, width=35, state="disabled")
        self.record_dir_entry.grid(row=3, column=2, sticky="ew", padx=5, pady=3)
        self.record_dir_btn = ttk.Button(cfg_frame, text="选择目录", command=self._choose_record_dir, state="disabled")
        self.record_dir_btn.grid(row=3, column=3, padx=5, pady=3)

        cfg_frame.columnconfigure(2, weight=1)

        # 操作按钮
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        ttk.Button(btn_frame, text="同时启动两个镜像", command=self.launch_both).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="停止所有 scrcpy", command=self.stop_all).pack(side="left", padx=5)

        # 日志
        log_frame = ttk.LabelFrame(self.root, text="日志")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, wrap="word", state="disabled", height=10)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

    def _browse_adb(self):
        path = filedialog.askopenfilename(title="选择 adb 可执行文件", filetypes=[("adb", "adb.exe adb"), ("All files", "*.*")])
        if path:
            self.adb_var.set(path)
            self.adb = AdbHelper(path)

    def _browse_scrcpy(self):
        path = filedialog.askopenfilename(title="选择 scrcpy 可执行文件", filetypes=[("scrcpy", "scrcpy.exe scrcpy"), ("All files", "*.*")])
        if path:
            self.scrcpy_var.set(path)

    def _toggle_record_dir(self):
        state = "normal" if self.record_var.get() else "disabled"
        self.record_dir_entry.configure(state=state)
        self.record_dir_btn.configure(state=state)

    def _choose_record_dir(self):
        d = filedialog.askdirectory(title="选择录屏保存目录", initialdir=self.record_dir_var.get() or str(Path.home()))
        if d:
            self.record_dir_var.set(d)

    def log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def refresh_devices(self):
        self.adb.adb_path = self.adb_var.get() or self.adb.adb_path
        try:
            self.devices = self.adb.list_devices()
        except Exception as e:
            messagebox.showerror("adb 错误", str(e))
            return

        items = [d.display_name() for d in self.devices]
        values_map = {d.display_name(): d.serial for d in self.devices}

        for combo in (self.combo_a, self.combo_b):
            combo["values"] = items

        # 尽量自动选中前两个可用设备
        available = [d for d in self.devices if d.status == "device"]
        if len(available) >= 1:
            self.combo_a.set(available[0].display_name())
        if len(available) >= 2:
            self.combo_b.set(available[1].display_name())

        self.log(f"检测到 {len(self.devices)} 个设备：")
        for d in self.devices:
            self.log(f"  {d.display_name()}")

    def _get_serial_from_combo(self, combo: ttk.Combobox) -> Optional[str]:
        text = combo.get()
        for d in self.devices:
            if d.display_name() == text:
                return d.serial
        return None

    def _build_scrcpy_args(self, serial: str, title_prefix: str, index: int) -> List[str]:
        exe = self.scrcpy_var.get() or self.scrcpy_path
        if not exe:
            raise RuntimeError("找不到 scrcpy，请手动指定路径。")

        args = [exe, "--serial", serial, "--window-title", f"{title_prefix} [{serial}]"]

        max_size = self.max_size_var.get().strip()
        if max_size and max_size != "0":
            args += ["--max-size", max_size]

        bitrate = self.bitrate_var.get().strip()
        if bitrate and bitrate != "0":
            # scrcpy 接受类似 8M 的格式
            args += ["--video-bit-rate", bitrate + "M"]

        fps = self.fps_var.get().strip()
        if fps and fps != "0":
            args += ["--max-fps", fps]

        if self.turn_off_var.get():
            args.append("--turn-screen-off")

        if self.record_var.get():
            record_dir = Path(self.record_dir_var.get() or str(Path.home() / "Videos"))
            record_dir.mkdir(parents=True, exist_ok=True)
            timestamp = self._timestamp()
            filename = record_dir / f"{title_prefix}_{serial}_{timestamp}.mp4"
            args += ["--record", str(filename)]
            self.log(f"设备 {serial} 将录制到：{filename}")

        # 窗口位置：A 在左，B 在右（Windows 下 --window-x/y 有效）
        if sys.platform == "win32":
            if index == 0:
                args += ["--window-x", "0", "--window-y", "40"]
            else:
                args += ["--window-x", "800", "--window-y", "40"]

        return args

    @staticmethod
    def _timestamp() -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _launch_one(self, serial: str, title_prefix: str, index: int):
        try:
            args = self._build_scrcpy_args(serial, title_prefix, index)
        except Exception as e:
            self.log(f"[错误] 构建参数失败：{e}")
            return

        self.log(f"启动：{' '.join(args)}")
        try:
            # Windows 上需要 CREATE_NEW_CONSOLE 来避免弹窗黑框；也可以直接 Popen
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
            self.processes.append(proc)
            threading.Thread(target=self._watch_process, args=(proc, serial), daemon=True).start()
        except Exception as e:
            self.log(f"[错误] 启动 scrcpy 失败：{e}")

    def _watch_process(self, proc: subprocess.Popen, serial: str):
        try:
            stdout, stderr = proc.communicate(timeout=3)
            if proc.returncode != 0 and proc.returncode is not None:
                err = (stderr or stdout or b"").decode("utf-8", errors="replace")[:500]
                self.log(f"[错误] 设备 {serial} 的 scrcpy 异常退出，代码 {proc.returncode}：{err}")
        except subprocess.TimeoutExpired:
            # 正常运行超过 3 秒，视为成功启动
            self.log(f"设备 {serial} 的 scrcpy 已启动（PID {proc.pid}）")

    def launch_both(self):
        self.scrcpy_path = self.scrcpy_var.get() or self.scrcpy_path
        if not self.scrcpy_path or not Path(self.scrcpy_path).exists():
            messagebox.showerror("找不到 scrcpy", "请手动指定 scrcpy 可执行文件路径。")
            return

        serial_a = self._get_serial_from_combo(self.combo_a)
        serial_b = self._get_serial_from_combo(self.combo_b)

        if not serial_a or not serial_b:
            messagebox.showwarning("未选择设备", "请在下拉框中选择两个设备。")
            return
        if serial_a == serial_b:
            messagebox.showwarning("重复选择", "两个下拉框选择了同一个设备，请改为不同设备。")
            return

        # 清理已结束进程
        self.processes = [p for p in self.processes if p.poll() is None]

        self._launch_one(serial_a, self.title_a_var.get() or "设备A", 0)
        self._launch_one(serial_b, self.title_b_var.get() or "设备B", 1)

    def stop_all(self):
        still_running = []
        for p in self.processes:
            if p.poll() is None:
                try:
                    p.terminate()
                    still_running.append(p)
                except Exception as e:
                    self.log(f"停止进程失败：{e}")
        self.processes = still_running
        self.log("已发送停止信号到所有 scrcpy 进程。")


def main():
    if "--check" in sys.argv:
        print(f"adb:      {AdbHelper().adb_path or 'NOT FOUND'}")
        print(f"scrcpy:   {ScrcpyFinder.find() or 'NOT FOUND'}")
        print(f"script:   {Path(__file__).resolve()}")
        print(f"work dir: {Path.cwd()}")
        return

    root = tk.Tk()
    app = DualScrcpyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
