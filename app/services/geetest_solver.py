"""
Geetest/Alibaba Slider CAPTCHA Solver
Based on https://github.com/peduajo/geetest-slice-captcha-solver

This solver downloads the piece and background images separately
and uses template matching to find the gap position.
"""
import cv2
import numpy as np
import httpx
import tempfile
from pathlib import Path
from app.utils.logging import get_logger

logger = get_logger(__name__)

PIXELS_EXTENSION = 10


def solve_from_urls(piece_url: str, background_url: str) -> int:
    """
    Solve slider CAPTCHA by downloading piece and background images.
    Returns the distance the slider needs to move.
    """
    try:
        # Download images
        with httpx.Client(timeout=10) as client:
            piece_data = client.get(piece_url).content
            bg_data = client.get(background_url).content
        
        # Save to temp files
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(piece_data)
            piece_path = f.name
        
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(bg_data)
            bg_path = f.name
        
        try:
            solver = PuzleSolver(piece_path, bg_path)
            distance = solver.get_position()
            logger.info("Geetest solver found distance: %d", distance)
            return distance
        finally:
            Path(piece_path).unlink(missing_ok=True)
            Path(bg_path).unlink(missing_ok=True)
            
    except Exception as e:
        logger.error("Geetest solver failed: %s", e)
        return 0


