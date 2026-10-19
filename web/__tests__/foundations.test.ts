import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { apiFetch, getCsrfToken, extractErrorCode, getDocuments, normalizeDocumentItem, getSafeRedirectUrl } from "@/lib/api";
import { getReviewReasonLabel, REVIEW_REASON_LABELS } from "@/lib/reviewReasons";
import { getStatusChipConfig, isFinalStatus } from "@/lib/statusChips";

describe("CSRF and fetch wrapper", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    // Setup document.cookie with test csrf_token
    Object.defineProperty(document, "cookie", {
      writable: true,
      value: "csrf_token=csrf-token-abc-123; session_token=dummy-session",
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("extracts csrf_token from cookie", () => {
    expect(getCsrfToken()).toBe("csrf-token-abc-123");
  });

  it("sends X-CSRF-Token on POST requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ success: true }),
    });
    global.fetch = fetchMock;

    await apiFetch("/v1/test", { method: "POST", body: JSON.stringify({ a: 1 }) });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, options] = fetchMock.mock.calls[0];
    const headers = options.headers as Headers;
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token-abc-123");
    expect(options.credentials).toBe("include");
  });

  it("sends X-CSRF-Token on DELETE requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ ok: true }),
    });
    global.fetch = fetchMock;

    await apiFetch("/v1/test/item", { method: "DELETE" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, options] = fetchMock.mock.calls[0];
    const headers = options.headers as Headers;
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token-abc-123");
  });

  it("does NOT send X-CSRF-Token on GET requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ data: [] }),
    });
    global.fetch = fetchMock;

    await apiFetch("/v1/test/items", { method: "GET" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, options] = fetchMock.mock.calls[0];
    const headers = options.headers as Headers;
    expect(headers.get("X-CSRF-Token")).toBeNull();
  });
});

describe("Error sanitization (never passes raw server text to UI)", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("masks 401 with raw detail message to INVALID_CREDENTIALS code", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({
        detail: {
          code: "INVALID_CREDENTIALS",
          message: "Database lookup failed for secret user admin@internal",
        },
      }),
    });

    const result = await apiFetch("/v1/auth/login", { method: "POST" });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(401);
      expect(result.code).toBe("INVALID_CREDENTIALS");
      // Result object must not contain message or raw server text
      expect(result).toEqual({ ok: false, status: 401, code: "INVALID_CREDENTIALS" });
    }
  });

  it("masks 404 raw text into safe code without leaking raw file paths", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({
        detail: "Document version doc_secret_999 not found in tenant path /var/data/phi/123",
      }),
    });

    const result = await apiFetch("/v1/assistants/ast_1/knowledge/documents/doc_1", { method: "GET" });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(404);
      expect(result.code).toBe("VERSION_NOT_FOUND");
      expect(result).toEqual({ ok: false, status: 404, code: "VERSION_NOT_FOUND" });
    }
  });

  it("extracts JOB_ACTIVE code on 409", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({
        detail: {
          code: "JOB_ACTIVE",
          message: "Document processing job job_abc is active",
        },
      }),
    });

    const result = await apiFetch("/v1/test", { method: "POST" });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(409);
      expect(result.code).toBe("JOB_ACTIVE");
      expect(result).toEqual({ ok: false, status: 409, code: "JOB_ACTIVE" });
    }
  });

  it("extractErrorCode fallbacks gracefully on empty or HTML response", () => {
    expect(extractErrorCode(500, null)).toBe("INTERNAL_SERVER_ERROR");
    expect(extractErrorCode(413, null)).toBe("FILE_TOO_LARGE");
    expect(extractErrorCode(403, null)).toBe("FORBIDDEN");
    expect(extractErrorCode(422, null)).toBe("VALIDATION_ERROR");
  });
});

describe("Review reasons mapping", () => {
  it("maps all 7 specified review reasons correctly", () => {
    expect(getReviewReasonLabel("low_ocr_confidence")).toBe("Text unclear");
    expect(getReviewReasonLabel("vision_failed")).toBe("Image read failed");
    expect(getReviewReasonLabel("vision_low_confidence")).toBe("Image read uncertain");
    expect(getReviewReasonLabel("vision_confidence_missing")).toBe("Image read not scored");
    expect(getReviewReasonLabel("image_too_large")).toBe("Image too large");
    expect(getReviewReasonLabel("processing_failed")).toBe("Processing failed");
    expect(getReviewReasonLabel("legacy_unspecified")).toBe("Needs review (older upload)");
  });

  it("falls back to 'Needs review' and logs UI warning with code only for unknown reason", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const label = getReviewReasonLabel("unknown_model_failure_code_x");
    expect(label).toBe("Needs review");
    expect(warnSpy).toHaveBeenCalledWith("[UI Warning] Unknown review reason code: unknown_model_failure_code_x");

    warnSpy.mockRestore();
  });
});

