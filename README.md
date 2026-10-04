# scrcpy Dual Viewer

在一个窗口里左右分栏，同时镜像两台 Android 设备（如手机 + AR 眼镜）。scrcpy 画面直接嵌入程序内部，不弹独立窗口；鼠标可直接在画面里点按、滑动操控设备。

## 功能

- 左右两个设备面板，各自独立选设备、启停
- 一键「启动全部 / 停止全部」
- 可调分辨率、码率、帧率、熄屏、同步录屏
- 深色界面，画面随分栏拖拽缩放
- Windows 10/11 与 Linux（X11）

## 界面

主窗口上方是工具栏（刷新设备 / 启动全部 / 停止全部 / 参数设置），中间左右两个设备面板，底部为日志区，状态栏显示 adb / scrcpy 路径。

## 依赖

- [adb](https://developer.android.com/tools/adb)（Android SDK platform-tools，需在 PATH 中）
- [scrcpy](https://github.com/Genymobile/scrcpy)（程序会自动在常见位置查找，也支持放在仓库 `scrcpy/` 目录下的便携版）
- Python 3.10+，PySide6

## 运行

```bash
pip install PySide6
python dual_scrcpy_qt.py
```

两台设备插线并开启 USB 调试后，点「刷新设备」，程序自动把前两台设备分配到 A / B，点「启动全部」。

### Linux 注意

- 需要 **X11 会话**（`echo $XDG_SESSION_TYPE` 输出 `x11`）。Wayland 协议不允许跨进程窗口嵌入，无法使用。
- 安装依赖：`sudo apt install adb scrcpy` + `pip install PySide6`

## 打包 Windows exe

```bash
pip install pyinstaller
# 先把 scrcpy-win64-vX.Y.zip 解压到仓库 scrcpy/ 目录
pyinstaller --noconfirm --onedir --windowed --name dual_mirror --add-data "scrcpy;scrcpy" dual_scrcpy_qt.py
```

产物在 `dist/dual_mirror/`，整个文件夹可移植（scrcpy 已内置）。

Linux 下打包把 `--add-data "scrcpy;scrcpy"` 的分号改成冒号 `"scrcpy:scrcpy"`。

## 旧版

`dual_scrcpy.py` 是早期的 tkinter 版本：启动两个独立 scrcpy 窗口，零第三方依赖（tkinter 为 Python 自带）。只需要并排弹窗、不想装 PySide6 时可用。

## 已知限制

- 嵌入画面里鼠标操控正常；键盘输入受 Qt 嵌入外部窗口的焦点限制，部分场景不响应（需要打字时可临时用独立 scrcpy 窗口）
- Linux Wayland 不支持嵌入

## License

MIT
