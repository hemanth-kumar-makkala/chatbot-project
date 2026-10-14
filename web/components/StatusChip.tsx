import React from "react";
import { getStatusChipConfig, type ChipVariant } from "@/lib/statusChips";

interface StatusChipProps {
  status: string;
  flaggedPagesCount?: number;
}

const variantStyles: Record<ChipVariant, { bg: string; color: string; border?: string }> = {
  queued: {
    bg: "var(--color-status-queued-bg)",
    color: "var(--color-status-queued-text)",
  },
  reading: {
    bg: "var(--color-status-in-progress-bg)",
    color: "var(--color-status-in-progress-text)",
  },
  scanning_images: {
    bg: "#EDE7F6",
    color: "var(--color-purple)",
  },
  ready: {
    bg: "var(--color-status-success-bg)",
    color: "var(--color-status-success-text)",
  },
  review: {
    bg: "var(--color-lavender)",
    color: "var(--color-navy)",
    border: "1px solid rgba(95, 61, 191, 0.3)",
  },
  failed: {
    bg: "var(--color-status-error-bg)",
    color: "var(--color-status-error-text)",
  },
  retired: {
    bg: "var(--color-status-retired-bg)",
    color: "var(--color-status-retired-text)",
  },
  unknown: {
    bg: "var(--color-border-subtle)",
    color: "var(--color-text-muted)",
  },
};

export function StatusChip({ status, flaggedPagesCount = 0 }: StatusChipProps) {
  const config = getStatusChipConfig(status, flaggedPagesCount);
  const style = variantStyles[config.variant] || variantStyles.unknown;

  return (
    <span
      data-testid="status-chip"
      data-variant={config.variant}
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "4px 10px",
        borderRadius: "var(--radius-full)",
        fontSize: "var(--font-size-xs)",
        fontWeight: 600,
        backgroundColor: style.bg,
        color: style.color,
        border: style.border || "none",
        whiteSpace: "nowrap",
      }}
    >
      {config.label}
    </span>
  );
}
