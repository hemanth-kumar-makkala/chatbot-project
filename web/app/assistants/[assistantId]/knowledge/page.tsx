"use client";

import { useEffect, useState, useCallback, useRef, use } from "react";
import { useRouter } from "next/navigation";
import { TopBar } from "@/components/TopBar";
import { UploadArea } from "@/components/UploadArea";
import { DocumentsTable } from "@/components/DocumentsTable";
import { ReviewDrawer } from "@/components/ReviewDrawer";
import { getDocuments, getCurrentUser, type DocumentItem, type User } from "@/lib/api";
import { isFinalStatus } from "@/lib/statusChips";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface KnowledgePageProps {
  params: Promise<{ assistantId: string }>;
}

export default function KnowledgePage({ params }: KnowledgePageProps) {
  const resolvedParams = use(params);
  const rawAssistantId = resolvedParams.assistantId;
  const assistantId =
    rawAssistantId === "00000000-0000-0002-0000-000000000001"
      ? DEV_DEFAULT_ASSISTANT_ID
      : rawAssistantId;
  const router = useRouter();
  const routerRef = useRef(router);
  routerRef.current = router;

  // Replace URL in browser if legacy typo assistantId was used
  useEffect(() => {
    if (rawAssistantId === "00000000-0000-0002-0000-000000000001") {
      routerRef.current.replace(`/assistants/${DEV_DEFAULT_ASSISTANT_ID}/knowledge`);
    }
  }, [rawAssistantId]);

  const [user, setUser] = useState<User | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Review drawer state
  const [reviewVersionId, setReviewVersionId] = useState<string | null>(null);
  const [reviewDocTitle, setReviewDocTitle] = useState<string>("");

  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Fetch documents from backend
  const loadDocuments = useCallback(async () => {
    const result = await getDocuments(assistantId);
    if (result.ok) {
      setDocuments(Array.isArray(result.data) ? result.data : []);
      setFetchError(null);
    } else {
      if (result.status === 401 || result.code === "UNAUTHORIZED") {
        routerRef.current.push(`/login?redirect=/assistants/${encodeURIComponent(assistantId)}/knowledge`);
        return;
      }
      setFetchError(`Unable to load documents (${result.code}).`);
    }
    setIsLoading(false);
  }, [assistantId]);

  // Load current session
  useEffect(() => {
    async function loadUser() {
      const res = await getCurrentUser();
      if (res.ok) {
        setUser(res.data);
      }
    }
    loadUser();
  }, []);

  // Initial load
  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  // 5-second polling while any document is not in a final state,
  // pausing when the document is hidden and cleaning up on unmount.
  useEffect(() => {
    function clearPoll() {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    }

    const docList = Array.isArray(documents) ? documents : [];
    const hasActiveDocs = docList.some((doc) => !isFinalStatus(doc.status));

    function startPollIfNeeded() {
      clearPoll();
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        return;
      }
      if (hasActiveDocs) {
        pollTimerRef.current = setInterval(() => {
          loadDocuments();
        }, 5000);
      }
    }

    startPollIfNeeded();

    function handleVisibilityChange() {
      if (document.visibilityState === "hidden") {
        clearPoll();
      } else {
        // Page became visible again, check and resume polling
        loadDocuments();
        startPollIfNeeded();
      }
    }

    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      clearPoll();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [documents, loadDocuments]);

  function handleOpenReview(doc: DocumentItem) {
    setReviewVersionId(doc.latest_version_id);
    setReviewDocTitle(doc.title);
  }

  function handleCloseReview() {
    setReviewVersionId(null);
    setReviewDocTitle("");
    loadDocuments();
  }

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "var(--color-bg-page)" }}>
      <TopBar
        assistantId={assistantId}
        assistantName={null}
        userEmail={user?.email}
      />

      <main className="container" style={{ paddingTop: "var(--spacing-8)", paddingBottom: "var(--spacing-12)" }}>
        <div style={{ marginBottom: "var(--spacing-6)" }}>
          <h1
            style={{
              fontSize: "var(--font-size-2xl)",
              fontWeight: 700,
              color: "var(--color-navy)",
              marginBottom: "var(--spacing-1)",
            }}
          >
            Knowledge Base
          </h1>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-muted)",
            }}
          >
            Upload client knowledge documents, track ingestion status, and review flagged pages.
          </p>
        </div>

        {fetchError && (
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
            {fetchError}
          </div>
        )}

        <UploadArea assistantId={assistantId} onUploadSuccess={loadDocuments} />

        <div style={{ marginTop: "var(--spacing-8)" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "var(--spacing-4)",
            }}
          >
            <h2
              style={{
                fontSize: "var(--font-size-lg)",
                fontWeight: 700,
                color: "var(--color-navy)",
              }}
            >
              Documents ({Array.isArray(documents) ? documents.length : 0})
            </h2>
            <button
              type="button"
              id="refresh-documents-button"
              onClick={loadDocuments}
              disabled={isLoading}
              style={{
                padding: "6px 12px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-white)",
                color: "var(--color-navy)",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                cursor: isLoading ? "wait" : "pointer",
              }}
            >
              Refresh
            </button>
          </div>

          <DocumentsTable
            assistantId={assistantId}
            documents={documents}
            isLoading={isLoading}
            onRefresh={loadDocuments}
            onReview={handleOpenReview}
          />
        </div>
      </main>

      <ReviewDrawer
        isOpen={Boolean(reviewVersionId)}
        assistantId={assistantId}
        versionId={reviewVersionId || ""}
        documentTitle={reviewDocTitle}
        onClose={handleCloseReview}
        onPromoted={handleCloseReview}
      />
    </div>
  );
}
