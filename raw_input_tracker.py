"""Physical keyboard and mouse activity tracking through Windows Raw Input."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import math
import queue
import threading
import time


WM_INPUT = 0x00FF
WM_QUIT = 0x0012
RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIDI_DEVICENAME = 0x20000007
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
USER32.GetRawInputDeviceInfoW.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT),
]
USER32.GetRawInputDeviceInfoW.restype = wintypes.UINT
USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL


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


@dataclass(frozen=True)
class ActivitySnapshot:
    activity_sequence: int
    intentional_sequence: int
    cursor_distance: float
    last_activity: float
    detail: str


@dataclass(frozen=True)
class MouseInputEvent:
    timestamp: float
    raw_dx: int
    raw_dy: int
    button_flags: int
    device: str
    cursor_position: tuple[int, int]
    cursor_distance: float


class RawInputTracker:
    """Tracks time since the last physical keyboard or mouse HID packet."""

    def __init__(self, record_mouse_events: bool = False) -> None:
        self._last_activity = time.monotonic()
        self._last_event = "watcher startup"
        self._activity_sequence = 0
        self._intentional_sequence = 0
        self._cursor_distance = 0.0
        self._cursor_position = self._get_cursor_position()
        self._lock = threading.Lock()
        self._device_names: dict[int, str] = {}
        self._mouse_events: queue.SimpleQueue[MouseInputEvent] | None = (
            queue.SimpleQueue() if record_mouse_events else None
        )
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
            if not USER32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise RuntimeError("Raw Input listener did not stop")

    def idle_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_activity

    def activity_snapshot(self) -> ActivitySnapshot:
        with self._lock:
            return ActivitySnapshot(
                activity_sequence=self._activity_sequence,
                intentional_sequence=self._intentional_sequence,
                cursor_distance=self._cursor_distance,
                last_activity=self._last_activity,
                detail=self._last_event,
            )

    def check_health(self) -> None:
        if self._error:
            raise RuntimeError("Raw Input listener failed") from self._error
        if self._ready.is_set() and not self._thread.is_alive():
            raise RuntimeError("Raw Input listener stopped unexpectedly")

    def drain_mouse_events(self) -> list[MouseInputEvent]:
        if self._mouse_events is None:
            raise RuntimeError("Mouse event recording was not enabled")
        events = []
        while True:
            try:
                events.append(self._mouse_events.get_nowait())
            except queue.Empty:
                return events

    def _handle_message(
        self,
        hwnd: wintypes.HWND,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == WM_INPUT:
            (
                meaningful,
                detail,
                intentional,
                mouse_movement,
                raw_dx,
                raw_dy,
                button_flags,
                device,
            ) = self._read_input(lparam)
            if meaningful:
                now = time.monotonic()
                with self._lock:
                    cursor_distance = 0.0
                    if mouse_movement:
                        position = self._get_cursor_position()
                        cursor_distance = math.dist(self._cursor_position, position)
                        self._cursor_distance += cursor_distance
                        self._cursor_position = position
                    self._last_activity = now
                    self._last_event = detail
                    self._activity_sequence += 1
                    if intentional:
                        self._intentional_sequence += 1
                    if self._mouse_events is not None and device:
                        self._mouse_events.put(
                            MouseInputEvent(
                                timestamp=time.time(),
                                raw_dx=raw_dx,
                                raw_dy=raw_dy,
                                button_flags=button_flags,
                                device=device,
                                cursor_position=self._cursor_position,
                                cursor_distance=cursor_distance,
                            )
                        )
        return USER32.DefWindowProcW(hwnd, message, wparam, lparam)

    @staticmethod
    def _get_cursor_position() -> tuple[int, int]:
        point = wintypes.POINT()
        if not USER32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return point.x, point.y

    def _device_name(self, device_handle: int) -> str:
        handle_value = int(device_handle or 0)
        cached = self._device_names.get(handle_value)
        if cached:
            return cached
        size = wintypes.UINT()
        if USER32.GetRawInputDeviceInfoW(
            device_handle, RIDI_DEVICENAME, None, ctypes.byref(size)
        ) == 0xFFFFFFFF:
            return "unknown device"
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        if USER32.GetRawInputDeviceInfoW(
            device_handle,
            RIDI_DEVICENAME,
            ctypes.cast(buffer, wintypes.LPVOID),
            ctypes.byref(size),
        ) == 0xFFFFFFFF:
            return "unknown device"
        name = buffer.value or "unknown device"
        self._device_names[handle_value] = name
        return name

    def _read_input(
        self, raw_input_handle: int
    ) -> tuple[bool, str, bool, bool, int, int, int, str]:
        size = wintypes.UINT()
        header_size = ctypes.sizeof(RAWINPUTHEADER)
        if USER32.GetRawInputData(
            raw_input_handle, RID_INPUT, None, ctypes.byref(size), header_size
        ) == 0xFFFFFFFF:
            return False, "unreadable Raw Input", False, False, 0, 0, 0, ""

        buffer = ctypes.create_string_buffer(size.value)
        if USER32.GetRawInputData(
            raw_input_handle, RID_INPUT, buffer, ctypes.byref(size), header_size
        ) == 0xFFFFFFFF:
            return False, "unreadable Raw Input", False, False, 0, 0, 0, ""

        raw = ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents
        device = self._device_name(raw.header.hDevice)
        if raw.header.dwType == RIM_TYPEKEYBOARD:
            return True, f"keyboard event from {device}", True, False, 0, 0, 0, ""
        if raw.header.dwType != RIM_TYPEMOUSE:
            return False, f"unsupported Raw Input from {device}", False, False, 0, 0, 0, ""

        mouse = raw.data.mouse
        meaningful = bool(
            mouse.lLastX
            or mouse.lLastY
            or mouse.buttons.data.usButtonFlags
            or mouse.ulRawButtons
        )
        detail = (
            f"mouse event dx={mouse.lLastX} dy={mouse.lLastY} "
            f"buttons=0x{mouse.buttons.data.usButtonFlags:04x} from {device}"
        )
        intentional = bool(mouse.buttons.data.usButtonFlags or mouse.ulRawButtons)
        mouse_movement = bool(mouse.lLastX or mouse.lLastY)
        return (
            meaningful,
            detail,
            intentional,
            mouse_movement,
            mouse.lLastX,
            mouse.lLastY,
            mouse.buttons.data.usButtonFlags,
            device,
        )

    def _message_loop(self) -> None:
        user32 = USER32
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        class_name = "PresenceGuardRawInputWindow"
        window = None

        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
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
