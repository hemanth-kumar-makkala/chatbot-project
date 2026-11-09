"use client";

import React from "react";

export interface ConnectionBadgeProps {
  status: "connected" | "not_connected";
  route?: string;
  label?: string;
}

export function ConnectionBadge({ status, route, label }: ConnectionBadgeProps) {
  const isConnected = status === "connected";
  const defaultLabel = isConnected ? "Connected" : "Not connected yet";
  const text = label || defaultLabel;

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "6px",
          padding: "2px 8px",
          borderRadius: "var(--radius-full)",
          fontSize: "var(--font-size-xs)",
          fontWeight: 600,
          backgroundColor: isConnected
            ? "var(--color-status-success-bg)"
            : "var(--color-bg-subtle)",
          color: isConnected
            ? "var(--color-status-success-text)"
            : "var(--color-text-secondary)",
          border: isConnected ? "none" : "1px solid var(--color-border)",
          lineHeight: 1.4,
        }}
        data-testid={`connection-badge-${status}`}
      >
        <span
          style={{
            width: "6px",
            height: "6px",
            borderRadius: "50%",
            backgroundColor: isConnected
              ? "var(--color-status-success-text)"
              : "var(--color-text-secondary)",
          }}
        />
        {text}
      </span>
      {route && (
        <span
          style={{
            fontSize: "var(--font-size-xs)",
            color: "var(--color-text-secondary)",
          }}
        >
          ({route}{!isConnected ? ", not built yet" : ""})
        </span>
      )}
    </div>
  );
}
