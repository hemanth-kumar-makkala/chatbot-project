"use client";

import { useEffect, useState, useRef, use } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { SourcePanel } from "@/components/SourcePanel";
import { ExtractionTable } from "@/components/ExtractionTable";
import { DataSourceChip } from "@/components/DataSourceChip";
import { getFeatureDataSource } from "@/lib/connections";
import {
  getDemoAssistantAnswer,
  getDemoStatusLines,
  DEMO_SUGGESTED_QUESTIONS,
  GOLDEN_FAX_DOCUMENTS,
  type GoldenFaxDocument,
} from "@/lib/demo/fixtures";
import {
  askQuestion,
  extractSessionDocument,
  getCurrentUser,
  submitFeedback,
  uploadSessionDocument,
  type QuestionResponse,
  type SourceCitation,
  type User,
} from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface HomePageProps {
  params: Promise<{ assistantId: string }>;
}

interface AttachedDocument {
  session_document_id?: string;
  filename: string;
  title: string;
  description?: string;
  page_count: number;
  retention_hours?: number;
  preview_page?: number;
  image_stem?: string;
  path?: string;
  isExtracted?: boolean;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: QuestionResponse;
  errorMessage?: string;
}

export default function AssistantHomePage({ params }: HomePageProps) {
  const resolvedParams = use(params);
  const assistantId = resolvedParams.assistantId;
  const router = useRouter();
  const routerRef = useRef(router);
  routerRef.current = router;

  const [user, setUser] = useState<User | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [demoStatusLines, setDemoStatusLines] = useState<string[]>([]);
  const [sourcePanelOpen, setSourcePanelOpen] = useState(false);
  const [activeSourceDoc, setActiveSourceDoc] = useState("");
  const [activeSourcePage, setActiveSourcePage] = useState<number>(1);
  const [activeSourceVersionId, setActiveSourceVersionId] = useState<string | undefined>(undefined);
  const [liveSourceWarning, setLiveSourceWarning] = useState(false);

  // Session upload & extraction state (Invariant 7)
  const [attachedDoc, setAttachedDoc] = useState<AttachedDocument | null>(null);
  const [attachPickerOpen, setAttachPickerOpen] = useState(false);
  const [arbitraryFileError, setArbitraryFileError] = useState<string | null>(null);
  const [liveExtractionError, setLiveExtractionError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractingStatusLine, setExtractingStatusLine] = useState<string | null>(null);
  const liveFileInputRef = useRef<HTMLInputElement | null>(null);

  // Feedback state (Task 5.5)
  const [feedbackMap, setFeedbackMap] = useState<
    Record<
      string,
      {
        rating: "up" | "down";
        reasonChoice?: string;
        comments?: string;
        saved?: boolean;
      }
    >
  >({});
  const [activeReasonMsgId, setActiveReasonMsgId] = useState<string | null>(null);
  const [selectedReasonChoice, setSelectedReasonChoice] = useState<string>("Inaccurate facts");
  const [reasonComments, setReasonComments] = useState<string>("");
  const [liveFeedbackWarningId, setLiveFeedbackWarningId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const askDataSource = getFeatureDataSource("chat_ask");
  const isLiveAssistant =
    assistantId !== "00000000-0000-0000-0002-000000000002" &&
    (assistantId === DEV_DEFAULT_ASSISTANT_ID || (askDataSource as string) === "live");

  useEffect(() => {
    async function loadAuth() {
      try {
        const res = await getCurrentUser();
        if (!res.ok) {
          routerRef.current.push("/login");
          return;
        }
        setUser(res.data);
      } catch {
        routerRef.current.push("/login");
      }
    }
    loadAuth();
  }, []);

  useEffect(() => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === "function") {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isSubmitting]);

  async function handleLiveFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (liveFileInputRef.current) {
      liveFileInputRef.current.value = "";
    }
    if (!file || isUploading || isExtracting || isSubmitting) return;

    setLiveExtractionError(null);
    setIsUploading(true);

    const activeConvId = conversationId || "00000000-0000-0000-0000-000000000001";
    try {
      const res = await uploadSessionDocument(assistantId, activeConvId, file);
      if (!res.ok) {
        setLiveExtractionError(res.code || "UPLOAD_FAILED");
        return;
      }

      if (!conversationId && res.data.conversation_id) {
        setConversationId(res.data.conversation_id);
      }

      const isAlreadyExtracted =
        res.data.status === "extracted" &&
        Boolean(res.data.extracted_fields && res.data.extracted_fields.length > 0);

      setAttachedDoc({
        session_document_id: res.data.session_document_id,
        filename: res.data.file_name,
        title: res.data.file_name,
        page_count: res.data.page_count,
        retention_hours: res.data.retention_hours || 24,
        path: res.data.page_image_url,
        isExtracted: isAlreadyExtracted,
      });

      if (isAlreadyExtracted) {
        const extractionResp: QuestionResponse = {
          request_id: `req-${Date.now()}`,
          tenant_id: "",
          conversation_id: res.data.conversation_id,
          message_id: `msg-${Date.now()}-a`,
          status: "answered",
          answer: "Extracted document fields via dual-read extraction subgraph.",
          citations: [],
          evidence_type: "extraction",
          extracted_fields: res.data.extracted_fields,
          sources: [
            {
              document_title: res.data.file_name,
              page_number: 1,
              version: "1",
              evidence_type: "visual",
              version_id: res.data.page_image_url,
            },
          ],
          status_lines: [
            "Session document stored under Invariant 7 (strictly isolated from knowledge index)",
            "Executed extraction subgraph: dual read compare and field state classification",
          ],
        };

        const asstMsg: ChatMessage = {
          id: `msg-${Date.now()}-a`,
          role: "assistant",
          content: extractionResp.answer,
          response: extractionResp,
        };

        setMessages((prev) => [...prev, asstMsg]);
      }
    } catch {
      setLiveExtractionError("UPLOAD_FAILED");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleExtractFields() {
    if (
      !attachedDoc ||
      !attachedDoc.session_document_id ||
      isExtracting ||
      isUploading ||
      isSubmitting ||
      attachedDoc.isExtracted
    ) {
      return;
    }

    setIsExtracting(true);
    setExtractingStatusLine("Page 1 is a scanned image, reading it now...");
    setLiveExtractionError(null);

    // Stage M: After 10s show loading vision model notice
    const visionModelTimer = setTimeout(() => {
      setExtractingStatusLine("Still working, loading the vision model...");
    }, 10_000);

    const activeConvId = conversationId || "00000000-0000-0000-0000-000000000001";
    try {
      const res = await extractSessionDocument(
        assistantId,
        activeConvId,
        attachedDoc.session_document_id
      );
      clearTimeout(visionModelTimer);
      if (!res.ok) {
        setLiveExtractionError(res.code || "EXTRACTION_FAILED");
        return;
      }

      setAttachedDoc((prev) => (prev ? { ...prev, isExtracted: true } : null));

      const extractionResp: QuestionResponse = {
        request_id: `req-${Date.now()}`,
        tenant_id: "",
        conversation_id: res.data.conversation_id,
        message_id: `msg-${Date.now()}-a`,
        status: "answered",
        answer: "Extracted document fields via dual-read extraction subgraph.",
        citations: [],
        evidence_type: "extraction",
        extracted_fields: res.data.extracted_fields,
        sources: [
          {
            document_title: res.data.file_name,
            page_number: 1,
            version: "1",
            evidence_type: "visual",
            version_id: res.data.page_image_url,
          },
        ],
        status_lines: [
          "Session document stored under Invariant 7 (strictly isolated from knowledge index)",
          "Executed extraction subgraph: dual read compare and field state classification",
        ],
      };

      const asstMsg: ChatMessage = {
        id: `msg-${Date.now()}-a`,
        role: "assistant",
        content: extractionResp.answer,
        response: extractionResp,
      };

      setMessages((prev) => [...prev, asstMsg]);
    } catch {
      clearTimeout(visionModelTimer);
      setLiveExtractionError("EXTRACTION_FAILED");
    } finally {
      clearTimeout(visionModelTimer);
      setIsExtracting(false);
      setExtractingStatusLine(null);
    }
  }

  // "New chat" button always starts an empty conversation
  function handleNewChat() {
    setMessages([]);
    setConversationId(null);
    setInputValue("");
    setAttachedDoc(null);
    setDemoStatusLines([]);
    setExtractingStatusLine(null);
    setIsUploading(false);
    setIsExtracting(false);
    setActiveReasonMsgId(null);
    setLiveFeedbackWarningId(null);
    setLiveSourceWarning(false);
    setArbitraryFileError(null);
    setLiveExtractionError(null);
  }

  async function handleFeedbackClick(msgId: string, rating: "up" | "down") {
    if (isLiveAssistant) {
      if (rating === "up") {
        setLiveFeedbackWarningId(null);
        const res = await submitFeedback(assistantId, msgId, "up");
        if (res.ok) {
          setFeedbackMap((prev) => ({
            ...prev,
            [msgId]: { rating: "up", saved: true },
          }));
          if (activeReasonMsgId === msgId) {
            setActiveReasonMsgId(null);
          }
        } else {
          setLiveFeedbackWarningId(msgId);
        }
      } else {
        // Thumbs down: open reason popover
        setActiveReasonMsgId(msgId);
        setSelectedReasonChoice("Inaccurate facts");
        setReasonComments("");
      }
      return;
    }

    if (rating === "up") {
      setFeedbackMap((prev) => ({
        ...prev,
        [msgId]: { rating: "up", saved: true },
      }));
      if (activeReasonMsgId === msgId) {
        setActiveReasonMsgId(null);
      }
    } else {
      setActiveReasonMsgId(msgId);
      setSelectedReasonChoice("Inaccurate facts");
      setReasonComments("");
    }
  }

  async function handleSubmitReason(msgId: string) {
    const fullReason =
      reasonComments.trim() !== ""
        ? `${selectedReasonChoice}: ${reasonComments.trim()}`
        : selectedReasonChoice;

    if (isLiveAssistant) {
      setLiveFeedbackWarningId(null);
      const res = await submitFeedback(assistantId, msgId, "down", fullReason);
      if (res.ok) {
        setFeedbackMap((prev) => ({
          ...prev,
          [msgId]: {
            rating: "down",
            reasonChoice: selectedReasonChoice,
            comments: reasonComments,
            saved: true,
          },
        }));
      } else {
        setLiveFeedbackWarningId(msgId);
      }
      setActiveReasonMsgId(null);
      setReasonComments("");
      return;
    }

    setFeedbackMap((prev) => ({
      ...prev,
      [msgId]: {
        rating: "down",
        reasonChoice: selectedReasonChoice,
        comments: reasonComments,
        saved: true,
      },
    }));
    setActiveReasonMsgId(null);
    setReasonComments("");
  }

  function handleOpenSource(docTitle: string, pageNumber: number = 1, versionId?: string) {
    setActiveSourceDoc(docTitle);
    setActiveSourcePage(pageNumber);
    setActiveSourceVersionId(versionId);
    setSourcePanelOpen(true);
    if (isLiveAssistant && !versionId) {
      setLiveSourceWarning(true);
    }
  }

  async function executeQuestion(query: string) {
    const trimmed = query.trim();
    if (!trimmed || isSubmitting) return;

    const userMsgId = `user-${Date.now()}`;
    const userMsg: ChatMessage = {
      id: userMsgId,
      role: "user",
      content: trimmed,
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputValue("");
    setIsSubmitting(true);
    setLiveSourceWarning(false);

    if (!isLiveAssistant) {
      const isExtraction =
        Boolean(attachedDoc) ||
        trimmed.toLowerCase().includes("extract") ||
        trimmed.toLowerCase().includes("all fields") ||
        trimmed.toLowerCase().includes("patient fax") ||
        trimmed.toLowerCase().includes("field table");
      const isVisual =
        isExtraction ||
        trimmed.toLowerCase().includes("image") ||
        trimmed.toLowerCase().includes("page 31") ||
        trimmed.toLowerCase().includes("medication") ||
        trimmed.toLowerCase().includes("scan") ||
        trimmed.toLowerCase().includes("urgent refill");

      const lines = isExtraction
        ? [
            "Searching Pinecrest Medical Group documents...",
            "Page 1 is a scanned image, reading it now...",
            "Executing dual OCR and vision extraction subgraph...",
            "Reconciling readings and running check-digit validators...",
          ]
        : getDemoStatusLines("Client A", isVisual, 1);

      setDemoStatusLines([lines[0]]);
      if (lines.length > 1) {
        setTimeout(() => {
          setDemoStatusLines(lines);
        }, 80);
      }
    } else {
      setDemoStatusLines([]);
    setExtractingStatusLine(null);
    setIsUploading(false);
    setIsExtracting(false);
    }

    const currentAttachedDocName = attachedDoc?.filename;
    if (attachedDoc) {
      setAttachedDoc(null);
    }

    try {
      if (!isLiveAssistant) {
        const demoRes = getDemoAssistantAnswer(
          assistantId,
          trimmed,
          conversationId,
          currentAttachedDocName
        );
        if (demoRes.conversation_id) {
          setConversationId(demoRes.conversation_id);
        }
        const asstMsg: ChatMessage = {
          id: demoRes.message_id || `asst-${Date.now()}`,
          role: "assistant",
          content: demoRes.answer,
          response: demoRes,
        };
        setMessages((prev) => [...prev, asstMsg]);
      } else {
        const res = await askQuestion(assistantId, trimmed, conversationId);
        if (res.ok) {
          if (res.data.conversation_id) {
            setConversationId(res.data.conversation_id);
          }
          const asstMsg: ChatMessage = {
            id: res.data.message_id || `asst-${Date.now()}`,
            role: "assistant",
            content: res.data.answer,
            response: res.data,
          };
          setMessages((prev) => [...prev, asstMsg]);
        } else {
          let errorMsgText = `Unable to complete question (${res.code}). Please try again.`;
          if (res.code === "RATE_LIMIT_EXCEEDED" || res.status === 429) {
            errorMsgText =
              "You've reached the query rate limit. Please wait a moment and try again.";
          } else if (res.code === "ASSISTANT_NOT_FOUND") {
            errorMsgText =
              "This assistant is unavailable for your account (not found or no tenant membership). Sign in with an authorized clinic account or select another assistant.";
          } else if (res.code === "CONVERSATION_NOT_FOUND") {
            errorMsgText =
              "This conversation could not be found. Start a + New chat and try again.";
          }
          const errorMsg: ChatMessage = {
            id: `err-${Date.now()}`,
            role: "assistant",
            content: "",
            errorMessage: errorMsgText,
          };
          setMessages((prev) => [...prev, errorMsg]);
        }
      }
    } catch {
      const errorMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        role: "assistant",
        content: "",
        errorMessage: "Unable to complete question (NETWORK_ERROR). Please try again.",
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    executeQuestion(inputValue);
  }

  function renderAssistantContent(msg: ChatMessage) {
    if (msg.errorMessage) {
      const isRateLimit = msg.errorMessage.includes("rate limit");
      return (
        <div
          data-testid={isRateLimit ? "rate-limit-error-message" : "chat-error-message"}
          style={{
            backgroundColor: "var(--color-status-error-bg)",
            color: "var(--color-status-error-text)",
            padding: "var(--spacing-3)",
            borderRadius: "var(--radius-md)",
            fontSize: "var(--font-size-sm)",
          }}
        >
          {msg.errorMessage}
        </div>
      );
    }

    const resp = msg.response;
    if (!resp) {
      return <div>{msg.content}</div>;
    }

    const status = resp.status;
    const isVisual = resp.evidence_type === "visual";
    const isTable = resp.evidence_type === "table" || resp.answer_mode === "table";
    const isGeneral = resp.route === "general" || status === "general";
    const isConflict = status === "conflict" || status === "conflicting";
    const isClarify = status === "clarify";
    const isError =
      status === "error" ||
      status === "safe_failure" ||
      Boolean(resp.error_code) ||
      Boolean(resp.public_error_ref);

    const statusLinesHeader = resp.status_lines && resp.status_lines.length > 0 && (
      <div
        data-testid="demo-message-status-lines"
        style={{
          fontSize: "var(--font-size-xs)",
          color: "var(--color-text-secondary)",
          marginBottom: "var(--spacing-2)",
          display: "flex",
          flexDirection: "column",
          gap: "2px",
        }}
      >
        {resp.status_lines.map((line: string, idx: number) => (
          <div key={`msg-sl-${idx}`}>› {line}</div>
        ))}
      </div>
    );

    // 0. PHI Extraction
    const isExtraction =
      resp.evidence_type === "extraction" ||
      Boolean(resp.extracted_fields && resp.extracted_fields.length > 0);
    if (isExtraction) {
      const fields = resp.extracted_fields || [];
      const firstSource = resp.sources?.[0];
      const docTitle =
        firstSource?.document_title ||
        attachedDoc?.filename ||
        attachedDoc?.title ||
        "session_document.pdf";
      const pageNum = firstSource?.page_number || 1;
      const stem = docTitle
        .toLowerCase()
        .replace(/\.[a-z0-9]+$/i, "")
        .trim()
        .replace(/[^a-z0-9_-]/g, "_");

      return (
        <div>
          {statusLinesHeader}
          <div style={{ marginBottom: "var(--spacing-2)" }}>
            <span
              style={{
                display: "inline-block",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "var(--color-bg-subtle)",
                color: "var(--color-text-primary)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border)",
              }}
            >
              Session Document Extraction
            </span>
          </div>

          <div
            data-testid="extraction-status-line"
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-secondary)",
              fontWeight: 600,
              marginBottom: "var(--spacing-3)",
            }}
          >
            › Page {pageNum} is a scanned image, reading it now...
          </div>

          <div
            data-testid="extraction-split-view"
            style={{
              display: "grid",
              gridTemplateColumns: "200px 1fr",
              gap: "var(--spacing-4)",
              alignItems: "start",
              margin: "var(--spacing-3) 0",
            }}
          >
            <div>
              <img
                data-testid="extraction-page-image"
                src={
                  firstSource?.version_id && firstSource.version_id.startsWith("/")
                    ? firstSource.version_id
                    : attachedDoc?.path && attachedDoc.path.startsWith("/")
                      ? attachedDoc.path
                      : `/demo/pages/${stem}-p${pageNum}.png`
                }
                alt={`${docTitle} Page ${pageNum} preview`}
                onClick={() => handleOpenSource(docTitle, pageNum)}
                style={{
                  width: "100%",
                  height: "auto",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  cursor: "pointer",
                }}
              />
            </div>
            <div>
              <ExtractionTable
                fields={fields}
                onOpenSource={(sourceDoc: string, page: number) => handleOpenSource(sourceDoc, page)}
              />
            </div>
          </div>
        </div>
      );
    }

    // 1. Refusal
    if (status === "refusal") {
      return (
        <div data-testid="chat-refusal-card">
          {statusLinesHeader}
          <div style={{ marginBottom: "var(--spacing-2)" }}>
            <span
              style={{
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "var(--color-bg-subtle)",
                color: "var(--color-text-secondary)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border)",
              }}
            >
              Refusal
            </span>
          </div>
          <div
            style={{
              padding: "var(--spacing-3)",
              backgroundColor: "var(--color-bg-subtle)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              fontSize: "var(--font-size-sm)",
              whiteSpace: "pre-wrap",
            }}
          >
            {resp.answer}
          </div>
          <div
            data-testid="refusal-no-evidence-note"
            style={{
              marginTop: "var(--spacing-2)",
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-secondary)",
            }}
          >
            No supporting evidence found in the documents
          </div>
        </div>
      );
    }

    // 2. Conflict
    if (isConflict) {
      const conflictList =
        resp.conflict_sources && resp.conflict_sources.length > 0
          ? resp.conflict_sources
          : (resp.sources || []).map((s) => ({
              document_title: s.document_title,
              version: s.version || "",
              page_number: s.page_number,
              evidence_type: s.evidence_type || "text",
              sheet_name: s.sheet_name,
              row_number: s.row_number,
            }));

      return (
        <div data-testid="chat-conflict-card">
          {statusLinesHeader}
          <div
            style={{
              padding: "var(--spacing-3)",
              backgroundColor: "var(--color-warning-soft)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              marginBottom: "var(--spacing-2)",
            }}
          >
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                color: "var(--color-warning-text)",
                marginBottom: "4px",
              }}
            >
              Conflicting Sources
            </div>
            <div style={{ fontSize: "var(--font-size-sm)", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
              {resp.answer}
            </div>
          </div>

          {conflictList.length > 0 && (
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
              {conflictList.map((s, idx) => (
                <button
                  key={`conf-s-${idx}`}
                  type="button"
                  onClick={() => handleOpenSource(s.document_title, s.page_number ?? 1)}
                  style={{
                    fontSize: "var(--font-size-xs)",
                    padding: "2px 8px",
                    borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--color-border)",
                    backgroundColor: "var(--color-bg-page)",
                  }}
                >
                  <span>{s.document_title}</span>
                  {s.evidence_type === "table" || s.sheet_name ? (
                    <>
                      {s.sheet_name ? ` Sheet: ${s.sheet_name}` : ""}
                      {s.row_number != null ? ` Row ${s.row_number}` : ""}
                    </>
                  ) : (
                    s.page_number != null ? ` p.${s.page_number}` : ""
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      );
    }

    // 3. Clarify
    if (isClarify) {
      const topics = resp.clarify_topics || ["Prior Auth Procedures", "Appeals Process", "Forms Directory"];
      return (
        <div data-testid="chat-clarify-card">
          {statusLinesHeader}
          <div style={{ fontSize: "var(--font-size-sm)", marginBottom: "var(--spacing-3)", whiteSpace: "pre-wrap" }}>
            {resp.answer}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {topics.map((t, idx) => (
              <button
                key={`topic-${idx}`}
                type="button"
                aria-label={t}
                onClick={() => executeQuestion(t)}
                style={{
                  padding: "8px 12px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-card)",
                  textAlign: "left",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-text-primary)",
                }}
              >
                <span aria-hidden="true">› </span>
                <span>{t}</span>
              </button>
            ))}
          </div>
        </div>
      );
    }

    // 4. General Task Draft
    if (isGeneral) {
      return (
        <div data-testid="chat-general-draft">
          {statusLinesHeader}
          <div style={{ marginBottom: "var(--spacing-2)" }}>
            <span
              data-testid="general-draft-badge"
              style={{
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "var(--color-warning-soft)",
                color: "var(--color-warning-text)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              General Task Draft — Not grounded in client documents
            </span>
          </div>
          <div style={{ fontSize: "var(--font-size-sm)", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
            {resp.answer}
          </div>
        </div>
      );
    }

    // 5. Safe Failure
    if (isError) {
      const errRef = resp.public_error_ref || resp.error_code || "ERR-7K42";
      return (
        <div data-testid="chat-safe-failure-card">
          {statusLinesHeader}
          <div
            style={{
              padding: "var(--spacing-3)",
              backgroundColor: "var(--color-status-error-bg)",
              color: "var(--color-status-error-text)",
              borderRadius: "var(--radius-md)",
              fontSize: "var(--font-size-sm)",
              whiteSpace: "pre-wrap",
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: "4px" }}>System Error</div>
            <div>
              {resp.answer || "Unable to answer due to a system error."} Reference: {errRef}
            </div>
          </div>
        </div>
      );
    }

    // 6. Safe friendly fallback on unknown response type
    if (status && status !== "answered" && status !== "unknown") {
      // Stage M: If response is extraction evidence or failure, never show confusing "unexpected response format"
      if (resp.evidence_type === "extraction" || status === "failed" || status === "error") {
        return (
          <div
            data-testid="chat-fallback-card"
            style={{
              padding: "var(--spacing-3)",
              backgroundColor: "var(--color-bg-subtle)",
              borderRadius: "var(--radius-md)",
              fontSize: "var(--font-size-sm)",
              whiteSpace: "pre-wrap",
            }}
          >
            {resp.answer || "Unable to complete extraction. Please try again."}
          </div>
        );
      }

      return (
        <div
          data-testid="chat-fallback-card"
          style={{
            padding: "var(--spacing-3)",
            backgroundColor: "var(--color-bg-subtle)",
            borderRadius: "var(--radius-md)",
            fontSize: "var(--font-size-sm)",
            whiteSpace: "pre-wrap",
          }}
        >
          Received an unexpected response format. Please try again.
        </div>
      );
    }

    // 7. Standard / Visual / Table Answer
    return (
      <div data-testid="chat-grounded-answer">
        {statusLinesHeader}

        {isVisual && (
          <div style={{ marginBottom: "var(--spacing-2)", display: "flex", gap: "6px" }}>
            <span
              data-testid="visual-reading-badge"
              style={{
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "var(--color-info-soft)",
                color: "var(--color-info)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Visual Reading
            </span>
          </div>
        )}

        {isTable && (
          <div
            data-testid="chat-table-answer"
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-secondary)",
              marginBottom: "4px",
            }}
          >
            Table evidence citation
          </div>
        )}

        {/* Thumbnail on visual answer */}
        {(() => {
          if (!isVisual) return null;
          const firstVisSource = resp.sources?.[0];
          const visDocTitle = firstVisSource?.document_title || "document.pdf";
          const visPageNum = firstVisSource?.page_number || 1;
          const visStem = visDocTitle
            .toLowerCase()
            .replace(/\.[a-z0-9]+$/i, "")
            .trim()
            .replace(/[^a-z0-9_-]/g, "_");
          const visThumbSrc =
            isLiveAssistant && firstVisSource?.version_id
              ? firstVisSource.version_id.startsWith("/")
                ? firstVisSource.version_id
                : `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents/${encodeURIComponent(firstVisSource.version_id)}/pages/${visPageNum}/thumbnail`
              : `/demo/pages/${visStem}-p${visPageNum}.png`;

          return (
            <div data-testid="page-thumbnail-placeholder" style={{ margin: "var(--spacing-2) 0" }}>
              <img
                data-testid="page-thumbnail-image"
                src={visThumbSrc}
                alt={`Thumbnail of ${visDocTitle} Page ${visPageNum}`}
                onClick={() => handleOpenSource(visDocTitle, visPageNum)}
                style={{
                  width: "120px",
                  height: "auto",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--color-border)",
                  cursor: "pointer",
                }}
              />
            </div>
          );
        })()}

        <div style={{ fontSize: "var(--font-size-sm)", lineHeight: 1.5, marginBottom: "var(--spacing-3)", whiteSpace: "pre-wrap" }}>
          {resp.answer}
        </div>

        {/* Source Chips */}
        {!isGeneral && status !== "refusal" && resp.sources && resp.sources.length > 0 && (
          <div
            data-testid="chat-source-chips"
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "var(--spacing-2)",
              alignItems: "center",
            }}
          >
            <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}>
              Sources:
            </span>
            {resp.sources.map((s: SourceCitation, sIdx: number) => (
              <button
                key={`source-${sIdx}`}
                type="button"
                data-testid={`source-chip-${s.document_title}`}
                onClick={() => handleOpenSource(s.document_title, s.page_number ?? 1, s.version_id || s.version)}
                style={{
                  fontSize: "var(--font-size-xs)",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-page)",
                  color: "var(--color-text-primary)",
                  cursor: "pointer",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px",
                }}
              >
                <span>{s.document_title}</span>
                {s.version && <span>{s.version}</span>}
                {s.evidence_type === "table" || s.sheet_name ? (
                  <>
                    {s.sheet_name && <span>Sheet: {s.sheet_name}</span>}
                    {s.row_number != null && <span>Row {s.row_number}</span>}
                  </>
                ) : (
                  s.page_number != null && <span>Page {s.page_number}</span>
                )}
                {s.language && (
                  <span style={{ color: "var(--color-text-secondary)" }}>
                    Source ({s.language})
                  </span>
                )}
              </button>
            ))}
          </div>
        )}


      </div>
    );
  }

  return (
    <AppShell
      assistantId={assistantId}
      assistantName={isLiveAssistant ? "Clinic A Assistant" : "Assistant"}
      workspaceName={user?.tenant_id || "Workspace"}
      userEmail={user?.email || null}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          height: "calc(100vh - 64px)",
          maxWidth: "1000px",
          margin: "0 auto",
          width: "100%",
        }}
      >
        {/* Header Bar */}
        <div
          style={{
            padding: "var(--spacing-4) var(--spacing-6)",
            borderBottom: "1px solid var(--color-border)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-3)" }}>
            <h1
              style={{
                fontSize: "var(--font-size-lg)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
              }}
            >
              Home
            </h1>
            <ConnectionBadge status="connected" label="Connected" />
          </div>

          <button
            type="button"
            onClick={handleNewChat}
            style={{
              padding: "6px 12px",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-page)",
              color: "var(--color-text-primary)",
              fontSize: "var(--font-size-xs)",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            + New chat
          </button>
        </div>

        {/* Chat Messages Stream */}
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "var(--spacing-6)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--spacing-4)",
          }}
        >
          {/* Document card at the top of the conversation */}
          {attachedDoc && (
            <div
              data-testid="session-document-card"
              style={{
                padding: "12px 16px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-bg-card)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "12px",
                marginBottom: "var(--spacing-2)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <span style={{ fontSize: "20px" }}>📄</span>
                <div>
                  <div
                    style={{ fontWeight: 600, fontSize: "var(--font-size-sm)" }}
                    data-testid="attached-doc-filename"
                  >
                    {attachedDoc.filename}
                  </div>
                  <div
                    style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}
                  >
                    <span data-testid="attached-doc-page-count">
                      {attachedDoc.page_count} page{attachedDoc.page_count !== 1 ? "s" : ""}
                    </span>
                    {" • "}
                    <span data-testid="attached-doc-retention">
                      Attached to this chat. Deleted after {attachedDoc.retention_hours || 24} hours
                    </span>
                  </div>
                </div>
              </div>
              <button
                type="button"
                data-testid="extract-fields-button"
                onClick={handleExtractFields}
                disabled={
                  isExtracting || isUploading || isSubmitting || Boolean(attachedDoc.isExtracted)
                }
                style={{
                  padding: "6px 14px",
                  borderRadius: "var(--radius-md)",
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 600,
                  backgroundColor: attachedDoc.isExtracted
                    ? "var(--color-bg-subtle)"
                    : "var(--color-primary)",
                  color: attachedDoc.isExtracted
                    ? "var(--color-text-secondary)"
                    : "var(--color-text-on-primary)",
                  border: "none",
                  cursor: attachedDoc.isExtracted || isExtracting ? "not-allowed" : "pointer",
                }}
              >
                {isExtracting
                  ? "Extracting..."
                  : attachedDoc.isExtracted
                  ? "Fields Extracted"
                  : "Extract fields"}
              </button>
            </div>
          )}

          {messages.length === 0 && (
            <div style={{ textAlign: "center", marginTop: "var(--spacing-8)" }}>
              <h2
                style={{
                  fontSize: "var(--font-size-base)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  marginBottom: "var(--spacing-2)",
                }}
              >
                How can I help you today?
              </h2>
              <p
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-text-secondary)",
                  maxWidth: "480px",
                  margin: "0 auto var(--spacing-6) auto",
                }}
              >
                Ask questions about authorized clinical policies, prior authorizations, formularies, or upload a session document.
              </p>

              {/* Suggested Questions in Empty State */}
              <div
                data-testid="demo-suggested-questions"
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                  gap: "var(--spacing-3)",
                  maxWidth: "680px",
                  margin: "0 auto",
                  textAlign: "left",
                }}
              >
                {DEMO_SUGGESTED_QUESTIONS.map((q, idx) => (
                  <button
                    key={`sug-${idx}`}
                    type="button"
                    onClick={() => executeQuestion(q.question)}
                    style={{
                      padding: "var(--spacing-3)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--color-border)",
                      backgroundColor: "var(--color-bg-card)",
                      textAlign: "left",
                      cursor: "pointer",
                    }}
                  >
                    <div
                      style={{
                        fontSize: "var(--font-size-xs)",
                        fontWeight: 600,
                        color: "var(--color-text-primary)",
                        marginBottom: "2px",
                      }}
                    >
                      {q.question}
                    </div>
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--color-text-secondary)",
                      }}
                    >
                      {q.typeLabel}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) => {
            const isUser = m.role === "user";
            return (
              <div
                key={m.id}
                data-testid="chat-message"
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: isUser ? "flex-end" : "flex-start",
                }}
              >
                <div
                  style={{
                    maxWidth: "80%",
                    padding: "var(--spacing-3) var(--spacing-4)",
                    borderRadius: "var(--radius-lg)",
                    backgroundColor: isUser
                      ? "var(--color-primary)"
                      : "var(--color-bg-card)",
                    color: isUser
                      ? "var(--color-text-on-primary)"
                      : "var(--color-text-primary)",
                    border: isUser ? "none" : "1px solid var(--color-border)",
                    boxShadow: "var(--shadow-sm)",
                  }}
                >
                  {isUser ? m.content : renderAssistantContent(m)}
                </div>

                {/* Feedback controls on assistant message */}
                {!isUser && !m.errorMessage && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--spacing-2)",
                      marginTop: "4px",
                      fontSize: "var(--font-size-xs)",
                      color: "var(--color-text-secondary)",
                    }}
                  >
                    <button
                      type="button"
                      data-testid="feedback-thumbs-up"
                      aria-label="Thumbs up"
                      onClick={() => handleFeedbackClick(m.id, "up")}
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--color-border)",
                        backgroundColor:
                          feedbackMap[m.id]?.rating === "up"
                            ? "var(--color-status-success-bg)"
                            : "var(--color-bg-page)",
                      }}
                    >
                      👍
                    </button>
                    <button
                      type="button"
                      data-testid="feedback-thumbs-down"
                      aria-label="Thumbs down"
                      onClick={() => handleFeedbackClick(m.id, "down")}
                      style={{
                        padding: "2px 6px",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--color-border)",
                        backgroundColor:
                          feedbackMap[m.id]?.rating === "down"
                            ? "var(--color-danger-soft)"
                            : "var(--color-bg-page)",
                      }}
                    >
                      👎
                    </button>

                    {feedbackMap[m.id]?.saved && (
                      <span
                        data-testid={isLiveAssistant ? "feedback-live-saved-note" : "feedback-demo-saved-note"}
                        style={{
                          fontSize: "11px",
                          color: "var(--color-status-success-text)",
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "4px",
                        }}
                      >
                        {!isLiveAssistant && <DataSourceChip mode="demo" label="Demo" />}
                        <span>{isLiveAssistant ? "Feedback recorded" : "Saved for this demo session"}</span>
                      </span>
                    )}

                    {liveFeedbackWarningId === m.id && (
                      <span
                        data-testid="feedback-live-error"
                        style={{
                          fontSize: "11px",
                          color: "var(--color-text-secondary)",
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "4px",
                        }}
                      >
                        <span>Failed to record feedback. Please try again.</span>
                      </span>
                    )}
                  </div>
                )}

                {/* Thumbs Down Reason Popover */}
                {activeReasonMsgId === m.id && (
                  <div
                    data-testid="feedback-reason-popover"
                    style={{
                      marginTop: "var(--spacing-2)",
                      padding: "var(--spacing-3)",
                      backgroundColor: "var(--color-bg-page)",
                      borderRadius: "var(--radius-md)",
                      border: "1px solid var(--color-border)",
                      boxShadow: "var(--shadow-sm)",
                      width: "280px",
                    }}
                  >
                    <div style={{ fontSize: "11px", fontWeight: 600, marginBottom: "4px" }}>
                      Why did this answer not help?
                    </div>
                    {["Inaccurate facts", "Too long", "Wrong source"].map((choice) => (
                      <label
                        key={choice}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                          fontSize: "var(--font-size-xs)",
                          cursor: "pointer",
                          marginBottom: "2px",
                        }}
                      >
                        <input
                          type="radio"
                          name={`reason-${m.id}`}
                          value={choice}
                          checked={selectedReasonChoice === choice}
                          onChange={(e) => setSelectedReasonChoice(e.target.value)}
                        />
                        {choice}
                      </label>
                    ))}
                    <textarea
                      data-testid="feedback-comments-input"
                      placeholder="Optional details..."
                      rows={2}
                      value={reasonComments}
                      onChange={(e) => setReasonComments(e.target.value)}
                      style={{
                        width: "100%",
                        padding: "4px 6px",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--color-border)",
                        fontSize: "var(--font-size-xs)",
                        marginTop: "4px",
                        marginBottom: "6px",
                      }}
                    />
                    <div style={{ display: "flex", justifyContent: "flex-end", gap: "4px" }}>
                      <button
                        type="button"
                        onClick={() => setActiveReasonMsgId(null)}
                        style={{
                          padding: "2px 8px",
                          fontSize: "11px",
                          border: "1px solid var(--color-border)",
                          borderRadius: "var(--radius-sm)",
                        }}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        data-testid="feedback-submit-reason"
                        onClick={() => handleSubmitReason(m.id)}
                        style={{
                          padding: "2px 8px",
                          fontSize: "11px",
                          backgroundColor: "var(--color-primary)",
                          color: "var(--color-text-on-primary)",
                          borderRadius: "var(--radius-sm)",
                          fontWeight: 600,
                        }}
                      >
                        Save feedback
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {(isSubmitting || extractingStatusLine) && (
            <div>
              {extractingStatusLine ? (
                <div
                  data-testid="live-extracting-status"
                  style={{
                    padding: "var(--spacing-3)",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--color-bg-card)",
                    border: "1px solid var(--color-border)",
                    maxWidth: "360px",
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  › {extractingStatusLine}
                </div>
              ) : isLiveAssistant ? (
                <div
                  data-testid="live-loading-indicator"
                  style={{
                    padding: "var(--spacing-3)",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--color-bg-card)",
                    border: "1px solid var(--color-border)",
                    maxWidth: "360px",
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  <span data-testid="chat-loading-indicator">Searching documents...</span>
                </div>
              ) : (
                <div
                  data-testid="demo-status-lines"
                  style={{
                    padding: "var(--spacing-3)",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--color-bg-card)",
                    border: "1px solid var(--color-border)",
                    maxWidth: "360px",
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                    display: "flex",
                    flexDirection: "column",
                    gap: "2px",
                  }}
                >
                  {demoStatusLines.map((l, idx) => (
                    <div key={`cur-sl-${idx}`}>› {l}</div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input Bar */}
        <div
          style={{
            padding: "var(--spacing-4) var(--spacing-6)",
            borderTop: "1px solid var(--color-border)",
            backgroundColor: "var(--color-bg-surface)",
          }}
        >
          {/* Attached Document Banner (Invariant 7) */}
          {attachedDoc && (
            <div
              data-testid="session-document-indicator"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "6px 10px",
                backgroundColor: "var(--color-bg-subtle)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border)",
                marginBottom: "var(--spacing-2)",
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-primary)",
              }}
            >
              <span>
                Session document attached: <strong>{attachedDoc.filename}</strong> — stays in this conversation only and is never added to the knowledge base.
              </span>
              <button
                type="button"
                onClick={() => setAttachedDoc(null)}
                style={{ fontSize: "12px", color: "var(--color-text-secondary)" }}
              >
                ✕
              </button>
            </div>
          )}

          <form
            onSubmit={handleSubmit}
            style={{
              display: "flex",
              gap: "var(--spacing-2)",
              alignItems: "center",
            }}
          >
            {/* Live Session Upload Input */}
            <input
              type="file"
              data-testid="live-file-input"
              ref={liveFileInputRef}
              onChange={handleLiveFileUpload}
              accept=".pdf,image/*"
              style={{ display: "none" }}
            />

            {/* Attach Button */}
            <button
              type="button"
              data-testid="chat-attach-button"
              disabled={isSubmitting || isUploading || isExtracting}
              onClick={() => {
                if (isLiveAssistant) {
                  liveFileInputRef.current?.click();
                } else {
                  setAttachPickerOpen(true);
                }
              }}
              title="Attach session fax document"
              style={{
                padding: "8px 12px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-bg-page)",
                fontSize: "var(--font-size-sm)",
                cursor:
                  isSubmitting || isUploading || isExtracting ? "not-allowed" : "pointer",
              }}
            >
              📎
            </button>

            <input
              type="text"
              role="textbox"
              placeholder="Ask a question about clinic documents..."
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              disabled={isSubmitting || isUploading || isExtracting}
              style={{
                flex: 1,
                padding: "10px 14px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-bg-page)",
                color: "var(--color-text-primary)",
                fontSize: "var(--font-size-sm)",
              }}
            />

            <button
              type="submit"
              data-testid="chat-submit-button"
              aria-label="Send"
              disabled={isSubmitting || isUploading || isExtracting || !inputValue.trim()}
              style={{
                padding: "10px 18px",
                borderRadius: "var(--radius-md)",
                backgroundColor:
                  isSubmitting || isUploading || isExtracting || !inputValue.trim()
                    ? "var(--color-bg-subtle)"
                    : "var(--color-primary)",
                color:
                  isSubmitting || isUploading || isExtracting || !inputValue.trim()
                    ? "var(--color-text-secondary)"
                    : "var(--color-text-on-primary)",
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                border: "none",
                cursor:
                  isSubmitting || isUploading || isExtracting || !inputValue.trim()
                    ? "not-allowed"
                    : "pointer",
              }}
            >
              Send
            </button>
          </form>

          {liveExtractionError && (
            <div
              role="alert"
              data-testid="live-extraction-error"
              style={{
                marginTop: "4px",
                fontSize: "11px",
                color: "var(--color-danger)",
              }}
            >
              {liveExtractionError}
            </div>
          )}
        </div>
      </div>

      {/* Attach Picker Modal (Demo Mode) */}
      {attachPickerOpen && (
        <div
          data-testid="demo-attach-picker"
          role="dialog"
          aria-label="Attach session document"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "var(--color-backdrop)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
            padding: "var(--spacing-4)",
          }}
        >
          <div
            style={{
              backgroundColor: "var(--color-bg-page)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--color-border)",
              maxWidth: "460px",
              width: "100%",
              padding: "var(--spacing-6)",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "var(--spacing-3)",
              }}
            >
              <h3
                style={{
                  fontSize: "var(--font-size-base)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                }}
              >
                Attach sample fax document
              </h3>
              <button
                type="button"
                onClick={() => setAttachPickerOpen(false)}
                style={{ fontSize: "14px", color: "var(--color-text-secondary)" }}
              >
                ✕
              </button>
            </div>

            <p
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-secondary)",
                marginBottom: "var(--spacing-4)",
              }}
            >
              Select a golden fax document for side-by-side extraction testing.
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: "var(--spacing-2)", marginBottom: "var(--spacing-4)" }}>
              {GOLDEN_FAX_DOCUMENTS.map((fax) => (
                <button
                  key={fax.filename}
                  type="button"
                  data-testid={`picker-doc-${fax.filename}`}
                  onClick={() => {
                    setAttachedDoc(fax);
                    setAttachPickerOpen(false);
                  }}
                  style={{
                    padding: "8px 12px",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    backgroundColor: "var(--color-bg-card)",
                    textAlign: "left",
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 500,
                    cursor: "pointer",
                  }}
                >
                  <span>{fax.filename}</span>
                  <span style={{ color: "var(--color-text-secondary)", marginLeft: "6px" }}>({fax.title})</span>
                </button>
              ))}
            </div>

            {/* Arbitrary Local File Input */}
            <div style={{ borderTop: "1px solid var(--color-border)", paddingTop: "var(--spacing-3)" }}>
              <label
                style={{
                  display: "block",
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "var(--color-text-secondary)",
                  marginBottom: "4px",
                }}
              >
                Or select local file:
              </label>
              <input
                type="file"
                data-testid="arbitrary-file-input"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    setArbitraryFileError("Demo only reads sample fax documents from the golden set");
                  }
                }}
                style={{ fontSize: "var(--font-size-xs)" }}
              />
              {arbitraryFileError && (
                <div
                  role="alert"
                  data-testid="demo-arbitrary-file-error"
                  style={{
                    marginTop: "6px",
                    fontSize: "11px",
                    color: "var(--color-danger)",
                  }}
                >
                  {arbitraryFileError}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* PDF.js Source Viewer Panel */}
      <SourcePanel
        isOpen={sourcePanelOpen}
        onClose={() => setSourcePanelOpen(false)}
        documentTitle={activeSourceDoc}
        initialPage={activeSourcePage}
        isLiveAssistant={isLiveAssistant}
        assistantId={assistantId}
        versionId={activeSourceVersionId}
      />
    </AppShell>
  );
}
