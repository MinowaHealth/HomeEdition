"""Quality label assignment from OCR confidence scores."""


def confidence_to_label(confidence: float) -> str:
    """Map OCR confidence (0.0-1.0) to traffic-light quality label.

    Args:
        confidence: Average word-level confidence from Tesseract (0.0-1.0).

    Returns:
        'green' (>= 0.85), 'yellow' (>= 0.60), or 'red' (< 0.60).
    """
    if confidence >= 0.85:
        return "green"
    elif confidence >= 0.60:
        return "yellow"
    else:
        return "red"


def worst_label(labels: list[str]) -> str:
    """Return the worst quality label from a list.

    Document-level label = worst page label. One red page makes
    the whole document red (conservative for provider trust).

    Args:
        labels: List of per-page quality labels.

    Returns:
        The worst label, or 'unknown' if empty.
    """
    if not labels:
        return "unknown"

    rank = {"red": 0, "yellow": 1, "green": 2}
    return min(labels, key=lambda label: rank.get(label, -1))
