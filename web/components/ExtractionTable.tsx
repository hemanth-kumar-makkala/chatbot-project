"use client";

import React from "react";
import type { ExtractedField, FieldExtractionState } from "@/lib/api";

interface ExtractionTableProps {
  fields: ExtractedField[];
  onOpenSource: (documentTitle: string, pageNumber: number) => void;
}

const KNOWN_STATES: Record<
  string,
  { label: string; bg: string; text: string; border: string }
> = {
  FILLED_HIGH: {
    label: "FILLED_HIGH",
    bg: "var(--color-status-success-bg)",
    text: "var(--color-status-success-text)",
    border: "#C8E6C9",
  },
  FILLED_MEDIUM: {
    label: "FILLED_MEDIUM",
    bg: "#FFF8E1",
    text: "var(--color-dark-gold)",
    border: "#FFE082",
  },
  CONFLICTING: {
    label: "CONFLICTING",
    bg: "#FFF3E0",
    text: "#E65100",
    border: "#FFCC80",
  },
  UNREADABLE: {
    label: "UNREADABLE",
    bg: "var(--color-status-error-bg)",
    text: "var(--color-status-error-text)",
    border: "#FFCDD2",
  },
  INVALID: {
    label: "INVALID",
    bg: "#FFEBEE",
    text: "#C62828",
    border: "#EF9A9A",
  },
  NOT_PRESENT: {
    label: "NOT_PRESENT",
    bg: "var(--color-status-queued-bg)",
    text: "#546E7A",
    border: "#CFD8DC",
  },
  NOT_APPLICABLE: {
    label: "NOT_APPLICABLE",
    bg: "var(--color-status-retired-bg)",
    text: "#616161",
    border: "#E0E0E0",
  },
};

