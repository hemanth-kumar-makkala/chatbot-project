"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser, type User } from "@/lib/api";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { getDataSourceMode } from "@/lib/connections";
import { getDemoAssistants, type DemoAssistant } from "@/lib/demo/fixtures";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface AssistantCardData {
  id: string;
  name: string;
  description: string;
  document_count?: number;
  last_updated?: string;
}

export default function AssistantsPage() {
  const router = useRouter();
  const routerRef = useRef(router);
  routerRef.current = router;

  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [newModalOpen, setNewModalOpen] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [selectedAssistant, setSelectedAssistant] = useState<AssistantCardData | null>(null);
  const [activeMenuId, setActiveMenuId] = useState<string | null>(null);

  // Form states for New Assistant dialog
  const [newAssistantName, setNewAssistantName] = useState("");
  const [newAssistantDesc, setNewAssistantDesc] = useState("");

  const mode = getDataSourceMode();
  const isLive = mode === "live";

  // In live mode, only the real seeded assistant is available
  // Demo assistants are never shown or queried in live mode
  const assistants: AssistantCardData[] = isLive
    ? [
        {
          id: DEV_DEFAULT_ASSISTANT_ID,
          name: "Clinic A Assistant",
          description:
            "Authorized clinical guidelines, prior authorization criteria, and formulary reference assistant.",
          document_count: 15,
          last_updated: "2 days ago",
        },
      ]
    : getDemoAssistants().map((d: DemoAssistant) => ({
        id: d.id,
        name: d.name,
        description: d.description,
        document_count: d.document_count,
        last_updated: "Recently",
      }));

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
      } finally {
        setLoading(false);
      }
    }
    loadAuth();
  }, []);

  function handleOpenAssistant(id: string) {
    router.push(`/assistants/${id}`);
  }

  function handleOpenSettings(id: string) {
    router.push(`/assistants/${id}/settings`);
  }

  function handleDeleteClick(asst: AssistantCardData) {
    setSelectedAssistant(asst);
    setActiveMenuId(null);
    setDeleteModalOpen(true);
  }

  if (loading) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          backgroundColor: "var(--color-bg-page)",
          color: "var(--color-text-secondary)",
        }}
      >
        Loading authorized assistants...
      </div>
    );
  }

  return (
    <AppShell
      userEmail={user?.email || null}
      workspaceName={user?.tenant_id || "Workspace"}
    >
      <div
        style={{
          maxWidth: "1080px",
          margin: "0 auto",
          padding: "var(--spacing-8) var(--spacing-6)",
        }}
      >
        {/* Page Header */}
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
                Assistants
              </h1>
              <ConnectionBadge
                status={isLive ? "not_connected" : "connected"}
                route="GET /v1/assistants"
                label={isLive ? "Not connected yet" : "Connected"}
              />
            </div>
            <p
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--color-text-secondary)",
              }}
            >
              Select an authorized client knowledge assistant to consult guidelines, search documents, or inspect transcripts.
            </p>
          </div>

          <button
            type="button"
            onClick={() => setNewModalOpen(true)}
            style={{
              backgroundColor: "var(--color-primary)",
              color: "var(--color-text-on-primary)",
              border: "none",
              borderRadius: "var(--radius-md)",
              padding: "8px 16px",
              fontSize: "var(--font-size-sm)",
              fontWeight: 600,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <span>+</span> New assistant
          </button>
        </div>

        {/* Assistants Cards Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: "var(--spacing-4)",
          }}
        >
          {assistants.map((asst) => (
            <div
              key={asst.id}
              style={{
                backgroundColor: "var(--color-bg-card)",
                borderRadius: "var(--radius-lg)",
                border: "1px solid var(--color-border)",
                padding: "var(--spacing-5)",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                position: "relative",
                transition: "border-color 0.15s ease",
              }}
            >
              <div>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    marginBottom: "var(--spacing-2)",
                  }}
                >
                  <h2
                    onClick={() => handleOpenAssistant(asst.id)}
                    style={{
                      fontSize: "var(--font-size-lg)",
                      fontWeight: 600,
                      color: "var(--color-text-primary)",
                      cursor: "pointer",
                      lineHeight: 1.3,
                    }}
                  >
                    {asst.name}
                  </h2>

                  {/* Card Action Menu Button */}
                  <div style={{ position: "relative" }}>
                    <button
                      type="button"
                      aria-label="Assistant options"
                      onClick={() =>
                        setActiveMenuId(activeMenuId === asst.id ? null : asst.id)
                      }
                      style={{
                        padding: "2px 8px",
                        fontSize: "var(--font-size-sm)",
                        color: "var(--color-text-secondary)",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--color-border)",
                        backgroundColor: "var(--color-bg-page)",
                      }}
                    >
                      •••
                    </button>

                    {activeMenuId === asst.id && (
                      <div
                        style={{
                          position: "absolute",
                          right: 0,
                          top: "100%",
                          marginTop: "4px",
                          backgroundColor: "var(--color-bg-page)",
                          border: "1px solid var(--color-border)",
                          borderRadius: "var(--radius-md)",
                          boxShadow: "var(--shadow-md)",
                          zIndex: 20,
                          minWidth: "120px",
                          padding: "4px",
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => handleOpenAssistant(asst.id)}
                          style={{
                            width: "100%",
                            textAlign: "left",
                            padding: "6px 10px",
                            fontSize: "var(--font-size-xs)",
                            color: "var(--color-text-primary)",
                            borderRadius: "var(--radius-sm)",
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.backgroundColor = "var(--color-bg-subtle)";
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = "transparent";
                          }}
                        >
                          Open
                        </button>
                        <button
                          type="button"
                          onClick={() => handleOpenSettings(asst.id)}
                          style={{
                            width: "100%",
                            textAlign: "left",
                            padding: "6px 10px",
                            fontSize: "var(--font-size-xs)",
                            color: "var(--color-text-primary)",
                            borderRadius: "var(--radius-sm)",
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.backgroundColor = "var(--color-bg-subtle)";
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = "transparent";
                          }}
                        >
                          Settings
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteClick(asst)}
                          style={{
                            width: "100%",
                            textAlign: "left",
                            padding: "6px 10px",
                            fontSize: "var(--font-size-xs)",
                            color: "var(--color-danger)",
                            borderRadius: "var(--radius-sm)",
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.backgroundColor = "var(--color-danger-soft)";
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = "transparent";
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                <p
                  style={{
                    fontSize: "var(--font-size-sm)",
                    color: "var(--color-text-secondary)",
                    lineHeight: 1.5,
                    marginBottom: "var(--spacing-3)",
                  }}
                >
                  {asst.description}
                </p>

                <div
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                    marginBottom: "var(--spacing-3)",
                  }}
                >
                  <code>ID: {asst.id}</code>
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  paddingTop: "var(--spacing-3)",
                  borderTop: "1px solid var(--color-border)",
                }}
              >
                <span
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                    backgroundColor: "var(--color-bg-subtle)",
                    padding: "2px 8px",
                    borderRadius: "var(--radius-sm)",
                  }}
                >
                  {asst.document_count ?? 0} documents
                </span>
                <button
                  type="button"
                  data-testid={`assistant-select-${asst.id}`}
                  onClick={() => handleOpenAssistant(asst.id)}
                  style={{
                    backgroundColor: "var(--color-primary)",
                    color: "var(--color-text-on-primary)",
                    border: "none",
                    borderRadius: "var(--radius-md)",
                    padding: "6px 12px",
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  Open →
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* New Assistant Dialog */}
      {newModalOpen && (
        <div
          role="dialog"
          aria-label="New assistant"
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
              boxShadow: "var(--shadow-lg)",
              width: "100%",
              maxWidth: "480px",
              padding: "var(--spacing-6)",
            }}
          >
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
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                }}
              >
                New assistant
              </h2>
              <button
                type="button"
                onClick={() => setNewModalOpen(false)}
                style={{
                  fontSize: "var(--font-size-lg)",
                  color: "var(--color-text-secondary)",
                }}
              >
                ✕
              </button>
            </div>

            {/* Live Mode Notice */}
            {isLive && (
              <div
                style={{
                  padding: "var(--spacing-3)",
                  backgroundColor: "var(--color-bg-subtle)",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  marginBottom: "var(--spacing-4)",
                }}
              >
                <div style={{ marginBottom: "4px" }}>
                  <ConnectionBadge
                    status="not_connected"
                    route="POST /v1/assistants"
                  />
                </div>
                <p
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  Provisioning assistants requires POST /v1/assistants (not built yet).
                </p>
              </div>
            )}

            <div style={{ marginBottom: "var(--spacing-4)" }}>
              <label
                htmlFor="new-assistant-name"
                style={{
                  display: "block",
                  fontSize: "var(--font-size-xs)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  marginBottom: "4px",
                }}
              >
                Assistant Name
              </label>
              <input
                id="new-assistant-name"
                type="text"
                placeholder="e.g. Pharmacy Support Bot"
                value={newAssistantName}
                onChange={(e) => setNewAssistantName(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-page)",
                  color: "var(--color-text-primary)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
            </div>

            <div style={{ marginBottom: "var(--spacing-6)" }}>
              <label
                htmlFor="new-assistant-desc"
                style={{
                  display: "block",
                  fontSize: "var(--font-size-xs)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  marginBottom: "4px",
                }}
              >
                Description
              </label>
              <textarea
                id="new-assistant-desc"
                rows={3}
                placeholder="Brief summary of knowledge scope and intended audience..."
                value={newAssistantDesc}
                onChange={(e) => setNewAssistantDesc(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-page)",
                  color: "var(--color-text-primary)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
            </div>

            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: "var(--spacing-2)",
              }}
            >
              <button
                type="button"
                onClick={() => setNewModalOpen(false)}
                style={{
                  padding: "8px 14px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-text-secondary)",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isLive}
                style={{
                  padding: "8px 14px",
                  borderRadius: "var(--radius-md)",
                  backgroundColor: isLive
                    ? "var(--color-bg-subtle)"
                    : "var(--color-primary)",
                  color: isLive
                    ? "var(--color-text-secondary)"
                    : "var(--color-text-on-primary)",
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 600,
                  cursor: isLive ? "not-allowed" : "pointer",
                }}
              >
                Create Assistant
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm Dialog */}
      {deleteModalOpen && selectedAssistant && (
        <div
          role="dialog"
          aria-label="Delete assistant"
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
              boxShadow: "var(--shadow-lg)",
              width: "100%",
              maxWidth: "440px",
              padding: "var(--spacing-6)",
            }}
          >
            <h2
              style={{
                fontSize: "var(--font-size-lg)",
                fontWeight: 600,
                color: "var(--color-danger)",
                marginBottom: "var(--spacing-2)",
              }}
            >
              Delete assistant
            </h2>
            <p
              style={{
                fontSize: "var(--font-size-sm)",
                color: "var(--color-text-secondary)",
                marginBottom: "var(--spacing-4)",
              }}
            >
              Are you sure you want to delete <strong>{selectedAssistant.name}</strong>? This action cannot be undone.
            </p>

            {isLive && (
              <div
                style={{
                  padding: "var(--spacing-3)",
                  backgroundColor: "var(--color-bg-subtle)",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  marginBottom: "var(--spacing-4)",
                }}
              >
                <div style={{ marginBottom: "4px" }}>
                  <ConnectionBadge
                    status="not_connected"
                    route={`DELETE /v1/assistants/${selectedAssistant.id}`}
                  />
                </div>
                <p
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  Deleting assistants requires DELETE /v1/assistants/{`{id}`} (not built yet).
                </p>
              </div>
            )}

            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: "var(--spacing-2)",
              }}
            >
              <button
                type="button"
                onClick={() => setDeleteModalOpen(false)}
                style={{
                  padding: "8px 14px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-text-secondary)",
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isLive}
                style={{
                  padding: "8px 14px",
                  borderRadius: "var(--radius-md)",
                  backgroundColor: isLive
                    ? "var(--color-bg-subtle)"
                    : "var(--color-danger)",
                  color: isLive
                    ? "var(--color-text-secondary)"
                    : "var(--color-text-on-primary)",
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 600,
                  cursor: isLive ? "not-allowed" : "pointer",
                }}
              >
                Confirm Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
