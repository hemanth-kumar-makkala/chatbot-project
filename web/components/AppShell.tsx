"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { APP_NAME } from "@/lib/appName";
import { logout } from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

export interface AppShellProps {
  assistantId?: string;
  assistantName?: string;
  workspaceName?: string;
  userEmail?: string | null;
  children: React.ReactNode;
}

export function AppShell({
  assistantId,
  assistantName,
  workspaceName = "Workspace",
  userEmail,
  children,
}: AppShellProps) {
  const router = useRouter();
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [switcherOpen, setSwitcherOpen] = useState(false);

  const [sidebarVisible, setSidebarVisible] = useState(true);

  // Safe pathname extraction to support environments where usePathname might be undefined in mocks
  let currentPath = "";
  try {
    currentPath = usePathname?.() || "";
  } catch {
    currentPath = "";
  }

  const assistantNavItems = [
    { label: "Home", href: `/assistants/${assistantId}` },
    { label: "Data sources", href: `/assistants/${assistantId}/data-sources` },
    { label: "Instructions", href: `/assistants/${assistantId}/instructions` },
    { label: "Guardrails", href: `/assistants/${assistantId}/guardrails` },
    { label: "Actions", href: `/assistants/${assistantId}/actions` },
    { label: "Conversations", href: `/assistants/${assistantId}/conversations` },
    { label: "Settings", href: `/assistants/${assistantId}/settings` },
  ];

  const workspaceNavItems = [
    { label: "Assistants", href: "/assistants" },
    { label: "Settings", href: "/settings" },
  ];

  async function handleLogout() {
    try {
      await logout();
    } finally {
      router.push("/login");
    }
  }

  // Close mobile drawer upon route navigation
  useEffect(() => {
    setMobileDrawerOpen(false);
  }, [currentPath]);

  const navContent = (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        backgroundColor: "var(--color-bg-surface)",
        color: "var(--color-text-primary)",
      }}
    >
      {/* Brand & Workspace Info */}
      <div
        style={{
          padding: "var(--spacing-4)",
          borderBottom: "1px solid var(--color-border)",
        }}
      >
        <div
          style={{
            fontSize: "var(--font-size-xs)",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            color: "var(--color-text-secondary)",
            marginBottom: "var(--spacing-1)",
          }}
        >
          {APP_NAME}
        </div>
        <div
          style={{
            fontSize: "var(--font-size-base)",
            fontWeight: 600,
            color: "var(--color-text-primary)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span>{workspaceName}</span>
          <Link
            href="/settings"
            title="Workspace settings"
            style={{
              fontSize: "var(--font-size-xs)",
              color: "var(--color-text-secondary)",
            }}
          >
            ⚙️
          </Link>
        </div>

        {/* Assistant Scope Switcher */}
        {assistantId && (
          <div style={{ marginTop: "var(--spacing-3)", position: "relative" }}>
            <button
              type="button"
              onClick={() => setSwitcherOpen((prev) => !prev)}
              aria-label="Switch assistant"
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "6px 10px",
                backgroundColor: "var(--color-bg-page)",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-md)",
                fontSize: "var(--font-size-sm)",
                fontWeight: 500,
                color: "var(--color-text-primary)",
                textAlign: "left",
              }}
            >
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {assistantName || "Clinic A Assistant"}
              </span>
              <span style={{ fontSize: "10px", color: "var(--color-text-secondary)" }}>
                ▼
              </span>
            </button>

            {switcherOpen && (
              <div
                style={{
                  position: "absolute",
                  top: "100%",
                  left: 0,
                  right: 0,
                  marginTop: "4px",
                  backgroundColor: "var(--color-bg-page)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                  boxShadow: "var(--shadow-md)",
                  zIndex: 20,
                  padding: "4px",
                }}
              >
                <Link
                  href="/assistants"
                  onClick={() => setSwitcherOpen(false)}
                  style={{
                    display: "block",
                    padding: "6px 10px",
                    fontSize: "var(--font-size-xs)",
                    color: "var(--color-text-secondary)",
                    borderRadius: "var(--radius-sm)",
                    textDecoration: "none",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = "var(--color-bg-subtle)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor = "transparent";
                  }}
                >
                  ← All Assistants
                </Link>
                <div
                  style={{
                    padding: "6px 10px",
                    fontSize: "var(--font-size-xs)",
                    fontWeight: 600,
                    color: "var(--color-text-primary)",
                  }}
                >
                  {assistantName || "Clinic A Assistant"} (Current)
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Main Navigation Links */}
      <nav
        aria-label={assistantId ? "Assistant navigation" : "Workspace navigation"}
        style={{
          flex: 1,
          padding: "var(--spacing-3) var(--spacing-2)",
          display: "flex",
          flexDirection: "column",
          gap: "2px",
          overflowY: "auto",
        }}
      >
        {(assistantId ? assistantNavItems : workspaceNavItems).map((item) => {
          const isActive =
            item.href === `/assistants/${assistantId}`
              ? currentPath === item.href
              : currentPath.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              style={{
                display: "flex",
                alignItems: "center",
                padding: "8px 12px",
                borderRadius: "var(--radius-md)",
                fontSize: "var(--font-size-sm)",
                fontWeight: isActive ? 600 : 400,
                color: isActive ? "var(--color-text-on-primary)" : "var(--color-text-primary)",
                backgroundColor: isActive ? "var(--color-primary)" : "transparent",
                textDecoration: "none",
                transition: "background-color 0.1s ease",
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.backgroundColor = "var(--color-bg-subtle)";
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.backgroundColor = "transparent";
                }
              }}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* User Footer */}
      <div
        style={{
          padding: "var(--spacing-3) var(--spacing-4)",
          borderTop: "1px solid var(--color-border)",
          fontSize: "var(--font-size-xs)",
          color: "var(--color-text-secondary)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--spacing-2)",
        }}
      >
        {userEmail && (
          <div
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {userEmail}
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Link
            href="/assistants"
            style={{ color: "var(--color-text-secondary)", textDecoration: "none" }}
          >
            Assistants
          </Link>
          <button
            type="button"
            onClick={handleLogout}
            style={{
              color: "var(--color-text-secondary)",
              fontWeight: 500,
              cursor: "pointer",
              padding: 0,
            }}
          >
            Log out
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div
      style={{
        display: "flex",
        minHeight: "100vh",
        backgroundColor: "var(--color-bg-page)",
      }}
    >
      {/* Desktop Sidebar */}
      {sidebarVisible && (
        <aside
          data-testid="chat-sidebar"
          style={{
            width: "260px",
            flexShrink: 0,
            borderRight: "1px solid var(--color-border)",
            display: "flex",
            flexDirection: "column",
          }}
          className="app-sidebar-desktop"
        >
          {navContent}
        </aside>
      )}

      {/* Mobile Drawer (When Open) */}
      {mobileDrawerOpen && (
        <>
          <div
            data-testid="sidebar-drawer-overlay"
            onClick={() => setMobileDrawerOpen(false)}
            style={{
              position: "fixed",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              backgroundColor: "var(--color-backdrop)",
              zIndex: 90,
            }}
          />
          <aside
            style={{
              position: "fixed",
              top: 0,
              bottom: 0,
              left: 0,
              width: "280px",
              maxWidth: "85vw",
              backgroundColor: "var(--color-bg-surface)",
              zIndex: 100,
              boxShadow: "var(--shadow-drawer)",
            }}
          >
            {navContent}
          </aside>
        </>
      )}

      {/* Main Content Area */}
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
        }}
      >
        {/* Mobile Header Bar */}
        <header
          className="app-mobile-header"
          style={{
            height: "56px",
            borderBottom: "1px solid var(--color-border)",
            padding: "0 var(--spacing-4)",
            alignItems: "center",
            justifyContent: "space-between",
            backgroundColor: "var(--color-bg-surface)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "var(--spacing-2)" }}>
            <button
              data-testid="sidebar-toggle-button"
              type="button"
              aria-label="Toggle navigation menu"
              onClick={() => {
                setMobileDrawerOpen((prev) => !prev);
                setSidebarVisible((prev) => !prev);
              }}
              style={{
                padding: "6px 8px",
                border: "1px solid var(--color-border)",
                borderRadius: "var(--radius-sm)",
                backgroundColor: "var(--color-bg-page)",
                color: "var(--color-text-primary)",
                fontWeight: 700,
              }}
            >
              ☰
            </button>
            <span style={{ fontWeight: 600, fontSize: "var(--font-size-sm)" }}>
              {assistantName || workspaceName}
            </span>
          </div>
          <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}>
            {APP_NAME}
          </span>
        </header>

        {/* Content Body */}
        <main
          style={{
            flex: 1,
            overflowY: "auto",
            backgroundColor: "var(--color-bg-page)",
          }}
        >
          {children}
        </main>
      </div>

      <style dangerouslySetInnerHTML={{
        __html: `
          @media (min-width: 769px) {
            .app-mobile-header {
              display: none !important;
            }
          }
          @media (max-width: 768px) {
            .app-sidebar-desktop {
              display: none !important;
            }
            .app-mobile-header {
              display: flex !important;
            }
          }
        `,
      }} />
    </div>
  );
}
