@echo off
title Presence Guard Mouse Movement Test
echo Presence Guard mouse movement diagnostic
echo.
echo This test records every mouse packet for five minutes.
echo Do not touch the mouse or keyboard during the test.
echo Any Raw Input packets and visible cursor movement will appear below.
echo.
pause
python "%~dp0input_diagnostic.py" --seconds 300 --output "%~dp0mouse_movement_test.log"
echo.
echo Test complete. Results were saved to mouse_movement_test.log.
echo Review the events above, then press any key to close.
pause >nul
