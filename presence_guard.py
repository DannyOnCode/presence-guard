"""Low-overhead Windows idle watcher for Presence Guard."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from raw_input_tracker import RawInputTracker


APP_NAME = "PresenceGuard"
ERROR_ALREADY_EXISTS = 183
HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
MONITOR_OFF = 2
SMTO_ABORTIFHUNG = 0x0002


@dataclass
class MonitorOffState:
    sleep_deadline: float
    activity_sequence: int
    intentional_sequence: int
    cursor_distance: float
    movement_pending: bool = False


def app_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            app_data_dir() / "presence_guard.log",
            maxBytes=512_000,
            backupCount=2,
            encoding="utf-8",
        )
    ]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def ensure_single_instance() -> object:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, "Local\\PresenceGuardWatcher")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        raise RuntimeError("Presence Guard is already running")
    return handle


def detect_person(args: argparse.Namespace) -> tuple[str, str]:
    frozen = bool(getattr(sys, "frozen", False))
    result_path: Path | None = None
    if frozen:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="detection-", suffix=".json", dir=app_data_dir()
        )
        os.close(file_descriptor)
        result_path = Path(temporary_name)
        command = [sys.executable, "--detect-person"]
    else:
        detector = Path(__file__).with_name("person_detector.py")
        command = [sys.executable, str(detector)]

    command.extend([
        "--camera",
        str(args.camera),
        "--confidence",
        str(args.confidence),
        "--frames",
        str(args.frames),
        "--frame-interval",
        str(args.frame_interval),
    ])
    if result_path:
        command.extend(["--result-file", str(result_path)])

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=args.detection_timeout,
                creationflags=creation_flags,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return "error", "person detector timed out"

        output = ""
        try:
            output = result_path.read_text(encoding="utf-8") if result_path else completed.stdout
            payload = json.loads(output.strip())
            return payload["status"], payload.get("detail", "")
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            stderr = completed.stderr.strip() if completed.stderr else ""
            detail = stderr or output.strip() or f"exit code {completed.returncode}"
            return "error", detail
    finally:
        if result_path:
            result_path.unlink(missing_ok=True)


def sleep_windows() -> None:
    # Hibernate=False, ForceCritical=False, DisableWakeEvent=False.
    if not ctypes.windll.powrprof.SetSuspendState(False, False, False):
        raise ctypes.WinError()


def turn_off_monitors() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    if user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        WM_SYSCOMMAND,
        SC_MONITORPOWER,
        MONITOR_OFF,
        SMTO_ABORTIFHUNG,
        2_000,
        ctypes.byref(result),
    ):
        return

    # A hung top-level window can make the broadcast time out even when other
    # recipients handled it. Queue a fallback rather than abandoning standby.
    if not user32.PostMessageW(
        HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Turn off monitors, then sleep Windows when idle and no face is visible"
    )
    parser.add_argument("--idle-seconds", type=float, default=180)
    parser.add_argument("--recheck-seconds", type=float, default=30)
    parser.add_argument("--sleep-delay-seconds", type=float, default=600)
    parser.add_argument("--cursor-distance", type=float, default=40)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--heartbeat-seconds", type=float, default=300)
    parser.add_argument("--resume-grace-seconds", type=float, default=120)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--confidence", type=float, default=0.65)
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument("--frame-interval", type=float, default=0.35)
    parser.add_argument("--detection-timeout", type=float, default=30)
    parser.add_argument("--dry-run", action="store_true", help="Log instead of sleeping")
    parser.add_argument("--verbose", action="store_true", help="Also log to the console")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    positive = (
        "idle_seconds",
        "recheck_seconds",
        "sleep_delay_seconds",
        "cursor_distance",
        "poll_seconds",
        "heartbeat_seconds",
        "resume_grace_seconds",
        "frames",
        "detection_timeout",
    )
    if any(getattr(args, name) <= 0 for name in positive):
        raise SystemExit("Timing values and --frames must be greater than zero")
    if not 0 < args.confidence <= 1:
        raise SystemExit("--confidence must be greater than 0 and at most 1")
    if args.frame_interval < 0:
        raise SystemExit("--frame-interval cannot be negative")


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Presence Guard currently supports Windows only")

    if "--detect-person" in sys.argv:
        detector_args = sys.argv[1:]
        detector_args.remove("--detect-person")
        import person_detector

        return person_detector.main(detector_args)

    args = build_parser().parse_args()
    validate_args(args)
    configure_logging(args.verbose)
    mutex = ensure_single_instance()
    del mutex  # The kernel handle remains valid for the life of this process.
    input_tracker = RawInputTracker()
    input_tracker.start()

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    logging.info(
        "Started with Raw Input: idle threshold=%ss, camera=%s, dry_run=%s",
        args.idle_seconds,
        args.camera,
        args.dry_run,
    )
    next_check = 0.0
    next_heartbeat = time.monotonic() + args.heartbeat_seconds
    monitor_off: MonitorOffState | None = None

    try:
        while not stopping:
            try:
                input_tracker.check_health()
            except RuntimeError:
                logging.exception("Raw Input listener failed; disabling power actions")
                break

            snapshot = input_tracker.activity_snapshot()
            now = time.monotonic()
            idle = now - snapshot.last_activity
            if now >= next_heartbeat:
                logging.info("Heartbeat: Raw Input idle for %.1fs", idle)
                next_heartbeat = now + args.heartbeat_seconds

            if monitor_off is not None:
                cursor_travel = snapshot.cursor_distance - monitor_off.cursor_distance
                if snapshot.intentional_sequence != monitor_off.intentional_sequence:
                    logging.info(
                        "Keyboard/button input resumed; delayed sleep cancelled: %s",
                        snapshot.detail,
                    )
                    monitor_off = None
                    next_check = 0.0
                elif snapshot.activity_sequence != monitor_off.activity_sequence:
                    monitor_off.activity_sequence = snapshot.activity_sequence
                    monitor_off.movement_pending = True
                    if cursor_travel >= args.cursor_distance:
                        logging.info(
                            "Cursor traveled %.1fpx; delayed sleep cancelled: %s",
                            cursor_travel,
                            snapshot.detail,
                        )
                        monitor_off = None
                        next_check = 0.0
                elif monitor_off.movement_pending and now - snapshot.last_activity >= 1.0:
                    logging.info(
                        "Cursor movement settled at %.1fpx below threshold; preserving delayed sleep",
                        cursor_travel,
                    )
                    if not args.dry_run:
                        try:
                            turn_off_monitors()
                        except OSError:
                            logging.exception("Monitor standby request failed")
                    monitor_off.cursor_distance = snapshot.cursor_distance
                    monitor_off.movement_pending = False
                elif now >= monitor_off.sleep_deadline:
                    # Do not suspend if an event arrived after this loop's snapshot.
                    final_snapshot = input_tracker.activity_snapshot()
                    if final_snapshot.activity_sequence != snapshot.activity_sequence:
                        continue
                    if args.dry_run:
                        logging.warning("DRY RUN: delayed sleep expired; would sleep Windows")
                        monitor_off = None
                        next_check = now + args.recheck_seconds
                    else:
                        logging.warning("No physical input during monitor-off delay; sleeping Windows")
                        try:
                            sleep_windows()
                            monitor_off = None
                            next_check = time.monotonic() + args.resume_grace_seconds
                            continue
                        except OSError:
                            logging.exception("Windows sleep request failed")
                            monitor_off = None
                            next_check = now + args.recheck_seconds
            elif idle >= args.idle_seconds and now >= next_check:
                logging.info("Raw Input idle for %.1fs; checking camera", idle)
                check_sequence = snapshot.activity_sequence
                status, detail = detect_person(args)

                # Physical input during camera startup/inference always wins.
                post_check = input_tracker.activity_snapshot()
                if post_check.activity_sequence != check_sequence:
                    logging.info("Physical input resumed during camera check; ignoring result")
                elif status == "present":
                    logging.info("Person detected%s", f": {detail}" if detail else "")
                elif status == "absent":
                    monitor_off = MonitorOffState(
                        sleep_deadline=time.monotonic() + args.sleep_delay_seconds,
                        activity_sequence=post_check.activity_sequence,
                        intentional_sequence=post_check.intentional_sequence,
                        cursor_distance=post_check.cursor_distance,
                    )
                    if args.dry_run:
                        logging.warning("DRY RUN: no face detected; would turn off monitors")
                    else:
                        logging.warning("No face detected; turning off monitors")
                        try:
                            turn_off_monitors()
                        except OSError:
                            logging.exception("Monitor standby request failed; staying awake")
                            monitor_off = None
                            next_check = time.monotonic() + args.recheck_seconds
                            continue
                    logging.info(
                        "Waiting %.0fs for physical input before sleep",
                        args.sleep_delay_seconds,
                    )
                else:
                    # Fail safe: camera/model errors must never cause sleep.
                    logging.error("Presence check failed; staying awake: %s", detail)

                next_check = time.monotonic() + args.recheck_seconds

            time.sleep(min(args.poll_seconds, 0.1) if monitor_off is not None else args.poll_seconds)
    finally:
        input_tracker.stop()

    logging.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
