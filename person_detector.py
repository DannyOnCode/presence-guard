"""Short-lived webcam person detector used by presence_guard.py."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.request


MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)
MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"


def model_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    path = root / "PresenceGuard" / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_model() -> Path:
    directory = model_dir()
    destination = directory / MODEL_FILENAME
    if destination.is_file() and destination.stat().st_size > 0:
        return destination

    temporary = destination.with_suffix(destination.suffix + ".download")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_network(cv2: object, minimum_score: float = 0.5) -> object:
    return cv2.FaceDetectorYN.create(
        str(download_model()), "", (320, 320), minimum_score, 0.3, 5000
    )


def face_detections(
    cv2: object, network: object, frame: object, minimum_score: float
) -> list[tuple[float, tuple[int, int, int, int]]]:
    frame_height, frame_width = frame.shape[:2]
    network.setInputSize((frame_width, frame_height))
    _result, faces = network.detect(frame)
    if faces is None:
        return []

    detections = []
    for face in faces:
        left, top, width, height = face[:4]
        score = float(face[14])
        if score < minimum_score:
            continue

        right = left + width
        bottom = top + height
        detections.append(
            (
                score,
                (
                    max(0, min(frame_width - 1, int(left))),
                    max(0, min(frame_height - 1, int(top))),
                    max(0, min(frame_width - 1, int(right))),
                    max(0, min(frame_height - 1, int(bottom))),
                ),
            )
        )
    return detections


def frame_person_score(
    cv2: object, network: object, frame: object, minimum_score: float = 0.5
) -> float:
    detections = face_detections(cv2, network, frame, minimum_score)
    return max((score for score, _box in detections), default=0.0)


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
    parser.add_argument("--confidence", type=float, default=0.65)
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
        network = load_network(cv2, args.confidence)

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
            best_person_score = 0.0
            max_attempts = args.frames * 3
            while successful_frames < args.frames and attempts < max_attempts:
                attempts += 1
                ok, frame = camera.read()
                if not ok or frame is None:
                    time.sleep(0.1)
                    continue
                successful_frames += 1
                person_score = frame_person_score(cv2, network, frame, args.confidence)
                best_person_score = max(best_person_score, person_score)
                if person_score >= args.confidence:
                    return result(
                        "present",
                        f"face score={person_score:.3f} in frame {successful_frames}",
                        args.result_file,
                    )
                if successful_frames < args.frames:
                    time.sleep(args.frame_interval)

            if successful_frames != args.frames:
                return result(
                    "error",
                    f"camera returned only {successful_frames}/{args.frames} usable frames",
                    args.result_file,
                )
            return result(
                "absent",
                f"best face score={best_person_score:.3f} across {args.frames} frames",
                args.result_file,
            )
        finally:
            camera.release()
    except Exception as error:
        return result("error", f"{type(error).__name__}: {error}", args.result_file)


if __name__ == "__main__":
    raise SystemExit(main())
