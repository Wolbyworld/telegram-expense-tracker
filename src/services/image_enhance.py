"""Receipt image enhancement pipeline.

Each function takes a PIL Image and returns a PIL Image.
Functions can be composed in any order via `enhance_receipt()`.
"""

import logging
import math

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

logger = logging.getLogger(__name__)


# --- Pillow-based (zero extra deps) ---


def fix_orientation(img: Image.Image) -> Image.Image:
    """Fix rotation — tries EXIF first, then CV-based text direction detection.

    Telegram strips EXIF, so we fall back to detecting whether text runs
    vertically (= image needs 90° rotation).
    """
    # Try EXIF first (works for non-Telegram sources)
    fixed = ImageOps.exif_transpose(img)
    if fixed.size != img.size:
        # EXIF actually rotated the image (dimensions changed)
        return fixed

    # CV-based: only attempt rotation on landscape images.
    # Portrait images are assumed correct (receipts are naturally tall).
    w_img, h_img = img.size
    if h_img >= w_img:
        return img

    cv_img = _pil_to_cv(img)
    h, w = cv_img.shape[:2]

    # Crop to center 60% to focus on the receipt, not the background
    margin_x, margin_y = w // 5, h // 5
    center = cv_img[margin_y : h - margin_y, margin_x : w - margin_x]

    gray = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
    # Adaptive threshold to isolate text from background
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 8)

    # Find short line segments (text-scale, not background edges)
    lines = cv2.HoughLinesP(thresh, 1, np.pi / 180, threshold=30, minLineLength=20, maxLineGap=5)

    if lines is None or len(lines) < 20:
        return img

    horizontal = 0
    vertical = 0
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = abs(math.degrees(math.atan2(y2 - y1, x2 - x1)))
        length = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        if angle < 25:
            horizontal += length
        elif angle > 65:
            vertical += length

    ratio = vertical / horizontal if horizontal > 0 else float("inf")

    # If vertical text features dominate, the image is sideways.
    # Default to +90° CCW — correct for most phone-landscape receipt photos.
    # Can't reliably distinguish right-side-up from upside-down without OCR.
    if ratio > 1.3:
        logger.info("Detected sideways text (V=%.0f H=%.0f ratio=%.2f), rotating 90° CCW", vertical, horizontal, ratio)
        return img.rotate(90, expand=True)

    return img


def auto_contrast(img: Image.Image, factor: float = 1.3) -> Image.Image:
    """Boost contrast to make faded thermal receipts more readable."""
    return ImageEnhance.Contrast(img).enhance(factor)


def sharpen(img: Image.Image, factor: float = 1.3) -> Image.Image:
    """Sharpen slightly to recover soft/blurry text."""
    return ImageEnhance.Sharpness(img).enhance(factor)


def trim_whitespace(img: Image.Image, threshold: int = 240) -> Image.Image:
    """Crop uniform-colored borders (desk, counter, etc.)."""
    gray = img.convert("L")
    # Create a mask of non-white pixels
    arr = np.array(gray)
    mask = arr < threshold
    if not mask.any():
        return img
    coords = np.argwhere(mask)
    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0)
    # Add small padding
    pad = 10
    y0 = max(0, y0 - pad)
    x0 = max(0, x0 - pad)
    y1 = min(arr.shape[0], y1 + pad)
    x1 = min(arr.shape[1], x1 + pad)
    return img.crop((x0, y0, x1, y1))


def limit_size(img: Image.Image, max_dim: int = 2000) -> Image.Image:
    """Resize to cap the longest side, preserving aspect ratio."""
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    if w > h:
        new_w, new_h = max_dim, int(h * max_dim / w)
    else:
        new_h, new_w = max_dim, int(w * max_dim / h)
    return img.resize((new_w, new_h), Image.LANCZOS)


# --- OpenCV-based ---


def _pil_to_cv(img: Image.Image) -> np.ndarray:
    """Convert PIL Image to OpenCV BGR array."""
    rgb = np.array(img.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _cv_to_pil(arr: np.ndarray) -> Image.Image:
    """Convert OpenCV BGR array to PIL Image."""
    rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def adaptive_threshold(img: Image.Image) -> Image.Image:
    """Convert to high-contrast black-on-white for maximum readability.

    Great for fading thermal receipts. Returns a grayscale image.
    """
    cv_img = _pil_to_cv(img)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    # Gaussian adaptive threshold for uneven lighting
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10
    )
    return Image.fromarray(thresh)


