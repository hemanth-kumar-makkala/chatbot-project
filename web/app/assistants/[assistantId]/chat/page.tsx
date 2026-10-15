"use client";

import { useEffect, useState, useRef, use } from "react";
import { useRouter } from "next/navigation";
import { TopBar } from "@/components/TopBar";
import { DataSourceChip } from "@/components/DataSourceChip";
import { getFeatureDataSource } from "@/lib/connections";
import {
  getDemoConversations,
  getDemoAssistantAnswer,
  type DemoConversation,
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
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const query = inputValue.trim();
    if (!query || isSubmitting) return;

    const userMsgId = `user-${Date.now()}`;
    const userMsg: ChatMessage = {
      id: userMsgId,
      role: "user",
      content: query,
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputValue("");
    setIsSubmitting(true);

    try {
      if (!isLiveAssistant || askDataSource === "demo") {
        // Demo assistant: strictly uses demo fixtures, never calls live backend
        const demoRes = getDemoAssistantAnswer(assistantId, query, conversationId);
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
        const res = await askQuestion(assistantId, query, conversationId);
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

    // 1. Standard Answer
    if (status === "answered") {
      return (
        <div>
          <div
            style={{
              lineHeight: 1.5,
              marginBottom: resp.sources && resp.sources.length > 0 ? "var(--spacing-3)" : 0,
            }}
          >
            {resp.answer}
          </div>

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
                <div
                  key={`${src.document_title}-${src.page_number}-${idx}`}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "var(--spacing-1)",
                    fontSize: "var(--font-size-xs)",
                    backgroundColor: "var(--color-bg-subtle)",
                    border: "1px solid var(--color-border)",
                    padding: "4px 8px",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--color-text-main)",
                  }}
                >
                  <span style={{ fontWeight: 600 }}>{src.document_title}</span>
                  <span style={{ opacity: 0.7 }}>({src.version})</span>
                  <span>• Page {src.page_number}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      );
    }

    // 2. Refusal
    if (status === "refusal") {
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
              Refusal
            </span>
          </div>
          <div style={{ lineHeight: 1.5 }}>{resp.answer}</div>
        </div>
      );
    }

    // 3. Conflicting Evidence
    if (status === "conflicting") {
      const conflictList = resp.conflict_sources || resp.sources || [];
      return (
        <div>
          <div style={{ marginBottom: "var(--spacing-2)" }}>
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
                <div
                  key={`conflict-${idx}`}
                  style={{
                    fontSize: "var(--font-size-xs)",
                    backgroundColor: "#FFF8E1",
                    border: "1px solid #FFE082",
                    padding: "6px 10px",
                    borderRadius: "var(--radius-sm)",
                    color: "#5D4037",
                  }}
                >
                  <strong>{src.document_title}</strong> ({src.version}) — Page {src.page_number}
                </div>
              ))}
            </div>
          )}
        </div>
      );
    }

    // 4. Safe Failure with Public Error Reference
    if (status === "safe_failure") {
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
          <div style={{ lineHeight: 1.5, marginBottom: "var(--spacing-2)" }}>{resp.answer}</div>
          {resp.public_error_ref && (
            <div
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-muted)",
                fontFamily: "monospace",
              }}
            >
              {`Reference: ${resp.public_error_ref}`}
            </div>
          )}
        </div>
      );
    }

    // 5. Clarify
    if (status === "clarify") {
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
          <div style={{ lineHeight: 1.5 }}>{resp.answer}</div>
        </div>
      );
    }

    // 6. Unknown response type fallback
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
                  maxWidth: "480px",
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
                  }}
                >
                  Ask any question regarding prior authorization guidelines, clinical protocols,
                  office hours, or payer requirements. All answers are strictly grounded in validated
                  documents.
                </p>
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
                  <span>Retrieving verified clinic documents...</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input Box */}
          <form
            onSubmit={handleSubmit}
            style={{
              padding: "var(--spacing-3) var(--spacing-4)",
              borderTop: "1px solid var(--color-border)",
              display: "flex",
              gap: "var(--spacing-2)",
              backgroundColor: "var(--color-bg-card)",
              maxWidth: "860px",
              width: "100%",
              margin: "0 auto",
            }}
          >
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Ask a question about clinic documents..."
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
              disabled={!inputValue.trim() || isSubmitting}
              style={{
                padding: "10px 20px",
                borderRadius: "var(--radius-md)",
                backgroundColor:
                  !inputValue.trim() || isSubmitting
                    ? "var(--color-border)"
                    : "var(--color-primary-blue)",
                color: "var(--color-white)",
                border: "none",
                fontWeight: 600,
                fontSize: "var(--font-size-sm)",
                cursor: !inputValue.trim() || isSubmitting ? "not-allowed" : "pointer",
                transition: "background-color 0.15s ease",
              }}
            >
              {isSubmitting ? "Sending..." : "Send"}
            </button>
          </form>
        </main>
      </div>
    </div>
  );
}
