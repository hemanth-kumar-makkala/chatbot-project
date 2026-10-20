"use client";

import { useEffect, useState, useCallback, useRef, use } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { UploadArea } from "@/components/UploadArea";
import { DocumentsTable } from "@/components/DocumentsTable";
import { ReviewDrawer } from "@/components/ReviewDrawer";
import { getDocuments, getCurrentUser, type DocumentItem, type User } from "@/lib/api";
import { isFinalStatus } from "@/lib/statusChips";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface DataSourcesPageProps {
  params: Promise<{ assistantId: string }>;
}

export default function DataSourcesPage({ params }: DataSourcesPageProps) {
  const resolvedParams = use(params);
  const rawAssistantId = resolvedParams.assistantId;
  const assistantId =
    rawAssistantId === "00000000-0000-0002-0000-000000000001"
      ? DEV_DEFAULT_ASSISTANT_ID
      : rawAssistantId;
  const router = useRouter();
  const routerRef = useRef(router);
  routerRef.current = router;

  const [user, setUser] = useState<User | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedSourceType, setSelectedSourceType] = useState<"files" | "snippet" | "qa">("files");

  // Review drawer state
  const [reviewVersionId, setReviewVersionId] = useState<string | null>(null);
  const [reviewDocTitle, setReviewDocTitle] = useState<string>("");

  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  const loadDocuments = useCallback(async () => {
    const result = await getDocuments(assistantId);
    if (result.ok) {
      setDocuments(Array.isArray(result.data) ? result.data : []);
      setFetchError(null);
    } else {
      if (result.status === 401 || result.code === "UNAUTHORIZED") {
        routerRef.current.push(
          `/login?redirect=/assistants/${encodeURIComponent(assistantId)}/data-sources`
        );
        return;
      }
      setFetchError(`Unable to load documents (${result.code}).`);
    }
    setIsLoading(false);
  }, [assistantId]);

  useEffect(() => {
    async function loadUser() {
      const res = await getCurrentUser();
      if (res.ok) {
        setUser(res.data);
      }
    }
    loadUser();
  }, []);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

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
      if (document.visibilityState === "visible") {
        loadDocuments();
        startPollIfNeeded();
      } else {
        clearPoll();
      }
    }

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }

    return () => {
      clearPoll();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
      }
    };
  }, [documents, loadDocuments]);

  function handleOpenReview(versionId: string, docTitle: string) {
    setReviewVersionId(versionId);
    setReviewDocTitle(docTitle);
  }

  function handleCloseReview() {
    setReviewVersionId(null);
    setReviewDocTitle("");
  }

  function handleReviewResolved() {
    loadDocuments();
  }

  // Filter documents by search query
  const filteredDocuments = documents.filter((doc) =>
    doc.title.toLowerCase().includes(searchQuery.toLowerCase().trim())
  );

  return (
    <AppShell
      assistantId={assistantId}
      assistantName={assistantId === DEV_DEFAULT_ASSISTANT_ID ? "Clinic A Assistant" : "Assistant"}
      workspaceName={user?.tenant_id || "Workspace"}
      userEmail={user?.email || null}
    >
      <div
        style={{
          maxWidth: "1080px",
          margin: "0 auto",
          padding: "var(--spacing-8) var(--spacing-6)",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: "var(--spacing-6)",
            gap: "var(--spacing-4)",
            flexWrap: "wrap",
          }}
        >
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--spacing-3)",
                marginBottom: "var(--spacing-1)",
              }}
            >
              <h1
                style={{
                  fontSize: "var(--font-size-2xl)",
                  fontWeight: 700,
                  color: "var(--color-text-primary)",
                }}
              >
                Data sources
              </h1>
              <ConnectionBadge status="connected" label="Connected" />
            </div>
            <p
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--color-text-secondary)",
              }}
            >
              Ingest, audit, and manage knowledge base files for this client assistant. Answers are strictly grounded in these verified sources.
            </p>
          </div>
        </div>

        {/* Source Cards Row */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: "var(--spacing-4)",
            marginBottom: "var(--spacing-6)",
          }}
        >
          {/* Card 1: Add files */}
          <div
            onClick={() => setSelectedSourceType("files")}
            style={{
              padding: "var(--spacing-4)",
              borderRadius: "var(--radius-lg)",
              border:
                selectedSourceType === "files"
                  ? "2px solid var(--color-primary)"
                  : "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-card)",
              cursor: "pointer",
              transition: "border-color 0.15s ease",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "var(--spacing-2)",
              }}
            >
              <span style={{ fontSize: "20px" }}>📄</span>
              <span
                style={{
                  fontSize: "var(--font-size-xs)",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-full)",
                  backgroundColor: "var(--color-status-success-bg)",
                  color: "var(--color-status-success-text)",
                  fontWeight: 600,
                }}
              >
                Supported
              </span>
            </div>
            <div
              style={{
                fontSize: "var(--font-size-base)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "4px",
              }}
            >
              Add files
            </div>
            <p
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-secondary)",
              }}
            >
              PDF documents, Excel spreadsheets (.xlsx), and CSV tables.
            </p>
          </div>

          {/* Card 2: Text snippet */}
          <div
            style={{
              padding: "var(--spacing-4)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-card)",
              opacity: 0.8,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "var(--spacing-2)",
              }}
            >
              <span style={{ fontSize: "20px" }}>📝</span>
              <span
                style={{
                  fontSize: "var(--font-size-xs)",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-full)",
                  backgroundColor: "var(--color-bg-subtle)",
                  color: "var(--color-text-secondary)",
                  fontWeight: 600,
                }}
              >
                Coming later
              </span>
            </div>
            <div
              style={{
                fontSize: "var(--font-size-base)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "4px",
              }}
            >
              Text snippet
            </div>
            <p
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-secondary)",
              }}
            >
              Direct text snippets, policy memos, and raw text entries.
            </p>
          </div>

          {/* Card 3: Add Q&A */}
          <div
            style={{
              padding: "var(--spacing-4)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-card)",
              opacity: 0.8,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "var(--spacing-2)",
              }}
            >
              <span style={{ fontSize: "20px" }}>❓</span>
              <span
                style={{
                  fontSize: "var(--font-size-xs)",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-full)",
                  backgroundColor: "var(--color-bg-subtle)",
                  color: "var(--color-text-secondary)",
                  fontWeight: 600,
                }}
              >
                Coming later
              </span>
            </div>
            <div
              style={{
                fontSize: "var(--font-size-base)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "4px",
              }}
            >
              Add Q&A
            </div>
            <p
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-secondary)",
              }}
            >
              Curated question-and-answer pairs for direct clinical rules.
            </p>
          </div>
        </div>

        {/* Upload Area for Files */}
        {selectedSourceType === "files" && (
          <section
            aria-label="Upload document"
            style={{ marginBottom: "var(--spacing-8)" }}
          >
            <UploadArea
              assistantId={assistantId}
              onUploadSuccess={loadDocuments}
            />
          </section>
        )}

        {/* Documents Management Section */}
        <section aria-label="Documents in knowledge base">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "var(--spacing-4)",
              gap: "var(--spacing-4)",
              flexWrap: "wrap",
            }}
          >
            <div>
              <h2
                style={{
                  fontSize: "var(--font-size-lg)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                }}
              >
                Documents
              </h2>
              <div
                style={{
                  fontSize: "var(--font-size-xs)",
                  color: "var(--color-text-secondary)",
                  marginTop: "2px",
                }}
              >
                Total: {documents.length} document{documents.length === 1 ? "" : "s"} in knowledge base
              </div>
            </div>

            {/* Document Search Filter */}
            <div style={{ minWidth: "240px" }}>
              <input
                type="text"
                placeholder="Search documents..."
                aria-label="Search documents"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  width: "100%",
                  padding: "6px 12px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-page)",
                  color: "var(--color-text-primary)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
            </div>
          </div>

          {fetchError && (
            <div
              role="alert"
              style={{
                backgroundColor: "var(--color-status-error-bg)",
                color: "var(--color-status-error-text)",
                padding: "var(--spacing-3)",
                borderRadius: "var(--radius-md)",
                marginBottom: "var(--spacing-4)",
                fontSize: "var(--font-size-sm)",
              }}
            >
              {fetchError}
            </div>
          )}

          <DocumentsTable
            assistantId={assistantId}
            documents={filteredDocuments}
            isLoading={isLoading}
            onRefresh={loadDocuments}
            onReview={(doc) => handleOpenReview(doc.latest_version_id || "", doc.title)}
          />
        </section>

        {/* Review Drawer */}
        <ReviewDrawer
          assistantId={assistantId}
          versionId={reviewVersionId || ""}
          documentTitle={reviewDocTitle}
          isOpen={Boolean(reviewVersionId)}
          onClose={handleCloseReview}
          onPromoted={handleReviewResolved}
        />
      </div>
    </AppShell>
  );
}
