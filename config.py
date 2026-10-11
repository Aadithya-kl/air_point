# config.py
# All tunable parameters for the AI Air Mouse system.
# Adjust these to calibrate for different environments; never scatter constants across modules.

# ── Camera ─────────────────────────────────────────────────────────────────────
CAMERA_INDEX = 0                  # Webcam device index
CAMERA_WIDTH = 640                # Capture resolution width (640x480 is faster for inference)
CAMERA_HEIGHT = 480               # Capture resolution height
CAMERA_FPS = 30                   # Target capture FPS; webcam must support this
CAMERA_BUFFER_SIZE = 1            # DirectShow buffer: 1 = always freshest frame

# ── MediaPipe ──────────────────────────────────────────────────────────────────
MP_MODEL_PATH = "models/hand_landmarker.task"  # float16 Tasks API bundle
MP_MAX_HANDS = 1
MP_DETECTION_CONFIDENCE = 0.7
MP_TRACKING_CONFIDENCE = 0.5

# ── Virtual Trackpad ───────────────────────────────────────────────────────────
# Shrink the active tracking zone inward from webcam edges by this many pixels.
# Prevents the user needing to stretch their hand to screen corners.
TRACKPAD_MARGIN = 100             # pixels, applied to all four edges

# ── 1€ Filter ─────────────────────────────────────────────────────────────────
# Casiez et al. 2012 — adaptive low-pass filter for cursor smoothing.
# mincutoff: lower = more smoothing at rest (reduce jitter)
# beta: higher = less lag at high speed (reduce lag during fast motion)
# dcutoff: derivative smoothing; usually leave at 1.0
ONE_EURO_MINCUTOFF = 0.2           # lower = more smoothing at rest (less jitter)
ONE_EURO_BETA = 0.01               # higher = more responsive to fast movements
ONE_EURO_DCUTOFF = 1.0

# ── Gesture Thresholds ────────────────────────────────────────────────────────
# All distances are Euclidean norms of normalized MediaPipe coordinates [0.0–1.0].

# Index tap thresholds
TAP_BENT_ANGLE          = 140.0   # degrees: index PIP angle below this is "bent"
TAP_STRAIGHT_ANGLE      = 160.0   # degrees: index PIP angle above this is "straight"
TAP_MAX_DURATION_S      = 0.3     # max time to complete one bend-release tap
TAP_DOUBLE_WINDOW_S     = 0.5     # max time between two taps to trigger click

# Optional legacy pinch click
USE_PINCH_CLICK         = False
THUMB_INDEX_CLICK_DIST  = 0.060
THUMB_MIDDLE_CLICK_DIST = 0.065
DOUBLE_CLICK_WINDOW_S   = 0.4

DRAG_HOLD_SECONDS = 0.4           # index tap held -> drag activates
GESTURE_COOLDOWN_SECONDS = 0.4    # min gap between discrete gesture events

# Zoom thresholds
ZOOM_MODE               = "APP"   # "APP" (Ctrl+=) or "MAGNIFIER" (Win+=)
ZOOM_BASELINE_SAMPLES   = 5
ZOOM_START_DIST_VAR     = 0.05    # hysteresis for zoom baseline
ZOOM_TICK_DELTA         = 0.03    # normalized distance change per zoom tick

# Screenshot thresholds (Phase 4.1: Two-Hand Open-Palm Hold)
SCREENSHOT_ENABLED         = True
SCREENSHOT_OVERLAY_ENABLED = True
SCREENSHOT_OVERLAY_POSITION= "TOP_CENTER"
SCREENSHOT_DIR             = "screenshots"
SCREENSHOT_HOLD_DURATION_S = 3.0
SCREENSHOT_GRACE_PERIOD_S  = 0.10
SCREENSHOT_HAND_CONFIDENCE_MIN = 0.85
SCREENSHOT_REARM_DURATION_S = 0.50

