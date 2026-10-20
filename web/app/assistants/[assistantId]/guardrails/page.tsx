"use client";

import { use, useState, useEffect } from "react";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { getCurrentUser, type User } from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface GuardrailsPageProps {
  params: Promise<{ assistantId: string }>;
}

export default function GuardrailsPage({ params }: GuardrailsPageProps) {
  const resolvedParams = use(params);
  const assistantId = resolvedParams.assistantId;

  const [user, setUser] = useState<User | null>(null);
  const [blockedTopics, setBlockedTopics] = useState(
    "Competitor pricing, medical advice outside client guidelines, personal health diagnoses"
  );
  const [refusalMessage, setRefusalMessage] = useState(
    "I don't have that in Client A's documents."
  );

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

  const lockedRules = [
    {
      title: "Answers only from documents or refuse",
      description: "Model generation is restricted strictly to validated evidence. Hallucinations outside verified client documents are prohibited (Invariant 4).",
    },
    {
      title: "Every answer cites document and page",
      description: "All factual claims cite the specific document title, version, and page number with clickable verification chips (Invariant 5).",
    },
    {
      title: "Never shows another client's data",
      description: "PostgreSQL Row-Level Security (RLS) and tenant isolation enforce absolute boundaries across all queries.",
    },
    {
      title: "Unreadable fields are never guessed",
      description: "Faint or distorted text that cannot be confidently transcribed is left blank and flagged for human review (Invariant 6).",
    },
    {
      title: "Documents uploaded in chat never enter the knowledge base",
      description: "Ad-hoc session documents uploaded during staff chat remain ephemeral to the conversation and are never ingested (Invariant 7).",
    },
  ];

  return (
    <AppShell
      assistantId={assistantId}
      assistantName={assistantId === DEV_DEFAULT_ASSISTANT_ID ? "Clinic A Assistant" : "Assistant"}
      workspaceName={user?.tenant_id || "Workspace"}
      userEmail={user?.email || null}
    >
      <div
        style={{
          maxWidth: "840px",
          margin: "0 auto",
          padding: "var(--spacing-8) var(--spacing-6)",
        }}
      >
        <div style={{ marginBottom: "var(--spacing-6)" }}>
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
              Guardrails
            </h1>
            <ConnectionBadge
              status="not_connected"
              route={`PATCH /v1/assistants/${assistantId}/guardrails`}
            />
          </div>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
            }}
          >
            Review automated system invariants and customize refusal criteria for this assistant.
          </p>
        </div>

        {/* Section 1: Locked Built-in Guardrails */}
        <section style={{ marginBottom: "var(--spacing-8)" }}>
          <h2
            style={{
              fontSize: "var(--font-size-base)",
              fontWeight: 600,
              color: "var(--color-text-primary)",
              marginBottom: "var(--spacing-3)",
            }}
          >
            Built-in Safety Invariants (Locked & Always Active)
          </h2>

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--spacing-3)",
            }}
          >
            {lockedRules.map((rule, idx) => (
              <div
                key={`locked-${idx}`}
                style={{
                  backgroundColor: "var(--color-bg-card)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                  padding: "var(--spacing-4)",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: "var(--spacing-4)",
                }}
              >
                <div>
                  <div
                    style={{
                      fontSize: "var(--font-size-sm)",
                      fontWeight: 600,
                      color: "var(--color-text-primary)",
                      marginBottom: "4px",
                    }}
                  >
                    {rule.title}
                  </div>
                  <div
                    style={{
                      fontSize: "var(--font-size-xs)",
                      color: "var(--color-text-secondary)",
                      lineHeight: 1.4,
                    }}
                  >
                    {rule.description}
                  </div>
                </div>

                <span
                  style={{
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 600,
                    padding: "2px 8px",
                    borderRadius: "var(--radius-full)",
                    backgroundColor: "var(--color-bg-subtle)",
                    color: "var(--color-text-secondary)",
                    border: "1px solid var(--color-border)",
                    whiteSpace: "nowrap",
                  }}
                >
                  🔒 Locked
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* Section 2: Configurable Guardrails */}
        <section
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
              color: "var(--color-text-primary)",
              marginBottom: "var(--spacing-4)",
            }}
          >
            Configurable Rules
          </h2>

          <div style={{ marginBottom: "var(--spacing-4)" }}>
            <label
              htmlFor="blocked-topics"
              style={{
                display: "block",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "4px",
              }}
            >
              Blocked Topics List
            </label>
            <input
              id="blocked-topics"
              type="text"
              value={blockedTopics}
              onChange={(e) => setBlockedTopics(e.target.value)}
              placeholder="Comma-separated topics to immediately refuse..."
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
              htmlFor="custom-refusal"
              style={{
                display: "block",
                fontSize: "var(--font-size-xs)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "4px",
              }}
            >
              Custom Refusal Message
            </label>
            <input
              id="custom-refusal"
              type="text"
              value={refusalMessage}
              onChange={(e) => setRefusalMessage(e.target.value)}
              placeholder="Message returned when evidence is missing..."
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
              Configuring guardrails requires PATCH /v1/assistants/{assistantId}/guardrails (not built yet).
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
              Save Guardrails
            </button>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
