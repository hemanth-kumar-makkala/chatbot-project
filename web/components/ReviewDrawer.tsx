"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import {
  getReviewData,
  acceptPage,
  excludePage,
  retryPage,
  promoteVersion,
  getPageImageUrl,
  type ReviewDataResponse,
  type FlaggedPage,
} from "@/lib/api";
import { getReviewReasonLabel } from "@/lib/reviewReasons";

interface ReviewDrawerProps {
  isOpen: boolean;
  assistantId: string;
  versionId: string;
  documentTitle: string;
  onClose: () => void;
  onPromoted: () => void;
}

export function ReviewDrawer({
  isOpen,
  assistantId,
  versionId,
  documentTitle,
  onClose,
  onPromoted,
}: ReviewDrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  const [loading, setLoading] = useState(false);
  const [reviewData, setReviewData] = useState<ReviewDataResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<Record<number, string | null>>({});
  const [promoting, setPromoting] = useState(false);

  const loadData = useCallback(async () => {
    if (!versionId) return;
    setLoading(true);
    setErrorMessage(null);

    const result = await getReviewData(assistantId, versionId);
    setLoading(false);

    if (result.ok) {
      setReviewData(result.data);
    } else {
      if (result.status === 409 || result.code === "JOB_ACTIVE") {
        setErrorMessage("This page is being processed. Try again in a moment.");
      } else {
        setErrorMessage(`Unable to load review data (${result.code}).`);
      }
    }
  }, [assistantId, versionId]);

  useEffect(() => {
    if (isOpen) {
      loadData();
      closeButtonRef.current?.focus();
    } else {
      setReviewData(null);
      setErrorMessage(null);
    }
  }, [isOpen, loadData]);

  // Focus trap and Escape key listener
  useEffect(() => {
    if (!isOpen) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }

      if (e.key === "Tab" && drawerRef.current) {
        const focusables = drawerRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        const first = focusables[0];
        const last = focusables[focusables.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  function handleActionError(status: number, code: string) {
    if (status === 409 || code === "JOB_ACTIVE") {
      setErrorMessage("This page is being processed. Try again in a moment.");
    } else {
      setErrorMessage(`Action failed (${code}). Please try again.`);
    }
  }

  async function handleAccept(pageNumber: number) {
    setActionLoading((prev) => ({ ...prev, [pageNumber]: "accept" }));
    setErrorMessage(null);

    const res = await acceptPage(assistantId, versionId, pageNumber);
    setActionLoading((prev) => ({ ...prev, [pageNumber]: null }));

    if (res.ok) {
      loadData();
    } else {
      handleActionError(res.status, res.code);
    }
  }

  async function handleExclude(pageNumber: number) {
    setActionLoading((prev) => ({ ...prev, [pageNumber]: "exclude" }));
    setErrorMessage(null);

    const res = await excludePage(assistantId, versionId, pageNumber);
    setActionLoading((prev) => ({ ...prev, [pageNumber]: null }));

    if (res.ok) {
      loadData();
    } else {
      handleActionError(res.status, res.code);
    }
  }

  async function handleRetry(pageNumber: number) {
    setActionLoading((prev) => ({ ...prev, [pageNumber]: "retry" }));
    setErrorMessage(null);

    const res = await retryPage(assistantId, versionId, pageNumber);
    setActionLoading((prev) => ({ ...prev, [pageNumber]: null }));

    if (res.ok) {
      loadData();
    } else {
      handleActionError(res.status, res.code);
    }
  }

  async function handlePromote() {
    setPromoting(true);
    setErrorMessage(null);

    const res = await promoteVersion(assistantId, versionId);
    setPromoting(false);

    if (res.ok) {
      onPromoted();
    } else {
      handleActionError(res.status, res.code);
    }
  }

  if (!isOpen) return null;

  const flaggedPages = reviewData?.flagged_pages || [];

  return (
    <div
      role="presentation"
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "var(--color-backdrop)",
        backdropFilter: "blur(2px)",
        zIndex: 1000,
        display: "flex",
        justifyContent: "flex-end",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-drawer-title"
        data-testid="review-drawer"
        style={{
          width: "100%",
          maxWidth: "680px",
          height: "100%",
          backgroundColor: "var(--color-bg-page)",
          boxShadow: "var(--shadow-drawer)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "var(--spacing-6)",
            borderBottom: "1px solid var(--color-border)",
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            background: "var(--color-bg-subtle)",
          }}
        >
          <div>
            <h2
              id="review-drawer-title"
              style={{
                fontSize: "var(--font-size-xl)",
                fontWeight: 700,
                color: "var(--color-navy)",
                marginBottom: "var(--spacing-1)",
              }}
            >
              Review Flagged Pages
            </h2>
            <p
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--color-text-muted)",
              }}
            >
              {documentTitle}
            </p>
          </div>

          <button
            ref={closeButtonRef}
            type="button"
            id="close-drawer-button"
            aria-label="Close review drawer"
            onClick={onClose}
            style={{
              padding: "8px",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-page)",
              color: "var(--color-text-primary)",
              cursor: "pointer",
              fontSize: "var(--font-size-sm)",
              fontWeight: 700,
              lineHeight: 1,
            }}
          >
            ✕
          </button>
        </div>

        {/* Error message */}
        {errorMessage && (
          <div
            role="alert"
            data-testid="drawer-error-alert"
            style={{
              margin: "var(--spacing-4) var(--spacing-6) 0",
              padding: "var(--spacing-3)",
              backgroundColor: "var(--color-status-error-bg)",
              color: "var(--color-status-error-text)",
              borderRadius: "var(--radius-md)",
              fontSize: "var(--font-size-sm)",
              border: "1px solid var(--color-danger)",
            }}
          >
            {errorMessage}
          </div>
        )}

        {/* Content */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "var(--spacing-6)",
          }}
        >
          {loading ? (
            <div style={{ textAlign: "center", padding: "var(--spacing-12)", color: "var(--color-text-muted)" }}>
              Loading review data...
            </div>
          ) : flaggedPages.length === 0 ? (
            <div
              style={{
                textAlign: "center",
                padding: "var(--spacing-12)",
                backgroundColor: "var(--color-bg-subtle)",
                borderRadius: "var(--radius-lg)",
                border: "1px dashed var(--color-border)",
              }}
            >
              <p
                style={{
                  fontSize: "var(--font-size-base)",
                  fontWeight: 600,
                  color: "var(--color-navy)",
                  marginBottom: "var(--spacing-2)",
                }}
              >
                No unreviewed pages remaining
              </p>
              <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-muted)", marginBottom: "var(--spacing-4)" }}>
                All flagged pages have been resolved. You can now promote this document version.
              </p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--spacing-6)" }}>
              {flaggedPages.map((page: FlaggedPage) => {
                const isPageLoading = Boolean(actionLoading[page.page_number]);
                const reasonLabel = getReviewReasonLabel(page.review_reason);
                const imageUrl = getPageImageUrl(assistantId, versionId, page.page_number);

                return (
                  <div
                    key={page.page_number}
                    data-testid={`flagged-page-${page.page_number}`}
                    style={{
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-lg)",
                      backgroundColor: "var(--color-white)",
                      boxShadow: "var(--shadow-sm)",
                      overflow: "hidden",
                    }}
                  >
                    {/* Page header */}
                    <div
                      style={{
                        padding: "var(--spacing-3) var(--spacing-4)",
                        backgroundColor: "var(--color-bg-subtle)",
                        borderBottom: "1px solid var(--color-border-subtle)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                      }}
                    >
                      <span style={{ fontWeight: 700, color: "var(--color-navy)", fontSize: "var(--font-size-sm)" }}>
                        Page {page.page_number}
                      </span>
                      <span
                        data-testid={`reason-label-${page.page_number}`}
                        style={{
                          fontSize: "var(--font-size-xs)",
                          fontWeight: 600,
                          padding: "2px 8px",
                          borderRadius: "var(--radius-full)",
                          backgroundColor: "var(--color-warning-soft)",
                          color: "var(--color-warning-text)",
                        }}
                      >
                        {reasonLabel}
                      </span>
                    </div>

                    {/* Page Image */}
                    <div
                      style={{
                        padding: "var(--spacing-4)",
                        backgroundColor: "var(--color-bg-subtle)",
                        textAlign: "center",
                        borderBottom: "1px solid var(--color-border-subtle)",
                      }}
                    >
                      <img
                        src={imageUrl}
                        alt={`Page ${page.page_number} visual`}
                        data-testid={`page-image-${page.page_number}`}
                        style={{
                          maxWidth: "100%",
                          maxHeight: "360px",
                          objectFit: "contain",
                          borderRadius: "var(--radius-sm)",
                          boxShadow: "var(--shadow-sm)",
                          backgroundColor: "var(--color-white)",
                        }}
                        onError={(e) => {
                          e.currentTarget.style.display = "none";
                        }}
                      />
                    </div>

                    {/* Reading text if any */}
                    {page.reading_text && (
                      <div style={{ padding: "var(--spacing-4)", borderBottom: "1px solid var(--color-border-subtle)" }}>
                        <span
                          style={{
                            display: "block",
                            fontSize: "var(--font-size-xs)",
                            fontWeight: 600,
                            color: "var(--color-text-muted)",
                            marginBottom: "var(--spacing-1)",
                          }}
                        >
                          Extracted Text:
                        </span>
                        <div
                          data-testid={`reading-text-${page.page_number}`}
                          style={{
                            fontSize: "var(--font-size-sm)",
                            color: "var(--color-text-primary)",
                            backgroundColor: "var(--color-bg-surface)",
                            padding: "var(--spacing-3)",
                            borderRadius: "var(--radius-md)",
                            fontFamily: "monospace",
                            whiteSpace: "pre-wrap",
                            maxHeight: "150px",
                            overflowY: "auto",
                          }}
                        >
                          {page.reading_text}
                        </div>
                      </div>
                    )}

                    {/* Actions per page */}
                    <div
                      style={{
                        padding: "var(--spacing-3) var(--spacing-4)",
                        display: "flex",
                        justifyContent: "flex-end",
                        gap: "var(--spacing-2)",
                        backgroundColor: "var(--color-white)",
                      }}
                    >
                      <button
                        type="button"
                        data-testid={`retry-btn-${page.page_number}`}
                        onClick={() => handleRetry(page.page_number)}
                        disabled={isPageLoading}
                        style={{
                          padding: "6px 14px",
                          borderRadius: "var(--radius-md)",
                          border: "1px solid var(--color-border)",
                          backgroundColor: "transparent",
                          color: "var(--color-navy)",
                          fontSize: "var(--font-size-xs)",
                          fontWeight: 600,
                          cursor: isPageLoading ? "wait" : "pointer",
                        }}
                      >
                        Retry
                      </button>

                      <button
                        type="button"
                        data-testid={`exclude-btn-${page.page_number}`}
                        onClick={() => handleExclude(page.page_number)}
                        disabled={isPageLoading}
                        style={{
                          padding: "6px 14px",
                          borderRadius: "var(--radius-md)",
                          border: "1px solid var(--color-danger)",
                          backgroundColor: "var(--color-status-error-bg)",
                          color: "var(--color-status-error-text)",
                          fontSize: "var(--font-size-xs)",
                          fontWeight: 600,
                          cursor: isPageLoading ? "wait" : "pointer",
                        }}
                      >
                        Exclude
                      </button>

                      <button
                        type="button"
                        data-testid={`accept-btn-${page.page_number}`}
                        onClick={() => handleAccept(page.page_number)}
                        disabled={isPageLoading}
                        style={{
                          padding: "6px 14px",
                          borderRadius: "var(--radius-md)",
                          border: "none",
                          backgroundColor: "var(--color-primary-blue)",
                          color: "var(--color-white)",
                          fontSize: "var(--font-size-xs)",
                          fontWeight: 600,
                          cursor: isPageLoading ? "wait" : "pointer",
                        }}
                      >
                        Accept
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer: Promote version action */}
        <div
          style={{
            padding: "var(--spacing-4) var(--spacing-6)",
            borderTop: "1px solid var(--color-border)",
            backgroundColor: "var(--color-bg-subtle)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
            Version: {versionId}
          </span>
          <button
            type="button"
            id="promote-version-button"
            data-testid="promote-version-btn"
            onClick={handlePromote}
            disabled={promoting}
            style={{
              padding: "10px 20px",
              borderRadius: "var(--radius-md)",
              backgroundColor: "var(--color-primary)",
              color: "var(--color-text-on-primary)",
              fontWeight: 700,
              fontSize: "var(--font-size-sm)",
              border: "none",
              cursor: promoting ? "wait" : "pointer",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            {promoting ? "Promoting..." : "Promote Version"}
          </button>
        </div>
      </div>
    </div>
  );
}
