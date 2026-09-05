# Presence Guard

Presence Guard is a lightweight Windows background watcher. After five minutes
without physical keyboard or mouse Raw Input, it briefly opens the webcam and
checks five frames for a face. If no face is found, Windows sleeps immediately.
Software-injected input from games and automation does not renew the timer.
Bags, legs, and other body-only detections do not count as active use.

Camera failures, model errors, timeouts, and input during detection all fail
safe and leave the PC awake. Frames are processed locally and are never saved.

## Install

Python 3.10 or newer is recommended.

```powershell
python -m pip install -r requirements.txt
```

The first camera check downloads the lightweight OpenCV YuNet face model
to `%LOCALAPPDATA%\PresenceGuard\models`. Later checks are fully offline.

## Test safely

Test the camera and detector directly:

```powershell
python person_detector.py
```

The result is `present`, `absent`, or `error`. Next, test the complete watcher
without allowing it to sleep Windows:

```powershell
python presence_guard.py --idle-seconds 10 --dry-run --verbose
```

Logs are stored at `%LOCALAPPDATA%\PresenceGuard\presence_guard.log`.

## Run

### Packaged executable

The ready-to-run Windows build is `dist\PresenceGuard.exe`. It includes Python
and OpenCV, so the target PC does not need Python installed. Double-click it to
run invisibly with the default five-minute threshold, or test it safely from a
terminal first:

```powershell
.\dist\PresenceGuard.exe --idle-seconds 10 --dry-run
```

Because the executable is windowless, inspect activity in
`%LOCALAPPDATA%\PresenceGuard\presence_guard.log`.

### Python source

Run with a console while tuning it:

```powershell
python presence_guard.py --verbose
```

Run invisibly in the background:

```powershell
pythonw presence_guard.py
```

Only one watcher can run at a time. Stop the invisible process from Task
Manager by ending `pythonw.exe`.

Common options:

```text
--idle-seconds 300       Time without input before checking
--recheck-seconds 30     Delay before checking again when you remain idle
--heartbeat-seconds 300  Interval for diagnostic idle-time log entries
--camera 0               Webcam index; try 1 for a second camera
--confidence 0.65        Face-detection confidence threshold
--frames 5               All frames must be person-free before sleep
--dry-run                Never sleep; only write what would happen to the log
```

## Start with Windows

Press `Win+R`, enter `shell:startup`, and add a shortcut whose target is:

```text
"C:\full\path\to\dist\PresenceGuard.exe"
```

Set the shortcut's **Start in** field to this project directory.

## Rebuild the executable

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name PresenceGuard presence_guard.py
```

## Resource design

The watcher itself imports no computer-vision libraries and polls Windows once
every two seconds. OpenCV, the model, and the webcam exist only in a temporary
child process during a presence check, then that process exits. GPU usage is
zero because inference uses OpenCV's CPU backend.

Actual CPU time depends on the processor and camera. Measure both phases on the
target PC rather than relying on estimates from another machine.

## Design considerations

Presence Guard uses Windows sleep rather than sending a monitor-standby
command. During testing, the monitor sometimes woke while a fullscreen game was
running, but the cause was not verified. One possible explanation is that an
application or driver held or renewed a Windows display-power request; another
is mouse, receiver, or other HID noise. The observations do not establish which
software or device was responsible, and no claim is made about any particular
application's implementation.

The computer remained asleep reliably during the same general usage scenario.
That result is consistent with applications being suspended during system
sleep, but it does not prove that an application caused the earlier monitor
wake. Tiny HID events may also wake a monitor without being permitted to wake
the computer.

An active display request can be checked while a game is focused by running
`powercfg /requests` from an elevated terminal.

YuNet is distributed through <https://github.com/opencv/opencv_zoo> under the
Apache License 2.0.
