多屏录制是一款支持 1～3 台 Android 设备的桌面镜像与录屏软件，适用于手机、Android 眼镜及其他 Android 设备的操作记录、产品演示和培训。

### 下载与使用

- **Windows 免安装**：`MultiScreenRecorder-0.2.1-windows-x64.exe`，下载后双击即可启动。
- **Windows 安装版**：`MultiScreenRecorder-0.2.1-windows-x64-Setup.exe`，安装后从开始菜单启动，支持卸载。
- **Linux 便携版**：`MultiScreenRecorder-0.2.1-linux-x64.tar.gz`，解压后运行 `MultiScreenRecorder`。支持 Ubuntu 22.04+ 或兼容系统，x86_64，X11 桌面。

三个包都已包含 Python、Qt、scrcpy 4.1、adb、FFmpeg/ffprobe 和中文字体。设备需开启 USB 调试并授权；某些 Windows 设备需 USB 驱动，Linux 需配置 USB 访问权限。Windows EXE 尚未代码签名，首次启动会解压内置组件。

### 功能

- 选择 1、2 或 3 台设备，在同一窗口预览和操控。
- 独立开始、暂停、继续、结束录制；最小化或隐藏到托盘后继续采集。
- 每台设备可选择录制其播放声音，Windows 可另加电脑麦克风讲解。
- 分别保存、合成保存或两者都保存，输出 H.264/AAC MP4；合成视频为 1920×1080，可自定义标题和布局。

### 本次修复与验收

- 修复 Windows 打包时混入其他开发工具 DLL 导致 QtCore 加载失败的问题。
- 修复英文 Windows 环境创建中文录屏目录时的编码错误。
- 补齐 Linux 内置媒体工具发现、中文字体和外部进程运行环境。
- Windows 25 项源码检查通过；Linux 17 项通过、8 项 Windows 专属检查跳过。
- 实际 Windows 单文件 EXE、安装后的 EXE 和解压后的 Linux 程序均通过启动、窗口嵌入/正常关闭、无窗口录制收尾、1～3 路分别与合成导出检查；Windows 安装和卸载通过。
- 校验值见 `SHA256SUMS.txt`，下载包检查结果见随附的 `smoke-*.json`。

验证使用受控窗口、模拟画面和测试音。真实手机/眼镜固件的播放音频支持、画质和长时间录制尚需实机验收。Linux 目前可录设备播放声音，电脑麦克风讲解仅支持 Windows。Wayland 请切换到 Xorg 会话使用镜像功能。
