"""Pillow adapter implementing ImageProcessor port.

Provides lossless downscaling, PNG format conversion, and iterative size reduction.
"""

from __future__ import annotations

import io

from PIL import Image

from app.ports.errors import ImageProcessingError
from app.ports.image_processor import ImageProcessor


class PillowImageProcessor(ImageProcessor):
    """Pillow implementation for page image preparation and scaling."""

    def prepare_image_for_vision(
        self,
        image_bytes: bytes,
        max_dimension: int,
        max_bytes: int,
        min_dimension: int = 512,
    ) -> bytes:
        """Losslessly downscale image to PNG format respecting max_dimension and max_bytes.

        Iteratively scales down by 0.85 factor if payload exceeds max_bytes down to
        min_dimension floor. Raises ImageProcessingError if constraints cannot be met.
        """
        try:
            with Image.open(io.BytesIO(image_bytes)) as raw_img:
                # Convert palette/transparency to standard RGB for consistent encoding
                current_img: Image.Image = (
                    raw_img.convert("RGB") if raw_img.mode not in ("RGB", "L") else raw_img.copy()
                )

                width, height = current_img.size
                current_max = max(width, height)

                # 1. Initial downscale if exceeding max_dimension
                if current_max > max_dimension:
                    scale = max_dimension / float(current_max)
                    new_w = max(1, int(round(width * scale)))
                    new_h = max(1, int(round(height * scale)))
                    current_img = current_img.resize(
                        (new_w, new_h), resample=Image.Resampling.LANCZOS
                    )
                    width, height = current_img.size
                    current_max = max(width, height)

                # 2. Save as lossless PNG
                buf = io.BytesIO()
                current_img.save(buf, format="PNG", optimize=True)
                payload = buf.getvalue()

                # 3. Iterative reduction if exceeding max_bytes
                while len(payload) > max_bytes:
                    next_max = int(current_max * 0.85)
                    if next_max < min_dimension or current_max <= min_dimension:
                        raise ImageProcessingError(
                            message=f"Image exceeds byte cap ({len(payload)} > {max_bytes}) "
                            f"at min_dimension floor ({min_dimension}px)",
                            code="IMAGE_TOO_LARGE",
                        )

                    scale = next_max / float(current_max)
                    new_w = max(1, int(round(width * scale)))
                    new_h = max(1, int(round(height * scale)))
                    current_img = current_img.resize(
                        (new_w, new_h), resample=Image.Resampling.LANCZOS
                    )
                    width, height = current_img.size
                    current_max = max(width, height)

                    buf = io.BytesIO()
                    current_img.save(buf, format="PNG", optimize=True)
                    payload = buf.getvalue()

                return payload
        except ImageProcessingError:
            raise
        except Exception as exc:
            raise ImageProcessingError(
                message="Failed to process image bytes",
                code="IMAGE_CORRUPT",
            ) from exc
