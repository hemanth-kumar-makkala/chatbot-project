"use client";

import { useEffect, useState } from "react";
import { DataSourceChip } from "@/components/DataSourceChip";

interface SourcePanelProps {
  isOpen: boolean;
  onClose: () => void;
  documentTitle: string;
  initialPage?: number;
  isLiveAssistant?: boolean;
}

function getDocStem(docTitle: string): string {
  const clean = docTitle.toLowerCase().replace(/\.pdf$/i, "").trim();
  if (clean.includes("prior auth") || clean.includes("prior_auth")) return "prior_auth_guide";
  if (clean.includes("skewed")) return "skewed_fax";
  if (clean.includes("compliance")) return "compliance_notice";
  if (clean.includes("eligibility")) return "eligibility_checklist";
  if (clean.includes("clean_fax") || clean.includes("clean fax")) return "clean_fax";
  if (clean.includes("faint")) return "faint_fax_2page";
  if (clean.includes("protocols")) return "clinical_protocols_manual";
  if (clean.includes("policy") && (clean.includes("v2") || clean.includes("2"))) return "front_desk_policy_v2";
  if (clean.includes("policy") || clean.includes("v1")) return "front_desk_policy_v1";
  if (clean.includes("intake")) return "patient_intake_form";
  return clean.replace(/[^a-z0-9_-]/g, "_");
}

export function SourcePanel({
  isOpen,
  onClose,
  documentTitle,
  initialPage = 1,
  isLiveAssistant = false,
}: SourcePanelProps) {
  const [currentPage, setCurrentPage] = useState<number>(initialPage);

  useEffect(() => {
    setCurrentPage(initialPage || 1);
  }, [documentTitle, initialPage]);

  if (!isOpen) return null;

  const stem = getDocStem(documentTitle);
  const pdfFileName = documentTitle.split("/").pop() || `${stem}.pdf`;

  return (
    <aside
      data-testid="source-panel"
      aria-label="Source Document Panel"
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: "500px",
        maxWidth: "92vw",
        backgroundColor: "var(--color-white)",
        boxShadow: "var(--shadow-xl)",
        borderLeft: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        zIndex: 1000,
      }}
    >
      {/* Panel Header */}
      <div
        style={{
          padding: "var(--spacing-4)",
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          backgroundColor: "var(--color-bg-subtle)",
        }}
      >
        <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", paddingRight: "var(--spacing-2)" }}>
          <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>
            Source Document Preview
          </div>
          <h3
            data-testid="source-panel-doc-title"
            style={{
              fontSize: "var(--font-size-base)",
              fontWeight: 600,
              color: "var(--color-navy)",
              margin: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {documentTitle}
          </h3>
        </div>
        <button
          type="button"
          data-testid="source-panel-close"
          aria-label="Close Source Panel"
          onClick={onClose}
          style={{
            background: "none",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-sm)",
            width: "32px",
            height: "32px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            color: "var(--color-text-main)",
            fontSize: "16px",
            fontWeight: "bold",
          }}
        >
          ✕
        </button>
      </div>

      {/* Page Navigation Controls */}
      {!isLiveAssistant && (
        <div
          style={{
            padding: "var(--spacing-3) var(--spacing-4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottom: "1px solid var(--color-border-subtle)",
            backgroundColor: "var(--color-white)",
          }}
        >
          <button
            type="button"
            data-testid="source-panel-prev-page"
            aria-label="Previous Page"
            disabled={currentPage <= 1}
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            style={{
              padding: "4px 12px",
              fontSize: "var(--font-size-xs)",
              fontWeight: 500,
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--color-border)",
              backgroundColor: currentPage <= 1 ? "var(--color-bg-subtle)" : "var(--color-white)",
              color: currentPage <= 1 ? "var(--color-text-muted)" : "var(--color-navy)",
              cursor: currentPage <= 1 ? "not-allowed" : "pointer",
            }}
          >
            Previous Page
          </button>

          <span
            data-testid="source-panel-page-indicator"
            style={{
              fontSize: "var(--font-size-sm)",
              fontWeight: 600,
              color: "var(--color-navy)",
            }}
          >
            Page {currentPage}
          </span>

          <button
            type="button"
            data-testid="source-panel-next-page"
            aria-label="Next Page"
            onClick={() => setCurrentPage((p) => p + 1)}
            style={{
              padding: "4px 12px",
              fontSize: "var(--font-size-xs)",
              fontWeight: 500,
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-white)",
              color: "var(--color-navy)",
              cursor: "pointer",
            }}
          >
            Next Page
          </button>
        </div>
      )}

      {/* Panel Body */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "var(--spacing-4)",
          backgroundColor: isLiveAssistant ? "var(--color-white)" : "var(--color-bg-subtle)",
        }}
      >
        {isLiveAssistant ? (
          <div
            style={{
              padding: "var(--spacing-6)",
              textAlign: "center",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "var(--spacing-3)",
            }}
          >
            <DataSourceChip mode="demo" />
            <p
              data-testid="live-source-panel-message"
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--color-text-muted)",
                lineHeight: 1.5,
                maxWidth: "320px",
              }}
            >
              Source preview needs the document file route (not built yet)
            </p>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--spacing-3)" }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-muted)",
              }}
            >
              <span>Golden Document Page #{currentPage}</span>
              <a
                href={`/demo/docs/${pdfFileName}#page=${currentPage}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  color: "var(--color-navy)",
                  textDecoration: "underline",
                  fontWeight: 500,
                }}
              >
                Open Original PDF (#page={currentPage})
              </a>
            </div>

            <div
              style={{
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                overflow: "hidden",
                backgroundColor: "var(--color-white)",
                boxShadow: "var(--shadow-sm)",
              }}
            >
              <img
                data-testid="source-panel-view-image"
                src={`/demo/pages/${stem}-p${currentPage}.png`}
                alt={`${documentTitle} Page ${currentPage}`}
                style={{
                  width: "100%",
                  height: "auto",
                  display: "block",
                }}
              />
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
