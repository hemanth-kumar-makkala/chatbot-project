"""Unit tests for ImageProcessor port and PillowImageProcessor adapter (Task 2.5).

Tests:
1. Downscaling produces lossless PNG within max_dimension.
2. Aspect ratio is preserved.
3. Iterative reduction scales down oversized images to fit within max_bytes.
4. If image cannot fit byte cap at min_dimension floor, raises ImageProcessingError.
"""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image

from app.adapters.images.pillow import PillowImageProcessor
from app.ports.errors import ImageProcessingError
from app.ports.image_processor import ImageProcessor


def _create_synthetic_test_image(width: int, height: int, pattern: str = "solid") -> bytes:
    """Helper creating raw image bytes of specified dimensions."""
    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    if pattern == "noise":
        # Draw high-entropy pattern to increase compressed size
        pixels = img.load()
        assert pixels is not None
        for x in range(0, width, 4):
            for y in range(0, height, 4):
                pixels[x, y] = (
                    random.randint(0, 255),
                    random.randint(0, 255),
                    random.randint(0, 255),
                )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestPillowImageProcessor:
    """Tests the Pillow implementation of the ImageProcessor port."""

    def test_implements_port_interface(self) -> None:
        """PillowImageProcessor is an instance of ImageProcessor port."""
        processor = PillowImageProcessor()
        assert isinstance(processor, ImageProcessor)

    def test_downscales_to_max_dimension_lossless_png(self) -> None:
        """Large image (2000x3000) is downscaled to max_dimension (1024) as a valid PNG."""
        raw_bytes = _create_synthetic_test_image(2000, 3000)
        processor = PillowImageProcessor()

        processed_bytes = processor.prepare_image_for_vision(
            image_bytes=raw_bytes,
            max_dimension=1024,
            max_bytes=2_000_000,
            min_dimension=512,
        )

        # 1. Valid lossless PNG magic bytes
        assert processed_bytes.startswith(b"\x89PNG\r\n\x1a\n")

        # 2. Max dimension is respected and aspect ratio preserved
        with Image.open(io.BytesIO(processed_bytes)) as img:
            assert max(img.width, img.height) <= 1024
            # 2000/3000 = 2/3 -> height should be 1024, width ~682
            assert img.height == 1024
            assert abs(img.width - 682) <= 2
            assert img.format == "PNG"

    def test_small_image_not_upscaled(self) -> None:
        """Image smaller than max_dimension is converted to PNG without upscaling."""
        raw_bytes = _create_synthetic_test_image(400, 300)
        processor = PillowImageProcessor()

        processed_bytes = processor.prepare_image_for_vision(
            image_bytes=raw_bytes,
            max_dimension=1024,
            max_bytes=2_000_000,
            min_dimension=512,
        )

        with Image.open(io.BytesIO(processed_bytes)) as img:
            assert img.width == 400
            assert img.height == 300
            assert img.format == "PNG"

    def test_iterative_reduction_under_byte_cap(self) -> None:
        """High-entropy image that exceeds max_bytes at initial scale is iteratively reduced."""
        raw_bytes = _create_synthetic_test_image(1200, 1200, pattern="noise")
        processor = PillowImageProcessor()

        # Strict cap of 150 KB forces iterative downscale from initial ~450 KB
        processed_bytes = processor.prepare_image_for_vision(
            image_bytes=raw_bytes,
            max_dimension=1024,
            max_bytes=150_000,
            min_dimension=256,
        )

        assert len(processed_bytes) <= 150_000
        with Image.open(io.BytesIO(processed_bytes)) as img:
            assert img.format == "PNG"
            assert max(img.width, img.height) <= 1024

    def test_exceeding_byte_cap_at_min_dimension_floor_raises_error(self) -> None:
        """If image cannot fit byte cap at min_dimension floor, ImageProcessingError is raised."""
        raw_bytes = _create_synthetic_test_image(1000, 1000, pattern="noise")
        processor = PillowImageProcessor()

        # Impossible 100-byte cap on a noisy image with 512px floor
        with pytest.raises(ImageProcessingError) as exc_info:
            processor.prepare_image_for_vision(
                image_bytes=raw_bytes,
                max_dimension=1024,
                max_bytes=100,
                min_dimension=512,
            )
        assert exc_info.value.code == "IMAGE_TOO_LARGE"