describe("Status chips mapping", () => {
  it("maps Queued status", () => {
    expect(getStatusChipConfig("queued")).toEqual({
      label: "Queued",
      variant: "queued",
    });
  });

  it("maps Reading status", () => {
    expect(getStatusChipConfig("reading")).toEqual({
      label: "Reading",
      variant: "reading",
    });
  });

  it("maps Scanning images status", () => {
    expect(getStatusChipConfig("scanning_images")).toEqual({
      label: "Scanning images",
      variant: "scanning_images",
    });
  });

  it("maps Ready with 0 flagged pages to green Ready chip", () => {
    expect(getStatusChipConfig("ready", 0)).toEqual({
      label: "Ready",
      variant: "ready",
    });
  });

  it("maps Ready with N flagged pages to lavender review chip", () => {
    expect(getStatusChipConfig("ready", 1)).toEqual({
      label: "Ready (1 page needs review)",
      variant: "review",
    });
    expect(getStatusChipConfig("ready", 4)).toEqual({
      label: "Ready (4 pages need review)",
      variant: "review",
    });
  });

  it("maps Failed status", () => {
    expect(getStatusChipConfig("failed")).toEqual({
      label: "Failed",
      variant: "failed",
    });
  });

  it("maps Retired status", () => {
    expect(getStatusChipConfig("retired")).toEqual({
      label: "Retired",
      variant: "retired",
    });
  });

  it("handles unknown status gracefully", () => {
    expect(getStatusChipConfig("in_transit")).toEqual({
      label: "In Transit",
      variant: "unknown",
    });
  });

  it("checks final status correctly", () => {
    expect(isFinalStatus("ready")).toBe(true);
    expect(isFinalStatus("failed")).toBe(true);
    expect(isFinalStatus("retired")).toBe(true);
    expect(isFinalStatus("queued")).toBe(false);
    expect(isFinalStatus("reading")).toBe(false);
    expect(isFinalStatus("scanning_images")).toBe(false);
  });
});

describe("Document response normalization", () => {
  it("normalizes backend { documents: [...] } object responses", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({
        documents: [
          {
            document_id: "doc_test_1",
            title: "Clinic Guidelines.pdf",
            active_version_id: "ver_test_1",
            created_at: "2026-10-14T10:00:00Z",
            is_retired: false,
            versions: [
              {
                version_id: "ver_test_1",
                ui_status: "Ready",
                raw_status: "ready",
                flagged_pages_count: 2,
                total_pages_count: 5,
                created_at: "2026-10-14T10:00:00Z",
              },
            ],
          },
        ],
      }),
    });

    const res = await getDocuments("ast_test");
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(Array.isArray(res.data)).toBe(true);
      expect(res.data).toHaveLength(1);
      expect(res.data[0].document_id).toBe("doc_test_1");
      expect(res.data[0].title).toBe("Clinic Guidelines.pdf");
      expect(res.data[0].status).toBe("ready");
      expect(res.data[0].latest_version_id).toBe("ver_test_1");
      expect(res.data[0].flagged_pages_count).toBe(2);
      expect(res.data[0].current_version).toBe(1);
    }
  });

  it("handles empty backend response gracefully without throwing", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ documents: [] }),
    });

    const res = await getDocuments("ast_test");
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(Array.isArray(res.data)).toBe(true);
      expect(res.data).toHaveLength(0);
    }
  });

  it("normalizeDocumentItem preserves already-flat DocumentItem", () => {
    const flat = {
      document_id: "doc_flat",
      title: "Flat.pdf",
      current_version: 2,
      status: "ready",
      uploaded_at: "2026-10-14T12:00:00Z",
      latest_version_id: "ver_2",
      flagged_pages_count: 0,
    };
    expect(normalizeDocumentItem(flat)).toEqual(flat);
  });
});

describe("getSafeRedirectUrl security hardening (STATUS.md UI Backlog)", () => {
  it("defaults to /assistants when target is null, empty, or root", () => {
    expect(getSafeRedirectUrl(null)).toBe("/assistants");
    expect(getSafeRedirectUrl("")).toBe("/assistants");
    expect(getSafeRedirectUrl("/")).toBe("/assistants");
  });

  it("permits valid relative paths starting with single slash", () => {
    expect(getSafeRedirectUrl("/assistants/ast-123/chat")).toBe("/assistants/ast-123/chat");
    expect(getSafeRedirectUrl("/assistants/ast-123/knowledge")).toBe("/assistants/ast-123/knowledge");
  });

  it("rejects protocol-relative URLs starting with //", () => {
    expect(getSafeRedirectUrl("//evil.com")).toBe("/assistants");
    expect(getSafeRedirectUrl("//evil.com/phish")).toBe("/assistants");
  });

  it("rejects Windows backslash bypass URLs starting with /\\ or \\", () => {
    expect(getSafeRedirectUrl("/\\evil.com")).toBe("/assistants");
    expect(getSafeRedirectUrl("\\evil.com")).toBe("/assistants");
  });

  it("rejects absolute scheme URLs (http, https, javascript, data)", () => {
    expect(getSafeRedirectUrl("https://evil.com")).toBe("/assistants");
    expect(getSafeRedirectUrl("http://evil.com")).toBe("/assistants");
    expect(getSafeRedirectUrl("javascript:alert(1)")).toBe("/assistants");
    expect(getSafeRedirectUrl("data:text/html,...")).toBe("/assistants");
  });
});
