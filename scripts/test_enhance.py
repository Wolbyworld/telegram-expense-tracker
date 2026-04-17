"""Dry-run: apply each enhancement function to every receipt image.

Outputs to receipts/enhanced/ with one subfolder per function plus a 'pipeline' folder
for the full pipeline result.

Usage:
    uv run python scripts/test_enhance.py
"""

import os
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.image_enhance import (
    adaptive_threshold,
    auto_contrast,
    deskew,
    edge_crop,
    enhance_receipt,
    fix_orientation,
    limit_size,
    perspective_correct,
    sharpen,
    trim_whitespace,
)

RECEIPTS_DIR = Path("receipts")
OUTPUT_DIR = RECEIPTS_DIR / "enhanced"

FUNCTIONS = [
    ("01_fix_orientation", fix_orientation),
    ("02_auto_contrast", auto_contrast),
    ("03_sharpen", sharpen),
    ("04_trim_whitespace", trim_whitespace),
    ("05_limit_size", limit_size),
    ("06_adaptive_threshold", adaptive_threshold),
    ("07_deskew", deskew),
    ("08_perspective_correct", perspective_correct),
    ("09_edge_crop", edge_crop),
    ("10_full_pipeline", enhance_receipt),
]


def main():
    images = sorted(RECEIPTS_DIR.glob("*.jpg")) + sorted(RECEIPTS_DIR.glob("*.png"))
    if not images:
        print("No receipt images found in", RECEIPTS_DIR)
        return

    print(f"Found {len(images)} receipt images\n")

    for func_name, func in FUNCTIONS:
        out_dir = OUTPUT_DIR / func_name
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"--- {func_name} ---")
        for img_path in images:
            try:
                img = Image.open(img_path)
                start = time.time()
                result = func(img)
                elapsed = time.time() - start

                out_path = out_dir / img_path.name
                if result.mode == "L":
                    result.save(out_path)
                else:
                    result.save(out_path, "JPEG", quality=90)

                orig_size = f"{img.size[0]}x{img.size[1]}"
                new_size = f"{result.size[0]}x{result.size[1]}"
                changed = "CHANGED" if orig_size != new_size or func_name in ("02_auto_contrast", "03_sharpen", "06_adaptive_threshold") else "same"

                print(f"  {img_path.name}: {orig_size} -> {new_size} ({elapsed:.2f}s) [{changed}]")
            except Exception as e:
                print(f"  {img_path.name}: ERROR - {e}")

        print()

    print(f"Results saved to {OUTPUT_DIR}/")
    print("Compare visually: open the folders side by side.")


if __name__ == "__main__":
    main()
