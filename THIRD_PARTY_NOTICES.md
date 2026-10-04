# Third-party software in binary releases

The application source is MIT licensed. Bundled components retain their own
licenses. Archives include available upstream license/readme files alongside the
tools. These licenses apply independently of the application source license.

| Component | License | Source / build information |
| --- | --- | --- |
| Python 3.11 | PSF license | https://www.python.org/downloads/source/ |
| PySide6 / Shiboken 6.11.2 and Qt 6.11.2 | LGPLv3 / GPLv3, as applicable | https://download.qt.io/official_releases/QtForPython/pyside6/ and https://download.qt.io/official_releases/qt/6.11/ |
| scrcpy 4.1 | Apache-2.0 | https://github.com/Genymobile/scrcpy/tree/v4.1 |
| Android Debug Bridge | Apache-2.0 and bundled dependencies | https://android.googlesource.com/platform/packages/modules/adb/ |
| FFmpeg 7.1.1 (Windows, Gyan essentials) | GPLv3 | https://github.com/GyanD/codexffmpeg/releases/tag/7.1.1 ; corresponding FFmpeg source: https://github.com/FFmpeg/FFmpeg/tree/db69d06eee ; dependency versions and configure flags are included in the tool README and `ffmpeg -version`. |
| FFmpeg 8.1.3 snapshot (Linux, BtbN GPL) | GPLv3 | https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-09-30-13-08 ; FFmpeg revision: https://github.com/FFmpeg/FFmpeg/tree/29e619e767 ; build scripts and dependency sources: https://github.com/BtbN/FFmpeg-Builds . |
| Noto Sans CJK SC | SIL Open Font License 1.1 | https://github.com/notofonts/noto-cjk/tree/f8d157532fbfaeda587e826d4cd5b21a49186f7c ; bundled `fonts/OFL.txt`. |
| PyInstaller bootloader 6.22.3 | GPLv2 with distribution exception | https://github.com/pyinstaller/pyinstaller/tree/v6.22.3 |

Full license texts: [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html),
[LGPLv3](https://www.gnu.org/licenses/lgpl-3.0.html),
[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0),
[PSF](https://docs.python.org/3.11/license.html).

Qt libraries are unmodified, dynamically loaded libraries. Linux bundles keep them
as separate files under `_internal/PySide6`; Windows one-file releases unpack them
at runtime. To use replacement compatible libraries or rebuild the application,
use the published Python source and `packaging/build_release.py` with your Qt build.
No restrictions on reverse engineering for debugging modifications to LGPL
components are imposed by this application. FFmpeg and scrcpy run as separate
executables. Pinned downloads and hashes are in `packaging/tools-lock.json`.
