# core/gestures/orchestrator.py
"""
GestureOrchestrator — routes HandsResult to per-hand processors.

Exposes process(hands: HandsResult) as a drop-in replacement for
the old GestureProcessor.process(landmarks: list[Landmark]).
"""

import logging

from core.tracker import HandsResult
from core.display import VirtualDesktop, TrackpadZone
from core.actuator import MouseActuator
from core.gestures.right_hand import RightHandProcessor
from core.gestures.left_hand import LeftHandProcessor
from core.gestures.two_hand import TwoHandProcessor

logger = logging.getLogger(__name__)


class GestureOrchestrator:
    """
    Coordinates right, left, and two-hand processors.

    Args:
        actuator: MouseActuator instance
        desktop:  VirtualDesktop for coordinate mapping
        trackpad: TrackpadZone for coordinate mapping
    """

    def __init__(
        self,
        actuator: MouseActuator,
        desktop: VirtualDesktop,
        trackpad: TrackpadZone,
    ) -> None:
        self._right = RightHandProcessor(actuator, desktop, trackpad)
        self._left = LeftHandProcessor(actuator)
        self._two = TwoHandProcessor(actuator)
        logger.info("GestureOrchestrator ready (dual-hand mode)")

    def process(self, hands: HandsResult) -> None:
        """Process one frame. Call once per frame regardless of hand visibility."""
        from config import POINTER_ONLY_MODE, PINCH_CLICK_ENABLED
        self._right.process(hands.right)

        # In Phase 1 and 2, left and two-hand gestures are disabled.
        if not POINTER_ONLY_MODE and not PINCH_CLICK_ENABLED:
            if hands.left is not None:
                self._left.process(hands.left)
            else:
                self._left.process(None)

            if hands.left is not None and hands.right is not None:
                self._two.process(hands.left, hands.right)

    @property
    def right_state(self) -> str:
        return self._right._state.name

    @property
    def cursor_pos(self) -> tuple[int, int] | None:
        return self._right.cursor_pos

    @property
    def visual_tips(self) -> dict[int, tuple[float, float, float]]:
        return self._right.visual_tips

    @property
    def click_state_text(self) -> str:
        return self._right.click_state_text

    @property
    def last_pinch_norm(self) -> float:
        return self._right.last_pinch_norm

    @property
    def is_cursor_locked(self) -> bool:
        return self._right.is_cursor_locked
