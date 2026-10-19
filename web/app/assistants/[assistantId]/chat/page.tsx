"use client";

import { useEffect, useState, useRef, use } from "react";
import { useRouter } from "next/navigation";
import { TopBar } from "@/components/TopBar";
import { DataSourceChip } from "@/components/DataSourceChip";
import { SourcePanel } from "@/components/SourcePanel";
import { ExtractionTable } from "@/components/ExtractionTable";
import { getFeatureDataSource } from "@/lib/connections";
import {
  getDemoConversations,
  getDemoAssistantAnswer,
  getDemoStatusLines,
  DEMO_SUGGESTED_QUESTIONS,
  GOLDEN_FAX_DOCUMENTS,
  type DemoConversation,
  type GoldenFaxDocument,
} from "@/lib/demo/fixtures";
import {
  askQuestion,
  getCurrentUser,
  type QuestionResponse,
  type SourceCitation,
  type User,
} from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface ChatPageProps {
  params: Promise<{ assistantId: string }>;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: QuestionResponse;
  errorMessage?: string;
}

export default function ChatPage({ params }: ChatPageProps) {
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
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [demoStatusLines, setDemoStatusLines] = useState<string[]>([]);
  const [sourcePanelOpen, setSourcePanelOpen] = useState(false);
  const [activeSourceDoc, setActiveSourceDoc] = useState("prior_auth_guide.pdf");
  const [activeSourcePage, setActiveSourcePage] = useState<number>(1);

  // UI-4 Session upload & extraction state
  const [attachedDoc, setAttachedDoc] = useState<GoldenFaxDocument | null>(null);
  const [attachPickerOpen, setAttachPickerOpen] = useState(false);
  const [liveAttachWarningOpen, setLiveAttachWarningOpen] = useState(false);
  const [arbitraryFileError, setArbitraryFileError] = useState<string | null>(null);

  function handleOpenSource(docTitle: string, pageNumber: number = 1) {
    setActiveSourceDoc(docTitle);
    setActiveSourcePage(pageNumber);
    setSourcePanelOpen(true);
  }

  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const isLiveAssistant = assistantId === DEV_DEFAULT_ASSISTANT_ID;
  const historyDataSource = getFeatureDataSource("conversation_history");
  const askDataSource = isLiveAssistant ? getFeatureDataSource("chat_ask") : "demo";
  const demoConversations = getDemoConversations(assistantId);

  useEffect(() => {
    async function loadAuth() {
      const res = await getCurrentUser();
      if (!res.ok) {
        routerRef.current.push("/login");
        return;
      }
      setUser(res.data);
    }
    loadAuth();
  }, []);

  useEffect(() => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === "function") {
      messagesEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isSubmitting]);

  function handleSelectConversation(conv: DemoConversation) {
    setSelectedConvId(conv.id);
    setConversationId(conv.id);
    const chatMsgs: ChatMessage[] = conv.messages.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      response: m.response,
    }));
    setMessages(chatMsgs);
  }

  function handleNewConversation() {
    setSelectedConvId(null);
    setConversationId(null);
    setMessages([]);
    setInputValue("");
    setDemoStatusLines([]);
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

    if (!isLiveAssistant || askDataSource === "demo") {
      const isExtraction =
        Boolean(attachedDoc) ||
        trimmed.toLowerCase().includes("extract") ||
        trimmed.toLowerCase().includes("all fields") ||
        trimmed.toLowerCase().includes("patient fax") ||
        trimmed.toLowerCase().includes("field table");
      const isVisual =
        isExtraction ||
        trimmed.toLowerCase().includes("amoxicillin") ||
        trimmed.toLowerCase().includes("image") ||
        trimmed.toLowerCase().includes("page 31") ||
        trimmed.toLowerCase().includes("medication") ||
        trimmed.toLowerCase().includes("evelyn reed") ||
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
    }

    if (attachedDoc) {
      setAttachedDoc(null);
    }

    try {
      if (!isLiveAssistant || askDataSource === "demo") {
        // Demo assistant: strictly uses demo fixtures, never calls live backend
        const demoRes = getDemoAssistantAnswer(assistantId, trimmed, conversationId);
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
          const errorMsg: ChatMessage = {
            id: `err-${Date.now()}`,
            role: "assistant",
            content: "",
            errorMessage: `Unable to complete question (${res.code}). Please try again.`,
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
      return (
        <div
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

    const statusLinesHeader = resp.status_lines && resp.status_lines.length > 0 && (
      <div
        data-testid="demo-message-status-lines"
        style={{
          fontSize: "var(--font-size-xs)",
          color: "var(--color-navy)",
          opacity: 0.85,
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

    // 0. PHI Field Extraction View (staff day step 6, PHASE-4 Task 4.6)
    const isExtraction =
      resp.evidence_type === "extraction" ||
      Boolean(resp.extracted_fields && resp.extracted_fields.length > 0);
    if (isExtraction) {
      const fields = resp.extracted_fields || [];
      const firstSource = resp.sources?.[0];
      const docTitle = firstSource?.document_title || "skewed_fax.pdf";
      const pageNum = firstSource?.page_number || 1;
      const clean = docTitle.toLowerCase().replace(/\.pdf$/i, "").trim();
      let stem = "skewed_fax";
      if (clean.includes("clean")) stem = "clean_fax";
      else if (clean.includes("faint")) stem = "faint_fax_2page";
      else if (clean.includes("intake")) stem = "patient_intake_form";
      else if (clean.includes("skewed")) stem = "skewed_fax";

      return (
        <div>
          {statusLinesHeader}
          <div style={{ marginBottom: "var(--spacing-2)" }}>
            <span
              style={{
                display: "inline-block",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "var(--color-lavender)",
                color: "var(--color-navy)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Session Document Extraction
            </span>
          </div>

          <div
            data-testid="extraction-status-line"
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-navy)",
              fontWeight: 600,
              marginBottom: "var(--spacing-3)",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <span>› Page {pageNum} is a scanned image, reading it now...</span>
          </div>

          <div
            style={{
              lineHeight: 1.5,
              marginBottom: "var(--spacing-3)",
              fontSize: "var(--font-size-sm)",
            }}
          >
            {resp.answer}
          </div>

          {/* Side-by-Side View: Page Image beside Field Table */}
          <div
            data-testid="extraction-split-view"
            style={{
              display: "grid",
              gridTemplateColumns: "220px 1fr",
              gap: "var(--spacing-4)",
              alignItems: "start",
              margin: "var(--spacing-3) 0",
            }}
          >
            {/* Left: Page Preview */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "var(--spacing-2)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "var(--color-navy)",
                }}
              >
                <span>Document Page {pageNum}</span>
                <span style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>Click to zoom</span>
              </div>
              <img
                data-testid="extraction-page-image"
                src={`/demo/pages/${stem}-p${pageNum}.png`}
                alt={`${docTitle} Page ${pageNum} scanned fax preview`}
                onClick={() => handleOpenSource(docTitle, pageNum)}
                style={{
                  width: "100%",
                  height: "auto",
                  maxHeight: "360px",
                  objectFit: "contain",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  boxShadow: "var(--shadow-sm)",
                  cursor: "pointer",
                  backgroundColor: "var(--color-white)",
                }}
                title={`Click to open ${docTitle} at page ${pageNum} in source viewer`}
              />
            </div>

            {/* Right: Structured Field Table */}
            <div>
              <ExtractionTable fields={fields} onOpenSource={handleOpenSource} />
            </div>
          </div>
        </div>
      );
    }

    // 1. General Task Draft (Ungrounded clerical draft)
    if (isGeneral) {
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
                border: "1px solid var(--color-border)",
                color: "var(--color-text-muted)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              General Task Draft — Not grounded in client documents
            </span>
          </div>
          <div style={{ lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{resp.answer}</div>
        </div>
      );
    }

    // 2. Standard Answer (Text, Table, or Visual)
    if (status === "answered") {
      return (
        <div>
          {statusLinesHeader}
          {isVisual && (
            <div style={{ marginBottom: "var(--spacing-2)" }}>
              <span
                style={{
                  display: "inline-block",
                  fontSize: "var(--font-size-xs)",
                  fontWeight: 600,
                  backgroundColor: "var(--color-lavender)",
                  color: "var(--color-navy)",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                Visual Reading
              </span>
            </div>
          )}

          {isTable && (
            <div style={{ marginBottom: "var(--spacing-2)" }}>
              <span
                style={{
                  display: "inline-block",
                  fontSize: "var(--font-size-xs)",
                  fontWeight: 600,
                  backgroundColor: "var(--color-light-blue)",
                  color: "var(--color-navy)",
                  padding: "2px 8px",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                Table Evidence
              </span>
            </div>
          )}

          <div
            style={{
              lineHeight: 1.5,
              marginBottom: resp.sources && resp.sources.length > 0 ? "var(--spacing-3)" : 0,
              whiteSpace: "pre-wrap",
            }}
          >
            {resp.answer}
          </div>

          {/* Visual Answer Page Real Thumbnail (PART B) */}
          {isVisual && (
            <div
              data-testid="page-thumbnail-placeholder"
              style={{ margin: "var(--spacing-2) 0 var(--spacing-3) 0" }}
            >
              {(() => {
                const firstSrc = resp.sources?.[0];
                const docTitle = firstSrc?.document_title || "skewed_fax.pdf";
                const pageNum = firstSrc?.page_number || 1;
                const clean = docTitle.toLowerCase().replace(/\.pdf$/i, "").trim();
                let stem = "skewed_fax";
                if (clean.includes("prior auth") || clean.includes("prior_auth")) stem = "prior_auth_guide";
                else if (clean.includes("skewed")) stem = "skewed_fax";
                else if (clean.includes("compliance")) stem = "compliance_notice";
                else if (clean.includes("eligibility")) stem = "eligibility_checklist";
                else if (clean.includes("clean_fax") || clean.includes("clean fax")) stem = "clean_fax";
                else if (clean.includes("faint")) stem = "faint_fax_2page";
                else if (clean.includes("protocols")) stem = "clinical_protocols_manual";
                else if (clean.includes("policy") && (clean.includes("v2") || clean.includes("2"))) stem = "front_desk_policy_v2";
                else if (clean.includes("policy") || clean.includes("v1")) stem = "front_desk_policy_v1";
                else if (clean.includes("intake")) stem = "patient_intake_form";
                else stem = clean.replace(/[^a-z0-9_-]/g, "_");

                return (
                  <button
                    type="button"
                    aria-label={`Open preview of ${docTitle} Page ${pageNum}`}
                    onClick={() => handleOpenSource(docTitle, pageNum)}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "var(--spacing-3)",
                      padding: "var(--spacing-2) var(--spacing-3)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-md)",
                      backgroundColor: "var(--color-bg-subtle)",
                      cursor: "pointer",
                      textAlign: "left",
                    }}
                  >
                    <img
                      data-testid="real-page-thumbnail"
                      src={`/demo/pages/${stem}-p${pageNum}-thumb.png`}
                      alt={`Thumbnail of ${docTitle} Page ${pageNum}`}
                      onClick={() => handleOpenSource(docTitle, pageNum)}
                      style={{
                        width: "48px",
                        height: "auto",
                        maxHeight: "64px",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--color-border)",
                        boxShadow: "var(--shadow-sm)",
                        display: "block",
                      }}
                    />
                    <div>
                      <div
                        style={{
                          fontSize: "var(--font-size-xs)",
                          fontWeight: 600,
                          color: "var(--color-navy)",
                        }}
                      >
                        Page Image Preview
                      </div>
                      <div
                        style={{
                          fontSize: "11px",
                          color: "var(--color-text-muted)",
                        }}
                      >
                        Page {pageNum} (Click to open source preview)
                      </div>
                    </div>
                  </button>
                );
              })()}
            </div>
          )}

          {/* Source Chips */}
          {resp.sources && resp.sources.length > 0 && (
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "var(--spacing-2)",
                marginTop: "var(--spacing-2)",
              }}
            >
              {resp.sources.map((src: SourceCitation, idx: number) => (
                <button
                  key={`${src.document_title}-${src.page_number || 0}-${idx}`}
                  type="button"
                  data-testid={`source-chip-${src.document_title}`}
                  onClick={() => handleOpenSource(src.document_title, src.page_number || 1)}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: "var(--spacing-1)",
                    fontSize: "var(--font-size-xs)",
                    backgroundColor: "var(--color-bg-subtle)",
                    border: "1px solid var(--color-border)",
                    padding: "4px 8px",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--color-text-main)",
                    cursor: "pointer",
                  }}
                >
                  <span
                    style={{ fontWeight: 600 }}
                    onClick={() => handleOpenSource(src.document_title, src.page_number || 1)}
                  >
                    {src.document_title}
                  </span>
                  {src.version && <span style={{ opacity: 0.7 }}>({src.version})</span>}
                  {src.sheet_name && <span>• Sheet: {src.sheet_name}</span>}
                  {src.row_number !== undefined && src.row_number !== null && (
                    <span>• Row {src.row_number}</span>
                  )}
                  {(!src.sheet_name && !src.row_number && src.page_number !== undefined) && (
                    <span>• Page {src.page_number}</span>
                  )}
                  {src.language && (
                    <span
                      style={{
                        fontStyle: "italic",
                        opacity: 0.85,
                        color: "var(--color-navy)",
                        backgroundColor: "var(--color-light-blue)",
                        padding: "1px 4px",
                        borderRadius: "2px",
                        fontSize: "11px",
                      }}
                    >
                      Source ({src.language})
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      );
    }

    // 3. Refusal
    if (status === "refusal") {
      return (
        <div>
          {statusLinesHeader}
          <div style={{ marginBottom: "var(--spacing-2)" }}>
            <span
              style={{
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "var(--color-status-error-bg)",
                color: "var(--color-status-error-text)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Refusal
            </span>
          </div>
          <div style={{ lineHeight: 1.5 }}>{resp.answer}</div>
        </div>
      );
    }

    // 4. Conflicting Evidence
    if (status === "conflicting") {
      const conflictList = resp.conflict_sources || resp.sources || [];
      return (
        <div>
          {statusLinesHeader}
          <div style={{ marginBottom: "var(--spacing-2)", display: "flex", alignItems: "center", gap: "var(--spacing-2)" }}>
            <span
              style={{
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "#FFF3E0",
                color: "#E65100",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Two documents disagree:
            </span>
            <span
              style={{
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "#FFF3E0",
                color: "#E65100",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Conflicting Sources
            </span>
          </div>
          <div style={{ lineHeight: 1.5, marginBottom: "var(--spacing-3)" }}>{resp.answer}</div>
          {conflictList.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--spacing-2)" }}>
              {conflictList.map((src: SourceCitation, idx: number) => (
                <button
                  key={`conflict-${idx}`}
                  type="button"
                  data-testid={`source-chip-${src.document_title}`}
                  onClick={() => handleOpenSource(src.document_title, src.page_number || 1)}
                  style={{
                    fontSize: "var(--font-size-xs)",
                    backgroundColor: "#FFF8E1",
                    border: "1px solid #FFE082",
                    padding: "6px 10px",
                    borderRadius: "var(--radius-sm)",
                    color: "#5D4037",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <strong onClick={() => handleOpenSource(src.document_title, src.page_number || 1)}>{src.document_title}</strong> ({src.version}) — Page {src.page_number}
                </button>
              ))}
            </div>
          )}
        </div>
      );
    }

    // 5. Safe Failure with Public Error Reference
    if (status === "safe_failure") {
      const refCode = resp.public_error_ref;
      return (
        <div>
          <div style={{ marginBottom: "var(--spacing-2)" }}>
            <span
              style={{
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "var(--color-status-error-bg)",
                color: "var(--color-status-error-text)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              System Error
            </span>
          </div>
          <div style={{ lineHeight: 1.5 }}>
            {resp.answer}
          </div>
          {refCode && !resp.answer.includes(refCode) && (
            <div
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-muted)",
                fontFamily: "monospace",
                marginTop: "var(--spacing-2)",
              }}
            >
              Reference: {refCode}
            </div>
          )}
        </div>
      );
    }

    // 6. Clarify with clickable topic chips
    if (status === "clarify") {
      const topics = resp.clarify_topics || [];
      return (
        <div>
          <div style={{ marginBottom: "var(--spacing-2)" }}>
            <span
              style={{
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                backgroundColor: "var(--color-light-blue)",
                color: "var(--color-navy)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              Clarification Needed
            </span>
          </div>
          <div style={{ lineHeight: 1.5, marginBottom: topics.length > 0 ? "var(--spacing-3)" : 0 }}>
            {resp.answer}
          </div>
          {topics.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--spacing-2)" }}>
              {topics.map((topic, idx) => (
                <button
                  key={`clarify-chip-${idx}`}
                  type="button"
                  onClick={() => executeQuestion(topic)}
                  style={{
                    backgroundColor: "var(--color-bg-subtle)",
                    color: "var(--color-primary-blue)",
                    border: "1px solid var(--color-primary-blue)",
                    borderRadius: "var(--radius-md)",
                    padding: "4px 10px",
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 600,
                    cursor: "pointer",
                    transition: "all 0.15s ease",
                  }}
                >
                  {topic}
                </button>
              ))}
            </div>
          )}
        </div>
      );
    }

    // 7. Unknown response type fallback
    return (
      <div style={{ color: "var(--color-text-muted)", fontSize: "var(--font-size-sm)" }}>
        Received an unexpected response format. Please try again.
      </div>
    );
  }

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "var(--color-bg-page)",
        overflow: "hidden",
      }}
    >
      <TopBar assistantId={assistantId} userEmail={user?.email || null} />

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Left Sidebar: Conversations List */}
        <aside
          style={{
            width: "300px",
            borderRight: "1px solid var(--color-border)",
            backgroundColor: "var(--color-bg-card)",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div
            style={{
              padding: "var(--spacing-3) var(--spacing-4)",
              borderBottom: "1px solid var(--color-border)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-2)" }}>
              <span
                style={{
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 700,
                  color: "var(--color-navy)",
                }}
              >
                Conversations
              </span>
              <DataSourceChip mode={historyDataSource} />
            </div>
            <button
              type="button"
              onClick={handleNewConversation}
              style={{
                backgroundColor: "var(--color-bg-subtle)",
                color: "var(--color-primary-blue)",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                padding: "4px 8px",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                cursor: "pointer",
                transition: "background-color 0.15s ease",
              }}
            >
              + New Conversation
            </button>
          </div>

          <div style={{ flex: 1, overflowY: "auto", padding: "var(--spacing-2)" }}>
            {demoConversations.map((conv) => {
              const isSelected = selectedConvId === conv.id;
              return (
                <div
                  key={conv.id}
                  onClick={() => handleSelectConversation(conv)}
                  style={{
                    padding: "var(--spacing-3)",
                    borderRadius: "var(--radius-md)",
                    marginBottom: "var(--spacing-2)",
                    cursor: "pointer",
                    backgroundColor: isSelected
                      ? "var(--color-bg-subtle)"
                      : "transparent",
                    border: isSelected
                      ? "1px solid var(--color-primary-blue)"
                      : "1px solid transparent",
                    transition: "all 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    if (!isSelected) {
                      e.currentTarget.style.backgroundColor = "var(--color-bg-subtle)";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isSelected) {
                      e.currentTarget.style.backgroundColor = "transparent";
                    }
                  }}
                >
                  <div
                    style={{
                      fontSize: "var(--font-size-sm)",
                      fontWeight: 600,
                      color: "var(--color-navy)",
                      marginBottom: "4px",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {conv.title}
                  </div>
                  <div
                    style={{
                      fontSize: "var(--font-size-xs)",
                      color: "var(--color-text-muted)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {conv.last_message_preview}
                  </div>
                </div>
              );
            })}
          </div>
        </aside>

        {/* Middle Main: Chat Interface */}
        <main
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            backgroundColor: "var(--color-bg-page)",
          }}
        >
          {/* Top Chat Bar Header */}
          <div
            style={{
              padding: "var(--spacing-3) var(--spacing-4)",
              borderBottom: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-card)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div>
              <span
                style={{
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 600,
                  color: "var(--color-navy)",
                }}
              >
                {selectedConvId
                  ? demoConversations.find((c) => c.id === selectedConvId)?.title ||
                    "Active Consultation"
                  : "New Consultation"}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-2)" }}>
              <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
                Query Engine:
              </span>
              <DataSourceChip mode={askDataSource} />
            </div>
          </div>

          {/* Messages Container */}
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              padding: "var(--spacing-4)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--spacing-4)",
              maxWidth: "860px",
              width: "100%",
              margin: "0 auto",
            }}
          >
            {messages.length === 0 && (
              <div
                style={{
                  margin: "auto",
                  textAlign: "center",
                  maxWidth: "680px",
                  padding: "var(--spacing-6)",
                  backgroundColor: "var(--color-bg-card)",
                  borderRadius: "var(--radius-lg)",
                  border: "1px solid var(--color-border)",
                }}
              >
                <h2
                  style={{
                    fontSize: "var(--font-size-lg)",
                    fontWeight: 600,
                    color: "var(--color-navy)",
                    marginBottom: "var(--spacing-2)",
                  }}
                >
                  Clinical & Operations Knowledge Assistant
                </h2>
                <p
                  style={{
                    fontSize: "var(--font-size-sm)",
                    color: "var(--color-text-muted)",
                    lineHeight: 1.5,
                    marginBottom: !isLiveAssistant ? "var(--spacing-4)" : 0,
                  }}
                >
                  Ask any question regarding prior authorization guidelines, clinical protocols,
                  office hours, or payer requirements. All answers are strictly grounded in validated
                  documents.
                </p>

                {!isLiveAssistant && (
                  <div
                    data-testid="demo-suggested-questions"
                    style={{ textAlign: "left", marginTop: "var(--spacing-4)" }}
                  >
                    <div
                      style={{
                        fontSize: "var(--font-size-xs)",
                        fontWeight: 700,
                        color: "var(--color-navy)",
                        marginBottom: "var(--spacing-2)",
                        textTransform: "uppercase",
                        letterSpacing: "0.5px",
                      }}
                    >
                      Demo Scenarios (Golden Set Questions):
                    </div>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr 1fr",
                        gap: "var(--spacing-2)",
                      }}
                    >
                      {DEMO_SUGGESTED_QUESTIONS.map((sq) => (
                        <button
                          key={sq.id}
                          type="button"
                          onClick={() => executeQuestion(sq.question)}
                          style={{
                            display: "flex",
                            flexDirection: "column",
                            alignItems: "flex-start",
                            padding: "8px 10px",
                            backgroundColor: "var(--color-bg-subtle)",
                            border: "1px solid var(--color-border)",
                            borderRadius: "var(--radius-md)",
                            cursor: "pointer",
                            textAlign: "left",
                            transition: "border-color 0.15s ease",
                          }}
                        >
                          <span
                            style={{
                              fontSize: "10px",
                              fontWeight: 700,
                              color: "var(--color-primary-blue)",
                              marginBottom: "2px",
                            }}
                          >
                            [{sq.id}] {sq.typeLabel}
                          </span>
                          <span
                            style={{
                              fontSize: "var(--font-size-xs)",
                              color: "var(--color-text-main)",
                              lineHeight: 1.3,
                            }}
                          >
                            {sq.question}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {messages.map((msg) => {
              const isUser = msg.role === "user";
              return (
                <div
                  key={msg.id}
                  style={{
                    display: "flex",
                    justifyContent: isUser ? "flex-end" : "flex-start",
                  }}
                >
                  <div
                    style={{
                      maxWidth: "78%",
                      padding: "var(--spacing-3) var(--spacing-4)",
                      borderRadius: "var(--radius-lg)",
                      backgroundColor: isUser
                        ? "var(--color-primary-blue)"
                        : "var(--color-bg-card)",
                      color: isUser ? "var(--color-white)" : "var(--color-text-main)",
                      border: isUser ? "none" : "1px solid var(--color-border)",
                      boxShadow: "var(--shadow-sm)",
                      fontSize: "var(--font-size-sm)",
                    }}
                  >
                    {isUser ? msg.content : renderAssistantContent(msg)}
                  </div>
                </div>
              );
            })}

            {isSubmitting && (
              <div style={{ display: "flex", justifyContent: "flex-start" }}>
                <div
                  data-testid="chat-loading-indicator"
                  style={{
                    backgroundColor: "var(--color-bg-card)",
                    border: "1px solid var(--color-border)",
                    padding: "var(--spacing-3) var(--spacing-4)",
                    borderRadius: "var(--radius-lg)",
                    fontSize: "var(--font-size-sm)",
                    color: "var(--color-text-muted)",
                    display: "flex",
                    alignItems: "center",
                    gap: "var(--spacing-2)",
                  }}
                >
                  <span
                    style={{
                      display: "inline-block",
                      width: "8px",
                      height: "8px",
                      borderRadius: "50%",
                      backgroundColor: "var(--color-primary-blue)",
                      animation: "pulse 1.2s infinite",
                    }}
                  />
                  {isLiveAssistant ? (
                    <span data-testid="live-loading-indicator">Loading...</span>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                      {demoStatusLines.map((line, idx) => (
                        <span key={`demo-status-${idx}`} style={{ color: "var(--color-navy)", fontWeight: 500 }}>
                          › {line}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input Container */}
          <div
            style={{
              padding: "var(--spacing-3) var(--spacing-4)",
              borderTop: "1px solid var(--color-border)",
              backgroundColor: "var(--color-bg-card)",
              maxWidth: "860px",
              width: "100%",
              margin: "0 auto",
            }}
          >
            {/* Live Assistant Session Upload Route Not Built Notice */}
            {liveAttachWarningOpen && (
              <div
                data-testid="session-upload-live-message"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "8px 12px",
                  backgroundColor: "var(--color-bg-subtle)",
                  border: "1px solid var(--color-navy)",
                  borderRadius: "var(--radius-md)",
                  marginBottom: "var(--spacing-2)",
                  fontSize: "var(--font-size-xs)",
                  color: "var(--color-navy)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-2)" }}>
                  <DataSourceChip mode="demo" />
                  <span>Session upload needs the session document route (Phase 4 Task 4.3, not built yet)</span>
                </div>
                <button
                  type="button"
                  onClick={() => setLiveAttachWarningOpen(false)}
                  style={{ cursor: "pointer", fontWeight: 700, color: "var(--color-navy)", padding: "0 4px" }}
                  title="Dismiss notice"
                >
                  ✕
                </button>
              </div>
            )}

            {/* Demo Golden Fax Picker Popover */}
            {attachPickerOpen && (
              <div
                data-testid="demo-attach-picker"
                style={{
                  padding: "var(--spacing-3)",
                  backgroundColor: "var(--color-bg-card)",
                  border: "1px solid var(--color-navy)",
                  borderRadius: "var(--radius-md)",
                  marginBottom: "var(--spacing-2)",
                  boxShadow: "var(--shadow-md)",
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
                  <span
                    style={{
                      fontSize: "var(--font-size-xs)",
                      fontWeight: 700,
                      color: "var(--color-navy)",
                      textTransform: "uppercase",
                      letterSpacing: "0.5px",
                    }}
                  >
                    Select Golden Fax Document (Session Upload)
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setAttachPickerOpen(false);
                      setArbitraryFileError(null);
                    }}
                    style={{ cursor: "pointer", fontWeight: 700, color: "var(--color-navy)", padding: "0 4px" }}
                    title="Close picker"
                  >
                    ✕
                  </button>
                </div>
                <p
                  style={{
                    fontSize: "11px",
                    color: "var(--color-text-muted)",
                    marginBottom: "var(--spacing-2)",
                  }}
                >
                  Document stays in this conversation only and is never added to the knowledge base (Invariant 7).
                </p>

                {/* Golden Fax Options List */}
                <div
                  data-testid="demo-golden-fax-list"
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "6px",
                    marginBottom: "var(--spacing-3)",
                  }}
                >
                  {GOLDEN_FAX_DOCUMENTS.map((doc) => (
                    <button
                      key={doc.filename}
                      type="button"
                      data-testid={`picker-doc-${doc.filename}`}
                      onClick={() => {
                        setAttachedDoc(doc);
                        setAttachPickerOpen(false);
                        setArbitraryFileError(null);
                      }}
                      style={{
                        padding: "8px 10px",
                        textAlign: "left",
                        backgroundColor: "var(--color-bg-subtle)",
                        border: "1px solid var(--color-border)",
                        borderRadius: "var(--radius-sm)",
                        cursor: "pointer",
                        transition: "border-color 0.15s ease",
                      }}
                    >
                      <div
                        style={{
                          fontSize: "var(--font-size-xs)",
                          fontWeight: 700,
                          color: "var(--color-primary-blue)",
                          marginBottom: "2px",
                        }}
                      >
                        {doc.filename}
                      </div>
                      <div
                        style={{
                          fontSize: "10px",
                          color: "var(--color-text-muted)",
                          lineHeight: 1.2,
                        }}
                      >
                        {doc.title} ({doc.page_count} page{doc.page_count > 1 ? "s" : ""})
                      </div>
                    </button>
                  ))}
                </div>

                {/* Arbitrary local file selector */}
                <div style={{ borderTop: "1px solid var(--color-border)", paddingTop: "8px" }}>
                  <label
                    style={{
                      display: "block",
                      fontSize: "11px",
                      fontWeight: 600,
                      color: "var(--color-navy)",
                      marginBottom: "4px",
                    }}
                  >
                    Or select local file:
                  </label>
                  <input
                    type="file"
                    data-testid="arbitrary-file-input"
                    onChange={(e) => {
                      const files = e.target.files;
                      if (files && files.length > 0) {
                        const fileName = files[0].name;
                        const match = GOLDEN_FAX_DOCUMENTS.find((d) => d.filename === fileName);
                        if (match) {
                          setAttachedDoc(match);
                          setAttachPickerOpen(false);
                          setArbitraryFileError(null);
                        } else {
                          setArbitraryFileError("Demo only reads sample fax documents from the golden set");
                        }
                      }
                    }}
                    style={{ fontSize: "11px" }}
                  />
                  {arbitraryFileError && (
                    <div
                      data-testid="demo-arbitrary-file-error"
                      style={{
                        marginTop: "6px",
                        color: "var(--color-status-error-text)",
                        backgroundColor: "var(--color-status-error-bg)",
                        padding: "6px 10px",
                        borderRadius: "var(--radius-sm)",
                        fontSize: "11px",
                        fontWeight: 600,
                      }}
                    >
                      {arbitraryFileError}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Attached Session Document Indicator Banner (Invariant 7) */}
            {attachedDoc && (
              <div
                data-testid="session-document-indicator"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "8px 12px",
                  backgroundColor: "var(--color-lavender)",
                  border: "1px solid var(--color-navy)",
                  borderRadius: "var(--radius-md)",
                  marginBottom: "var(--spacing-2)",
                  fontSize: "var(--font-size-xs)",
                  color: "var(--color-navy)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <span style={{ fontSize: "14px" }}>🔒</span>
                  <span>
                    Session document: <strong>{attachedDoc.filename}</strong> — stays in this conversation only and is never added to the knowledge base (Invariant 7)
                  </span>
                </div>
                <button
                  type="button"
                  data-testid="remove-attached-doc"
                  onClick={() => setAttachedDoc(null)}
                  style={{
                    cursor: "pointer",
                    color: "var(--color-navy)",
                    fontWeight: 700,
                    padding: "0 6px",
                    fontSize: "14px",
                  }}
                  title="Remove attached session document"
                >
                  ✕
                </button>
              </div>
            )}

            {/* Input Form */}
            <form
              onSubmit={handleSubmit}
              style={{
                display: "flex",
                gap: "var(--spacing-2)",
              }}
            >
              <button
                type="button"
                data-testid="chat-attach-button"
                onClick={() => {
                  if (isLiveAssistant) {
                    setLiveAttachWarningOpen((prev) => !prev);
                  } else {
                    setAttachPickerOpen((prev) => !prev);
                    setArbitraryFileError(null);
                  }
                }}
                style={{
                  padding: "10px 14px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-subtle)",
                  color: "var(--color-navy)",
                  fontWeight: 600,
                  fontSize: "var(--font-size-sm)",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  cursor: "pointer",
                  transition: "background-color 0.15s ease",
                }}
                title="Attach document for session extraction (stays in this conversation only)"
              >
                <span>📎</span>
                <span>Attach</span>
              </button>

              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                placeholder={
                  attachedDoc
                    ? `Attached: ${attachedDoc.filename}. Ask for all fields or press Send...`
                    : "Ask a question about clinic documents..."
                }
                disabled={isSubmitting}
                style={{
                  flex: 1,
                  padding: "10px 14px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  fontSize: "var(--font-size-sm)",
                  outline: "none",
                  fontFamily: "inherit",
                }}
              />
              <button
                type="submit"
                disabled={(!inputValue.trim() && !attachedDoc) || isSubmitting}
                style={{
                  padding: "10px 20px",
                  borderRadius: "var(--radius-md)",
                  backgroundColor:
                    (!inputValue.trim() && !attachedDoc) || isSubmitting
                      ? "var(--color-border)"
                      : "var(--color-primary-blue)",
                  color: "var(--color-white)",
                  border: "none",
                  fontWeight: 600,
                  fontSize: "var(--font-size-sm)",
                  cursor:
                    (!inputValue.trim() && !attachedDoc) || isSubmitting
                      ? "not-allowed"
                      : "pointer",
                  transition: "background-color 0.15s ease",
                }}
              >
                {isSubmitting ? "Sending..." : "Send"}
              </button>
            </form>
          </div>
        </main>
      </div>

      {/* Source Document Viewer Panel (PART E) */}
      <SourcePanel
        isOpen={sourcePanelOpen}
        onClose={() => setSourcePanelOpen(false)}
        documentTitle={activeSourceDoc}
        initialPage={activeSourcePage}
        isLiveAssistant={isLiveAssistant}
      />
    </div>
  );
}
