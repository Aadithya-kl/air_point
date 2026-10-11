# core/app_launcher.py
"""
Service to safely launch desktop applications on Windows.
Focuses on reliable, un-privileged Windows-supported mechanisms (protocol URIs, AUMID).
"""

import os
import subprocess
import logging

logger = logging.getLogger(__name__)


class AppLauncher:
    """
    Dedicated application launch service for Windows.
    Prefers native protocol URIs (e.g. 'whatsapp:') and AppUserModeIDs.
    """

    def __init__(
        self,
        whatsapp_uri: str = "whatsapp:",
        whatsapp_aumid: str = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    ) -> None:
        self.whatsapp_uri = whatsapp_uri
        self.whatsapp_aumid = whatsapp_aumid

    def open_whatsapp(self) -> bool:
        """
        Activates or opens the installed WhatsApp Desktop application on Windows.
        Tries protocol URI first, then falls back to registered AUMID.
        Returns True on success, False on failure.
        """
        # Primary: Registered Windows Protocol URI
        try:
            logger.info("Opening WhatsApp Desktop via protocol '%s'", self.whatsapp_uri)
            os.startfile(self.whatsapp_uri)
            return True
        except Exception as e:
            logger.warning("Protocol launch failed (%s). Attempting AUMID fallback...", e)

        # Fallback: AppUserModeID via Windows Explorer shell
        try:
            target = f"shell:AppsFolder\\{self.whatsapp_aumid}"
            logger.info("Opening WhatsApp Desktop via AUMID '%s'", target)
            subprocess.Popen(["explorer.exe", target])
            return True
        except Exception as e2:
            logger.error("AUMID launch failed: %s", e2)
            return False
