"""Print meaningful Windows Raw Input events and their source devices."""

from __future__ import annotations

import argparse
import time

from raw_input_tracker import RawInputTracker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90)
    args = parser.parse_args()

    tracker = RawInputTracker()
    tracker.start()
    previous = tracker.last_event()
    started = time.monotonic()
    print(f"Watching Raw Input sources for {args.seconds:.0f} seconds...", flush=True)
    try:
        while time.monotonic() - started < args.seconds:
            time.sleep(0.1)
            current = tracker.last_event()
            if current != previous:
                elapsed = time.monotonic() - started
                print(f"{elapsed:5.1f}s | {current}", flush=True)
                previous = current
    finally:
        tracker.stop()
    print(f"Finished; final idle={tracker.idle_seconds():.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
