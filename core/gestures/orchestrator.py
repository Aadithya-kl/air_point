"""
GestureOrchestrator - routes HandsResult to per-hand processors and manages Pointer Ownership.
"""

import logging
import time
from enum import Enum, auto

from core.tracker import HandsResult
from core.display import VirtualDesktop, TrackpadZone
from core.actuator import MouseActuator
from core.gestures.right_hand import RightHandProcessor
from core.gestures.left_hand import LeftHandProcessor
from core.gestures.two_hand import TwoHandProcessor
from core.gestures.screenshot import ScreenshotController
from core.gestures.w_gesture import WGestureRecognizer, WState
from core.app_launcher import AppLauncher
from core.gestures.lip_mute_gesture import LipMuteGestureRecognizer, LipMuteState
from core.gestures.ear_volume_gesture import EarVolumeGestureRecognizer, EarVolumeDirection
from core.audio_controller import AudioController
from core.face_tracker import FaceResult

logger = logging.getLogger(__name__)

class PointerOwner(Enum):
    NONE = auto()
    RIGHT = auto()
    LEFT = auto()

class GestureOrchestrator:
    def __init__(
        self,
        actuator: MouseActuator,
        desktop: VirtualDesktop,
        trackpad: TrackpadZone,
    ) -> None:
        self._right = RightHandProcessor(actuator, desktop, trackpad)
        self._left = LeftHandProcessor(actuator, desktop, trackpad)
        self._two = TwoHandProcessor(actuator)
        self._screenshot = ScreenshotController(actuator)
        self._app_launcher = AppLauncher()
        self._w_gesture = WGestureRecognizer(self._app_launcher)
        self._audio_controller = AudioController()
        self._lip_gesture = LipMuteGestureRecognizer(self._audio_controller)
        self._ear_volume = EarVolumeGestureRecognizer(self._audio_controller)
        self._last_cursor_pos: tuple[int, int] | None = (0, 0)
        self._was_right_locked: bool = False
        self._was_ear_active: bool = False
        self._actuator = actuator
        
        self._pointer_owner = PointerOwner.NONE
        self._owner_switch_time: float | None = None
        
        self._GRACE_INTERVAL_S = 0.2
        
        logger.info("GestureOrchestrator ready (dual-hand mode)")

    def process(self, hands: HandsResult, face: FaceResult | None = None) -> None:
        from config import POINTER_ONLY_MODE, PINCH_CLICK_ENABLED
        now = time.perf_counter()

        screenshot_active = self._screenshot.process(hands, now)
        
        if screenshot_active:
            if self._last_cursor_pos is not None:
                self._actuator.move(self._last_cursor_pos[0], self._last_cursor_pos[1])
            self._right.process(None)
            self._left.process(None)
            self._pointer_owner = PointerOwner.NONE
            return

        # Phase 4: Two-hand W gesture to open WhatsApp Desktop
        self._w_gesture.process(hands, now)
        if self._w_gesture.state in (WState.CANDIDATE, WState.CONFIRMED):
            if self._last_cursor_pos is not None:
                self._actuator.move(self._last_cursor_pos[0], self._last_cursor_pos[1])
            self._right.process(None)
            self._left.process(None)
            self._pointer_owner = PointerOwner.NONE
            return

        # Check if an active mouse drag is in progress
        is_active_drag = (
            (getattr(self._actuator, 'is_dragging', False) is True)
            or getattr(self._left, 'left_drag_state', '') in ('DRAGGING', 'GRAB_CANDIDATE')
        )

        # Phase 5: Lip-touch audio mute / restore (protected: never interrupts active drag)
        if not is_active_drag:
            self._lip_gesture.process(hands, face, now)
            if self._lip_gesture.is_holding:
                if self._last_cursor_pos is not None:
                    self._actuator.move(self._last_cursor_pos[0], self._last_cursor_pos[1])
                self._right.process(None)
                self._left.process(None)
                self._pointer_owner = PointerOwner.NONE
                return
        else:
            if self._lip_gesture.state != LipMuteState.IDLE:
                self._lip_gesture.reset()

        # Phase 6: Ear-touch continuous volume adjustment
        self._ear_volume.process(hands, face, now)
        ear_active = self._ear_volume.is_active
        ear_touching_hand = self._ear_volume.touching_hand if ear_active else None

        if ear_active:
            # Only suppress the hand that is actively touching an ear
            # Candidate proximity NEVER locks or pauses cursor
            if ear_touching_hand == "RIGHT":
                self._right.process(None)
                if hands.left is not None:
                    self._left.process(hands.left)
                else:
                    self._left.process(None)
            elif ear_touching_hand == "LEFT":
                self._left.process(None)
                if hands.right is not None:
                    self._right.process(hands.right)
                else:
                    self._right.process(None)
            else:
                self._right.process(None)
                self._left.process(None)

            # Check if the non-touching hand can control cursor
            left_can_point = (ear_touching_hand == "RIGHT" and hands.left is not None and getattr(self._left, "is_pointing", False))
            right_can_point = (ear_touching_hand == "LEFT" and hands.right is not None and getattr(self._right, "_cursor_pos", None) is not None)

            if not (left_can_point or right_can_point):
                if self._last_cursor_pos is not None:
                    self._actuator.move(self._last_cursor_pos[0], self._last_cursor_pos[1])
                self._pointer_owner = PointerOwner.NONE
                self._was_ear_active = True
                return
        else:
            if hands.left is not None:
                self._left.process(hands.left)
            else:
                self._left.process(None)

            if getattr(self._left, 'just_started_drag', False):
                if hasattr(self._right, 'resolve_pending_click'):
                    self._right.resolve_pending_click()

            self._right.process(hands.right)

        if hands.left is not None and hands.right is not None:
            self._two.process(hands.left, hands.right)

        # POINTER OWNERSHIP LOGIC
        # Priority:
        # 1. Right hand (if valid pointer and not touching ear)
        # 2. Left hand (if valid pointer and no right hand)
        
        right_wants_pointer = (
            hands.right is not None
            and ear_touching_hand != "RIGHT"
            and getattr(self._right, '_cursor_pos', None) is not None
        )
        left_wants_pointer = (
            hands.left is not None
            and ear_touching_hand != "LEFT"
            and getattr(self._left, 'is_pointing', False)
            and getattr(self._left, 'cursor_pos', None) is not None
        )

        new_owner = PointerOwner.NONE
        
        # Determine candidate owner
        if right_wants_pointer:
            new_owner = PointerOwner.RIGHT
        elif left_wants_pointer:
            new_owner = PointerOwner.LEFT
            
        old_owner = self._pointer_owner
        # Pointer switching: Right hand has instant priority; Left hand takes over smoothly when right hand is absent
        if new_owner != self._pointer_owner:
            if new_owner == PointerOwner.RIGHT or self._pointer_owner == PointerOwner.NONE:
                self._pointer_owner = new_owner
                self._owner_switch_time = None
            elif self._owner_switch_time is None:
                self._owner_switch_time = now
            elif (now - self._owner_switch_time) >= 0.08:
                self._pointer_owner = new_owner
                self._owner_switch_time = None
        else:
            self._owner_switch_time = None

        # Execute pointer movement
        right_locked = getattr(self._right, 'is_cursor_locked', False)
        
        if self._pointer_owner == PointerOwner.RIGHT and getattr(self._right, '_cursor_pos', None) is not None:
            raw_x, raw_y = self._right._cursor_pos
            screen_x = raw_x
            screen_y = raw_y
            
            # DIAGNOSTIC: Detect large cursor jumps
            if self._last_cursor_pos is not None:
                jump_dist = ((screen_x - self._last_cursor_pos[0])**2 + (screen_y - self._last_cursor_pos[1])**2)**0.5
                just_unlocked = (
                    self._was_ear_active
                    or (not right_locked and self._was_right_locked)
                    or (new_owner != old_owner)
                )
                if jump_dist > 150:
                    logger.warning(
                        f"CURSOR JUMP DETECTED: {jump_dist:.1f}px | Owner: RIGHT | Locked: {right_locked} | "
                        f"Unlocked/Reacquired: {just_unlocked}"
                    )
                    # Bounded smooth recovery when reacquiring after gesture or lock
                    if just_unlocked and jump_dist > 180:
                        blend_factor = 0.50
                        screen_x = int(self._last_cursor_pos[0] + (screen_x - self._last_cursor_pos[0]) * blend_factor)
                        screen_y = int(self._last_cursor_pos[1] + (screen_y - self._last_cursor_pos[1]) * blend_factor)
                    
            self._actuator.move(screen_x, screen_y)
            self._last_cursor_pos = (screen_x, screen_y)
            self._was_ear_active = False
            
        elif self._pointer_owner == PointerOwner.LEFT and getattr(self._left, 'cursor_pos', None) is not None:
            raw_x, raw_y = self._left.cursor_pos
            screen_x = raw_x
            screen_y = raw_y
            
            # DIAGNOSTIC: Detect large cursor jumps
            if self._last_cursor_pos is not None:
                jump_dist = ((screen_x - self._last_cursor_pos[0])**2 + (screen_y - self._last_cursor_pos[1])**2)**0.5
                just_unlocked = (
                    self._was_ear_active
                    or (new_owner != old_owner)
                )
                if jump_dist > 150:
                    logger.warning(
                        f"CURSOR JUMP DETECTED: {jump_dist:.1f}px | Owner: LEFT | "
                        f"Unlocked/Reacquired: {just_unlocked}"
                    )
                    if just_unlocked and jump_dist > 180:
                        blend_factor = 0.50
                        screen_x = int(self._last_cursor_pos[0] + (screen_x - self._last_cursor_pos[0]) * blend_factor)
                        screen_y = int(self._last_cursor_pos[1] + (screen_y - self._last_cursor_pos[1]) * blend_factor)
                    
            self._actuator.move(screen_x, screen_y)
            self._last_cursor_pos = (screen_x, screen_y)
            self._was_ear_active = False
            
        self._was_right_locked = right_locked

    @property
    def right_state(self) -> str:
        return getattr(self._right, '_state').name

    @property
    def cursor_pos(self) -> tuple[int, int] | None:
        return self._last_cursor_pos

    @property
    def right_visual_tips(self) -> dict[int, tuple[float, float, float]]:
        return getattr(self._right, 'visual_tips', {})
        
    @property
    def left_visual_tips(self) -> dict[int, tuple[float, float, float]]:
        return getattr(self._left, 'visual_tips', {})

    @property
    def click_state_text(self) -> str:
        return getattr(self._right, '_click_state_text', "DISABLED")

    @property
    def last_pinch_norm(self) -> float:
        return getattr(self._right, 'last_pinch_norm', 0.0)

    @property
    def is_cursor_locked(self) -> bool:
        return getattr(self._right, 'is_cursor_locked', False)
        
    @property
    def pointer_owner(self) -> str:
        return self._pointer_owner.name

    @property
    def w_gesture(self) -> WGestureRecognizer:
        return self._w_gesture

    @property
    def w_state(self) -> str:
        return self._w_gesture.state.name

    @property
    def w_status_text(self) -> str:
        return self._w_gesture.status_text

    def set_w_activation_callback(self, cb) -> None:
        self._w_gesture.set_activation_callback(cb)

    @property
    def lip_gesture(self) -> LipMuteGestureRecognizer:
        return self._lip_gesture

    @property
    def lip_state(self) -> str:
        return self._lip_gesture.state.name

    @property
    def lip_status_text(self) -> str:
        return self._lip_gesture.status_text

    @property
    def audio_controller(self) -> AudioController:
        return self._audio_controller

    def set_lip_activation_callback(self, cb) -> None:
        self._lip_gesture.set_activation_callback(cb)

    @property
    def ear_volume_gesture(self) -> EarVolumeGestureRecognizer:
        return self._ear_volume

    @property
    def ear_volume_direction(self) -> str:
        return self._ear_volume.direction.name

    @property
    def ear_volume_status_text(self) -> str:
        return self._ear_volume.status_text

    def set_ear_volume_callback(self, cb) -> None:
        self._ear_volume.set_callback(cb)

