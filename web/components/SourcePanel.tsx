"use client";

import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import { getDocumentTablePreview, type TablePreviewSheet } from "@/lib/api";

// Set worker from installed package (no CDN)
if (typeof window !== "undefined" && !pdfjsLib.GlobalWorkerOptions.workerSrc) {
  try {
    pdfjsLib.GlobalWorkerOptions.workerPort = new Worker(
      new URL("pdfjs-dist/build/pdf.worker.mjs", import.meta.url),
      { type: "module" }
    );
  } catch {
    pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
      "pdfjs-dist/build/pdf.worker.min.mjs",
      import.meta.url
    ).toString();
  }
}

interface SourcePanelProps {
  isOpen: boolean;
  onClose: () => void;
  documentTitle: string;
  initialPage?: number;
  isLiveAssistant?: boolean;
  assistantId?: string;
  versionId?: string;
  sheetName?: string;
  highlightedRow?: number | null;
}

const DEMO_TABLE_SHEETS: Record<string, TablePreviewSheet[]> = {
  "pa_turnaround_times.xlsx": [
    {
      sheet_name: "Sheet1",
      caption: "*** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***",
      headers: ["Payer Name", "Service Category", "Urgency", "Standard SLA", "Liaison Contact"],
      rows: [
        {
          row_number: 1,
          cells: {
            "Payer Name": "Apex Health Insurance",
            "Service Category": "Diagnostic Imaging",
            Urgency: "Routine",
            "Standard SLA": "48 Hours",
            "Liaison Contact": "555-0120",
          },
        },
        {
          row_number: 2,
          cells: {
            "Payer Name": "Apex Health Insurance",
            "Service Category": "Inpatient Surgery",
            Urgency: "Expedited",
            "Standard SLA": "24 Hours",
            "Liaison Contact": "555-0120",
          },
        },
        {
          row_number: 3,
          cells: {
            "Payer Name": "Beacon Mutual Healthcare",
            "Service Category": "Outpatient Therapy",
            Urgency: "Routine",
            "Standard SLA": "72 Hours",
            "Liaison Contact": "555-0121",
          },
        },
        {
          row_number: 4,
          cells: {
            "Payer Name": "Crestview Health",
            "Service Category": "Specialty Medication",
            Urgency: "Expedited",
            "Standard SLA": "24 Hours",
            "Liaison Contact": "555-0122",
          },
        },
        {
          row_number: 5,
          cells: {
            "Payer Name": "Summit Care Plan",
            "Service Category": "Standard Clinical Review",
            Urgency: "Routine",
            "Standard SLA": "72 Hours",
            "Liaison Contact": "555-0123",
          },
        },
      ],
    },
  ],
  "accepted_plans.csv": [
    {
      sheet_name: "Sheet1",
      headers: [
        "Payer Name",
        "Plan Code",
        "Network Tier",
        "Standard Copay",
        "Deductible",
        "Pre-Auth",
      ],
      rows: [
        {
          row_number: 1,
          cells: {
            "Payer Name": "Apex Health Insurance",
            "Plan Code": "APX-HMO-01",
            "Network Tier": "In-Network Tier 1",
            "Standard Copay": "$20.00",
            Deductible: "$250.00",
            "Pre-Auth": "Yes",
          },
        },
        {
          row_number: 2,
          cells: {
            "Payer Name": "Apex Health Insurance",
            "Plan Code": "APX-PPO-02",
            "Network Tier": "In-Network Tier 2",
            "Standard Copay": "$35.00",
            Deductible: "$750.00",
            "Pre-Auth": "No",
          },
        },
        {
          row_number: 3,
          cells: {
            "Payer Name": "Beacon Mutual Healthcare",
            "Plan Code": "BCN-EPO-10",
            "Network Tier": "In-Network Tier 1",
            "Standard Copay": "$25.00",
            Deductible: "$300.00",
            "Pre-Auth": "Yes",
          },
        },
        {
          row_number: 4,
          cells: {
            "Payer Name": "Crestview Health",
            "Plan Code": "CRST-GLD-05",
            "Network Tier": "In-Network Gold Tier",
            "Standard Copay": "$15.00",
            Deductible: "$500.00",
            "Pre-Auth": "No",
          },
        },
        {
          row_number: 5,
          cells: {
            "Payer Name": "Summit Care Plan",
            "Plan Code": "SMT-SLV-08",
            "Network Tier": "In-Network Silver Tier",
            "Standard Copay": "$30.00",
            Deductible: "$1000.00",
            "Pre-Auth": "Yes",
          },
        },
      ],
    },
  ],
};

function getDocStem(docTitle: string): string {
  return docTitle
    .toLowerCase()
    .replace(/\.[a-z0-9]+$/i, "")
    .trim()
    .replace(/[^a-z0-9_-]/g, "_");
}

