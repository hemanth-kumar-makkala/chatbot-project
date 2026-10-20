"use client";

import { use, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { getCurrentUser, type User } from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

interface ActionsPageProps {
  params: Promise<{ assistantId: string }>;
}

export default function ActionsPage({ params }: ActionsPageProps) {
  const resolvedParams = use(params);
  const assistantId = resolvedParams.assistantId;

  const [user, setUser] = useState<User | null>(null);

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
              Actions
            </h1>
            <ConnectionBadge
              status="not_connected"
              label="Coming soon"
            />
          </div>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
            }}
          >
            Outbound tool execution, webhook triggers, and third-party EHR integration.
          </p>
        </div>

        {/* Empty State Card */}
        <div
          style={{
            backgroundColor: "var(--color-bg-card)",
            borderRadius: "var(--radius-lg)",
            border: "1px solid var(--color-border)",
            padding: "var(--spacing-8) var(--spacing-6)",
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontSize: "36px",
              marginBottom: "var(--spacing-3)",
            }}
          >
            🛡️
          </div>
          <h2
            style={{
              fontSize: "var(--font-size-lg)",
              fontWeight: 600,
              color: "var(--color-text-primary)",
              marginBottom: "var(--spacing-2)",
            }}
          >
            Actions that call other systems need a separate security review
          </h2>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
              maxWidth: "560px",
              margin: "0 auto var(--spacing-6) auto",
              lineHeight: 1.5,
            }}
          >
            Coming soon. Actions that call other systems need a separate security review. Letting an AI model trigger external tool calls against live healthcare databases or EHR systems carries patient safety and PHI disclosure risks. Outbound actions remain disabled until tenant-scoped audit and approval workflows are verified.
          </p>

          <div
            style={{
              display: "inline-block",
              padding: "var(--spacing-3) var(--spacing-4)",
              backgroundColor: "var(--color-bg-subtle)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-secondary)",
            }}
          >
            Security Policy: Release 1 restricts execution to local RAG retrieval only (Invariant 1).
          </div>
        </div>
      </div>
    </AppShell>
  );
}
