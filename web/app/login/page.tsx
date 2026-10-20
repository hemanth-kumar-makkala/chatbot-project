"use client";

import { useState, type FormEvent, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { login, getSafeRedirectUrl } from "@/lib/api";
import { APP_NAME } from "@/lib/appName";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectUrl = getSafeRedirectUrl(searchParams.get("redirect"));

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email || !password) {
      setErrorMessage("Please enter both email and password.");
      return;
    }

    setLoading(true);
    setErrorMessage(null);

    const result = await login(email, password);

    if (result.ok) {
      // Session is stored in httpOnly cookie 'session_token' by backend
      // Never store any tokens in localStorage or sessionStorage
      router.push(redirectUrl);
    } else {
      setLoading(false);
      if (result.code === "INVALID_CREDENTIALS" || result.status === 401) {
        setErrorMessage("Invalid email or password. Please check your credentials.");
      } else {
        setErrorMessage(`Unable to sign in (${result.code}). Please try again.`);
      }
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
        backgroundColor: "var(--color-bg-surface)",
        padding: "var(--spacing-4)",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "420px",
          backgroundColor: "var(--color-bg-page)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-md)",
          padding: "var(--spacing-8)",
          border: "1px solid var(--color-border)",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: "var(--spacing-6)" }}>
          <h1
            style={{
              fontSize: "var(--font-size-2xl)",
              fontWeight: 700,
              color: "var(--color-text-primary)",
              marginBottom: "var(--spacing-1)",
              letterSpacing: "-0.5px",
            }}
          >
            {APP_NAME}
          </h1>
          <p
            style={{
              fontSize: "var(--font-size-sm)",
              color: "var(--color-text-secondary)",
            }}
          >
            Client Portal Sign In
          </p>
        </div>

        {errorMessage && (
          <div
            role="alert"
            style={{
              backgroundColor: "var(--color-status-error-bg)",
              color: "var(--color-status-error-text)",
              padding: "var(--spacing-3)",
              borderRadius: "var(--radius-md)",
              marginBottom: "var(--spacing-4)",
              fontSize: "var(--font-size-sm)",
              border: "1px solid var(--color-danger)",
            }}
          >
            {errorMessage}
          </div>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <div style={{ marginBottom: "var(--spacing-4)" }}>
            <label
              htmlFor="email"
              style={{
                display: "block",
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "var(--spacing-1)",
              }}
            >
              Email address
            </label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                fontSize: "var(--font-size-base)",
                backgroundColor: "var(--color-bg-page)",
                color: "var(--color-text-primary)",
              }}
              placeholder="admin@example.com"
            />
          </div>

          <div style={{ marginBottom: "var(--spacing-6)" }}>
            <label
              htmlFor="password"
              style={{
                display: "block",
                fontSize: "var(--font-size-sm)",
                fontWeight: 600,
                color: "var(--color-text-primary)",
                marginBottom: "var(--spacing-1)",
              }}
            >
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{
                width: "100%",
                padding: "10px 12px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                fontSize: "var(--font-size-base)",
                backgroundColor: "var(--color-bg-page)",
                color: "var(--color-text-primary)",
              }}
              placeholder="••••••••"
            />
          </div>

          <button
            type="submit"
            id="login-submit-button"
            disabled={loading}
            style={{
              width: "100%",
              padding: "12px",
              backgroundColor: "var(--color-primary)",
              color: "var(--color-text-on-primary)",
              fontWeight: 700,
              fontSize: "var(--font-size-base)",
              borderRadius: "var(--radius-md)",
              cursor: loading ? "not-allowed" : "pointer",
              boxShadow: "var(--shadow-sm)",
              transition: "opacity 0.15s ease",
              opacity: loading ? 0.7 : 1,
            }}
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div
          style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: "var(--color-bg-surface)",
            color: "var(--color-text-primary)",
          }}
        >
          Loading...
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
