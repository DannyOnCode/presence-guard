"""Live diagnostic preview for Presence Guard's person detector."""

from __future__ import annotations

import sys
import time

from person_detector import face_detections, load_network


def main() -> int:
    import cv2

    network = load_network(cv2)
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    camera = cv2.VideoCapture(0, backend)
    if not camera.isOpened():
        raise SystemExit("Could not open camera 0")

    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    previous_time = time.perf_counter()
    smoothed_fps = 0.0

    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                continue

            detections = face_detections(cv2, network, frame, minimum_score=0.5)
            now = time.perf_counter()
            current_fps = 1.0 / max(now - previous_time, 0.001)
            smoothed_fps = current_fps if not smoothed_fps else 0.9 * smoothed_fps + 0.1 * current_fps
            previous_time = now

            for score, (left, top, right, bottom) in detections:
                accepted = score >= 0.65
                color = (0, 200, 0) if accepted else (0, 165, 255)
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                cv2.putText(
                    frame,
                    f"face {score:.3f} {'PRESENT' if accepted else 'LOW CONFIDENCE'}",
                    (left, max(22, top - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    color,
                    2,
                    cv2.LINE_AA,
                )

            cv2.putText(
                frame,
                f"YuNet face tracking | {smoothed_fps:.1f} FPS | Q/Esc to close",
                (12, 26),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imshow("Presence Guard - Live Model Tracking", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
