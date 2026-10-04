"""Validate WM_CLOSE -> SDL_QUIT using scrcpy's actual bundled SDL3 DLL."""
import argparse
import ctypes
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument("--dll", required=True)
parser.add_argument("--title", required=True)
parser.add_argument("--marker", required=True)
args = parser.parse_args()

lib = ctypes.CDLL(args.dll)
lib.SDL_Init.argtypes = [ctypes.c_uint32]
lib.SDL_Init.restype = ctypes.c_bool
lib.SDL_CreateWindow.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint64]
lib.SDL_CreateWindow.restype = ctypes.c_void_p
lib.SDL_PollEvent.argtypes = [ctypes.c_void_p]
lib.SDL_PollEvent.restype = ctypes.c_bool
lib.SDL_DestroyWindow.argtypes = [ctypes.c_void_p]
lib.SDL_DestroyWindow.restype = None
lib.SDL_Quit.argtypes = []
lib.SDL_Quit.restype = None
lib.SDL_GetError.argtypes = []
lib.SDL_GetError.restype = ctypes.c_char_p

if not lib.SDL_Init(0x20):  # SDL_INIT_VIDEO
    raise RuntimeError(lib.SDL_GetError().decode())
window = lib.SDL_CreateWindow(args.title.encode(), 360, 480, 0)
if not window:
    lib.SDL_Quit()
    raise RuntimeError(lib.SDL_GetError().decode())

try:
    # SDL_Event has 128 bytes of padding; a uint64 array also ensures alignment.
    event = (ctypes.c_uint64 * 16)()
    deadline = time.monotonic() + 15
    quit_received = False
    while time.monotonic() < deadline and not quit_received:
        while lib.SDL_PollEvent(ctypes.byref(event)):
            if ctypes.cast(ctypes.byref(event), ctypes.POINTER(ctypes.c_uint32))[0] == 0x100:
                quit_received = True  # SDL_EVENT_QUIT
                Path(args.marker).write_text("SDL_QUIT received", encoding="utf-8")
        time.sleep(0.01)
    if not quit_received:
        raise RuntimeError("SDL did not receive a quit event")
finally:
    lib.SDL_DestroyWindow(window)
    lib.SDL_Quit()
