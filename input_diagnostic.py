"""Print meaningful Windows Raw Input events and their source devices."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import math
import time

from raw_input_tracker import RawInputTracker


HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170


def cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError()
    return point.x, point.y


def set_monitor_power(state: int) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    if not user32.PostMessageW(
        wintypes.HWND(HWND_BROADCAST), WM_SYSCOMMAND, SC_MONITORPOWER, state
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--monitor-off", action="store_true")
    args = parser.parse_args()

    tracker = RawInputTracker()
    tracker.start()
    previous_sequence, _detail, _intentional = tracker.activity_snapshot()
    previous_position = cursor_position()
    cumulative_distance = 0.0
    started = time.monotonic()
    print(f"Watching Raw Input sources for {args.seconds:.0f} seconds...", flush=True)
    if args.monitor_off:
        print("Turning monitors off for the measurement...", flush=True)
        set_monitor_power(2)
    try:
        while time.monotonic() - started < args.seconds:
            time.sleep(0.1)
            sequence, detail, intentional = tracker.activity_snapshot()
            if sequence != previous_sequence:
                elapsed = time.monotonic() - started
                position = cursor_position()
                distance = math.dist(previous_position, position)
                cumulative_distance += distance
                skipped = sequence - previous_sequence - 1
                print(
                    f"{elapsed:5.1f}s | cursor={position} moved={distance:6.1f}px "
                    f"total={cumulative_distance:7.1f}px skipped={skipped} "
                    f"intentional={intentional} | {detail}",
                    flush=True,
                )
                previous_sequence = sequence
                previous_position = position
    finally:
        tracker.stop()
        if args.monitor_off:
            set_monitor_power(-1)
    print(
        f"Finished; final idle={tracker.idle_seconds():.1f}s "
        f"cursor travel={cumulative_distance:.1f}px",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
