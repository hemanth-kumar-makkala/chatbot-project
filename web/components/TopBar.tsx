"use client";

import { useState } from "react";
import Link from "next/link";
import * as nextNavigation from "next/navigation";
import { logout } from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";
import { DEMO_ASSISTANTS } from "@/lib/demo/fixtures";
import { APP_NAME } from "@/lib/appName";

interface TopBarProps {
  assistantName?: string | null;
  assistantId?: string;
  userEmail?: string | null;
}

export function TopBar({ assistantName, assistantId, userEmail }: TopBarProps) {
  const router = nextNavigation.useRouter();
  let pathname = "";
  try {
    if (typeof nextNavigation.usePathname === "function") {
      pathname = nextNavigation.usePathname() || "";
    } else if (typeof window !== "undefined" && window.location) {
      pathname = window.location.pathname || "";
    }
  } catch {
    pathname = "";
  }
  const [menuOpen, setMenuOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  async function handleLogout() {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      router.push("/login");
    }
  }

  const isKnowledgeActive = pathname.includes("/knowledge");
  const isChatActive = pathname.includes("/chat");

  return (
    <header
      style={{
        backgroundColor: "var(--color-bg-page)",
        borderBottom: "1px solid var(--color-border)",
        color: "var(--color-text-primary)",
      }}
    >
      <div
        className="container"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          height: "64px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-4)" }}>
          <span
            style={{
              fontSize: "var(--font-size-xl)",
              fontWeight: 700,
              letterSpacing: "-0.5px",
              color: "var(--color-text-primary)",
            }}
          >
            {APP_NAME}
          </span>
          {/* Client Switcher (PART D) */}
          {assistantId && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-2)", marginLeft: "var(--spacing-1)" }}>
              <select
                id="client-switcher"
                data-testid="client-switcher"
                aria-label="Switch Client Assistant"
                value={assistantId || DEV_DEFAULT_ASSISTANT_ID}
                onChange={(e) => {
                  const targetId = e.target.value;
                  router.push(`/assistants/${encodeURIComponent(targetId)}/chat`);
                }}
                style={{
                  backgroundColor: "var(--color-bg-surface)",
                  color: "var(--color-text-primary)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                  padding: "4px 8px",
                  fontSize: "var(--font-size-xs)",
                  fontWeight: 500,
                  cursor: "pointer",
                  outline: "none",
                }}
              >
                {DEMO_ASSISTANTS.map((asst) => (
                  <option
                    key={asst.id}
                    value={asst.id}
                    style={{ color: "var(--color-text-primary)", backgroundColor: "var(--color-bg-page)" }}
                  >
                    {asst.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <nav style={{ display: "flex", gap: "var(--spacing-2)", marginLeft: "var(--spacing-2)" }}>
            <Link
              href={`/assistants/${encodeURIComponent(assistantId || DEV_DEFAULT_ASSISTANT_ID)}/knowledge`}
              style={{
                fontSize: "var(--font-size-sm)",
                color: isKnowledgeActive ? "var(--color-primary)" : "var(--color-text-secondary)",
                backgroundColor: isKnowledgeActive ? "var(--color-primary-soft)" : "transparent",
                border: isKnowledgeActive ? "1px solid var(--color-primary-soft)" : "1px solid transparent",
                padding: "4px 12px",
                borderRadius: "var(--radius-md)",
                textDecoration: "none",
                fontWeight: isKnowledgeActive ? 600 : 500,
              }}
            >
              Knowledge
            </Link>
            <Link
              href={`/assistants/${encodeURIComponent(assistantId || DEV_DEFAULT_ASSISTANT_ID)}/chat`}
              style={{
                fontSize: "var(--font-size-sm)",
                color: isChatActive ? "var(--color-primary)" : "var(--color-text-secondary)",
                backgroundColor: isChatActive ? "var(--color-primary-soft)" : "transparent",
                border: isChatActive ? "1px solid var(--color-primary-soft)" : "1px solid transparent",
                padding: "4px 12px",
                borderRadius: "var(--radius-md)",
                textDecoration: "none",
                fontWeight: isChatActive ? 600 : 500,
              }}
            >
              Chat
            </Link>
          </nav>
        </div>

        <div style={{ position: "relative" }}>
          <button
            type="button"
            id="user-menu-button"
            aria-expanded={menuOpen}
            aria-haspopup="true"
            onClick={() => setMenuOpen((prev) => !prev)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--spacing-2)",
              color: "var(--color-text-primary)",
              backgroundColor: "var(--color-bg-surface)",
              border: "1px solid var(--color-border)",
              padding: "6px 14px",
              borderRadius: "var(--radius-md)",
              cursor: "pointer",
              fontSize: "var(--font-size-sm)",
            }}
          >
            <span>{userEmail || "Account"}</span>
            <span aria-hidden="true" style={{ fontSize: "10px" }}>▼</span>
          </button>

          {menuOpen && (
            <div
              role="menu"
              aria-orientation="vertical"
              aria-labelledby="user-menu-button"
              style={{
                position: "absolute",
                right: 0,
                marginTop: "var(--spacing-2)",
                width: "180px",
                backgroundColor: "var(--color-bg-page)",
                borderRadius: "var(--radius-md)",
                boxShadow: "var(--shadow-lg)",
                border: "1px solid var(--color-border)",
                zIndex: 100,
                overflow: "hidden",
              }}
            >
              {userEmail && (
                <div
                  style={{
                    padding: "var(--spacing-3)",
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                    borderBottom: "1px solid var(--color-border-subtle)",
                    wordBreak: "break-all",
                  }}
                >
                  Signed in as <strong>{userEmail}</strong>
                </div>
              )}
              <button
                type="button"
                role="menuitem"
                id="logout-button"
                onClick={handleLogout}
                disabled={loggingOut}
                style={{
                  width: "100%",
                  textAlign: "left",
                  padding: "var(--spacing-3)",
                  fontSize: "var(--font-size-sm)",
                  color: "var(--color-text-primary)",
                  cursor: "pointer",
                  display: "block",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = "var(--color-bg-subtle)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = "transparent";
                }}
              >
                {loggingOut ? "Signing out..." : "Sign out"}
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
