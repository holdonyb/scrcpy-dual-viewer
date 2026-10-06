# 验证说明

## 0.2.2 麦克风选择与采集隔离

2026-10-06，Windows 11 本地 29 项源码检查通过，进程退出码 0。使用 `tests/run_tests.py`，采集测试显式结束工作线程并销毁无父对象的 Recorder。测试入口启用原生崩溃日志；清理 Qt 使用 PySide 的 `QApplication.shutdown()`，按照绑定对象生命周期关闭，避免直接销毁应用对象时发生测试退出崩溃。依据见 [Qt for Python 测试关闭说明](https://doc.qt.io/qtforpython-6/considerations.html#testing-support)。

- 从 DirectShow 枚举输出读取显示名称和 Alternative name，覆盖中文、同名输入、名称含引号、摄像头附带音频和无音频输入的过滤。
- 下拉框支持实际输入选择；刷新时按标识保持选择，名称变化保留原设备，同名输入分别显示，设备消失后多次刷新也不自动换到另一个输入。
- 用两路模拟设备复现麦克风不存在、启动失败、打开失败、录制中退出和停止时输入管道中断；健康设备继续采集，提示讲解停止，分别视频和合成视频均有画面及设备测试音。
- 打开麦克风失败后暂停/继续不会再次启动失败输入；有效的既有讲解片段保留，无效麦克风文件不进入导出。
- 实际发布包自检新增 `optional_microphone_failure`，使用真实 FFmpeg 模拟两个采集进程和失败的音频输入，验证 Recorder 持续运行并保存三个 MP4；不打开真实麦克风。

发布构建中的 Windows 29 项检查全部通过；Linux 20 项通过、9 项 Windows 专属检查跳过，两平台测试进程均正常退出。实际免安装 EXE、安装后的 EXE 和解压后的 Linux 可执行文件均通过全部自检，包括 `optional_microphone_failure=true`、1～3 路分别与合成导出；Windows 安装和卸载通过。记录见 [0.2.2 发布构建](https://github.com/holdonyb/scrcpy-dual-viewer/actions/runs/37475718702)。各包的校验值和实际程序报告随 [v0.2.2](https://github.com/holdonyb/scrcpy-dual-viewer/releases/tag/v0.2.2) 提供。

名称匹配依据 [FFmpeg DirectShow 文档](https://ffmpeg.org/ffmpeg-devices.html#dshow)及 [FFmpeg 7.1.1 源码](https://github.com/FFmpeg/FFmpeg/blob/n7.1.1/libavdevice/dshow.c)：输入可用显示名称或 Alternative name，名称匹配按原字符串比较。用户电脑的具体麦克风可用性和长时间双设备录制仍需实机确认。

## 0.2.1 发布验收记录

2026-10-04，版本 0.2.1 完成 Windows 11 本地验证和 Windows / Ubuntu 22.04 发布包验收。打包使用 Python 3.11、PySide6 6.11.2、scrcpy 4.1；Windows 内置 Gyan FFmpeg 7.1.1 essentials，Linux 内置 BtbN FFmpeg 8.1.3 快照。

```powershell
.\.venv\Scripts\python.exe tests/run_tests.py
```

本地 25 项检查通过；改用英文 Windows 区域设置后重复运行也通过，进程退出码 0。发布构建中 Windows 25 项通过（31.251 秒）；Linux 17 项通过、8 项 Windows 专属检查跳过（6.105 秒）。完整记录见 [成功的发布构建](https://github.com/holdonyb/scrcpy-dual-viewer/actions/runs/37195731656)。[旧版测试输出](reviews/2026-10-04/regression-results.txt) 保留作 0.2.0 历史记录。

## 实际下载包验收

- Windows 单文件 EXE 实际启动成功，使用包内 scrcpy/adb/FFmpeg/ffprobe 和 Noto 中文字体。
- Windows 安装包完成静默安装，安装后的 EXE 通过同样检查，卸载进程正常结束。
- Linux tar.gz 在 Ubuntu 22.04 解压后，直接启动包内可执行文件，在 Xvfb 的 X11 环境检查界面与原生子窗口嵌入、正常关闭。
- 三种可执行程序均完成真实 FFmpeg 无窗口采集、正常停止和 MP4 文件收尾，以及 1、2、3 路分别/合成导出；检查 H.264 编码、音轨、1920×1080 合成及两个暂停片段的拼接时长。
- 包含构建时防止混入开发工具 DLL 的修复，及英文系统下创建中文录屏目录的修复。
- 发布附件 `SHA256SUMS.txt` 提供文件校验，`smoke-windows.json`、`smoke-windows-installed.json`、`smoke-linux.json` 保存实际二进制检查结果。自检使用模拟输入，不控制设备或采集麦克风。

## 验证内容

- 便携工具发现、设备授权与离线提示、设备分配保留、单设备使用、无线设备安全文件名、参数校验和保存。
- Windows 原生窗口嵌入、鼠标点击传递、异步关闭，以及 scrcpy 包内实际 SDL3 窗口的正常关闭。
- 独立无窗口采集、隐藏控制台停止信号、MP4 正常收尾、暂停和继续、后台运行、保存后退出。
- 1、2、3 路分别与合成输出、H.264 编码、1920×1080 合成、输出时长、旋转和无音轨视频。
- 设备声音选择与电脑讲解混合：用合成测试音确认未选择的声音不混入、讲解在合成中只混一次。
- 取消准备、采集意外断开、导出失败提示与片段保留。

## 验证范围

本次使用合成设备、受控测试窗口、FFmpeg 实际媒体文件和测试音，没有录制真实手机或电脑麦克风。截图和合成示例中的模拟画面有明确标注。

真实手机和眼镜的设备播放声音、画质、延迟、旋转、并发编码能力和长时间录制仍需连接设备验证。Linux X11 的验收使用虚拟显示环境；电脑麦克风讲解目前仅支持 Windows。

部分检查依赖 Windows、FFmpeg/ffprobe 或 scrcpy 的 SDL3.dll；缺少相关环境时 unittest 会跳过对应项，运行结果应同时检查跳过数量。