export function ExtractionTable({ fields, onOpenSource }: ExtractionTableProps) {
  return (
    <div
      data-testid="extraction-field-table"
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-md)",
        overflow: "hidden",
        backgroundColor: "var(--color-bg-card)",
      }}
    >
      <div
        style={{
          padding: "var(--spacing-2) var(--spacing-3)",
          backgroundColor: "var(--color-bg-subtle)",
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span
          style={{
            fontSize: "var(--font-size-xs)",
            fontWeight: 700,
            color: "var(--color-navy)",
            textTransform: "uppercase",
            letterSpacing: "0.5px",
          }}
        >
          Extracted Field Adjudication ({fields.length} fields)
        </span>
        <span
          style={{
            fontSize: "11px",
            color: "var(--color-text-muted)",
          }}
        >
          Click row to inspect source page
        </span>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: "var(--font-size-xs)",
            textAlign: "left",
          }}
        >
          <thead>
            <tr
              style={{
                backgroundColor: "var(--color-bg-subtle)",
                borderBottom: "1px solid var(--color-border)",
                color: "var(--color-text-muted)",
              }}
            >
              <th style={{ padding: "8px 12px", fontWeight: 600 }}>Field Name</th>
              <th style={{ padding: "8px 12px", fontWeight: 600 }}>Extracted Value</th>
              <th style={{ padding: "8px 12px", fontWeight: 600 }}>Status Badge</th>
              <th style={{ padding: "8px 12px", fontWeight: 600, textAlign: "right" }}>
                Source
              </th>
            </tr>
          </thead>
          <tbody>
            {fields.map((field, idx) => {
              const stateKey = String(field.state || "").toUpperCase();
              const isKnown = Boolean(KNOWN_STATES[stateKey]);

              if (!isKnown) {
                // Unknown field state from data: logs the code only (spec & task rule)
                console.warn("Unknown extraction field state:", field.state);
              }

              const badgeConfig = isKnown
                ? KNOWN_STATES[stateKey]
                : {
                    label: "UNKNOWN",
                    bg: "var(--color-status-queued-bg)",
                    text: "#757575",
                    border: "#BDBDBD",
                  };

              const isUnreadable = stateKey === "UNREADABLE";
              const isConflicting = stateKey === "CONFLICTING";
              const isHighlighted = isUnreadable || isConflicting;

              const rowBg = isUnreadable
                ? "#FFF5F5"
                : isConflicting
                ? "#FFF9F0"
                : idx % 2 === 0
                ? "var(--color-white)"
                : "var(--color-bg-subtle)";

              const rowTestId = `extraction-row-${field.name
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, "-")
                .replace(/^-|-$/g, "")}`;

              return (
                <tr
                  key={`field-${idx}-${field.name}`}
                  data-testid={rowTestId}
                  data-highlighted={isHighlighted ? "true" : "false"}
                  onClick={() => onOpenSource(field.document_title, field.page_number)}
                  title={`Click to inspect ${field.document_title} at page ${field.page_number}`}
                  style={{
                    backgroundColor: rowBg,
                    borderBottom: "1px solid var(--color-border-subtle)",
                    cursor: "pointer",
                    transition: "background-color 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = isHighlighted
                      ? isUnreadable
                        ? "#FFEAEA"
                        : "#FFF0E0"
                      : "#F0F4FA";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor = rowBg;
                  }}
                >
                  {/* Field Name */}
                  <td
                    style={{
                      padding: "8px 12px",
                      fontWeight: 600,
                      color: "var(--color-navy)",
                      verticalAlign: "middle",
                    }}
                  >
                    {field.name}
                  </td>

                  {/* Extracted Value Cell */}
                  <td
                    data-testid="field-value-cell"
                    style={{
                      padding: "8px 12px",
                      verticalAlign: "middle",
                      color: "var(--color-text-main)",
                    }}
                  >
                    {isUnreadable ? (
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "4px",
                          color: "var(--color-status-error-text)",
                          fontWeight: 600,
                          fontSize: "11px",
                        }}
                      >
                        <span data-testid="unreadable-flag" title="Unreadable field flagged, never guessed">
                          ⚠️
                        </span>
                        [Blank - Unreadable]
                      </span>
                    ) : isConflicting ? (
                      <div
                        data-testid="conflicting-readings"
                        style={{
                          display: "inline-flex",
                          gap: "8px",
                          alignItems: "center",
                          backgroundColor: "#FFE0B2",
                          padding: "2px 6px",
                          borderRadius: "4px",
                          fontSize: "11px",
                        }}
                      >
                        <span>
                          Read 1: <strong>{field.read1_value || "—"}</strong>
                        </span>
                        <span>|</span>
                        <span>
                          Read 2: <strong>{field.read2_value || "—"}</strong>
                        </span>
                      </div>
                    ) : stateKey === "INVALID" ? (
                      <div>
                        <span style={{ fontWeight: 600 }}>{field.value || "—"}</span>
                        {field.validator_error && (
                          <span
                            style={{
                              display: "block",
                              fontSize: "10px",
                              color: "#C62828",
                              marginTop: "2px",
                            }}
                          >
                            (Validator: {field.validator_error})
                          </span>
                        )}
                      </div>
                    ) : stateKey === "NOT_PRESENT" ? (
                      <span style={{ color: "var(--color-text-muted)" }}>—</span>
                    ) : stateKey === "NOT_APPLICABLE" ? (
                      <span style={{ color: "var(--color-text-muted)" }}>N/A</span>
                    ) : (
                      <span>{field.value || "—"}</span>
                    )}
                  </td>

                  {/* Field State Badge */}
                  <td style={{ padding: "8px 12px", verticalAlign: "middle" }}>
                    <span
                      data-testid={`field-badge-${badgeConfig.label}`}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        padding: "2px 8px",
                        borderRadius: "12px",
                        fontSize: "10px",
                        fontWeight: 700,
                        backgroundColor: badgeConfig.bg,
                        color: badgeConfig.text,
                        border: `1px solid ${badgeConfig.border}`,
                        letterSpacing: "0.4px",
                      }}
                    >
                      {badgeConfig.label}
                    </span>
                  </td>

                  {/* Cited Page Reference */}
                  <td
                    style={{
                      padding: "8px 12px",
                      textAlign: "right",
                      verticalAlign: "middle",
                    }}
                  >
                    <span
                      style={{
                        fontSize: "11px",
                        color: "var(--color-primary-blue)",
                        fontWeight: 600,
                      }}
                    >
                      p. {field.page_number}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
