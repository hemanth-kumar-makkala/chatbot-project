"use client";

import React from "react";
import type { DataSourceMode } from "@/lib/connections";

interface DataSourceChipProps {
  mode: DataSourceMode;
  label?: string;
}

export function DataSourceChip({ mode, label }: DataSourceChipProps) {
  const isConnected = mode === "live";
  const displayLabel = label || (isConnected ? "Connected" : "Not connected yet");

  return (
    <span
      data-testid="data-source-chip"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        fontSize: "var(--font-size-xs)",
        fontWeight: 600,
        padding: "2px 8px",
        borderRadius: "var(--radius-full)",
        backgroundColor: isConnected
          ? "var(--color-status-success-bg)"
          : "var(--color-bg-subtle)",
        color: isConnected
          ? "var(--color-status-success-text)"
          : "var(--color-text-secondary)",
        border: isConnected ? "none" : "1px solid var(--color-border)",
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
          backgroundColor: isConnected
            ? "var(--color-status-success-text)"
            : "var(--color-text-secondary)",
        }}
      />
      {displayLabel}
    </span>
  );
}