# Fist detection — all finger tips must be below their PIP joints
FIST_CURL_THRESHOLD = 0.0         # unused numerically; fist = all 5 curled (boolean)

# Wrist scroll (peace-sign posture)
WRIST_VELOCITY_THRESHOLD = 0.008  # normalized units/s — minimum wrist flick to register
SCROLL_TICK_SCALE        = 0.003  # velocity / scale = tick count (clamped 1–5)
SCROLL_COOLDOWN_S        = 0.12   # minimum gap between scroll emissions

# Scroll: number of OS scroll ticks per gesture detection cycle (legacy fallback)
SCROLL_TICKS = 3

# Two-hand gestures
ZOOM_WRIST_DELTA    = 0.05        # normalized wrist-to-wrist distance change per zoom tick
LOCK_SCREEN_HOLD_S  = 1.0         # both fists held this long → Win+L

# ── Handedness ────────────────────────────────────────────────────────────────
# We now mirror the camera frame (selfie-mode) before it reaches MediaPipe.
# Therefore, an anatomical left hand visually appears as a right hand.
# The handedness mapping in tracker.py handles this automatically.

# ── Debug ─────────────────────────────────────────────────────────────────────
# Set True to open a debug window showing detected landmarks, hand labels (L/R),
# gesture state, and pinch distances. Press Q in the window to quit.
DEBUG_GESTURES = True

# ── Milestone 2: Intelligent Mouse Clicking ──────────────────────────────────
# Disables complex gestures but enables index-cursor and pinch-to-click.
POINTER_ONLY_MODE = False
PINCH_CLICK_ENABLED = True
DRAG_ENABLED = True
DRAG_ACTIVATION_DURATION_S = 0.45

# Pinch normalization: distance between thumb tip (4) and index tip (8)
# divided by palm width (distance between index MCP (5) and pinky MCP (17)).
PINCH_START_NORM_DIST = 0.50    # ratio must drop below this to register PINCH CANDIDATE
PINCH_RELEASE_NORM_DIST = 0.80  # ratio must rise above this to RELEASE
PINCH_CONFIRM_S = 0.10          # duration (80-120ms) to hold candidate before CLICK
PINCH_COOLDOWN_S = 0.1          # cooldown after release before next click can arm (reduced for double click)

# Double Click Configuration
# Mode A: 'instant' - fires native click immediately upon confirmation. A second fast pinch snaps coords to form native double click.
# Mode B: 'comfortable' - buffers the click, waits for a possible second slower pinch, then fires a native double-click pair.
SINGLE_CLICK_MODE = "comfortable" # 'instant' or 'comfortable'
DOUBLE_CLICK_GESTURE_WINDOW_MS = 750 # ms to wait for second pinch in comfortable mode
DOUBLE_CLICK_POSITION_TOLERANCE_PX = 60 # pixels of tolerance to snap to previous click

# ── Milestone 3: Gesture-Controlled Zoom ─────────────────────────────────────
# Zoom uses the right hand. Pose: index and middle extended, other fingers curled.
ZOOM_ENABLED = True
ZOOM_BACKEND = "active_application" # "active_application" (Ctrl+/-) or "windows_magnifier" (Win+/-)

ZOOM_ACTIVATION_DURATION_S = 0.3    # Hold the pose this long to enter zoom mode
ZOOM_EXIT_DURATION_S = 0.2          # Drop the pose this long to exit zoom mode
ZOOM_COOLDOWN_S = 0.4               # Minimum time between zoom steps to prevent rapid firing

ZOOM_SPREAD_THRESHOLD = 0.15        # Normalized distance INCREASE over baseline to trigger zoom-in
ZOOM_CLOSE_THRESHOLD = -0.15        # Normalized distance DECREASE below baseline to trigger zoom-out


# ── Windows OS ────────────────────────────────────────────────────────────────
# Do not change these unless you understand the Windows scheduler implications.
TIMER_RESOLUTION_MS = 1          # Sets Windows multimedia timer floor to 1ms
# psutil priority constant: HIGH_PRIORITY_CLASS = 0x80
# Do NOT use REALTIME_PRIORITY_CLASS (0x100) — it can starve system threads.
PROCESS_PRIORITY = 0x80