def deskew(img: Image.Image) -> Image.Image:
    """Detect text angle and rotate to straighten tilted photos.

    Uses Hough line transform to find the dominant angle.
    """
    cv_img = _pil_to_cv(img)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)

    # Edge detection
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    # Hough lines
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10)
    if lines is None:
        return img

    # Compute angles of all detected lines
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        # Only consider near-horizontal lines (within 45 degrees)
        if -45 < angle < 45:
            angles.append(angle)

    if not angles:
        return img

    # Median angle is more robust than mean
    median_angle = float(np.median(angles))

    # Only deskew if the tilt is significant but not too extreme
    if abs(median_angle) < 0.5 or abs(median_angle) > 15:
        return img

    logger.info("Deskewing by %.1f degrees", median_angle)

    # Rotate
    h, w = cv_img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)

    # Compute new bounding box size
    cos = abs(M[0, 0])
    sin = abs(M[0, 1])
    new_w = int(h * sin + w * cos)
    new_h = int(h * cos + w * sin)
    M[0, 2] += (new_w - w) / 2
    M[1, 2] += (new_h - h) / 2

    rotated = cv2.warpAffine(cv_img, M, (new_w, new_h), borderMode=cv2.BORDER_REPLICATE)
    return _cv_to_pil(rotated)


def perspective_correct(img: Image.Image) -> Image.Image:
    """Find the receipt rectangle and warp it flat.

    Uses contour detection to find the largest quadrilateral.
    Returns original if no clear rectangle is found.
    """
    cv_img = _pil_to_cv(img)
    h, w = cv_img.shape[:2]
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)

    # Dilate to close gaps in edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img

    # Find the largest contour by area
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    receipt_contour = None
    for contour in contours[:5]:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)

        # Must be a quadrilateral and cover at least 20% of the image
        area = cv2.contourArea(approx)
        if len(approx) == 4 and area > 0.2 * w * h:
            receipt_contour = approx
            break

    if receipt_contour is None:
        return img

    # Order points: top-left, top-right, bottom-right, bottom-left
    pts = receipt_contour.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1)
    ordered = np.array([
        pts[np.argmin(s)],      # top-left
        pts[np.argmin(d)],      # top-right
        pts[np.argmax(s)],      # bottom-right
        pts[np.argmax(d)],      # bottom-left
    ], dtype=np.float32)

    # Compute output dimensions
    w1 = np.linalg.norm(ordered[1] - ordered[0])
    w2 = np.linalg.norm(ordered[2] - ordered[3])
    h1 = np.linalg.norm(ordered[3] - ordered[0])
    h2 = np.linalg.norm(ordered[2] - ordered[1])
    out_w = int(max(w1, w2))
    out_h = int(max(h1, h2))

    dst = np.array([
        [0, 0], [out_w - 1, 0],
        [out_w - 1, out_h - 1], [0, out_h - 1],
    ], dtype=np.float32)

    M = cv2.getPerspectiveTransform(ordered, dst)
    warped = cv2.warpPerspective(cv_img, M, (out_w, out_h))

    logger.info("Perspective corrected to %dx%d", out_w, out_h)
    return _cv_to_pil(warped)


def edge_crop(img: Image.Image) -> Image.Image:
    """Tighter crop using edge detection — finds the receipt boundary
    even on non-uniform backgrounds."""
    cv_img = _pil_to_cv(img)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 30, 100)

    # Dilate to connect edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=3)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img

    # Largest contour bounding box
    largest = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest)

    # Only crop if the detected region is at least 30% of the image
    img_area = cv_img.shape[0] * cv_img.shape[1]
    if w * h < 0.3 * img_area:
        return img

    # Add padding
    pad = 15
    x = max(0, x - pad)
    y = max(0, y - pad)
    w = min(cv_img.shape[1] - x, w + 2 * pad)
    h = min(cv_img.shape[0] - y, h + 2 * pad)

    cropped = cv_img[y : y + h, x : x + w]
    return _cv_to_pil(cropped)


# --- Pipeline ---


def enhance_receipt(img: Image.Image) -> Image.Image:
    """Full enhancement pipeline. Returns the enhanced image."""
    img = fix_orientation(img)
    img = deskew(img)
    img = edge_crop(img)
    img = auto_contrast(img)
    img = sharpen(img)
    img = limit_size(img)
    return img
