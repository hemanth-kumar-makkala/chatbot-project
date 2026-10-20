"use client";

import { useEffect, useState } from "react";
import { getCurrentUser, type User } from "@/lib/api";
import { AppShell } from "@/components/AppShell";
import { ConnectionBadge } from "@/components/ConnectionBadge";

export default function WorkspaceSettingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [activeTab, setActiveTab] = useState<"general" | "members">("general");

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
      userEmail={user?.email || null}
      workspaceName={user?.tenant_id || "Workspace"}
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
            Workspace settings
          </h1>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
            }}
          >
            Configure your tenant workspace identity, review client boundaries, and manage team access.
          </p>
        </div>

        {/* Settings Navigation Tabs */}
        <div
          style={{
            display: "flex",
            gap: "var(--spacing-2)",
            borderBottom: "1px solid var(--color-border)",
            marginBottom: "var(--spacing-6)",
          }}
        >
          <button
            type="button"
            onClick={() => setActiveTab("general")}
            style={{
              padding: "var(--spacing-2) var(--spacing-4)",
              fontSize: "var(--font-size-sm)",
              fontWeight: 600,
              color:
                activeTab === "general"
                  ? "var(--color-text-primary)"
                  : "var(--color-text-secondary)",
              borderBottom:
                activeTab === "general" ? "2px solid var(--color-primary)" : "none",
            }}
          >
            General
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("members")}
            style={{
              padding: "var(--spacing-2) var(--spacing-4)",
              fontSize: "var(--font-size-sm)",
              fontWeight: 600,
              color:
                activeTab === "members"
                  ? "var(--color-text-primary)"
                  : "var(--color-text-secondary)",
              borderBottom:
                activeTab === "members" ? "2px solid var(--color-primary)" : "none",
            }}
          >
            Members
          </button>
        </div>

        {/* Tab 1: General */}
        {activeTab === "general" && (
          <div
            style={{
              backgroundColor: "var(--color-bg-card)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--color-border)",
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
              <div>
                <h2
                  style={{
                    fontSize: "var(--font-size-base)",
                    fontWeight: 600,
                    color: "var(--color-text-primary)",
                  }}
                >
                  Workspace details
                </h2>
                <p
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  Tenant identity and client scope bounded by authenticated session.
                </p>
              </div>
              <ConnectionBadge status="connected" label="Connected" />
            </div>

            <div style={{ marginBottom: "var(--spacing-4)" }}>
              <label
                htmlFor="workspace-name"
                style={{
                  display: "block",
                  fontSize: "var(--font-size-xs)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  marginBottom: "4px",
                }}
              >
                Workspace Name
              </label>
              <input
                id="workspace-name"
                type="text"
                readOnly
                value={user?.tenant_id || "Clinic A"}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-subtle)",
                  color: "var(--color-text-primary)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
            </div>

            <div>
              <label
                htmlFor="user-email"
                style={{
                  display: "block",
                  fontSize: "var(--font-size-xs)",
                  fontWeight: 600,
                  color: "var(--color-text-primary)",
                  marginBottom: "4px",
                }}
              >
                Staff User Account
              </label>
              <input
                id="user-email"
                type="text"
                readOnly
                value={user?.email || "staff@clinic.com"}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  backgroundColor: "var(--color-bg-subtle)",
                  color: "var(--color-text-primary)",
                  fontSize: "var(--font-size-sm)",
                }}
              />
            </div>
          </div>
        )}

        {/* Tab 2: Members */}
        {activeTab === "members" && (
          <div
            style={{
              backgroundColor: "var(--color-bg-card)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--color-border)",
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
              <div>
                <h2
                  style={{
                    fontSize: "var(--font-size-base)",
                    fontWeight: 600,
                    color: "var(--color-text-primary)",
                  }}
                >
                  Workspace Members
                </h2>
                <p
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                  }}
                >
                  Invite colleagues, assign staff roles, and audit access permissions.
                </p>
              </div>
              <ConnectionBadge
                status="not_connected"
                route="GET /v1/tenant/members"
              />
            </div>

            <div
              style={{
                padding: "var(--spacing-4)",
                backgroundColor: "var(--color-bg-subtle)",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
              }}
            >
              <p
                style={{
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-text-secondary)",
                }}
              >
                Member management requires GET /v1/tenant/members (not built yet).
              </p>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
