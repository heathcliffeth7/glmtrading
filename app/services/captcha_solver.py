"""
Sider CAPTCHA Solver wrapper for Qwen token refresh.

Uses sider-captcha-solver (https://github.com/TomokotoKiyoshi/Sider_CAPTCHA_Solver)
for slider CAPTCHA recognition with deep learning.

API (v1.0.3):
    from sider_captcha_solver import CaptchaPredictor
    predictor = CaptchaPredictor(device='cpu')
    result = predictor.predict('image.png')
    # result = {'gap_x': x, 'gap_y': y, 'slider_x': sx, 'slider_y': sy, 'distance': d, ...}
"""
from typing import Optional, Dict, Any
from pathlib import Path
import tempfile

from app.utils.logging import get_logger

logger = get_logger(__name__)


class SliderCaptchaSolver:
    """Wrapper for sider-captcha-solver library (v1.0.3 API)."""

    def __init__(self):
        self._predictor = None
        self._loaded = False

    def _load_model(self):
        """Lazy load model on first use."""
        if self._loaded:
            return

        try:
            from sider_captcha_solver import CaptchaPredictor
            self._predictor = CaptchaPredictor(device='cpu')
            self._loaded = True
            logger.info("Sider CAPTCHA Solver loaded (CPU mode)")
        except ImportError as e:
            logger.error("sider-captcha-solver not installed: %s", e)
            self._loaded = True  # Don't retry
        except Exception as e:
            logger.error("Failed to load CaptchaPredictor: %s", e)
            self._loaded = True

    def solve(self, image_bytes: bytes, retries: int = 2) -> Optional[int]:
        """
        Solve slider captcha from image bytes.

        Args:
            image_bytes: PNG/JPG image data as bytes
            retries: Number of retry attempts

        Returns:
            Sliding distance in pixels, or None if failed.
        """
        self._load_model()

        if not self._predictor:
            logger.error("Predictor not available")
            return None

        # Write to temp file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(image_bytes)
            tmp_path = Path(f.name)

        try:
            for attempt in range(retries + 1):
                try:
                    result = self._predictor.predict(str(tmp_path))

                    # v1.0.3 API returns dict with gap_x, slider_x, distance, etc.
                    distance = self._extract_distance(result)

                    if distance is not None:
                        logger.info(
                            "CAPTCHA solved (attempt %d): distance=%dpx, result=%s",
                            attempt + 1, distance, result
                        )
                        return distance

                    if attempt < retries:
                        logger.warning("No valid distance found, retrying...")

                except Exception as e:
                    logger.error("Solve attempt %d failed: %s", attempt + 1, e)
                    if attempt >= retries:
                        return None

            return None

        finally:
            tmp_path.unlink(missing_ok=True)

    def _extract_distance(self, result: Any) -> Optional[int]:
        """Extract sliding distance from prediction result."""
        if result is None:
            return None

        # Try various result formats
        if isinstance(result, dict):
            # Direct distance field
            if 'distance' in result:
                dist = result['distance']
                if isinstance(dist, (int, float)) and 10 < dist < 300:
                    return int(dist)

            # Calculate from gap_x and slider_x
            gap_x = result.get('gap_x') or result.get('gap', {}).get('x')
            slider_x = result.get('slider_x') or result.get('slider', {}).get('x')

            if gap_x is not None and slider_x is not None:
                dist = abs(float(gap_x) - float(slider_x))
                if 10 < dist < 300:
                    return int(dist)

        # Tuple/list format (gap_x, gap_y, slider_x, slider_y)
        if isinstance(result, (tuple, list)) and len(result) >= 4:
            gap_x, gap_y, slider_x, slider_y = result[:4]
            dist = abs(float(gap_x) - float(slider_x))
            if 10 < dist < 300:
                return int(dist)

        logger.warning("Could not extract distance from result: %s", result)
        return None

    def solve_from_file(self, image_path: str) -> Optional[int]:
        """
        Solve slider captcha from image file path.

        Args:
            image_path: Path to image file

        Returns:
            Sliding distance in pixels, or None if failed.
        """
        self._load_model()

        if not self._predictor:
            return None

        try:
            result = self._predictor.predict(image_path)
            distance = self._extract_distance(result)

            if distance:
                logger.info("CAPTCHA solved from file: distance=%dpx", distance)

            return distance

        except Exception as e:
            logger.error("Solve from file failed: %s", e)
            return None


# Singleton instance
_solver_instance: Optional[SliderCaptchaSolver] = None


def get_captcha_solver() -> SliderCaptchaSolver:
    """Get singleton solver instance."""
    global _solver_instance
    if _solver_instance is None:
        _solver_instance = SliderCaptchaSolver()
    return _solver_instance
