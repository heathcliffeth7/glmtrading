"""
Session Manager - Cookie and localStorage persistence for Qwen.

Manuel login sonrasi session dosyasini kullanir.
Session expire oldugunda hata firlati, manuel login gerekir.
"""

import json
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, Page

from app.utils.logging import get_logger

logger = get_logger(__name__)

# Default session file path
DEFAULT_SESSION_FILE = Path("/root/trading/qwen_session.json")


class SessionExpiredError(Exception):
    """Raised when Qwen session has expired and manual login is required."""

    pass


class SessionManager:
    """
    Manage Qwen session persistence.

    Handles:
    - Loading session from file (cookies + localStorage)
    - Saving session after successful requests
    - Detecting login status
    - Session validation
    """

    def __init__(self, session_file: Optional[Path] = None):
        """
        Initialize session manager.

        Args:
            session_file: Path to session storage file
        """
        self._session_file = session_file or DEFAULT_SESSION_FILE
        self._is_valid = False

    @property
    def session_file(self) -> Path:
        """Get session file path."""
        return self._session_file

    @property
    def session_exists(self) -> bool:
        """Check if session file exists."""
        return self._session_file.exists()

    def get_storage_state_path(self) -> Optional[str]:
        """Get storage state path if exists."""
        if self._session_file.exists():
            return str(self._session_file)
        return None

    async def load_session(self, context: BrowserContext) -> bool:
        """
        Load session into browser context.

        Note: This is typically handled by passing storage_state
        to context creation. This method is for manual loading.

        Args:
            context: Browser context to load session into

        Returns:
            True if session loaded successfully
        """
        if not self._session_file.exists():
            logger.warning("Session file not found: %s", self._session_file)
            return False

        try:
            # Read session data
            session_data = json.loads(self._session_file.read_text())

            # Add cookies to context
            cookies = session_data.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)
                logger.info("Loaded %d cookies from session", len(cookies))

            return True

        except Exception as e:
            logger.error("Failed to load session: %s", e)
            return False

    async def save_session(self, context: BrowserContext) -> bool:
        """
        Save current session to file.

        Args:
            context: Browser context to save session from

        Returns:
            True if saved successfully
        """
        try:
            await context.storage_state(path=str(self._session_file))
            logger.info("Session saved to: %s", self._session_file)
            self._is_valid = True
            return True

        except Exception as e:
            logger.error("Failed to save session: %s", e)
            return False

    async def is_logged_in(self, page: Page) -> bool:
        """
        Check if user is logged into Qwen.

        Multiple detection strategies (priority order):
        1. localStorage token check
        2. Avatar/user menu element visible
        3. No "Log in" button visible

        Args:
            page: Page to check login status

        Returns:
            True if logged in
        """
        try:
            # Strategy 1: Check localStorage for auth tokens
            has_token = await page.evaluate("""
                () => {
                    const token = localStorage.getItem('token') ||
                                  localStorage.getItem('access_token') ||
                                  localStorage.getItem('auth_token');
                    return !!token;
                }
            """)

            if has_token:
                logger.debug("Login detected: localStorage token found")
                self._is_valid = True
                return True

            # Strategy 2: Check for user avatar/menu
            avatar_visible = await page.locator(
                "[class*='avatar'], [class*='user-menu'], [class*='profile']"
            ).first.is_visible(timeout=1000)

            if avatar_visible:
                logger.debug("Login detected: avatar/user menu visible")
                self._is_valid = True
                return True

        except Exception:
            pass  # Element not found, continue to next check

        try:
            # Strategy 3: Check if "Log in" button is visible
            login_button = page.locator("text=Log in")
            login_visible = await login_button.is_visible(timeout=2000)

            if login_visible:
                logger.warning("Not logged in: 'Log in' button visible")
                self._is_valid = False
                return False

            # No login button and passed other checks - assume logged in
            logger.debug("Login assumed: no 'Log in' button visible")
            self._is_valid = True
            return True

        except Exception as e:
            logger.debug("Login check exception: %s", e)
            # If we can't determine, assume logged in (will fail on request if not)
            return True

    async def validate_session(self, page: Page) -> bool:
        """
        Validate session is still active.

        Args:
            page: Page to validate session on

        Returns:
            True if session is valid

        Raises:
            SessionExpiredError: If session has expired
        """
        if not await self.is_logged_in(page):
            self._is_valid = False
            raise SessionExpiredError(
                f"Qwen session expired. Please login manually and save session to: "
                f"{self._session_file}"
            )

        self._is_valid = True
        return True

    async def get_jwt_token(self, page: Page) -> Optional[str]:
        """
        Extract JWT token from localStorage.

        Args:
            page: Page to extract token from

        Returns:
            JWT token string or None
        """
        try:
            token = await page.evaluate("""
                () => {
                    return localStorage.getItem('token') ||
                           localStorage.getItem('access_token') ||
                           localStorage.getItem('auth_token') ||
                           null;
                }
            """)
            return token

        except Exception as e:
            logger.debug("Failed to get JWT token: %s", e)
            return None

    async def get_cookies_string(self, page: Page) -> str:
        """
        Get cookies as string for HTTP headers.

        Args:
            page: Page to get cookies from

        Returns:
            Cookie string (e.g., "key1=val1; key2=val2")
        """
        try:
            cookies = await page.context.cookies()
            cookie_str = "; ".join(
                f"{c['name']}={c['value']}" for c in cookies
            )
            return cookie_str

        except Exception as e:
            logger.debug("Failed to get cookies: %s", e)
            return ""

    def invalidate_session(self):
        """Mark session as invalid (does not delete file)."""
        self._is_valid = False
        logger.warning("Session marked as invalid")

    def delete_session_file(self):
        """Delete session file (for complete reset)."""
        if self._session_file.exists():
            self._session_file.unlink()
            logger.info("Session file deleted: %s", self._session_file)
        self._is_valid = False

    @property
    def is_valid(self) -> bool:
        """Check if session is currently valid."""
        return self._is_valid
