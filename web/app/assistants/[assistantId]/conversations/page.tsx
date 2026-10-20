"use client";

import { use, useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { getCurrentUser, type User } from "@/lib/api";
import { getDataSourceMode } from "@/lib/connections";
import { getDemoConversations, type DemoConversation } from "@/lib/demo/fixtures";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface ConversationsPageProps {
  params: Promise<{ assistantId: string }>;
}

export default function ConversationsPage({ params }: ConversationsPageProps) {
  const resolvedParams = use(params);
  const assistantId = resolvedParams.assistantId;
  const router = useRouter();

  const [user, setUser] = useState<User | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedConv, setSelectedConv] = useState<DemoConversation | null>(null);

  const mode = getDataSourceMode();
  const isLive = mode === "live";

  // In live mode, backend route GET /v1/assistants/{id}/conversations is not built yet
  // In demo mode, load demo golden set conversations
  const conversations: DemoConversation[] = isLive
    ? []
    : getDemoConversations(assistantId);

  useEffect(() => {
    async function loadAuth() {
      try {
        const res = await getCurrentUser();
        if (res.ok) {
          setUser(res.data);
        }
      } catch {
        // Handled silently
      }
    }
    loadAuth();
  }, []);

  const filteredConversations = conversations.filter((c) =>
    c.title.toLowerCase().includes(searchQuery.toLowerCase().trim())
  );

  function handleNewChat() {
    router.push(`/assistants/${assistantId}`);
  }

  return (
    <AppShell
      assistantId={assistantId}
      assistantName={assistantId === DEV_DEFAULT_ASSISTANT_ID ? "Clinic A Assistant" : "Assistant"}
      workspaceName={user?.tenant_id || "Workspace"}
      userEmail={user?.email || null}
    >
      <div
        style={{
          maxWidth: "1180px",
          margin: "0 auto",
          padding: "var(--spacing-8) var(--spacing-6)",
          height: "calc(100vh - 64px)",
          display: "flex",
          flexDirection: "column",
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
                Conversations
              </h1>
              <ConnectionBadge
                status={isLive ? "not_connected" : "connected"}
                route={`GET /v1/assistants/${assistantId}/conversations`}
                label={isLive ? "Not connected yet" : "Connected"}
              />
            </div>
            <p
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--color-text-secondary)",
              }}
            >
              Audit past staff consultation transcripts, source citations, and user feedback ratings.
            </p>
          </div>

          <button
            type="button"
            onClick={handleNewChat}
            style={{
              backgroundColor: "var(--color-primary)",
              color: "var(--color-text-on-primary)",
              border: "none",
              borderRadius: "var(--radius-md)",
              padding: "8px 16px",
              fontSize: "var(--font-size-sm)",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            + New chat
          </button>
        </div>

        {/* Live Mode Notice */}
        {isLive && (
          <div
            style={{
              padding: "var(--spacing-4)",
              backgroundColor: "var(--color-bg-card)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--color-border)",
              marginBottom: "var(--spacing-6)",
            }}
          >
            <div style={{ marginBottom: "6px" }}>
              <ConnectionBadge
                status="not_connected"
                route={`GET /v1/assistants/${assistantId}/conversations`}
              />
            </div>
            <p
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--color-text-secondary)",
                lineHeight: 1.5,
              }}
            >
              Conversation history requires GET /v1/assistants/{assistantId}/conversations (not built yet). In live mode, transcripts from earlier sessions are not persisted to a backend database yet. Use the <strong>Home</strong> tab to ask live grounded questions.
            </p>
          </div>
        )}

        {/* Two-Pane Layout (List & Transcript) */}
        {!isLive && (
          <div
            style={{
              flex: 1,
              display: "grid",
              gridTemplateColumns: "340px 1fr",
              gap: "var(--spacing-4)",
              minHeight: 0,
            }}
          >
            {/* Left: Conversations Directory */}
            <div
              style={{
                backgroundColor: "var(--color-bg-card)",
                borderRadius: "var(--radius-lg)",
                border: "1px solid var(--color-border)",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  padding: "var(--spacing-3)",
                  borderBottom: "1px solid var(--color-border)",
                  display: "flex",
                  gap: "var(--spacing-2)",
                }}
              >
                <input
                  type="text"
                  placeholder="Search conversations..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  style={{
                    flex: 1,
                    padding: "6px 10px",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    fontSize: "var(--font-size-xs)",
                    backgroundColor: "var(--color-bg-page)",
                    color: "var(--color-text-primary)",
                  }}
                />
                <button
                  type="button"
                  title="Refresh conversations"
                  style={{
                    padding: "6px 10px",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    fontSize: "var(--font-size-xs)",
                    backgroundColor: "var(--color-bg-page)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  ↻
                </button>
              </div>

              <div style={{ flex: 1, overflowY: "auto", padding: "4px" }}>
                {filteredConversations.map((c) => {
                  const isSelected = selectedConv?.id === c.id;
                  return (
                    <div
                      key={c.id}
                      onClick={() => setSelectedConv(c)}
                      style={{
                        padding: "var(--spacing-3)",
                        borderRadius: "var(--radius-md)",
                        backgroundColor: isSelected
                          ? "var(--color-bg-subtle)"
                          : "transparent",
                        cursor: "pointer",
                        marginBottom: "4px",
                        border: isSelected
                          ? "1px solid var(--color-border)"
                          : "1px solid transparent",
                      }}
                    >
                      <div
                        style={{
                          fontSize: "var(--font-size-sm)",
                          fontWeight: isSelected ? 600 : 500,
                          color: "var(--color-text-primary)",
                          marginBottom: "2px",
                        }}
                      >
                        {c.title}
                      </div>
                      <div
                        style={{
                          fontSize: "var(--font-size-xs)",
                          color: "var(--color-text-secondary)",
                          display: "flex",
                          justifyContent: "space-between",
                        }}
                      >
                        <span>{c.messages.length} messages</span>
                        <span>{c.created_at}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Right: Transcript View */}
            <div
              style={{
                backgroundColor: "var(--color-bg-card)",
                borderRadius: "var(--radius-lg)",
                border: "1px solid var(--color-border)",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                padding: "var(--spacing-4)",
              }}
            >
              {selectedConv ? (
                <div style={{ flex: 1, overflowY: "auto" }}>
                  <h2
                    style={{
                      fontSize: "var(--font-size-base)",
                      fontWeight: 600,
                      color: "var(--color-text-primary)",
                      marginBottom: "var(--spacing-4)",
                      paddingBottom: "var(--spacing-2)",
                      borderBottom: "1px solid var(--color-border)",
                    }}
                  >
                    Transcript: {selectedConv.title}
                  </h2>

                  <div style={{ display: "flex", flexDirection: "column", gap: "var(--spacing-3)" }}>
                    {selectedConv.messages.map((m) => (
                      <div
                        key={m.id}
                        style={{
                          padding: "var(--spacing-3)",
                          borderRadius: "var(--radius-md)",
                          backgroundColor:
                            m.role === "user"
                              ? "var(--color-bg-subtle)"
                              : "var(--color-bg-page)",
                          border: "1px solid var(--color-border)",
                        }}
                      >
                        <div
                          style={{
                            fontSize: "var(--font-size-xs)",
                            fontWeight: 600,
                            textTransform: "uppercase",
                            color: "var(--color-text-secondary)",
                            marginBottom: "4px",
                          }}
                        >
                          {m.role}
                        </div>
                        <div
                          style={{
                            fontSize: "var(--font-size-sm)",
                            color: "var(--color-text-primary)",
                            lineHeight: 1.5,
                          }}
                        >
                          {m.content}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div
                  style={{
                    flex: 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "var(--color-text-secondary)",
                    fontSize: "var(--font-size-sm)",
                  }}
                >
                  Select a conversation from the left to inspect its transcript.
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
