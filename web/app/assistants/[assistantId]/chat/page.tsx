"use client";

import { useEffect, useState, useRef, use } from "react";
import { useRouter } from "next/navigation";
import { TopBar } from "@/components/TopBar";
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
      routerRef.current.replace(`/assistants/${DEV_DEFAULT_ASSISTANT_ID}/chat`);
    }
  }, [rawAssistantId]);

  const [user, setUser] = useState<User | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);

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

    // 1. Answered with sources
    if (status === "answered" || status === "completed") {
      const isVisual = resp.evidence_type === "visual";
      return (
        <div>
          <div style={{ lineHeight: 1.5, marginBottom: resp.sources && resp.sources.length > 0 ? "var(--spacing-3)" : 0 }}>
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
            <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--spacing-2)", marginTop: "var(--spacing-2)" }}>
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
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        backgroundColor: "var(--color-bg-page)",
      }}
    >
      <TopBar assistantId={assistantId} userEmail={user?.email || null} />

      <main
        className="container"
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          maxWidth: "860px",
          width: "100%",
          margin: "0 auto",
          padding: "var(--spacing-4) var(--spacing-4)",
        }}
      >
        <div
          style={{
            flex: 1,
            backgroundColor: "var(--color-bg-card)",
            borderRadius: "var(--radius-lg)",
            border: "1px solid var(--color-border)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          {/* Header */}
          <div
            style={{
              padding: "var(--spacing-3) var(--spacing-4)",
              borderBottom: "1px solid var(--color-border-subtle)",
              backgroundColor: "var(--color-bg-subtle)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div>
              <h1
                style={{
                  fontSize: "var(--font-size-base)",
                  fontWeight: 600,
                  color: "var(--color-navy)",
                  margin: 0,
                }}
              >
                Clinical Knowledge Chat
              </h1>
              <p
                style={{
                  fontSize: "var(--font-size-xs)",
                  color: "var(--color-text-muted)",
                  margin: "2px 0 0 0",
                }}
              >
                Answers strictly derived from authenticated tenant documents
              </p>
            </div>
          </div>

          {/* Messages list */}
          <div
            style={{
              flex: 1,
              overflowY: "auto",
              padding: "var(--spacing-4)",
              display: "flex",
              flexDirection: "column",
              gap: "var(--spacing-4)",
            }}
          >
            {messages.length === 0 && (
              <div
                style={{
                  margin: "auto",
                  textAlign: "center",
                  color: "var(--color-text-muted)",
                  padding: "var(--spacing-8)",
                }}
              >
                <div style={{ fontSize: "var(--font-size-2xl)", marginBottom: "var(--spacing-2)" }}>💬</div>
                <div style={{ fontWeight: 600, color: "var(--color-navy)" }}>Ask anything about client protocols</div>
                <div style={{ fontSize: "var(--font-size-sm)", marginTop: "4px" }}>
                  e.g., &quot;What are standard clinic hours?&quot; or &quot;What is prior authorization criteria?&quot;
                </div>
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
                      maxWidth: "80%",
                      borderRadius: "var(--radius-lg)",
                      padding: "var(--spacing-3) var(--spacing-4)",
                      fontSize: "var(--font-size-sm)",
                      backgroundColor: isUser ? "var(--color-primary-blue)" : "var(--color-bg-card)",
                      color: isUser ? "var(--color-white)" : "var(--color-text-main)",
                      border: isUser ? "none" : "1px solid var(--color-border)",
                      boxShadow: isUser ? "var(--shadow-sm)" : "var(--shadow-xs)",
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
              padding: "var(--spacing-3)",
              borderTop: "1px solid var(--color-border)",
              display: "flex",
              gap: "var(--spacing-2)",
              backgroundColor: "var(--color-bg-card)",
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
                backgroundColor: !inputValue.trim() || isSubmitting ? "var(--color-border)" : "var(--color-primary-blue)",
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
        </div>
      </main>
    </div>
  );
}
