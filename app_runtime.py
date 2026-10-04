"""Resource and subprocess support for source and frozen releases."""
import ctypes
import os
from pathlib import Path
import sys

VERSION = "0.2.1"


def bundle_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def external_environment(base=None):
    env = dict(os.environ if base is None else base)
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        if env.get("LD_LIBRARY_PATH_ORIG"):
            env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
        else:
            env.pop("LD_LIBRARY_PATH", None)
        for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            if str(bundle_root()) in env.get(key, ""):
                env.pop(key, None)
    return env


def initialize_desktop(app):
    # Qt has loaded its plugins before clearing the Windows DLL search override.
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    from PySide6.QtGui import QFont, QFontDatabase, QIcon
    font = bundle_root() / "fonts/NotoSansCJKsc-Regular.otf"
    if font.is_file():
        ident = QFontDatabase.addApplicationFont(str(font))
        families = QFontDatabase.applicationFontFamilies(ident)
        if families:
            app.setFont(QFont(families[0], 10))
    icon = bundle_root() / "packaging/assets/icon.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
