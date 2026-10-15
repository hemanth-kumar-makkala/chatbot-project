"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser, type User } from "@/lib/api";
import { TopBar } from "@/components/TopBar";
import { DataSourceChip } from "@/components/DataSourceChip";
import { getFeatureDataSource } from "@/lib/connections";
import { getDemoAssistants, type DemoAssistant } from "@/lib/demo/fixtures";

export default function AssistantsPage() {
  const router = useRouter();
  const routerRef = useRef(router);
  routerRef.current = router;

  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const pickerMode = getFeatureDataSource("assistant_picker");
  const assistants = getDemoAssistants();

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

  function handleSelectAssistant(assistantId: string) {
    router.push(`/assistants/${assistantId}/chat`);
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
          color: "var(--color-text-muted)",
        }}
      >
        Loading authorized assistants...
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
      <TopBar userEmail={user?.email || null} />

      <main
        className="container"
        style={{
          flex: 1,
          maxWidth: "880px",
          width: "100%",
          margin: "0 auto",
          padding: "var(--spacing-8) var(--spacing-4)",
        }}
      >
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
            <h1
              style={{
                fontSize: "var(--font-size-2xl)",
                fontWeight: 700,
                color: "var(--color-navy)",
                marginBottom: "var(--spacing-1)",
              }}
            >
              Select Client Assistant
            </h1>
            <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-text-muted)" }}>
              Choose an authorized client knowledge base to begin staff consultation or review guidelines.
            </p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-2)" }}>
            <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-muted)" }}>
              Assistant Discovery:
            </span>
            <DataSourceChip mode={pickerMode} />
          </div>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(270px, 1fr))",
            gap: "var(--spacing-4)",
          }}
        >
          {assistants.map((asst: DemoAssistant) => (
            <div
              key={asst.id}
              onClick={() => handleSelectAssistant(asst.id)}
              style={{
                backgroundColor: "var(--color-bg-card)",
                borderRadius: "var(--radius-lg)",
                border: "1px solid var(--color-border)",
                padding: "var(--spacing-5)",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                cursor: "pointer",
                transition: "transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = "translateY(-2px)";
                e.currentTarget.style.boxShadow = "var(--shadow-md)";
                e.currentTarget.style.borderColor = "var(--color-primary-blue)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = "translateY(0)";
                e.currentTarget.style.boxShadow = "none";
                e.currentTarget.style.borderColor = "var(--color-border)";
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    color: "var(--color-primary-blue)",
                    marginBottom: "var(--spacing-1)",
                  }}
                >
                  {asst.client_name}
                </div>
                <h2
                  style={{
                    fontSize: "var(--font-size-lg)",
                    fontWeight: 600,
                    color: "var(--color-navy)",
                    marginBottom: "var(--spacing-2)",
                    lineHeight: 1.3,
                  }}
                >
                  {asst.name}
                </h2>
                <p
                  style={{
                    fontSize: "var(--font-size-sm)",
                    color: "var(--color-text-muted)",
                    lineHeight: 1.5,
                    marginBottom: "var(--spacing-4)",
                  }}
                >
                  {asst.description}
                </p>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  paddingTop: "var(--spacing-3)",
                  borderTop: "1px solid var(--color-border-subtle)",
                }}
              >
                <span
                  style={{
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-subtle)",
                    backgroundColor: "var(--color-bg-subtle)",
                    padding: "2px 8px",
                    borderRadius: "var(--radius-sm)",
                  }}
                >
                  {asst.document_count} documents
                </span>
                <button
                  type="button"
                  data-testid={`assistant-select-${asst.id}`}
                  style={{
                    backgroundColor: "var(--color-primary-blue)",
                    color: "var(--color-white)",
                    border: "none",
                    borderRadius: "var(--radius-md)",
                    padding: "6px 14px",
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  Open Chat →
                </button>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
