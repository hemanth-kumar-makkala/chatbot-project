"""Port interface for ImageProcessor.

Provides abstract contracts for image transformations, format conversions,
and constraint enforcement before sending page images to multimodal models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ImageProcessor(ABC):
    """Abstract port for document and page image transformations."""

    @abstractmethod
    def prepare_image_for_vision(
        self,
        image_bytes: bytes,
        max_dimension: int,
        max_bytes: int,
        min_dimension: int = 512,
    ) -> bytes:
        """Losslessly downscale image to PNG format respecting max_dimension and max_bytes.

        Args:
            image_bytes: Raw input image bytes.
            max_dimension: Maximum allowed width or height in pixels.
            max_bytes: Maximum allowed payload size in bytes.
            min_dimension: Safety floor for iterative reduction. If the image
                cannot fit max_bytes above this floor, raises ImageProcessingError.

        Returns:
            bytes: Valid lossless PNG image bytes satisfying constraints.

        Raises:
            ImageProcessingError: If the image is corrupt, cannot be read,
                or cannot satisfy max_bytes without falling below min_dimension.
        """
        ...
