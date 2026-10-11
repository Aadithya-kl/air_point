# core/audio_controller.py
"""
Windows System Output Audio Controller.
Controls the default speaker/output endpoint mute and restores the previous volume level.
Uses Core Audio via pycaw / comtypes on Windows with graceful fallback for testing.
"""

import logging
import sys

logger = logging.getLogger(__name__)


class AudioController:
    """
    Manages system output audio mute and restore states.
    Preserves exact volume scalar (0.0 - 1.0) before muting and restores it upon unmuting.
    """

    def __init__(self, endpoint_override=None) -> None:
        self._endpoint_override = endpoint_override
        self._muted_by_air_point: bool = False
        self._saved_volume_scalar: float | None = None
        self._last_device_name: str | None = None

    @property
    def muted_by_air_point(self) -> bool:
        """Returns True if the system was muted by Air Point and has not yet been restored."""
        return self._muted_by_air_point

    @property
    def saved_volume_scalar(self) -> float | None:
        """Returns the volume scalar saved before the last mute action."""
        return self._saved_volume_scalar

    def _get_endpoint(self):
        """Retrieves the default output endpoint volume interface."""
        if self._endpoint_override is not None:
            return self._endpoint_override

        if sys.platform != "win32":
            raise OSError("AudioController requires Windows for Core Audio control")

        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception as e:
            logger.debug("CoInitialize warning: %s", e)

        try:
            from pycaw.pycaw import AudioUtilities
            speakers = AudioUtilities.GetSpeakers()
            if speakers is None:
                raise RuntimeError("No default audio output device found")
            self._last_device_name = getattr(speakers, "FriendlyName", "Speakers")
            volume = getattr(speakers, "EndpointVolume", None)
            if volume is None:
                raise RuntimeError("EndpointVolume interface not available")
            return volume
        except Exception as e:
            logger.error("Failed to acquire Windows output audio endpoint: %s", e)
            raise

    def get_current_state(self) -> tuple[bool, float] | None:
        """Returns (is_muted: bool, volume_scalar: float) or None if unavailable."""
        try:
            endpoint = self._get_endpoint()
            is_muted = bool(endpoint.GetMute())
            vol = float(endpoint.GetMasterVolumeLevelScalar())
            return is_muted, vol
        except Exception as e:
            logger.warning("Failed to query audio state: %s", e)
            return None

    def mute(self) -> bool:
        """
        Mutes the default output endpoint and saves the current volume level.
        Returns True if successfully verified as muted.
        """
        try:
            endpoint = self._get_endpoint()
            # Read and preserve current volume scalar before muting
            current_vol = float(endpoint.GetMasterVolumeLevelScalar())
            self._saved_volume_scalar = current_vol

            # Actual system mute
            endpoint.SetMute(1, None)

            # Verify mute state
            is_muted = bool(endpoint.GetMute())
            if not is_muted:
                logger.error("Mute verification failed: endpoint still unmuted")
                return False

            self._muted_by_air_point = True
            logger.info("System output audio muted. Saved volume: %.2f%%", current_vol * 100.0)
            return True
        except Exception as e:
            logger.error("Audio mute operation failed: %s", e)
            return False

    def restore(self) -> bool:
        """
        Unmutes the output endpoint and restores the saved volume scalar.
        Returns True if successfully verified as unmuted.
        """
        try:
            endpoint = self._get_endpoint()

            # Unmute endpoint
            endpoint.SetMute(0, None)

            # Restore preserved volume level if available
            if self._saved_volume_scalar is not None:
                endpoint.SetMasterVolumeLevelScalar(self._saved_volume_scalar, None)
                logger.info("Restored volume scalar to: %.2f%%", self._saved_volume_scalar * 100.0)

            # Verify unmuted state
            is_muted = bool(endpoint.GetMute())
            if is_muted:
                logger.error("Restore verification failed: endpoint remains muted")
                return False

            self._muted_by_air_point = False
            return True
        except Exception as e:
            logger.error("Audio restore operation failed: %s", e)
            return False

    def toggle_mute(self) -> tuple[bool, str]:
        """
        Alternates between mute and restore on subsequent invocations.
        Returns (success: bool, action_name: str) where action_name is 'MUTED', 'RESTORED', or 'FAILED'.
        """
        if not self._muted_by_air_point:
            success = self.mute()
            return (True, "MUTED") if success else (False, "FAILED")
        else:
            success = self.restore()
            return (True, "RESTORED") if success else (False, "FAILED")
