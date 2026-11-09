"use client";

import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import { DataSourceChip } from "@/components/DataSourceChip";

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
}

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
}: SourcePanelProps) {
  const [currentPage, setCurrentPage] = useState<number>(initialPage);
  const [totalPages, setTotalPages] = useState<number>(1);
  const [scale, setScale] = useState<number>(1.0);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const renderTaskRef = useRef<any>(null);
  const pdfDocRef = useRef<any>(null);

  useEffect(() => {
    setCurrentPage(initialPage || 1);
  }, [documentTitle, initialPage]);

  // Load PDF Document when panel opens or documentTitle/version changes
  useEffect(() => {
    if (!isOpen) return;

    let isMounted = true;
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
  }, [isOpen, documentTitle, isLiveAssistant, assistantId, versionId]);

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
            Source Document Preview (pdf.js)
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

      {/* Page Navigation & Zoom Toolbar */}
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
          </div>
      </div>
    </aside>
  );
}
