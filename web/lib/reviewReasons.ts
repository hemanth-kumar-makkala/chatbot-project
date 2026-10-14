/**
 * Review reason labels per docs/ui/KNOWLEDGE_PAGE_SPEC.md:
 * - low_ocr_confidence: "Text unclear"
 * - vision_failed: "Image read failed"
 * - vision_low_confidence: "Image read uncertain"
 * - vision_confidence_missing: "Image read not scored"
 * - image_too_large: "Image too large"
 * - processing_failed: "Processing failed"
 * - legacy_unspecified: "Needs review (older upload)"
 *
 * Unknown values show "Needs review" and log a UI warning with the code only.
 */

export const REVIEW_REASON_LABELS: Record<string, string> = {
  low_ocr_confidence: "Text unclear",
  vision_failed: "Image read failed",
  vision_low_confidence: "Image read uncertain",
  vision_confidence_missing: "Image read not scored",
  image_too_large: "Image too large",
  processing_failed: "Processing failed",
  legacy_unspecified: "Needs review (older upload)",
};

export function getReviewReasonLabel(code: string | null | undefined): string {
  if (!code) {
    return "Needs review";
  }

  const label = REVIEW_REASON_LABELS[code];
  if (label) {
    return label;
  }

  // Unknown values show "Needs review" and log a UI warning with the code only
  console.warn(`[UI Warning] Unknown review reason code: ${code}`);
  return "Needs review";
}
