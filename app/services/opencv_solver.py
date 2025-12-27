import cv2
import numpy as np
from app.utils.logging import get_logger

logger = get_logger(__name__)

def find_gap(image_path: str) -> int:
    """Find the gap in a slider captcha image using edge detection."""
    try:
        img = cv2.imread(image_path)
        if img is None:
            return 0
        
        height, width = img.shape[:2]
        
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Focus on the image area (middle part, excluding text on top/bottom)
        # Alibaba CAPTCHA has text on top and slider on bottom
        img_top = int(height * 0.1)
        img_bottom = int(height * 0.6)  # Only the puzzle image area
        roi = gray[img_top:img_bottom, :]
        
        # Apply Canny edge detection
        edges = cv2.Canny(roi, 100, 200)
        cv2.imwrite("/root/qwen_edges_debug.png", edges)
        
        # The gap appears as a dark rectangle with distinct edges
        # Look for vertical edges that indicate the gap boundaries
        
        # Sum edges vertically to find peaks (columns with lots of edges)
        edge_sum = np.sum(edges, axis=0)
        
        # The piece starts on the left (around x=10-50)
        # The gap is somewhere in the middle-right (usually x > 100)
        
        # Find the position with highest edge concentration in the right 2/3
        search_start = width // 3
        right_edge_sum = edge_sum[search_start:]
        
        if len(right_edge_sum) == 0:
            return 0
        
        # Find peaks in edge density (gap edges)
        # Use a sliding window to find the area with most edge activity
        window_size = 50  # Approximate piece width
        max_activity = 0
        gap_pos = 0
        
        for i in range(len(right_edge_sum) - window_size):
            activity = np.sum(right_edge_sum[i:i+window_size])
            if activity > max_activity:
                max_activity = activity
                gap_pos = i + window_size // 2  # Center of the window
        
        # Add back the search offset
        gap_x = gap_pos + search_start
        
        # The slider starts at approximately x=20-30
        slider_start = 25
        distance = gap_x - slider_start
        
        logger.info("OpenCV found gap at x=%d, distance=%d (edge activity=%d)", 
                    gap_x, distance, max_activity)
        
        return int(distance) if distance > 50 else 0
        
    except Exception as e:
        logger.error("OpenCV gap finding failed: %s", e)
        return 0

if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        dist = find_gap(sys.argv[1])
        print(f"Final Distance: {dist}")

