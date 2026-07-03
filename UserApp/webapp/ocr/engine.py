"""OCR engine: page rendering, pre-processing, and Tesseract extraction.

Handles PDFs, TIFFs, JPEGs, and PNGs. Pre-processes degraded fax images
(deskew, threshold, denoise) before running Tesseract LSTM.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import magic
import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageOps

log = logging.getLogger(__name__)

# Tesseract engine config
OCR_LANGUAGES = os.environ.get("OCR_LANGUAGES", "eng")
TESSERACT_OEM = 3   # LSTM neural net engine
TESSERACT_PSM = 6   # Assume uniform block of text


@dataclass
class PageResult:
    """OCR result for a single page."""
    page_number: int
    text: str
    confidence: float      # 0.0–1.0
    image_path: str        # saved page PNG path


def render_pages(file_path: str, output_dir: str) -> list[Path]:
    """Convert a document file to per-page PNG images.

    Args:
        file_path: Path to the original document (PDF, TIFF, JPEG, PNG).
        output_dir: Directory to write page_NNN.png files.

    Returns:
        List of paths to rendered page images, ordered by page number.
    """
    mime = magic.from_file(file_path, mime=True)
    os.makedirs(output_dir, exist_ok=True)

    if mime == "application/pdf":
        return _render_pdf(file_path, output_dir)
    elif mime in ("image/tiff", "image/x-tiff"):
        return _render_tiff(file_path, output_dir)
    elif mime in ("image/jpeg", "image/png", "image/bmp", "image/webp"):
        return _render_single_image(file_path, output_dir)
    else:
        raise ValueError(f"Unsupported MIME type for OCR: {mime}")


def _render_pdf(file_path: str, output_dir: str) -> list[Path]:
    """Convert PDF pages to PNGs using poppler (pdftoppm)."""
    images = convert_from_path(file_path, dpi=300, fmt="png")
    paths = []
    for i, img in enumerate(images, start=1):
        page_path = Path(output_dir) / f"page_{i:03d}.png"
        img.save(str(page_path), "PNG")
        paths.append(page_path)
    log.info("Rendered %d pages from PDF: %s", len(paths), file_path)
    return paths


def _render_tiff(file_path: str, output_dir: str) -> list[Path]:
    """Convert multi-page TIFF (common fax format) to per-page PNGs."""
    img = Image.open(file_path)
    paths = []
    page = 0
    while True:
        page += 1
        page_path = Path(output_dir) / f"page_{page:03d}.png"
        img.save(str(page_path), "PNG")
        paths.append(page_path)
        try:
            img.seek(page)  # 0-indexed seek, page is already incremented
        except EOFError:
            break
    log.info("Rendered %d pages from TIFF: %s", len(paths), file_path)
    return paths


def _render_single_image(file_path: str, output_dir: str) -> list[Path]:
    """Single-page image — copy as page_001.png."""
    page_path = Path(output_dir) / "page_001.png"
    img = Image.open(file_path)
    img.save(str(page_path), "PNG")
    log.info("Rendered 1 page from image: %s", file_path)
    return [page_path]


def preprocess(image: Image.Image) -> Image.Image:
    """Pre-process an image for OCR accuracy on degraded fax documents.

    Pipeline:
        1. Grayscale conversion
        2. Deskew (via min-area bounding)
        3. Adaptive thresholding (Otsu's method)
        4. Noise reduction (median filter)
        5. Border removal (auto-crop black borders)

    Args:
        image: Input PIL Image.

    Returns:
        Pre-processed PIL Image ready for Tesseract.
    """
    # 1. Grayscale
    img = ImageOps.grayscale(image)

    # 2. Deskew — use Tesseract's own OSD for angle detection
    #    (more reliable than Hough on document images)
    try:
        osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        angle = osd.get("rotate", 0)
        if angle and angle != 0:
            img = img.rotate(-angle, expand=True, fillcolor=255)
            log.debug("Deskewed by %d degrees", angle)
    except pytesseract.TesseractError:
        log.debug("OSD detection failed, skipping deskew")

    # 3. Adaptive threshold (Otsu-style via point operation)
    #    Pillow doesn't have cv2.threshold, but we can binarize
    #    with a calculated threshold from the histogram
    histogram = img.histogram()
    total_pixels = sum(histogram)
    running_sum = 0
    weighted_sum = sum(i * h for i, h in enumerate(histogram))
    running_weighted = 0
    best_threshold = 128
    best_variance = 0

    for t in range(256):
        running_sum += histogram[t]
        if running_sum == 0 or running_sum == total_pixels:
            continue
        running_weighted += t * histogram[t]
        mean_bg = running_weighted / running_sum
        mean_fg = (weighted_sum - running_weighted) / (total_pixels - running_sum)
        variance = running_sum * (total_pixels - running_sum) * (mean_bg - mean_fg) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = t

    lut = [255 if i > best_threshold else 0 for i in range(256)]
    img = img.point(lut)

    # 4. Noise reduction — median filter (3x3 kernel)
    #    Removes salt-and-pepper noise common in fax transmissions
    img = img.filter(ImageFilter.MedianFilter(size=3))

    # 5. Border removal — auto-crop white/black borders
    #    Invert, find content bounding box, crop
    inverted = ImageOps.invert(img)
    bbox = inverted.getbbox()
    if bbox:
        # Add small margin (10px) around detected content
        margin = 10
        w, h = img.size
        bbox = (
            max(0, bbox[0] - margin),
            max(0, bbox[1] - margin),
            min(w, bbox[2] + margin),
            min(h, bbox[3] + margin),
        )
        img = img.crop(bbox)

    return img


def ocr_page(page_image_path: str) -> tuple[str, float]:
    """Run OCR on a single page image.

    Opens the image, applies pre-processing, runs Tesseract, and
    calculates average word-level confidence.

    Args:
        page_image_path: Path to page PNG file.

    Returns:
        Tuple of (extracted_text, confidence_0_to_1).
    """
    img = Image.open(page_image_path)
    processed = preprocess(img)

    # Tesseract config string
    config = f"--oem {TESSERACT_OEM} --psm {TESSERACT_PSM}"

    # Get word-level data for confidence calculation
    data = pytesseract.image_to_data(
        processed,
        lang=OCR_LANGUAGES,
        config=config,
        output_type=pytesseract.Output.DICT,
    )

    # Extract text
    text = pytesseract.image_to_string(
        processed,
        lang=OCR_LANGUAGES,
        config=config,
    ).strip()

    # Calculate average confidence from word-level scores
    # Tesseract returns -1 for non-text elements, filter those out
    word_confidences = [
        c / 100.0
        for c in data["conf"]
        if isinstance(c, (int, float)) and c >= 0
    ]

    if word_confidences:
        confidence = sum(word_confidences) / len(word_confidences)
    else:
        confidence = 0.0

    log.debug(
        "OCR page %s: %d words, confidence=%.3f",
        page_image_path,
        len(word_confidences),
        confidence,
    )

    return text, confidence


def process_document(file_path: str, output_dir: str) -> list[PageResult]:
    """Full OCR pipeline: render pages, pre-process, extract text.

    Args:
        file_path: Path to original document file.
        output_dir: Directory for page images and intermediate files.

    Returns:
        List of PageResult objects, one per page.
    """
    page_images = render_pages(file_path, output_dir)
    results = []

    for page_path in page_images:
        page_num = int(page_path.stem.split("_")[1])  # page_001.png → 1
        text, confidence = ocr_page(str(page_path))
        results.append(PageResult(
            page_number=page_num,
            text=text,
            confidence=confidence,
            image_path=str(page_path),
        ))

    log.info(
        "OCR complete: %s — %d pages, confidences: %s",
        file_path,
        len(results),
        [f"{r.confidence:.2f}" for r in results],
    )

    return results
