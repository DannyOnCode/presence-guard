"""Low-overhead Windows idle watcher for Presence Guard."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sleep Windows when idle and no person is visible")
    parser.add_argument("--idle-seconds", type=float, default=120)
    parser.add_argument("--recheck-seconds", type=float, default=30)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--heartbeat-seconds", type=float, default=300)
    parser.add_argument("--resume-grace-seconds", type=float, default=120)
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--confidence", type=float, default=0.35)
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

    try:
        while not stopping:
            idle = input_tracker.idle_seconds()
            now = time.monotonic()
            if now >= next_heartbeat:
                logging.info("Heartbeat: Raw Input idle for %.1fs", idle)
                next_heartbeat = now + args.heartbeat_seconds

            if idle >= args.idle_seconds and now >= next_check:
                logging.info("Raw Input idle for %.1fs; checking camera", idle)
                status, detail = detect_person(args)

                # Physical input during camera startup/inference always wins.
                current_idle = input_tracker.idle_seconds()
                if current_idle < args.idle_seconds:
                    logging.info("Physical input resumed during camera check; ignoring result")
                elif status == "present":
                    logging.info("Person detected%s", f": {detail}" if detail else "")
                elif status == "absent":
                    if args.dry_run:
                        logging.warning("DRY RUN: no person detected; would sleep Windows")
                    else:
                        logging.warning("No person detected; sleeping Windows")
                        try:
                            sleep_windows()
                            next_check = time.monotonic() + args.resume_grace_seconds
                            continue
                        except OSError:
                            logging.exception("Windows sleep request failed")
                else:
                    # Fail safe: camera/model errors must never cause sleep.
                    logging.error("Presence check failed; staying awake: %s", detail)

                next_check = time.monotonic() + args.recheck_seconds

            time.sleep(args.poll_seconds)
    finally:
        input_tracker.stop()

    logging.info("Stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
