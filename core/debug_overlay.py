# core/debug_overlay.py
"""
Futuristic JARVIS-inspired UI overlay.
Renders matte black/charcoal backgrounds, electric-blue/cyan highlights,
and a precise 2D hand skeleton over the live webcam feed.
"""

import cv2
import numpy as np
import math

from core.tracker import HandsResult, Landmark

# Standard MediaPipe hand skeleton connections
_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),          # index
    (5, 9), (9, 10), (10, 11), (11, 12),     # middle
    (9, 13), (13, 14), (14, 15), (15, 16),   # ring
    (13, 17), (17, 18), (18, 19), (19, 20),  # pinky
    (0, 17), (5, 9), (9, 13), (13, 17),      # palm
]

# Color palette (BGR format for OpenCV)
_CYAN   = (255, 230, 0)      # Electric Cyan
_BLUE   = (255, 150, 0)      # Deep Electric Blue
_EMERALD = (50, 230, 80)     # Vibrant Emerald Green (Left Pointer Ring)
_WHITE  = (240, 240, 240)    # Off-white
_GRAY   = (100, 100, 100)    # Subdued gray
_ALERT  = (50, 50, 255)      # Red for missing
_BG     = (25, 25, 25)       # Deep charcoal

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _lm_px(lm: Landmark, w: int, h: int) -> tuple[int, int]:
    return int(lm.x * w), int(lm.y * h)


def _draw_hand(img: np.ndarray, lms: list[Landmark], color: tuple, label: str) -> None:
    h, w = img.shape[:2]
    # Draw thin, precise 2D tracking lines
    for a, b in _CONNECTIONS:
        cv2.line(img, _lm_px(lms[a], w, h), _lm_px(lms[b], w, h), color, 1, cv2.LINE_AA)
    # Draw small subtle joints
    for lm in lms:
        cv2.circle(img, _lm_px(lm, w, h), 3, color, -1, cv2.LINE_AA)
    
    # Label at wrist
    wx, wy = _lm_px(lms[0], w, h)
    cv2.putText(img, label, (wx + 15, wy), _FONT, 0.4, color, 1, cv2.LINE_AA)


