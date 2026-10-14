"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { logout } from "@/lib/api";

interface TopBarProps {
  assistantName?: string | null;
  assistantId?: string;
  userEmail?: string | null;
}

export function TopBar({ assistantName, assistantId, userEmail }: TopBarProps) {
  const router = useRouter();
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

  return (
    <header
      style={{
        background: "var(--gradient-brand)",
        color: "var(--color-white)",
        boxShadow: "var(--shadow-md)",
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
              fontSize: "var(--font-size-2xl)",
              fontWeight: 700,
              letterSpacing: "-0.5px",
              color: "var(--color-white)",
            }}
          >
            Staffingly
          </span>
          <span
            style={{
              fontSize: "var(--font-size-sm)",
              opacity: 0.85,
              borderLeft: "1px solid rgba(255, 255, 255, 0.3)",
              paddingLeft: "var(--spacing-3)",
            }}
          >
            Knowledge Assistant
          </span>
          {assistantName && (
            <span
              style={{
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                backgroundColor: "rgba(255, 255, 255, 0.15)",
                padding: "4px 10px",
                borderRadius: "var(--radius-full)",
              }}
            >
              {assistantName}
            </span>
          )}
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
              color: "var(--color-white)",
              backgroundColor: "rgba(255, 255, 255, 0.12)",
              border: "1px solid rgba(255, 255, 255, 0.25)",
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
                backgroundColor: "var(--color-white)",
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
                    color: "var(--color-text-muted)",
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
                  color: "var(--color-navy)",
                  cursor: "pointer",
                  display: "block",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = "var(--color-light-lilac)";
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
