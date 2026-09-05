"""Physical keyboard and mouse activity tracking through Windows Raw Input."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time


WM_INPUT = 0x00FF
WM_QUIT = 0x0012
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02
HID_USAGE_GENERIC_KEYBOARD = 0x06
HWND_MESSAGE = -3
LRESULT = ctypes.c_ssize_t


WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

USER32 = ctypes.WinDLL("user32", use_last_error=True)
USER32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
USER32.DefWindowProcW.restype = LRESULT
USER32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
USER32.GetRawInputData.restype = wintypes.UINT


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWMOUSEBUTTONDATA(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
    ]


class RAWMOUSEBUTTONS(ctypes.Union):
    _fields_ = [
        ("ulButtons", wintypes.ULONG),
        ("data", RAWMOUSEBUTTONDATA),
    ]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("buttons", RAWMOUSEBUTTONS),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWINPUTDATA(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", RAWINPUTDATA)]


class RawInputTracker:
    """Tracks time since the last physical keyboard or mouse HID packet."""

    def __init__(self) -> None:
        self._last_activity = time.monotonic()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread_id = 0
        self._window_proc = WNDPROC(self._handle_message)
        self._thread = threading.Thread(target=self._message_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Raw Input listener did not start")
        if self._error:
            raise RuntimeError("Raw Input listener failed") from self._error

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=2)

    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_activity

    def _handle_message(
        self,
        hwnd: wintypes.HWND,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == WM_INPUT and self._is_meaningful_input(lparam):
            self._last_activity = time.monotonic()
        return USER32.DefWindowProcW(hwnd, message, wparam, lparam)

    @staticmethod
    def _is_meaningful_input(raw_input_handle: int) -> bool:
        size = wintypes.UINT()
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        if USER32.GetRawInputData(
            raw_input_handle, RID_INPUT, None, ctypes.byref(size), header_size
        ) == 0xFFFFFFFF:
            return False

        buffer = ctypes.create_string_buffer(size.value)
        if USER32.GetRawInputData(
            raw_input_handle, RID_INPUT, buffer, ctypes.byref(size), header_size
        ) == 0xFFFFFFFF:
            return False

        raw = ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents
        if raw.header.dwType == RIM_TYPEKEYBOARD:
            return True
        if raw.header.dwType != RIM_TYPEMOUSE:
            return False

        mouse = raw.data.mouse
        return bool(
            mouse.lLastX
            or mouse.lLastY
            or mouse.buttons.data.usButtonFlags
            or mouse.ulRawButtons
        )

    def _message_loop(self) -> None:
        user32 = USER32
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        class_name = "PresenceGuardRawInputWindow"
        window = None

        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            instance = kernel32.GetModuleHandleW(None)

            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.ATOM
            window_class = WNDCLASSW(
                lpfnWndProc=self._window_proc,
                hInstance=instance,
                lpszClassName=class_name,
            )
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())

            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                wintypes.LPVOID,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND
            window = user32.CreateWindowExW(
                0,
                class_name,
                "",
                0,
                0,
                0,
                0,
                0,
                wintypes.HWND(HWND_MESSAGE),
                None,
                instance,
                None,
            )
            if not window:
                raise ctypes.WinError(ctypes.get_last_error())

            devices = (RAWINPUTDEVICE * 2)(
                RAWINPUTDEVICE(
                    HID_USAGE_PAGE_GENERIC,
                    HID_USAGE_GENERIC_MOUSE,
                    RIDEV_INPUTSINK,
                    window,
                ),
                RAWINPUTDEVICE(
                    HID_USAGE_PAGE_GENERIC,
                    HID_USAGE_GENERIC_KEYBOARD,
                    RIDEV_INPUTSINK,
                    window,
                ),
            )
            if not user32.RegisterRawInputDevices(
                devices, len(devices), ctypes.sizeof(RAWINPUTDEVICE)
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            self._ready.set()
            message = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as error:
            self._error = error
            self._ready.set()
        finally:
            if window:
                user32.DestroyWindow(window)
