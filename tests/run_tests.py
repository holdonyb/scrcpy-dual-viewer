"""Run the suite and dispose Qt before Python destroys signal callbacks."""
import faulthandler
import gc
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
faulthandler.enable()
sys.path.insert(0, str(ROOT))
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
import shiboken6
app = QApplication.instance()
if app:
    app.closeAllWindows()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    del suite
    print("Disposing test callbacks before QApplication", flush=True)
    gc.collect()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    shiboken6.delete(app)
    print("QApplication disposed", flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
