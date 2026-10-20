"use client";

import { use, useState, useEffect } from "react";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { getCurrentUser, type User } from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface InstructionsPageProps {
  params: Promise<{ assistantId: string }>;
}

export default function InstructionsPage({ params }: InstructionsPageProps) {
  const resolvedParams = use(params);
  const assistantId = resolvedParams.assistantId;

  const [user, setUser] = useState<User | null>(null);
  const [instructionsText, setInstructionsText] = useState(
    "You are a clinical staff knowledge assistant. Respond concisely and professionally using bullet points when helpful. Use plain medical terminology suitable for clinical coordinators."
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

  return (
    <AppShell
      assistantId={assistantId}
      assistantName={assistantId === DEV_DEFAULT_ASSISTANT_ID ? "Clinic A Assistant" : "Assistant"}
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
              Instructions
            </h1>
            <ConnectionBadge
              status="not_connected"
              route={`PATCH /v1/assistants/${assistantId}/instructions`}
            />
          </div>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
            }}
          >
            Define the persona, conversational tone, intended audience, and scope for this assistant.
          </p>
        </div>

        {/* Invariant 4 Immutable Grounding Notice */}
        <div
          style={{
            backgroundColor: "var(--color-bg-subtle)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            padding: "var(--spacing-4)",
            marginBottom: "var(--spacing-6)",
            display: "flex",
            alignItems: "flex-start",
            gap: "var(--spacing-3)",
          }}
        >
          <span style={{ fontSize: "18px" }}>ℹ️</span>
          <div>
            <div
              style={{
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "2px",
              }}
            >
              Evidence Grounding Invariant
            </div>
            <p
              style={{
                fontSize: "var(--font-size-xs)",
                color: "var(--color-text-secondary)",
                lineHeight: 1.5,
              }}
            >
              Instructions shape tone and scope. Answers still come only from your documents.
            </p>
          </div>
        </div>

        {/* Instructions Form */}
        <div
          style={{
            backgroundColor: "var(--color-bg-card)",
            borderRadius: "var(--radius-lg)",
            border: "1px solid var(--color-border)",
            padding: "var(--spacing-6)",
          }}
        >
          <div style={{ marginBottom: "var(--spacing-4)" }}>
            <label
              htmlFor="assistant-instructions"
              style={{
                display: "block",
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "var(--spacing-2)",
              }}
            >
              System Prompt & Persona Guidelines
            </label>
            <textarea
              id="assistant-instructions"
              rows={8}
              value={instructionsText}
              onChange={(e) => setInstructionsText(e.target.value)}
              style={{
                width: "100%",
                padding: "10px 14px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-bg-page)",
                color: "var(--color-text-primary)",
                fontSize: "var(--font-size-sm)",
                lineHeight: 1.5,
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
              Updating instructions requires PATCH /v1/assistants/{assistantId}/instructions (not built yet).
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
              Save Instructions
            </button>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