def solve_from_combined(image_path: str) -> int:
    """
    Solve slider CAPTCHA from a combined screenshot.
    The piece is typically on the left and the gap is on the right.
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            return 0
        
        height, width = img.shape[:2]
        
        # For Alibaba CAPTCHA, the layout is:
        # - Background image with gap (takes most of the area)
        # - Floating piece on the left (semi-transparent overlay)
        
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Focus on the puzzle area (exclude text and slider)
        roi_top = int(height * 0.05)
        roi_bottom = int(height * 0.55)
        roi = gray[roi_top:roi_bottom, :]
        roi_color = img[roi_top:roi_bottom, :]
        
        # Save ROI for debug
        cv2.imwrite("/root/qwen_roi_debug.png", roi)
        
        # Apply Sobel operator like the original geetest solver
        grad = _sobel_operator_gray(roi)
        cv2.imwrite("/root/qwen_grad_combined.png", grad)
        
        # Find the piece in the left part
        piece_width_est = int(width * 0.15)  # Piece is about 15% of width
        left_part = grad[:, :piece_width_est]
        
        # Get piece bounds
        x_inf, y_sup, y_inf = _get_piece_bounds(left_part)
        if x_inf == 0 and y_sup == 0 and y_inf == 0:
            logger.warning("Could not find piece bounds")
            return 0
            
        # Extract template from the piece area
        template = grad[y_sup:y_inf, x_inf:piece_width_est]
        
        if template.size == 0:
            logger.warning("Template is empty")
            return 0
            
        # Save template for debug
        cv2.imwrite("/root/qwen_template_combined.png", template)
        
        # Add border extension like original solver
        template = _extend_template_boundary(template)
        
        # Prepare background (only the area where gap could be)
        search_start = piece_width_est
        background = grad[y_sup:y_inf, search_start:]
        background = _extend_background_boundary(background)
        
        # Template matching
        if background.shape[0] < template.shape[0] or background.shape[1] < template.shape[1]:
            logger.warning("Background smaller than template")
            return 0
            
        res = cv2.matchTemplate(background, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        
        # Calculate distance
        gap_x = max_loc[0] + search_start + PIXELS_EXTENSION
        distance = gap_x - x_inf
        
        logger.info("Geetest combined solver: gap_x=%d, piece_x=%d, distance=%d, confidence=%.2f", 
                    gap_x, x_inf, distance, max_val)
        
        return int(distance) if distance > 30 else 0
        
    except Exception as e:
        logger.error("Geetest combined solver failed: %s", e)
        return 0


def _sobel_operator_gray(gray_img):
    """Apply Sobel operator to grayscale image."""
    scale = 1
    delta = 0
    ddepth = cv2.CV_16S
    
    grad_x = cv2.Sobel(gray_img, ddepth, 1, 0, ksize=3, scale=scale, delta=delta, borderType=cv2.BORDER_DEFAULT)
    grad_y = cv2.Sobel(gray_img, ddepth, 0, 1, ksize=3, scale=scale, delta=delta, borderType=cv2.BORDER_DEFAULT)
    abs_grad_x = cv2.convertScaleAbs(grad_x)
    abs_grad_y = cv2.convertScaleAbs(grad_y)
    grad = cv2.addWeighted(abs_grad_x, 0.5, abs_grad_y, 0.5, 0)
    
    return grad


def _get_piece_bounds(left_part):
    """Find the bounds of the puzzle piece in the left part of the image."""
    # Threshold to find piece edges
    _, thresh = cv2.threshold(left_part, 50, 255, cv2.THRESH_BINARY)
    
    # Find contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if not contours:
        return 0, 0, 0
    
    # Find the largest contour
    cnt = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(cnt)
    
    return x, y, y + h


def _extend_template_boundary(template):
    """Extend template boundaries with zeros."""
    extra_border = np.zeros((template.shape[0], PIXELS_EXTENSION), dtype=np.uint8)
    template = np.hstack((extra_border, template, extra_border))
    
    extra_border = np.zeros((PIXELS_EXTENSION, template.shape[1]), dtype=np.uint8)
    template = np.vstack((extra_border, template, extra_border))
    
    return template


def _extend_background_boundary(background):
    """Extend background boundaries with zeros."""
    extra_border = np.zeros((PIXELS_EXTENSION, background.shape[1]), dtype=np.uint8)
    return np.vstack((extra_border, background, extra_border))


class PuzleSolver:
    """Original GeeTest puzzle solver for separate piece and background images."""
    
    def __init__(self, piece_path, background_path):
        self.piece_path = piece_path
        self.background_path = background_path

    def get_position(self):
        template, x_inf, y_sup, y_inf = self._piece_preprocessing()
        background = self._background_preprocessing(y_sup, y_inf)

        res = cv2.matchTemplate(background, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        top_left = max_loc

        origin = x_inf
        end = top_left[0] + PIXELS_EXTENSION

        return end - origin

    def _background_preprocessing(self, y_sup, y_inf):
        background = self._sobel_operator(self.background_path)
        background = background[y_sup:y_inf, :]
        background = _extend_background_boundary(background)
        background = self._img_to_grayscale(background)

        return background

    def _piece_preprocessing(self):
        img = self._sobel_operator(self.piece_path)
        x, w, y, h = self._crop_piece(img)
        template = img[y:h, x:w]

        template = _extend_template_boundary(template)
        template = self._img_to_grayscale(template)

        return template, x, y, h

    def _crop_piece(self, img):
        white_rows = []
        white_columns = []
        r, c = img.shape

        for row in range(r):
            for x in img[row, :]:
                if x != 0:
                    white_rows.append(row)

        for column in range(c):
            for x in img[:, column]:
                if x != 0:
                    white_columns.append(column)

        if not white_columns or not white_rows:
            return 0, c, 0, r
            
        x = white_columns[0]
        w = white_columns[-1]
        y = white_rows[0]
        h = white_rows[-1]

        return x, w, y, h

    def _sobel_operator(self, img_path):
        scale = 1
        delta = 0
        ddepth = cv2.CV_16S

        img = cv2.imread(img_path, cv2.IMREAD_COLOR)
        if img is None:
            return np.zeros((100, 100), dtype=np.uint8)
            
        img = cv2.GaussianBlur(img, (3, 3), 0)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        grad_x = cv2.Sobel(gray, ddepth, 1, 0, ksize=3, scale=scale, delta=delta, borderType=cv2.BORDER_DEFAULT)
        grad_y = cv2.Sobel(gray, ddepth, 0, 1, ksize=3, scale=scale, delta=delta, borderType=cv2.BORDER_DEFAULT)
        abs_grad_x = cv2.convertScaleAbs(grad_x)
        abs_grad_y = cv2.convertScaleAbs(grad_y)
        grad = cv2.addWeighted(abs_grad_x, 0.5, abs_grad_y, 0.5, 0)

        return grad

    def _img_to_grayscale(self, img):
        tmp_path = "/tmp/sobel.png"
        cv2.imwrite(tmp_path, img)
        return cv2.imread(tmp_path, 0)


if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) > 1:
        if len(sys.argv) == 3:
            # Two arguments: piece_path and background_path
            solver = PuzleSolver(sys.argv[1], sys.argv[2])
            dist = solver.get_position()
        else:
            # One argument: combined screenshot
            dist = solve_from_combined(sys.argv[1])
        print(f"Distance: {dist}")
