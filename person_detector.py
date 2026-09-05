"""Short-lived webcam person detector used by presence_guard.py."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.request


MODEL_BASE = "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master"
MODEL_FILES = {
    "deploy.prototxt": f"{MODEL_BASE}/deploy.prototxt",
    "mobilenet_iter_73000.caffemodel": f"{MODEL_BASE}/mobilenet_iter_73000.caffemodel",
}
PERSON_CLASS_ID = 15


def model_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    path = root / "PresenceGuard" / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_models() -> tuple[Path, Path]:
    directory = model_dir()
    for filename, url in MODEL_FILES.items():
        destination = directory / filename
        if destination.is_file() and destination.stat().st_size > 0:
            continue
        temporary = destination.with_suffix(destination.suffix + ".download")
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return directory / "deploy.prototxt", directory / "mobilenet_iter_73000.caffemodel"


def frame_has_person(cv2: object, network: object, frame: object, confidence: float) -> bool:
    blob = cv2.dnn.blobFromImage(
        frame,
        scalefactor=0.007843,
        size=(300, 300),
        mean=(127.5, 127.5, 127.5),
        swapRB=False,
        crop=False,
    )
    network.setInput(blob)
    detections = network.forward()
    for index in range(detections.shape[2]):
        class_id = int(detections[0, 0, index, 1])
        score = float(detections[0, 0, index, 2])
        if class_id == PERSON_CLASS_ID and score >= confidence:
            return True
    return False


def result(status: str, detail: str = "", result_file: str | None = None) -> int:
    output = json.dumps({"status": status, "detail": detail})
    if result_file:
        Path(result_file).write_text(output, encoding="utf-8")
    if sys.stdout is not None:
        print(output, flush=True)
    return 0 if status in {"present", "absent"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument("--frame-interval", type=float, default=0.35)
    parser.add_argument("--result-file")
    args = parser.parse_args(argv)

    try:
        import cv2
    except ImportError:
        return result(
            "error",
            "OpenCV is not installed; run: pip install -r requirements.txt",
            args.result_file,
        )

    try:
        prototxt, weights = download_models()
        network = cv2.dnn.readNetFromCaffe(str(prototxt), str(weights))

        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        camera = cv2.VideoCapture(args.camera, backend)
        if not camera.isOpened():
            return result("error", f"could not open camera {args.camera}", args.result_file)

        try:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            # Discard initial frames while the camera adjusts its exposure.
            for _ in range(6):
                camera.read()
                time.sleep(0.08)

            successful_frames = 0
            attempts = 0
            max_attempts = args.frames * 3
            while successful_frames < args.frames and attempts < max_attempts:
                attempts += 1
                ok, frame = camera.read()
                if not ok or frame is None:
                    time.sleep(0.1)
                    continue
                successful_frames += 1
                if frame_has_person(cv2, network, frame, args.confidence):
                    return result(
                        "present", f"detected in frame {successful_frames}", args.result_file
                    )
                if successful_frames < args.frames:
                    time.sleep(args.frame_interval)

            if successful_frames != args.frames:
                return result(
                    "error",
                    f"camera returned only {successful_frames}/{args.frames} usable frames",
                    args.result_file,
                )
            return result("absent", f"checked {args.frames} frames", args.result_file)
        finally:
            camera.release()
    except Exception as error:
        return result("error", f"{type(error).__name__}: {error}", args.result_file)


if __name__ == "__main__":
    raise SystemExit(main())
