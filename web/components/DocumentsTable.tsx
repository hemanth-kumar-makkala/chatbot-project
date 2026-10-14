"use client";

import { useState } from "react";
import type { DocumentItem } from "@/lib/api";
import { StatusChip } from "./StatusChip";
import { ConfirmDialog } from "./ConfirmDialog";
import { retireDocument } from "@/lib/api";

interface DocumentsTableProps {
  assistantId: string;
  documents: DocumentItem[];
  isLoading: boolean;
  onRefresh: () => void;
  onReview: (doc: DocumentItem) => void;
}

export function DocumentsTable({
  assistantId,
  documents,
  isLoading,
  onRefresh,
  onReview,
}: DocumentsTableProps) {
  const docList = Array.isArray(documents) ? documents : [];
  const [retiringDoc, setRetiringDoc] = useState<DocumentItem | null>(null);
  const [isRetiring, setIsRetiring] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function handleConfirmRetire() {
    if (!retiringDoc) return;
    setIsRetiring(true);
    setActionError(null);

    const result = await retireDocument(assistantId, retiringDoc.document_id);
    setIsRetiring(false);

    if (result.ok) {
      setRetiringDoc(null);
      onRefresh();
    } else {
      setActionError(`Failed to retire document (${result.code}). Please try again.`);
    }
  }

  function formatDate(isoString: string): string {
    if (!isoString) return "—";
    try {
      const date = new Date(isoString);
      return date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return isoString;
    }
  }

  return (
    <div>
      {actionError && (
        <div
          role="alert"
          style={{
            marginBottom: "var(--spacing-4)",
            padding: "var(--spacing-3)",
            backgroundColor: "var(--color-status-error-bg)",
            color: "var(--color-status-error-text)",
            borderRadius: "var(--radius-md)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          {actionError}
        </div>
      )}

      <div
        style={{
          backgroundColor: "var(--color-bg-card)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-sm)",
          border: "1px solid var(--color-border)",
          overflow: "hidden",
        }}
      >
        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              textAlign: "left",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <thead>
              <tr
                style={{
                  backgroundColor: "var(--color-bg-subtle)",
                  borderBottom: "1px solid var(--color-border)",
                }}
              >
                <th style={{ padding: "12px 16px", fontWeight: 600, color: "var(--color-navy)" }}>Title</th>
                <th style={{ padding: "12px 16px", fontWeight: 600, color: "var(--color-navy)", width: "100px" }}>
                  Version
                </th>
                <th style={{ padding: "12px 16px", fontWeight: 600, color: "var(--color-navy)", width: "220px" }}>
                  Status
                </th>
                <th style={{ padding: "12px 16px", fontWeight: 600, color: "var(--color-navy)", width: "140px" }}>
                  Uploaded
                </th>
                <th
                  style={{
                    padding: "12px 16px",
                    fontWeight: 600,
                    color: "var(--color-navy)",
                    width: "180px",
                    textAlign: "right",
                  }}
                >
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {docList.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    style={{
                      padding: "var(--spacing-8)",
                      textAlign: "center",
                      color: "var(--color-text-muted)",
                    }}
                  >
                    {isLoading ? "Loading documents..." : "No documents uploaded yet. Upload a document above to get started."}
                  </td>
                </tr>
              ) : (
                docList.map((doc) => {
                  const isRetired = doc.status.toLowerCase() === "retired";
                  const canReview = doc.flagged_pages_count > 0;

                  return (
                    <tr
                      key={doc.document_id}
                      data-testid={`document-row-${doc.document_id}`}
                      style={{
                        borderBottom: "1px solid var(--color-border-subtle)",
                        transition: "background-color 0.15s ease",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.backgroundColor = "var(--color-bg-subtle)";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.backgroundColor = "transparent";
                      }}
                    >
                      <td
                        style={{
                          padding: "14px 16px",
                          fontWeight: 500,
                          color: "var(--color-navy)",
                        }}
                      >
                        {doc.title}
                      </td>
                      <td style={{ padding: "14px 16px", color: "var(--color-text-muted)" }}>
                        v{doc.current_version}
                      </td>
                      <td style={{ padding: "14px 16px" }}>
                        <StatusChip status={doc.status} flaggedPagesCount={doc.flagged_pages_count} />
                      </td>
                      <td style={{ padding: "14px 16px", color: "var(--color-text-muted)" }}>
                        {formatDate(doc.uploaded_at)}
                      </td>
                      <td style={{ padding: "14px 16px", textAlign: "right", whiteSpace: "nowrap" }}>
                        <div style={{ display: "inline-flex", gap: "var(--spacing-2)" }}>
                          {canReview && (
                            <button
                              type="button"
                              data-testid={`review-btn-${doc.document_id}`}
                              onClick={() => onReview(doc)}
                              style={{
                                padding: "6px 12px",
                                borderRadius: "var(--radius-md)",
                                backgroundColor: "var(--color-lavender)",
                                color: "var(--color-navy)",
                                fontWeight: 600,
                                fontSize: "var(--font-size-xs)",
                                border: "1px solid rgba(95, 61, 191, 0.3)",
                              }}
                            >
                              Review
                            </button>
                          )}

                          {!isRetired ? (
                            <button
                              type="button"
                              data-testid={`retire-btn-${doc.document_id}`}
                              onClick={() => setRetiringDoc(doc)}
                              style={{
                                padding: "6px 12px",
                                borderRadius: "var(--radius-md)",
                                border: "1px solid var(--color-border)",
                                backgroundColor: "transparent",
                                color: "var(--color-status-retired-text)",
                                fontWeight: 600,
                                fontSize: "var(--font-size-xs)",
                              }}
                            >
                              Retire
                            </button>
                          ) : (
                            <span
                              style={{
                                padding: "6px 12px",
                                fontSize: "var(--font-size-xs)",
                                color: "var(--color-text-subtle)",
                              }}
                            >
                              Retired
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmDialog
        isOpen={Boolean(retiringDoc)}
        title="Retire Document"
        message={
          retiringDoc ? (
            <span>
              Are you sure you want to retire <strong>{retiringDoc.title}</strong>? It will no longer be used for assistant answers.
            </span>
          ) : ""
        }
        confirmLabel="Retire Document"
        cancelLabel="Cancel"
        isDestructive={true}
        isLoading={isRetiring}
        onConfirm={handleConfirmRetire}
        onCancel={() => setRetiringDoc(null)}
      />
    </div>
  );
}
