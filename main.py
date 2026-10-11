# main.py
"""
AI Air Mouse — Entry point.

Startup sequence (ORDER MATTERS):
  1. Configure logging
  2. Set Windows timer resolution to 1ms
  3. Elevate process to HIGH_PRIORITY_CLASS
  4. Register main thread with MMCSS ("Games" profile)
  5. Build virtual desktop + trackpad zone (DPI awareness already set at display.py import)
  6. Disable Windows pointer acceleration (restore on exit)
  7. Start async camera
  8. Start MediaPipe hand tracker
  9. Instantiate gesture processor + actuator
  10. Run inference loop
  11. On exit: restore timer resolution, restore pointer precision, release all resources

Exit handling:
  - Ctrl+C (KeyboardInterrupt) -> clean shutdown
  - Any unhandled exception -> log traceback, then clean shutdown
  - atexit handler as final safety net to ensure pointer precision is restored
"""

import atexit
import ctypes
import logging
import os
import time
import traceback

import psutil

import cv2
from pynput import keyboard

from config import PROCESS_PRIORITY, TIMER_RESOLUTION_MS, DEBUG_GESTURES
from core.camera import AsyncCamera
from core.tracker import HandTracker, HandsResult
from core.face_tracker import FaceTracker
from core.gestures import GestureOrchestrator
from core.actuator import MouseActuator
from core.display import build_virtual_desktop, build_trackpad_zone
from config import SCREENSHOT_OVERLAY_ENABLED, SCREENSHOT_OVERLAY_POSITION

if SCREENSHOT_OVERLAY_ENABLED:
    from core.overlay import ScreenshotOverlay
else:
    ScreenshotOverlay = None

if DEBUG_GESTURES:
    from core.debug_overlay import draw_debug_frame

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("airmouse.main")


# ── Windows OS Setup ──────────────────────────────────────────────────────────

def _apply_windows_performance() -> dict:
    """
    Apply all Windows OS performance settings.
    Returns a dict of original values so they can be restored on exit.
    """
    original = {}

    # 1. Timer resolution: set to 1ms floor (default is 15.6ms)
    try:
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(TIMER_RESOLUTION_MS)
        original["timer_period"] = TIMER_RESOLUTION_MS
        logger.info("Windows timer resolution set to %dms", TIMER_RESOLUTION_MS)
    except Exception as e:
        logger.warning("Failed to set timer resolution: %s", e)

    # 2. Process priority: HIGH_PRIORITY_CLASS
    try:
        proc = psutil.Process(os.getpid())
        proc.nice(PROCESS_PRIORITY)
        logger.info("Process priority set to HIGH_PRIORITY_CLASS (0x%X)", PROCESS_PRIORITY)
    except psutil.AccessDenied:
        logger.warning("Could not elevate process priority — run as administrator for best performance")

    # 3. MMCSS: register main thread as "Games" workload class
    # This tells the Windows scheduler to give us CPU time more aggressively
    # and deprioritize background tasks (Defender, Windows Update, etc.) relative to us.
    try:
        avrt = ctypes.windll.avrt
        task_index = ctypes.c_ulong(0)
        avrt.AvSetMmThreadCharacteristicsW.restype = ctypes.c_void_p
        handle = avrt.AvSetMmThreadCharacteristicsW("Games", ctypes.byref(task_index))
        if handle:
            original["mmcss_handle"] = handle
            logger.info("MMCSS thread registered as 'Games' (handle=%d)", handle)
        else:
            logger.warning("MMCSS registration returned NULL handle")
    except Exception as e:
        logger.warning("MMCSS registration failed: %s", e)

    # 4. Disable "Enhance Pointer Precision" (Windows mouse acceleration)
    # This ballistic curve applied by Windows corrupts the 1€ Filter output.
    # SPI_GETMOUSE = 3, SPI_SETMOUSE = 4
    # The parameter is [threshold1, threshold2, acceleration]
    # acceleration=0 disables enhance pointer precision
    original_mouse_params = (ctypes.c_int * 3)(0, 0, 0)
    get_result = ctypes.windll.user32.SystemParametersInfoW(3, 0, original_mouse_params, 0)
    if not get_result:
        logger.warning("SystemParametersInfoW(SPI_GETMOUSE) failed — cannot save/restore mouse settings")
    else:
        original["mouse_params"] = list(original_mouse_params)
        no_accel = (ctypes.c_int * 3)(0, 0, 0)
        set_result = ctypes.windll.user32.SystemParametersInfoW(4, 0, no_accel, 2)
        if not set_result:
            logger.warning("SystemParametersInfoW(SPI_SETMOUSE) failed — pointer acceleration may not be disabled")
        else:
            logger.info("Windows pointer acceleration disabled")

    return original