export function SourcePanel({
  isOpen,
  onClose,
  documentTitle,
  initialPage = 1,
  isLiveAssistant = false,
  assistantId,
  versionId,
  sheetName,
  highlightedRow,
}: SourcePanelProps) {
  const [currentPage, setCurrentPage] = useState<number>(initialPage);
  const [totalPages, setTotalPages] = useState<number>(1);
  const [scale, setScale] = useState<number>(1.0);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [tableSheets, setTableSheets] = useState<TablePreviewSheet[]>([]);
  const [activeSheetName, setActiveSheetName] = useState<string>(sheetName || "Sheet1");

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const renderTaskRef = useRef<any>(null);
  const pdfDocRef = useRef<any>(null);
  const highlightedRowRef = useRef<HTMLTableRowElement | null>(null);

  const isTableDoc = /\.(xlsx|csv|tsv)$/i.test(documentTitle) || Boolean(sheetName);

  useEffect(() => {
    setCurrentPage(initialPage || 1);
  }, [documentTitle, initialPage]);

  // Clear previous document and load either Table Preview or PDF Document when source changes
  useEffect(() => {
    if (!isOpen) return;

    let isMounted = true;

    // 1. Always clear the previous document state and canvas immediately
    pdfDocRef.current = null;
    setTableSheets([]);
    setError(null);
    setTotalPages(1);
    if (canvasRef.current) {
      try {
        const ctx = canvasRef.current.getContext("2d");
        ctx?.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
        canvasRef.current.width = 0;
        canvasRef.current.height = 0;
      } catch {
        // ignore in non-canvas environments
      }
    }

    // 2. Spreadsheet / CSV Table Preview path
    if (isTableDoc) {
      async function loadTable() {
        setLoading(true);
        setError(null);
        try {
          if (isLiveAssistant && assistantId && versionId) {
            const res = await getDocumentTablePreview(assistantId, versionId);
            if (!isMounted) return;
            if (res.ok && res.data.sheets && res.data.sheets.length > 0) {
              setTableSheets(res.data.sheets);
              const preferred =
                res.data.sheets.find((s) => s.sheet_name === sheetName)?.sheet_name ||
                res.data.sheets[0].sheet_name;
              setActiveSheetName(preferred);
              return;
            }
          }
          // Demo / fallback table preview
          const key = documentTitle.toLowerCase();
          const demoSheets =
            DEMO_TABLE_SHEETS[key] || DEMO_TABLE_SHEETS["pa_turnaround_times.xlsx"];
          if (!isMounted) return;
          const mapped = demoSheets.map((s) =>
            sheetName ? { ...s, sheet_name: sheetName } : s
          );
          setTableSheets(mapped);
          setActiveSheetName(mapped[0]?.sheet_name || sheetName || "Sheet1");
        } catch (err: any) {
          if (!isMounted) return;
          setError(err?.message || "Failed to load spreadsheet preview");
        } finally {
          if (isMounted) setLoading(false);
        }
      }

      loadTable();
      return () => {
        isMounted = false;
      };
    }

    // 3. PDF Document path
    let pdfUrl: string | null = null;
    if (isLiveAssistant) {
      if (assistantId && versionId) {
        pdfUrl = `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents/${encodeURIComponent(versionId)}/file`;
      } else {
        const stem = getDocStem(documentTitle);
        pdfUrl = `/demo/docs/${stem}.pdf`;
      }
    } else {
      const stem = getDocStem(documentTitle);
      pdfUrl = `/demo/docs/${stem}.pdf`;
    }

    async function loadPdf() {
      setLoading(true);
      setError(null);
      try {
        const loadingTask = pdfjsLib.getDocument({ url: pdfUrl! });
        const doc = await loadingTask.promise;
        if (!isMounted) return;
        pdfDocRef.current = doc;
        setTotalPages(doc.numPages || 1);
      } catch (err: any) {
        if (!isMounted) return;
        setError(err?.message || "Failed to load document");
      } finally {
        if (isMounted) setLoading(false);
      }
    }

    loadPdf();

    return () => {
      isMounted = false;
      if (renderTaskRef.current) {
        try {
          renderTaskRef.current.cancel();
        } catch {
          // ignore
        }
      }
    };
  }, [isOpen, documentTitle, isLiveAssistant, assistantId, versionId, sheetName, isTableDoc]);

  // Scroll highlighted row into view when table preview renders
  useEffect(() => {
    if (!isOpen || !isTableDoc || !highlightedRow || tableSheets.length === 0) return;
    if (
      highlightedRowRef.current &&
      typeof highlightedRowRef.current.scrollIntoView === "function"
    ) {
      highlightedRowRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [isOpen, isTableDoc, highlightedRow, tableSheets, activeSheetName]);

  // Render current page when page, scale, or doc changes
  useEffect(() => {
    if (!isOpen || !pdfDocRef.current) return;

    let isMounted = true;

    async function renderPage() {
      try {
        const doc = pdfDocRef.current;
        const pageNum = Math.min(Math.max(1, currentPage), doc.numPages || 1);
        const page = await doc.getPage(pageNum);
        if (!isMounted) return;

        const viewport = page.getViewport({ scale });
        const canvas = canvasRef.current;
        if (!canvas) return;

        canvas.width = viewport.width;
        canvas.height = viewport.height;

        const ctx = canvas.getContext("2d");
        if (!ctx) return;

        if (renderTaskRef.current) {
          try {
            renderTaskRef.current.cancel();
          } catch {
            // ignore
          }
        }

        const task = page.render({
          canvasContext: ctx,
          viewport: viewport,
        });
        renderTaskRef.current = task;
        await task.promise;
      } catch (err: any) {
        if (err?.name !== "RenderingCancelledException" && isMounted) {
          // handle error gracefully
        }
      }
    }

    renderPage();

    return () => {
      isMounted = false;
      if (renderTaskRef.current) {
        try {
          renderTaskRef.current.cancel();
        } catch {
          // ignore
        }
      }
    };
  }, [isOpen, currentPage, scale, isLiveAssistant, totalPages]);

  if (!isOpen) return null;

  return (
    <aside
      data-testid="source-panel"
      aria-label="Source Document Panel"
      style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: "560px",
        maxWidth: "94vw",
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
            {isTableDoc ? "Source Table Preview" : "Source Document Preview (pdf.js)"}
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

      {/* Page Navigation & Zoom Toolbar (PDF only) */}
      {!isTableDoc && (
      <div
        style={{
          padding: "var(--spacing-3) var(--spacing-4)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: "1px solid var(--color-border-subtle)",
          backgroundColor: "var(--color-white)",
          flexWrap: "wrap",
          gap: "var(--spacing-2)",
        }}
      >
        {/* Navigation Controls */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-2)" }}>
          <button
            type="button"
            data-testid="source-panel-prev-page"
            aria-label="Previous Page"
            disabled={currentPage <= 1 || loading}
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            style={{
              padding: "4px 10px",
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
              fontSize: "var(--font-size-xs)",
              fontWeight: 600,
              color: "var(--color-navy)",
              padding: "0 var(--spacing-1)",
            }}
          >
            Page {currentPage} of {totalPages || 1}
          </span>

          <button
            type="button"
            data-testid="source-panel-next-page"
            aria-label="Next Page"
            disabled={(totalPages > 0 && currentPage >= totalPages) || loading}
            onClick={() => setCurrentPage((p) => (totalPages ? Math.min(totalPages, p + 1) : p + 1))}
            style={{
              padding: "4px 10px",
              fontSize: "var(--font-size-xs)",
              fontWeight: 500,
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--color-border)",
              backgroundColor: totalPages > 0 && currentPage >= totalPages ? "var(--color-bg-subtle)" : "var(--color-white)",
              color: totalPages > 0 && currentPage >= totalPages ? "var(--color-text-muted)" : "var(--color-navy)",
              cursor: totalPages > 0 && currentPage >= totalPages ? "not-allowed" : "pointer",
            }}
          >
            Next Page
          </button>
        </div>

        {/* Zoom Controls */}
        <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-1)" }}>
          <button
            type="button"
            data-testid="source-panel-zoom-out"
            aria-label="Zoom Out"
            disabled={scale <= 0.5 || loading}
            onClick={() => setScale((s) => Math.max(0.5, Number((s - 0.25).toFixed(2))))}
            style={{
              padding: "4px 8px",
              fontSize: "var(--font-size-xs)",
              fontWeight: 600,
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--color-border)",
              backgroundColor: scale <= 0.5 ? "var(--color-bg-subtle)" : "var(--color-white)",
              color: "var(--color-navy)",
              cursor: scale <= 0.5 ? "not-allowed" : "pointer",
            }}
          >
            −
          </button>
          <span
            data-testid="source-panel-zoom-level"
            style={{
              fontSize: "var(--font-size-xs)",
              fontWeight: 600,
              color: "var(--color-text-muted)",
              minWidth: "42px",
              textAlign: "center",
            }}
          >
            {Math.round(scale * 100)}%
          </span>
          <button
            type="button"
            data-testid="source-panel-zoom-in"
            aria-label="Zoom In"
            disabled={scale >= 3.0 || loading}
            onClick={() => setScale((s) => Math.min(3.0, Number((s + 0.25).toFixed(2))))}
            style={{
              padding: "4px 8px",
              fontSize: "var(--font-size-xs)",
              fontWeight: 600,
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--color-border)",
              backgroundColor: scale >= 3.0 ? "var(--color-bg-subtle)" : "var(--color-white)",
              color: "var(--color-navy)",
              cursor: scale >= 3.0 ? "not-allowed" : "pointer",
            }}
          >
            +
          </button>
        </div>
      </div>
      )}

      {/* Panel Body */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "var(--spacing-4)",
          backgroundColor: "var(--color-bg-subtle)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "flex-start",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: "100%" }}>
            {error && (
              <div
                style={{
                  padding: "var(--spacing-3)",
                  backgroundColor: "var(--color-status-error-bg)",
                  color: "var(--color-status-error-text)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "var(--font-size-xs)",
                  marginBottom: "var(--spacing-3)",
                  width: "100%",
                }}
              >
                {error}
              </div>
            )}
            {isTableDoc ? (
              (() => {
                const activeSheet =
                  tableSheets.find((s) => s.sheet_name === activeSheetName) || tableSheets[0];
                if (!activeSheet) {
                  return (
                    <div
                      data-testid="source-panel-table-preview"
                      style={{
                        padding: "var(--spacing-4)",
                        fontSize: "var(--font-size-sm)",
                        color: "var(--color-text-secondary)",
                      }}
                    >
                      {loading ? "Loading table preview..." : "No table rows available."}
                    </div>
                  );
                }
                return (
                  <div
                    data-testid="source-panel-table-preview"
                    style={{
                      width: "100%",
                      backgroundColor: "var(--color-white)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-md)",
                      boxShadow: "var(--shadow-sm)",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        padding: "10px 14px",
                        borderBottom: "1px solid var(--color-border)",
                        backgroundColor: "var(--color-bg-subtle)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: "8px",
                      }}
                    >
                      <span
                        data-testid="source-table-sheet-name"
                        style={{
                          fontSize: "var(--font-size-xs)",
                          fontWeight: 600,
                          color: "var(--color-navy)",
                        }}
                      >
                        Sheet: {activeSheet.sheet_name}
                      </span>
                      {highlightedRow != null && (
                        <span
                          style={{
                            fontSize: "11px",
                            fontWeight: 600,
                            padding: "2px 8px",
                            borderRadius: "var(--radius-sm)",
                            backgroundColor: "var(--color-warning-soft)",
                            color: "var(--color-warning-text)",
                          }}
                        >
                          Cited Row {highlightedRow}
                        </span>
                      )}
                    </div>
                    {activeSheet.caption && (
                      <div
                        style={{
                          padding: "8px 14px",
                          fontSize: "11px",
                          color: "var(--color-text-secondary)",
                          borderBottom: "1px solid var(--color-border-subtle)",
                        }}
                      >
                        {activeSheet.caption}
                      </div>
                    )}
                    <div style={{ overflowX: "auto" }}>
                      <table
                        data-testid="source-preview-table"
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
                            }}
                          >
                            <th style={{ padding: "8px 10px", fontWeight: 600, width: "48px" }}>
                              Row
                            </th>
                            {activeSheet.headers.map((h) => (
                              <th key={h} style={{ padding: "8px 10px", fontWeight: 600 }}>
                                {h}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {activeSheet.rows.map((r) => {
                            const isHighlighted = highlightedRow === r.row_number;
                            return (
                              <tr
                                key={r.row_number}
                                ref={isHighlighted ? highlightedRowRef : null}
                                data-testid={`source-table-row-${r.row_number}`}
                                data-highlighted={isHighlighted ? "true" : "false"}
                                style={{
                                  borderBottom: "1px solid var(--color-border-subtle)",
                                  backgroundColor: isHighlighted
                                    ? "var(--color-warning-soft)"
                                    : "transparent",
                                  fontWeight: isHighlighted ? 600 : 400,
                                }}
                              >
                                <td style={{ padding: "8px 10px", color: "var(--color-text-secondary)" }}>
                                  {r.row_number}
                                </td>
                                {activeSheet.headers.map((h) => (
                                  <td key={h} style={{ padding: "8px 10px" }}>
                                    {r.cells[h] != null ? String(r.cells[h]) : "—"}
                                  </td>
                                ))}
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                );
              })()
            ) : (
            <div
              style={{
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                overflow: "hidden",
                backgroundColor: "var(--color-white)",
                boxShadow: "var(--shadow-md)",
                maxWidth: "100%",
              }}
            >
              <canvas
                ref={canvasRef}
                data-testid="pdf-canvas"
                style={{
                  display: "block",
                  maxWidth: "100%",
                  height: "auto",
                }}
              />
            </div>
            )}
          </div>
      </div>
    </aside>
  );
}
