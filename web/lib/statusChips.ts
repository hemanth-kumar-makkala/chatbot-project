/**
 * Document status chip configuration per docs/ui/KNOWLEDGE_PAGE_SPEC.md:
 * - Queued
 * - Reading
 * - Scanning images
 * - Ready (green)
 * - Ready (N pages need review) (lavender)
 * - Failed (red)
 * - Retired (grey)
 */

export type ChipVariant =
  | "queued"
  | "reading"
  | "scanning_images"
  | "ready"
  | "review"
  | "failed"
  | "retired"
  | "unknown";

export interface StatusChipConfig {
  label: string;
  variant: ChipVariant;
}

export function getStatusChipConfig(
  status: string | null | undefined,
  flaggedPagesCount: number = 0
): StatusChipConfig {
  const normalized = (status || "").toLowerCase().trim();

  switch (normalized) {
    case "queued":
      return {
        label: "Queued",
        variant: "queued",
      };

    case "reading":
      return {
        label: "Reading",
        variant: "reading",
      };

    case "scanning_images":
      return {
        label: "Scanning images",
        variant: "scanning_images",
      };

    case "ready":
      if (flaggedPagesCount > 0) {
        const pageText = flaggedPagesCount === 1 ? "1 page needs review" : `${flaggedPagesCount} pages need review`;
        return {
          label: `Ready (${pageText})`,
          variant: "review",
        };
      }
      return {
        label: "Ready",
        variant: "ready",
      };

    case "failed":
      return {
        label: "Failed",
        variant: "failed",
      };

    case "retired":
      return {
        label: "Retired",
        variant: "retired",
      };

    default:
      return {
        label: status ? status.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "Unknown",
        variant: "unknown",
      };
  }
}

/**
 * Returns true if document is in a final processing state.
 * Documents table refreshes every 5s while any document is NOT in a final state,
 * and stops when all are final.
 * Final states: ready, failed, retired.
 */
export function isFinalStatus(status: string | null | undefined): boolean {
  const s = (status || "").toLowerCase().trim();
  return s === "ready" || s === "failed" || s === "retired";
}