def draw_debug_frame(
    frame: np.ndarray,
    hands: HandsResult,
    right_state: str,
    cursor_pos: tuple[int, int] | None = None,
    right_visual_tips: dict[int, tuple[float, float, float]] | None = None,
    left_visual_tips: dict[int, tuple[float, float, float]] | None = None,
    inference_time_ms: float = 0.0,
    total_time_ms: float = 0.0,
    fps: float = 0.0,
    cam_fps: float = 0.0,
    click_state_text: str = "DISABLED",
    last_pinch_norm: float = 0.0,
    is_cursor_locked: bool = False,
    orchestrator = None
) -> np.ndarray:
    """
    Returns a copy of frame with futuristic UI overlay.
    """
    from config import POINTER_ONLY_MODE, PINCH_CLICK_ENABLED
    out = frame.copy()
    h, w = out.shape[:2]

    # Dim the raw webcam feed slightly for better UI contrast
    out = cv2.addWeighted(out, 0.7, np.zeros_like(out), 0.3, 0)

    # Hand Skeletons
    if hands.right is not None:
        _draw_hand(out, hands.right, _CYAN, "RIGHT_HAND [SYS_PRIMARY]")
        
        if right_visual_tips:
            for tip_idx, (vx, vy, vz) in right_visual_tips.items():
                px = (int(vx * w), int(vy * h))
                
                # Highlight thumb and index if PINCH DETECTED
                is_pinching = click_state_text in ("PINCH DETECTED", "DOUBLE-CLICK CANDIDATE", "CLICK PENDING", "SINGLE CLICK", "DOUBLE CLICK", "WAITING FOR RELEASE", "RELEASE TO CLICK") and (tip_idx in (4, 8))
                
                # Highlight index and middle if ZOOM DETECTED
                is_zooming = "ZOOM" in click_state_text and (tip_idx in (8, 12))
                
                circle_color = _WHITE if is_pinching or is_zooming else _BLUE
                
                # Solid electric-blue circle (or white if pinched)
                cv2.circle(out, px, 7, circle_color, -1, cv2.LINE_AA)
                # Subtle dark outline
                cv2.circle(out, px, 7, _BG, 2, cv2.LINE_AA)
                
                # If this is the index finger (8), draw a tracking reticle around it
                if tip_idx == 8:
                    if is_cursor_locked:
                        cv2.circle(out, px, 14, _WHITE, 1, cv2.LINE_AA)
                        cv2.line(out, (px[0] - 18, px[1]), (px[0] + 18, px[1]), _WHITE, 1, cv2.LINE_AA)
                        cv2.line(out, (px[0], px[1] - 18), (px[0], px[1] + 18), _WHITE, 1, cv2.LINE_AA)
                    else:
                        cv2.circle(out, px, 14, _CYAN, 1, cv2.LINE_AA)
                        cv2.line(out, (px[0] - 18, px[1]), (px[0] + 18, px[1]), _CYAN, 1, cv2.LINE_AA)
                        cv2.line(out, (px[0], px[1] - 18), (px[0], px[1] + 18), _CYAN, 1, cv2.LINE_AA)
    
    if hands.left is not None and not POINTER_ONLY_MODE:
        _draw_hand(out, hands.left, _BLUE, "LEFT_HAND [SYS_MODIFIER]")
        if left_visual_tips:
            left_drag_state = orchestrator._left.drag_state_text if orchestrator else "READY"
            for tip_idx, (vx, vy, vz) in left_visual_tips.items():
                px = (int(vx * w), int(vy * h))
                
                is_pinching = left_drag_state in ("CANDIDATE", "ACTIVE", "DROP") and (tip_idx in (4, 8))
                
                if tip_idx == 8:
                    # Distinctive Left Index Pointer Ring (Vibrant Emerald Green with Reticle Crosshairs)
                    # Immediately distinguishable from Right Cyan pointer reticle
                    reticle_color = _WHITE if is_pinching else _EMERALD
                    cv2.circle(out, px, 14, reticle_color, 1, cv2.LINE_AA)
                    cv2.line(out, (px[0] - 18, px[1]), (px[0] + 18, px[1]), reticle_color, 1, cv2.LINE_AA)
                    cv2.line(out, (px[0], px[1] - 18), (px[0], px[1] + 18), reticle_color, 1, cv2.LINE_AA)
                    if left_drag_state == "ACTIVE":
                        cv2.circle(out, px, 18, _WHITE, 1, cv2.LINE_AA)
                    circle_color = _WHITE if is_pinching else _EMERALD
                else:
                    circle_color = _WHITE if is_pinching else _BLUE
                
                cv2.circle(out, px, 7, circle_color, -1, cv2.LINE_AA)
                cv2.circle(out, px, 7, _BG, 2, cv2.LINE_AA)

    # ── HUD / DASHBOARD ──
    # Top left dashboard panel
    panel_w = 400
    panel_h = 270 if PINCH_CLICK_ENABLED else 230
    overlay = out.copy()
    cv2.rectangle(overlay, (10, 10), (10 + panel_w, 10 + panel_h), _BG, -1)
    # Glass-like effect
    cv2.addWeighted(overlay, 0.85, out, 0.15, 0, out)
    
    # Panel border
    cv2.rectangle(out, (10, 10), (10 + panel_w, 10 + panel_h), _CYAN, 1)

    # Title
    cv2.putText(out, "VIRTUAL POINTER OS v2.0", (25, 35), _FONT, 0.6, _WHITE, 2, cv2.LINE_AA)
    cv2.line(out, (25, 45), (10 + panel_w - 10, 45), _GRAY, 1)

    # HUD Lines
    cursor_lock_str = " [LOCKED]" if is_cursor_locked else ""
    cursor_text = f"CURSOR WIN: {cursor_pos[0]:04d}, {cursor_pos[1]:04d}{cursor_lock_str}" if cursor_pos else "CURSOR WIN: OFFLINE"
    if hands.right is not None:
        idx_norm = f"{hands.right[8].x:.3f}, {hands.right[8].y:.3f}"
        r_status = f"TRACKING (ACTIVE) CONF: {hands.right_score:.2f}"
    else:
        idx_norm = "N/A"
        r_status = "LOST (REACQUIRING...)"

    l_status = f"TRACKING CONF: {hands.left_score:.2f}" if hands.left is not None else "LOST"
    
    # Calculate zoom norm if in zoom state, otherwise NA
    zoom_norm = "N/A"
    if "ZOOM" in right_state or "ZOOM" in click_state_text:
        palm_w = math.sqrt(
            (hands.right[5].x - hands.right[17].x)**2 +
            (hands.right[5].y - hands.right[17].y)**2 +
            (hands.right[5].z - hands.right[17].z)**2
        )
        if palm_w > 1e-6:
            z_norm = math.sqrt(
                (hands.right[8].x - hands.right[12].x)**2 +
                (hands.right[8].y - hands.right[12].y)**2 +
                (hands.right[8].z - hands.right[12].z)**2
            ) / palm_w
            zoom_norm = f"{z_norm:.3f}"
    
    hud_lines = [
        f"STATE     : {right_state}",
        f"FPS       : CAM {cam_fps:.1f} | PROC {fps:.1f}",
        f"TIME      : INF {inference_time_ms:.1f}ms | TOT {total_time_ms:.1f}ms",
        f"SENSOR_R  : {r_status}",
        f"PINCH NORM: {idx_norm}",
        f"ZOOM NORM : {zoom_norm}",
        cursor_text,
    ]
    
    if PINCH_CLICK_ENABLED:
        hud_lines.append(f"CLICK     : {click_state_text} (d={last_pinch_norm:.2f})")
    
    if not POINTER_ONLY_MODE:
        hud_lines.append(f"SENSOR_L  : {l_status}")
        left_drag_state = orchestrator._left.drag_state_text if orchestrator else "READY"
        left_pinch = orchestrator._left.last_pinch_norm if orchestrator else 0.0
        hud_lines.append(f"DRAG (L)  : {left_drag_state} (d={left_pinch:.2f})")

    if orchestrator and getattr(orchestrator, '_screenshot', None):
        ss = orchestrator._screenshot
        if ss.hud_left: hud_lines.append(ss.hud_left)
        if ss.hud_right: hud_lines.append(ss.hud_right)
        if ss.hud_text: hud_lines.append(ss.hud_text)

    if orchestrator and getattr(orchestrator, '_lip_gesture', None):
        lip = orchestrator._lip_gesture
        if lip.state.name != "IDLE":
            hud_lines.append(f"LIP MUTE  : {lip.status_text}")

    if orchestrator and getattr(orchestrator, '_ear_volume', None):
        ear = orchestrator._ear_volume
        if ear.is_active:
            hud_lines.append(f"EAR VOL   : {ear.status_text}")

    for idx, line in enumerate(hud_lines):
        y = 70 + idx * 22
        color = _WHITE if "TRACKING" in line or "CURSOR" in line else _GRAY
        if "LOST" in line or "OFFLINE" in line:
            color = _ALERT
        if "STATE" in line or "CLICK" in line or "ZOOM" in line or "SCREENSHOT" in line or "EAR VOL" in line or "LIP MUTE" in line:
            color = _CYAN
        if "LEFT CLICK" in line or "SINGLE CLICK" in line or "DOUBLE CLICK" in line or "PENDING" in line or "ZOOM:" in line or "SCREENSHOT:" in line:
            color = _WHITE
        if "FPS" in line or "TIME" in line:
            color = _BLUE
        if "ERROR" in line:
            color = _ALERT
        cv2.putText(out, line, (25, y), _FONT, 0.5, color, 1, cv2.LINE_AA)

    # Help text at bottom
    cv2.putText(out, "Q: TERMINATE SYSTEM", (15, h - 15), _FONT, 0.4, _GRAY, 1, cv2.LINE_AA)

    return out
