"""Print meaningful Windows Raw Input events and their source devices."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import time

from raw_input_tracker import RawInputTracker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("mouse_movement_test.log"),
    )
    args = parser.parse_args()

    output = args.output.open("a", encoding="utf-8", buffering=1)

    def emit(message: str) -> None:
        print(message, flush=True)
        print(message, file=output, flush=True)

    tracker = RawInputTracker(record_mouse_events=True)
    tracker.start()
    cumulative_distance = 0.0
    started = time.monotonic()
    emit("")
    emit(f"Test started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    emit(f"Watching Raw Input sources for {args.seconds:.0f} seconds...")
    try:
        while time.monotonic() - started < args.seconds:
            time.sleep(0.1)
            for event in tracker.drain_mouse_events():
                elapsed = time.monotonic() - started
                timestamp = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S.%f")[:-3]
                cumulative_distance += event.cursor_distance
                emit(
                    f"{timestamp} +{elapsed:7.3f}s | "
                    f"raw dx={event.raw_dx:6d} dy={event.raw_dy:6d} "
                    f"buttons=0x{event.button_flags:04x} | "
                    f"cursor={event.cursor_position} moved={event.cursor_distance:7.3f}px "
                    f"total={cumulative_distance:9.3f}px | {event.device}"
                )
    finally:
        tracker.stop()
    emit(
        f"Finished; final idle={tracker.idle_seconds():.1f}s "
        f"cursor travel={cumulative_distance:.1f}px"
    )
    output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
