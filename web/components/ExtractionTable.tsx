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
    border: "var(--color-border)",
  },
  FILLED_MEDIUM: {
    label: "FILLED_MEDIUM",
    bg: "var(--color-warning-soft)",
    text: "var(--color-warning-text)",
    border: "var(--color-border)",
  },
  CONFLICTING: {
    label: "CONFLICTING",
    bg: "var(--color-warning-soft)",
    text: "var(--color-warning-text)",
    border: "var(--color-border)",
  },
  UNREADABLE: {
    label: "UNREADABLE",
    bg: "var(--color-status-error-bg)",
    text: "var(--color-status-error-text)",
    border: "var(--color-border)",
  },
  INVALID: {
    label: "INVALID",
    bg: "var(--color-status-error-bg)",
    text: "var(--color-status-error-text)",
    border: "var(--color-border)",
  },
  NOT_PRESENT: {
    label: "NOT_PRESENT",
    bg: "var(--color-neutral-bg)",
    text: "var(--color-neutral-text)",
    border: "var(--color-border)",
  },
  NOT_APPLICABLE: {
    label: "NOT_APPLICABLE",
    bg: "var(--color-neutral-bg)",
    text: "var(--color-neutral-text)",
    border: "var(--color-border)",
  },
};

export function ExtractionTable({ fields, onOpenSource }: ExtractionTableProps) {
  const [isNotPresentExpanded, setIsNotPresentExpanded] = React.useState(false);

  const isNotPresentState = (f: ExtractedField) => (f.state || "").toUpperCase() === "NOT_PRESENT";
  const presentFields = fields.filter((f) => !isNotPresentState(f));
  const notPresentFields = fields.filter(isNotPresentState);

  const renderFieldRow = (field: ExtractedField, idx: number) => {
    const stateKey = (field.state || "").toUpperCase();
    const isKnown = Object.prototype.hasOwnProperty.call(KNOWN_STATES, stateKey);

    if (!isKnown) {
      // Unknown field state from data: logs the code only (spec & task rule)
      console.warn("Unknown extraction field state:", field.state);
    }

    const badgeConfig = isKnown
      ? KNOWN_STATES[stateKey]
      : {
          label: "UNKNOWN",
          bg: "var(--color-neutral-bg)",
          text: "var(--color-neutral-text)",
          border: "var(--color-border)",
        };

    const isUnreadable = stateKey === "UNREADABLE";
    const isConflicting = stateKey === "CONFLICTING";
    const isHighlighted = isUnreadable || isConflicting;

    const rowBg = isUnreadable
      ? "var(--color-danger-soft)"
      : isConflicting
      ? "var(--color-warning-soft)"
      : idx % 2 === 0
      ? "var(--color-bg-page)"
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
          borderBottom: "1px solid var(--color-border)",
          cursor: "pointer",
          transition: "background-color 0.15s ease",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.backgroundColor = isHighlighted
            ? isUnreadable
              ? "var(--color-danger-soft)"
              : "var(--color-warning-soft)"
            : "var(--color-bg-subtle)";
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
            color: "var(--color-text-primary)",
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
            color: "var(--color-text-primary)",
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
                backgroundColor: "var(--color-warning-soft)",
                padding: "2px 6px",
                borderRadius: "4px",
                fontSize: "11px",
              }}
            >
              <span>
                Read 1 (OCR): <strong>{field.read1_value || "—"}</strong>
              </span>
              <span>|</span>
              <span>
                Read 2 (vision): <strong>{field.read2_value || "—"}</strong>
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
                    color: "var(--color-danger)",
                    marginTop: "2px",
                  }}
                >
                  (Validator: {field.validator_error})
                </span>
              )}
            </div>
          ) : stateKey === "NOT_PRESENT" ? (
            <span style={{ color: "var(--color-text-secondary)" }}>—</span>
          ) : stateKey === "NOT_APPLICABLE" ? (
            <span style={{ color: "var(--color-text-secondary)" }}>N/A</span>
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
              color: "var(--color-primary)",
              fontWeight: 600,
            }}
          >
            p. {field.page_number}
          </span>
        </td>
      </tr>
    );
  };
  return (
    <div
      style={{
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--color-border)",
        overflow: "hidden",
        marginTop: "var(--spacing-3)",
        backgroundColor: "var(--color-bg-page)",
      }}
    >
      <div
        style={{
          padding: "10px 14px",
          backgroundColor: "var(--color-bg-subtle)",
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          style={{
            fontSize: "var(--font-size-xs)",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            color: "var(--color-text-primary)",
          }}
        >
          Extracted Document Fields (Side-by-Side Review)
        </span>
        <span
          style={{
            fontSize: "11px",
            color: "var(--color-text-secondary)",
          }}
        >
          {fields.length} fields extracted
        </span>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table
          data-testid="extraction-field-table"
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
                color: "var(--color-text-secondary)",
                fontWeight: 600,
              }}
            >
              <th style={{ padding: "8px 12px" }}>Field</th>
              <th style={{ padding: "8px 12px" }}>Extracted Value</th>
              <th style={{ padding: "8px 12px", width: "130px" }}>State</th>
              <th style={{ padding: "8px 12px", width: "80px", textAlign: "right" }}>Cited Page</th>
            </tr>
          </thead>
          <tbody>
            {presentFields.map((field, idx) => renderFieldRow(field, idx))}
            {notPresentFields.length > 0 && (
              <tr
                data-testid="not-present-toggle-row"
                onClick={() => setIsNotPresentExpanded((prev) => !prev)}
                style={{
                  backgroundColor: "var(--color-bg-subtle)",
                  borderBottom: "1px solid var(--color-border)",
                  cursor: "pointer",
                  userSelect: "none",
                }}
              >
                <td
                  colSpan={4}
                  style={{
                    padding: "10px 14px",
                    color: "var(--color-text-secondary)",
                    fontWeight: 600,
                    fontSize: "var(--font-size-xs)",
                  }}
                >
                  <button
                    type="button"
                    data-testid="not-present-toggle"
                    onClick={(e) => {
                      e.stopPropagation();
                      setIsNotPresentExpanded((prev) => !prev);
                    }}
                    aria-expanded={isNotPresentExpanded}
                    style={{
                      background: "none",
                      border: "none",
                      padding: 0,
                      margin: 0,
                      font: "inherit",
                      color: "inherit",
                      cursor: "pointer",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "8px",
                    }}
                  >
                    <span aria-hidden="true" style={{ fontSize: "10px" }}>
                      {isNotPresentExpanded ? "▼" : "▶"}
                    </span>
                    <span>
                      {notPresentFields.length} {notPresentFields.length === 1 ? "field" : "fields"} not present in this document
                    </span>
                  </button>
                </td>
              </tr>
            )}
            {isNotPresentExpanded &&
              notPresentFields.map((field, idx) =>
                renderFieldRow(field, presentFields.length + idx)
              )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
