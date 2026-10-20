import React from "react";
import { getStatusChipConfig, type ChipVariant } from "@/lib/statusChips";

interface StatusChipProps {
  status: string;
  flaggedPagesCount?: number;
}

const variantStyles: Record<ChipVariant, { bg: string; color: string; border?: string }> = {
  queued: {
    bg: "var(--color-neutral-bg)",
    color: "var(--color-neutral-text)",
  },
  reading: {
    bg: "var(--color-info-soft)",
    color: "var(--color-info)",
  },
  scanning_images: {
    bg: "var(--color-info-soft)",
    color: "var(--color-info)",
  },
  ready: {
    bg: "var(--color-status-success-bg)",
    color: "var(--color-status-success-text)",
  },
  review: {
    bg: "var(--color-warning-soft)",
    color: "var(--color-warning-text)",
    border: "1px solid var(--color-border)",
  },
  failed: {
    bg: "var(--color-status-error-bg)",
    color: "var(--color-status-error-text)",
  },
  retired: {
    bg: "var(--color-neutral-bg)",
    color: "var(--color-neutral-text)",
  },
  unknown: {
    bg: "var(--color-neutral-bg)",
    color: "var(--color-neutral-text)",
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
