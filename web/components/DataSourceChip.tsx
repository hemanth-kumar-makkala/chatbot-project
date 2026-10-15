"use client";

import React from "react";
import type { DataSourceMode } from "@/lib/connections";

interface DataSourceChipProps {
  mode: DataSourceMode;
  label?: string;
}

export function DataSourceChip({ mode, label }: DataSourceChipProps) {
  const isLive = mode === "live";
  const displayLabel = label || (isLive ? "Live" : "Demo");

  return (
    <span
      data-testid="data-source-chip"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "5px",
        fontSize: "var(--font-size-xs)",
        fontWeight: 600,
        padding: "2px 8px",
        borderRadius: "var(--radius-sm)",
        backgroundColor: isLive ? "var(--color-status-success-bg)" : "var(--color-lavender)",
        color: isLive ? "var(--color-status-success-text)" : "var(--color-navy)",
        border: isLive ? "1px solid #C8E6C9" : "1px solid #C4B0F7",
        letterSpacing: "0.2px",
        userSelect: "none",
        lineHeight: 1.4,
      }}
    >
      <span
        style={{
          display: "inline-block",
          width: "6px",
          height: "6px",
          borderRadius: "50%",
          backgroundColor: isLive ? "var(--color-status-success-text)" : "var(--color-purple)",
        }}
      />
      {displayLabel}
    </span>
  );
}
