"use client";

import { use, useState, useEffect } from "react";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { getCurrentUser, type User } from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface AssistantSettingsPageProps {
  params: Promise<{ assistantId: string }>;
}

export default function AssistantSettingsPage({ params }: AssistantSettingsPageProps) {
  const resolvedParams = use(params);
  const assistantId = resolvedParams.assistantId;

  const [user, setUser] = useState<User | null>(null);
  const [name, setName] = useState(
    assistantId === DEV_DEFAULT_ASSISTANT_ID ? "Clinic A Assistant" : "Assistant"
  );
  const [description, setDescription] = useState(
    "Authorized clinical guidelines, prior authorization criteria, and formulary reference assistant."
  );
  const [copied, setCopied] = useState(false);

  // Dialogs state
  const [deleteChatsOpen, setDeleteChatsOpen] = useState(false);
  const [deleteAssistantOpen, setDeleteAssistantOpen] = useState(false);

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

  function handleCopyId() {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(assistantId);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  return (
    <AppShell
      assistantId={assistantId}
      assistantName={name}
      workspaceName={user?.tenant_id || "Workspace"}
      userEmail={user?.email || null}
    >
      <div
        style={{
          maxWidth: "800px",
          margin: "0 auto",
          padding: "var(--spacing-8) var(--spacing-6)",
        }}
      >
        <div style={{ marginBottom: "var(--spacing-6)" }}>
          <h1
            style={{
              fontSize: "var(--font-size-2xl)",
              fontWeight: 700,
              color: "var(--color-text-primary)",
              marginBottom: "var(--spacing-1)",
            }}
          >
            Assistant settings
          </h1>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
            }}
          >
            Manage metadata, identifiers, and lifecycle actions for this assistant.
          </p>
        </div>

        {/* General Settings */}
        <div
          style={{
            backgroundColor: "var(--color-bg-card)",
            borderRadius: "var(--radius-lg)",
            border: "1px solid var(--color-border)",
            padding: "var(--spacing-6)",
            marginBottom: "var(--spacing-8)",
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
            <div>
              <h2
                style={{
                  fontSize: "var(--font-size-base)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                }}
              >
                Assistant details
              </h2>
              <p
                style={{
                  fontSize: "var(--font-size-xs)",
                  color: "var(--color-text-secondary)",
                }}
              >
                Basic information about the assistant, including its unique identifier.
              </p>
            </div>
            <ConnectionBadge
              status="not_connected"
              route={`PATCH /v1/assistants/${assistantId}`}
            />
          </div>

          {/* Assistant ID with Copy Button */}
          <div style={{ marginBottom: "var(--spacing-4)" }}>
            <label
              htmlFor="asst-id"
              style={{
                display: "block",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "4px",
              }}
            >
              Assistant ID
            </label>
            <div style={{ display: "flex", gap: "var(--spacing-2)" }}>
              <input
                id="asst-id"
                type="text"
                readOnly
                value={assistantId}
                style={{
                  flex: 1,
                  padding: "8px 12px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-subtle)",
                  color: "var(--color-text-primary)",
                  fontSize: "var(--font-size-sm)",
                  fontFamily: "monospace",
                }}
              />
              <button
                type="button"
                onClick={handleCopyId}
                style={{
                  padding: "8px 14px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-page)",
                  color: "var(--color-text-primary)",
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                {copied ? "Copied!" : "Copy ID"}
              </button>
            </div>
          </div>

          {/* Name */}
          <div style={{ marginBottom: "var(--spacing-4)" }}>
            <label
              htmlFor="asst-name"
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
              id="asst-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
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

          {/* Description */}
          <div style={{ marginBottom: "var(--spacing-6)" }}>
            <label
              htmlFor="asst-description"
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
              id="asst-description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
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
              justifyContent: "space-between",
              alignItems: "center",
              paddingTop: "var(--spacing-4)",
              borderTop: "1px solid var(--color-border)",
            }}
          >
            <span
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-secondary)",
              }}
            >
              Updating assistant details requires PATCH /v1/assistants/{assistantId} (not built yet).
            </span>

            <button
              type="button"
              disabled
              style={{
                backgroundColor: "var(--color-bg-subtle)",
                color: "var(--color-text-secondary)",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                padding: "8px 16px",
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                cursor: "not-allowed",
              }}
            >
              Save Details
            </button>
          </div>
        </div>

        {/* Danger Zone */}
        <div
          style={{
            backgroundColor: "var(--color-bg-card)",
            borderRadius: "var(--radius-lg)",
            border: "1px solid var(--color-border)",
            padding: "var(--spacing-6)",
          }}
        >
          <h2
            style={{
              fontSize: "var(--font-size-base)",
              fontWeight: 600,
              color: "var(--color-danger)",
              marginBottom: "var(--spacing-4)",
            }}
          >
            Danger zone
          </h2>

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "var(--spacing-4) 0",
              borderBottom: "1px solid var(--color-border)",
              gap: "var(--spacing-4)",
            }}
          >
            <div>
              <div
                style={{
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  marginBottom: "2px",
                }}
              >
                Delete all conversations
              </div>
              <div
                style={{
                  fontSize: "var(--font-size-xs)",
                  color: "var(--color-text-secondary)",
                }}
              >
                Clear all past question transcripts and session messages for this assistant.
              </div>
            </div>

            <button
              type="button"
              onClick={() => setDeleteChatsOpen(true)}
              style={{
                padding: "8px 14px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-danger)",
                backgroundColor: "var(--color-bg-page)",
                color: "var(--color-danger)",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              Delete all conversations
            </button>
          </div>

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              paddingTop: "var(--spacing-4)",
              gap: "var(--spacing-4)",
            }}
          >
            <div>
              <div
                style={{
                  fontSize: "var(--font-size-sm)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  marginBottom: "2px",
                }}
              >
                Delete assistant
              </div>
              <div
                style={{
                  fontSize: "var(--font-size-xs)",
                  color: "var(--color-text-secondary)",
                }}
              >
                Permanently decommission this assistant and remove its knowledge base references.
              </div>
            </div>

            <button
              type="button"
              onClick={() => setDeleteAssistantOpen(true)}
              style={{
                padding: "8px 14px",
                borderRadius: "var(--radius-md)",
                backgroundColor: "var(--color-danger)",
                color: "var(--color-text-on-primary)",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                cursor: "pointer",
                whiteSpace: "nowrap",
                border: "none",
              }}
            >
              Delete assistant
            </button>
          </div>
        </div>

        {/* Confirm Dialog: Delete Conversations */}
        {deleteChatsOpen && (
          <div
            role="dialog"
            aria-label="Delete all conversations"
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
                maxWidth: "440px",
                width: "100%",
                padding: "var(--spacing-6)",
              }}
            >
              <h3
                style={{
                  fontSize: "var(--font-size-lg)",
                  fontWeight: 600,
                  color: "var(--color-danger)",
                  marginBottom: "var(--spacing-2)",
                }}
              >
                Delete all conversations
              </h3>
              <p
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-text-secondary)",
                  marginBottom: "var(--spacing-4)",
                }}
              >
                Are you sure you want to delete all conversations for this assistant? This action cannot be undone.
              </p>

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
                    route={`DELETE /v1/assistants/${assistantId}/conversations`}
                  />
                </div>
                <p
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  Deleting conversations requires DELETE /v1/assistants/{assistantId}/conversations (not built yet).
                </p>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--spacing-2)" }}>
                <button
                  type="button"
                  onClick={() => setDeleteChatsOpen(false)}
                  style={{
                    padding: "8px 14px",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    fontSize: "var(--font-size-sm)",
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled
                  style={{
                    padding: "8px 14px",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--color-bg-subtle)",
                    color: "var(--color-text-secondary)",
                    fontSize: "var(--font-size-sm)",
                    fontWeight: 600,
                    cursor: "not-allowed",
                  }}
                >
                  Confirm Delete
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Confirm Dialog: Delete Assistant */}
        {deleteAssistantOpen && (
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
                maxWidth: "440px",
                width: "100%",
                padding: "var(--spacing-6)",
              }}
            >
              <h3
                style={{
                  fontSize: "var(--font-size-lg)",
                  fontWeight: 600,
                  color: "var(--color-danger)",
                  marginBottom: "var(--spacing-2)",
                }}
              >
                Delete assistant
              </h3>
              <p
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-text-secondary)",
                  marginBottom: "var(--spacing-4)",
                }}
              >
                Are you sure you want to delete <strong>{name}</strong>?
              </p>

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
                    route={`DELETE /v1/assistants/${assistantId}`}
                  />
                </div>
                <p
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  Deleting an assistant requires DELETE /v1/assistants/{assistantId} (not built yet).
                </p>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "var(--spacing-2)" }}>
                <button
                  type="button"
                  onClick={() => setDeleteAssistantOpen(false)}
                  style={{
                    padding: "8px 14px",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    fontSize: "var(--font-size-sm)",
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled
                  style={{
                    padding: "8px 14px",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--color-bg-subtle)",
                    color: "var(--color-text-secondary)",
                    fontSize: "var(--font-size-sm)",
                    fontWeight: 600,
                    cursor: "not-allowed",
                  }}
                >
                  Confirm Delete
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