# Left Hand Drag Settings
LEFT_DRAG_ENABLED = True
LEFT_DRAG_CONFIRM_S = 0.10             # Confirm hold duration (100ms) before drag activates
LEFT_DRAG_ACTIVATION_DURATION_S = 0.10  # Fast grab activation
LEFT_DRAG_START_NORM_DIST = 0.55       # Pinch entry threshold (thumb-index dist / palm width)
LEFT_DRAG_RELEASE_NORM_DIST = 0.75     # Pinch release threshold (hysteresis prevents rapid oscillation)
LEFT_DRAG_LOST_TIMEOUT_S = 0.06        # Safety timeout before releasing drag if hand lost

# ── Phase 4: Two-Hand W Gesture / WhatsApp ───────────────────────────────────
W_GESTURE_ENABLED = True
W_HOLD_DURATION_S = 0.70         # Required hold duration (seconds) before activation
W_GRACE_PERIOD_S = 0.15          # Grace period for brief frame loss
W_REARM_DURATION_S = 0.40        # Delay after release before gesture can rearm
W_INDEX_TOUCH_THRESHOLD = 1.35   # Max normalized distance between index fingertips (relative to palm width)
W_HAND_CONFIDENCE_MIN = 0.65     # Minimum tracking confidence for both hands
WHATSAPP_URI = "whatsapp:"       # Windows protocol URI for WhatsApp Desktop

# ── Phase 5: Lip-Touch Audio Mute / Restore ─────────────────────────────────
LIP_MUTE_ENABLED = True
LIP_MUTE_HOLD_DURATION_S = 0.60       # Required hold duration (seconds) before audio toggle
LIP_MUTE_PROXIMITY_THRESHOLD = 0.50   # Max 2D distance between index tip and lip center (relative to mouth width)
LIP_MUTE_RELEASE_THRESHOLD = 1.20     # Distance (relative to mouth width) to confirm release (hysteresis)
LIP_MUTE_MAX_DEPTH_DELTA = 0.28       # Max normalized relative depth difference |z_tip - z_lip| / fw to reject foreground hands
LIP_MUTE_REARM_DURATION_S = 0.30      # Delay after release before gesture can rearm
LIP_MUTE_FACE_CONFIDENCE_MIN = 0.50   # Minimum face detection confidence
LIP_MUTE_MAX_STABILITY_DELTA = 0.45   # Max drift of index fingertip during hold to prevent swipe triggers
MP_FACE_MODEL_PATH = "models/face_landmarker.task"

# ── Phase 6: Ear-Touch Continuous Volume Control ────────────────────────────
EAR_VOLUME_ENABLED = True
EAR_VOLUME_ARM_DURATION_S = 0.20       # Confirmation/arming duration (seconds) before volume adjustment starts
EAR_VOLUME_SPEED_PER_SEC = 0.30        # Volume adjustment speed (30% per second)
EAR_VOLUME_UPDATE_INTERVAL_S = 0.05    # Time-based rate limiter (50ms interval ~ 20 updates/sec)
EAR_VOLUME_PROXIMITY_THRESHOLD = 0.28  # Max 2D distance between index tip and ear (relative to face width)
EAR_VOLUME_RELEASE_THRESHOLD = 0.45    # Distance to confirm ear release (hysteresis)
EAR_VOLUME_MAX_DEPTH_DELTA = 0.40      # Max normalized relative depth difference |z_tip - z_ear| / fw to reject hands floating in foreground
EAR_VOLUME_CONFIDENCE_MIN = 0.50       # Minimum tracking confidence required for face and hands
EAR_VOLUME_LOST_TIMEOUT_S = 0.12       # Grace timeout before cancelling volume adjustment if tracking briefly lost