def _restore_windows_settings(original: dict) -> None:
    """Restore all Windows settings mutated during startup."""

    # Restore timer resolution
    if "timer_period" in original:
        ctypes.windll.winmm.timeEndPeriod(original["timer_period"])
        logger.info("Windows timer resolution restored")

    # Unregister MMCSS
    if "mmcss_handle" in original:
        try:
            ctypes.windll.avrt.AvRevertMmThreadCharacteristics(original["mmcss_handle"])
            logger.info("MMCSS thread unregistered")
        except Exception as e:
            logger.warning("MMCSS revert failed: %s", e)

    # Restore pointer precision
    if "mouse_params" in original:
        restored = (ctypes.c_int * 3)(*original["mouse_params"])
        ctypes.windll.user32.SystemParametersInfoW(4, 0, restored, 2)
        logger.info("Windows pointer acceleration restored")


# ── Main Loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    original_settings: dict = {}

    def _emergency_restore():
        """atexit safety net — runs even on unhandled exceptions."""
        if original_settings:
            _restore_windows_settings(original_settings)

    atexit.register(_emergency_restore)

    logger.info("=== AI Air Mouse starting ===")

    # Apply OS settings before anything else
    original_settings.update(_apply_windows_performance())

    # Build display geometry
    desktop = build_virtual_desktop()
    trackpad = build_trackpad_zone()

    # Instantiate subsystems
    actuator = MouseActuator(desktop.total_width, desktop.total_height)

    frame_count = 0
    fps_clock = time.perf_counter()
    none_count = 0
    
    # State for pause/resume
    is_paused = False
    is_running = True
    
    is_tracking_active = False
    has_tracked_once = False
    hand_missing_since = None
    hand_present_since = None

    def on_activate_pause():
        nonlocal is_paused, is_tracking_active, hand_missing_since, hand_present_since
        is_paused = not is_paused
        if is_paused and getattr(actuator, 'is_dragging', False):
            actuator.drag_end()
        logger.info("SYSTEM PAUSED" if is_paused else "SYSTEM RESUMED")
        if overlay:
            overlay.show_pause_toast(is_paused)
        hand_missing_since = None
        hand_present_since = None
        
    def on_activate_quit():
        nonlocal is_running
        is_running = False
        logger.info("Emergency Stop triggered")

    def on_activate_status():
        if overlay and processor:
            r_h = getattr(hands, 'right', None) if 'hands' in locals() and hands else None
            l_h = getattr(hands, 'left', None) if 'hands' in locals() and hands else None
            trk = "Both Hands" if (r_h and l_h) else ("Right Hand" if r_h else ("Left Hand" if l_h else "No Hands"))
            overlay.show_runtime_status({
                "fps": current_fps,
                "owner": processor.pointer_owner,
                "tracking": trk,
                "paused": is_paused,
            })

    hotkey_listener = keyboard.GlobalHotKeys({
        '<ctrl>+<alt>+p': on_activate_pause,
        '<ctrl>+<alt>+q': on_activate_quit,
        '<ctrl>+<alt>+s': on_activate_status,
    })
    hotkey_listener.start()

    with AsyncCamera() as camera, HandTracker() as tracker, FaceTracker() as face_tracker:
        processor = GestureOrchestrator(actuator, desktop, trackpad)

        logger.info("Running. Press Ctrl+Alt+Q to exit, Ctrl+Alt+P to pause, Ctrl+Alt+S for status.")
        
        current_fps = 0.0
        last_frame_time = time.perf_counter()

        last_processed_frame = None
        
        overlay = None
        if ScreenshotOverlay:
            overlay = ScreenshotOverlay(SCREENSHOT_OVERLAY_POSITION)
            overlay.set_audio_controller(processor.audio_controller)
            processor._screenshot.pre_capture_cb = overlay.hide
            processor._screenshot.post_capture_cb = overlay.show
            processor.set_w_activation_callback(lambda success: overlay.show_app_toast("WhatsApp", success))
            processor.set_lip_activation_callback(lambda action, success: overlay.show_audio_toast(action, success))
            processor.set_ear_volume_callback(
                lambda dir_name, vol, active: overlay.show_ear_volume(
                    dir_name, vol, active, is_muted=processor.audio_controller.is_muted
                )
            )
            
        last_face_result = None
        try:
            while is_running:
                frame = camera.read()
                if frame is not None:
                    # Skip if we already processed this exact frame object
                    if frame is last_processed_frame:
                        time.sleep(0.001)
                        continue
                    last_processed_frame = frame
                    frame = cv2.flip(frame, 1)

                if frame is None:
                    # First frames haven't arrived yet — yield briefly
                    none_count += 1
                    if none_count > 5000:  # ~5 seconds of no frames
                        logger.error("Camera produced no frames for ~5 seconds — camera may be disconnected")
                        break
                    time.sleep(0.001)
                    continue

                none_count = 0
                if overlay:
                    overlay.update_state(processor._screenshot, is_paused)
                
                if not is_paused:
                    t_start = time.perf_counter()
                    t1 = t_start
                    hands = tracker.process(frame)

                    # Hand tracking state transition detection
                    has_hands = (hands is not None) and (hands.right is not None or hands.left is not None)
                    now_ts = time.perf_counter()
                    if has_hands:
                        hand_missing_since = None
                        if not is_tracking_active:
                            if hand_present_since is None:
                                hand_present_since = now_ts
                            elif (now_ts - hand_present_since) >= 0.15:
                                is_tracking_active = True
                                if has_tracked_once and overlay:
                                    overlay.show_tracking_toast("REACQUIRED")
                                has_tracked_once = True
                    else:
                        hand_present_since = None
                        if is_tracking_active and has_tracked_once:
                            if hand_missing_since is None:
                                hand_missing_since = now_ts
                            elif (now_ts - hand_missing_since) >= 0.35:
                                is_tracking_active = False
                                if overlay:
                                    overlay.show_tracking_toast("LOST")

                    if face_tracker.is_ready:
                        # Update face landmarks on alternating frames; cache for 1 frame (~33ms)
                        # Re-run immediately if face was missing or not yet detected
                        if frame_count % 2 == 0 or last_face_result is None or not last_face_result.detected:
                            face = face_tracker.process(frame)
                            last_face_result = face
                        else:
                            face = last_face_result
                    else:
                        face = None
                    t2 = time.perf_counter()
                    inference_time_ms = (t2 - t1) * 1000.0
                    processor.process(hands, face)
                else:
                    t_start = time.perf_counter()
                    hands = HandsResult()
                    inference_time_ms = 0.0
                    processor.process(None, None) # reset filter and state

                now = time.perf_counter()
                dt = now - last_frame_time
                last_frame_time = now
                total_time_ms = (now - t_start) * 1000.0
                
                if dt > 0:
                    inst_fps = 1.0 / dt
                    current_fps = 0.9 * current_fps + 0.1 * inst_fps if current_fps > 0 else inst_fps

                if DEBUG_GESTURES:
                    annotated = draw_debug_frame(
                        frame, 
                        hands, 
                        "PAUSED" if is_paused else processor.right_state, 
                        processor.cursor_pos,
                        processor.right_visual_tips,
                        processor.left_visual_tips,
                        inference_time_ms,
                        total_time_ms,
                        current_fps,
                        camera.fps,
                        processor.click_state_text,
                        processor.last_pinch_norm,
                        processor.is_cursor_locked,
                        processor
                    )
                    cv2.imshow("VIRTUAL POINTER", annotated)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break

                # FPS telemetry — log once per 5 seconds
                frame_count += 1
                now = time.perf_counter()
                if now - fps_clock >= 5.0:
                    fps = frame_count / (now - fps_clock)
                    logger.info("FPS: %.1f", fps)
                    frame_count = 0
                    fps_clock = now

        except KeyboardInterrupt:
            logger.info("Ctrl+C received — shutting down cleanly")
        except Exception:
            logger.error("Unhandled exception:\n%s", traceback.format_exc())
        finally:
            if overlay:
                overlay.close()
            if DEBUG_GESTURES:
                cv2.destroyAllWindows()
            # Ensure drag is never left pressed on exit
            if getattr(actuator, 'is_dragging', False):
                actuator.drag_end()
            _restore_windows_settings(original_settings)
            original_settings.clear()  # prevent atexit double-restore
            logger.info("=== AI Air Mouse stopped ===")


if __name__ == "__main__":
    run()
