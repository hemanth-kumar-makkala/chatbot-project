/**
 * Client Knowledge Assistant API Client
 * Strict adherence to docs/ui/API_CONTRACT_NOTES.md
 */

import { DEV_DEFAULT_ASSISTANT_ID } from "./devDefaults";

export interface User {
  id: string;
  email: string;
  name?: string | null;
  tenant_id: string;
}

export interface LoginResponse {
  ok: boolean;
  user: User;
}

export interface UploadResponse {
  document_id: string;
  version_id: string;
  assistant_id: string;
  filename: string;
  content_hash: string;
  duplicate: boolean;
}

export interface DocumentItem {
  document_id: string;
  title: string;
  current_version: number;
  status:
    | "queued"
    | "reading"
    | "scanning_images"
    | "ready"
    | "failed"
    | "retired"
    | string;
  uploaded_at: string;
  latest_version_id: string;
  flagged_pages_count: number;
}

export interface FlaggedPage {
  page_number: number;
  review_reason: string;
  reading_text: string | null;
  status: string;
}

export interface ReviewDataResponse {
  document_id: string;
  version_id: string;
  document_title: string;
  status: string;
  flagged_pages: FlaggedPage[];
}

export interface PageDecisionResponse {
  ok: boolean;
  page_number: number;
  decision: "accepted" | "excluded";
}

export interface PromoteResponse {
  ok: boolean;
  version_id: string;
  status: string;
}

export interface RetryResponse {
  ok: boolean;
  job_id: string;
  page_number: number;
}

export interface RetireResponse {
  ok: boolean;
  document_id: string;
  status: "retired";
}

export type ApiResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; code: string };

/**
 * Reads the csrf_token cookie safely in browser environments.
 */
export function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Normalizes error responses to a strict typed { ok: false, status, code }
 * NEVER passes raw server text, exception traces, or messages to the UI.
 */
export function extractErrorCode(status: number, data: unknown): string {
  if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    // Check detail.code
    if (obj.detail && typeof obj.detail === "object") {
      const detailObj = obj.detail as Record<string, unknown>;
      if (typeof detailObj.code === "string" && /^[A-Z0-9_]+$/.test(detailObj.code)) {
        return detailObj.code;
      }
    }
    // Check top-level code
    if (typeof obj.code === "string" && /^[A-Z0-9_]+$/.test(obj.code)) {
      return obj.code;
    }
    // Check known string details mapped to safe codes
    if (typeof obj.detail === "string") {
      const detailStr = obj.detail.trim().toLowerCase();
      if (/^[a-z0-9_]+$/.test(detailStr)) {
        return detailStr.toUpperCase();
      }
      if (detailStr.includes("version") && detailStr.includes("not found")) return "VERSION_NOT_FOUND";
      if (detailStr.includes("document") && detailStr.includes("not found")) return "DOCUMENT_NOT_FOUND";
      if (detailStr.includes("page") && detailStr.includes("not found")) return "PAGE_NOT_FOUND";
      if (detailStr.includes("no reviewable pages")) return "NO_REVIEWABLE_PAGES";
      if (detailStr.includes("unreviewed pages")) return "UNREVIEWED_PAGES_REMAIN";
      if (detailStr.includes("not in reviewable state")) return "PAGE_NOT_REVIEWABLE";
      if (detailStr.includes("invalid email or password")) return "INVALID_CREDENTIALS";
      if (detailStr.includes("csrf")) return "CSRF_ERROR";
      if (detailStr.includes("tenant") && (detailStr.includes("mismatch") || detailStr.includes("scope") || detailStr.includes("forbidden"))) {
        return "TENANT_MISMATCH";
      }
      if (detailStr.includes("job") && detailStr.includes("active")) return "JOB_ACTIVE";
    }
  }

  // Fallbacks by HTTP status
  switch (status) {
    case 400:
      return "BAD_REQUEST";
    case 401:
      return "UNAUTHORIZED";
    case 403:
      return "FORBIDDEN";
    case 404:
      return "NOT_FOUND";
    case 409:
      return "CONFLICT";
    case 413:
      return "FILE_TOO_LARGE";
    case 422:
      return "VALIDATION_ERROR";
    case 429:
      return "RATE_LIMIT_EXCEEDED";
    case 500:
      return "INTERNAL_SERVER_ERROR";
    default:
      return `HTTP_${status}`;
  }
}

/**
 * Universal fetch wrapper for API communication.
 * - Always includes credentials (cookies)
 * - Adds X-CSRF-Token on state-modifying methods (POST, PUT, PATCH, DELETE)
 * - Returns typed result hiding all raw server text
 */
export async function apiFetch<T>(
  url: string,
  options: RequestInit = {}
): Promise<ApiResult<T>> {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});

  // CSRF token requirement
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const csrfToken = getCsrfToken();
    if (csrfToken && !headers.has("X-CSRF-Token")) {
      headers.set("X-CSRF-Token", csrfToken);
    }
  }

  const fetchOptions: RequestInit = {
    ...options,
    method,
    headers,
    credentials: "include",
  };

  const normalizedUrl = url.replace(
    "00000000-0000-0002-0000-000000000001",
    DEV_DEFAULT_ASSISTANT_ID
  );

  try {
    const res = await fetch(normalizedUrl, fetchOptions);

    if (res.ok) {
      if (res.status === 204) {
        return { ok: true, status: res.status, data: undefined as unknown as T };
      }
      const contentType = res.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const data = await res.json();
        return { ok: true, status: res.status, data };
      }
      const text = await res.text();
      return { ok: true, status: res.status, data: text as unknown as T };
    }

    // Error case: parse response safely without leaking raw server text to callers
    let errBody: unknown = null;
    try {
      const contentType = res.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        errBody = await res.json();
      }
    } catch {
      // Body not JSON or unreadable
    }

    const code = extractErrorCode(res.status, errBody);
    return { ok: false, status: res.status, code };
  } catch (error) {
    // Network or client-side failure
    return { ok: false, status: 0, code: "NETWORK_ERROR" };
  }
}

/* Typed API Methods */

export async function login(email: string, password: string): Promise<ApiResult<LoginResponse>> {
  return apiFetch<LoginResponse>("/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(): Promise<ApiResult<{ ok: boolean }>> {
  return apiFetch<{ ok: boolean }>("/v1/auth/logout", {
    method: "POST",
  });
}

export async function getCurrentUser(): Promise<ApiResult<User>> {
  return apiFetch<User>("/v1/auth/me", {
    method: "GET",
  });
}

export function normalizeDocumentItem(raw: any): DocumentItem {
  if (
    raw &&
    typeof raw.status === "string" &&
    typeof raw.current_version === "number" &&
    typeof raw.latest_version_id === "string"
  ) {
    return raw as DocumentItem;
  }

  const versions = Array.isArray(raw?.versions) ? raw.versions : [];
  const latestVersion = versions.length > 0 ? versions[versions.length - 1] : null;

  const currentVersion = versions.length > 0 ? versions.length : 1;
  const latestVersionId =
    latestVersion?.version_id || raw?.active_version_id || raw?.latest_version_id || "";

  let status = "queued";
  if (raw?.is_retired) {
    status = "retired";
  } else if (latestVersion?.raw_status) {
    status = latestVersion.raw_status;
  } else if (latestVersion?.ui_status) {
    status = latestVersion.ui_status.toLowerCase();
  } else if (raw?.status) {
    status = raw.status;
  }

  const flaggedPagesCount =
    typeof latestVersion?.flagged_pages_count === "number"
      ? latestVersion.flagged_pages_count
      : typeof raw?.flagged_pages_count === "number"
      ? raw.flagged_pages_count
      : 0;

  const uploadedAt =
    raw?.created_at || latestVersion?.created_at || raw?.uploaded_at || new Date().toISOString();

  return {
    document_id: String(raw?.document_id || ""),
    title: String(raw?.title || "Untitled Document"),
    current_version: currentVersion,
    status,
    uploaded_at: String(uploadedAt),
    latest_version_id: String(latestVersionId),
    flagged_pages_count: flaggedPagesCount,
  };
}

export async function getDocuments(assistantId: string): Promise<ApiResult<DocumentItem[]>> {
  const result = await apiFetch<unknown>(
    `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents`,
    {
      method: "GET",
    }
  );

  if (!result.ok) {
    return result;
  }

  let list: unknown[] = [];
  if (Array.isArray(result.data)) {
    list = result.data;
  } else if (
    result.data &&
    typeof result.data === "object" &&
    Array.isArray((result.data as { documents?: unknown[] }).documents)
  ) {
    list = (result.data as { documents: unknown[] }).documents;
  }

  const items = list.map(normalizeDocumentItem);
  return { ok: true, status: result.status, data: items };
}

export async function uploadDocument(
  assistantId: string,
  file: File
): Promise<ApiResult<UploadResponse>> {
  const res = await apiFetch<any>(
    `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/upload`,
    {
      method: "POST",
      headers: {
        "X-Filename": file.name,
        "Content-Type": file.type || "application/octet-stream",
      },
      body: file,
    }
  );
  if (!res.ok) {
    return res;
  }
  const data = res.data || {};
  return {
    ok: true,
    status: res.status,
    data: {
      document_id: String(data.document_id || ""),
      version_id: String(data.document_version_id || data.version_id || ""),
      assistant_id: String(data.assistant_id || assistantId),
      filename: String(data.filename || file.name),
      content_hash: String(data.content_hash || ""),
      duplicate: Boolean(data.is_duplicate ?? data.duplicate ?? false),
    },
  };
}

export async function retireDocument(
  assistantId: string,
  documentId: string
): Promise<ApiResult<RetireResponse>> {
  return apiFetch<RetireResponse>(
    `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents/${encodeURIComponent(documentId)}`,
    {
      method: "DELETE",
    }
  );
}

export async function getReviewData(
  assistantId: string,
  versionId: string
): Promise<ApiResult<ReviewDataResponse>> {
  const res = await apiFetch<any>(
    `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents/${encodeURIComponent(versionId)}/review`,
    {
      method: "GET",
    }
  );
  if (!res.ok) {
    return res;
  }
  const rawData = res.data || {};
  const rawPages = Array.isArray(rawData.flagged_pages) ? rawData.flagged_pages : [];
  const flagged_pages: FlaggedPage[] = rawPages.map((p: any) => {
    let readingText = p.reading_text || null;
    if (!readingText && Array.isArray(p.readings) && p.readings.length > 0) {
      readingText = p.readings.map((r: any) => r.text).filter(Boolean).join("\n\n");
    }
    return {
      page_number: Number(p.page_number),
      review_reason: String(p.review_reason || "legacy_unspecified"),
      reading_text: readingText,
      status: p.is_excluded ? "excluded" : p.needs_review ? "needs_review" : "accepted",
    };
  });

  return {
    ok: true,
    status: res.status,
    data: {
      document_id: String(rawData.document_id || ""),
      version_id: String(rawData.version_id || versionId),
      document_title: String(rawData.document_title || ""),
      status: String(rawData.status || "needs_review"),
      flagged_pages,
    },
  };
}

export async function acceptPage(
  assistantId: string,
  versionId: string,
  pageNumber: number
): Promise<ApiResult<PageDecisionResponse>> {
  return apiFetch<PageDecisionResponse>(
    `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents/${encodeURIComponent(versionId)}/pages/${pageNumber}/accept`,
    {
      method: "POST",
    }
  );
}

export async function excludePage(
  assistantId: string,
  versionId: string,
  pageNumber: number
): Promise<ApiResult<PageDecisionResponse>> {
  return apiFetch<PageDecisionResponse>(
    `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents/${encodeURIComponent(versionId)}/pages/${pageNumber}/exclude`,
    {
      method: "POST",
    }
  );
}

export async function retryPage(
  assistantId: string,
  versionId: string,
  pageNumber: number
): Promise<ApiResult<RetryResponse>> {
  return apiFetch<RetryResponse>(
    `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents/${encodeURIComponent(versionId)}/pages/${pageNumber}/retry`,
    {
      method: "POST",
    }
  );
}

export async function promoteVersion(
  assistantId: string,
  versionId: string
): Promise<ApiResult<PromoteResponse>> {
  return apiFetch<PromoteResponse>(
    `/v1/assistants/${encodeURIComponent(assistantId)}/knowledge/documents/${encodeURIComponent(versionId)}/promote`,
    {
      method: "POST",
    }
  );
}

export function getPageImageUrl(
  assistantId: string,
  versionId: string,
  pageNumber: number
): string {
  const normId =
    assistantId === "00000000-0000-0002-0000-000000000001"
      ? DEV_DEFAULT_ASSISTANT_ID
      : assistantId;
  return `/v1/assistants/${encodeURIComponent(normId)}/knowledge/documents/${encodeURIComponent(versionId)}/pages/${pageNumber}/image`;
}

export interface SourceCitation {
  document_title: string;
  version: string;
  page_number: number;
  evidence_type: string;
  version_id?: string | null;
  sheet_name?: string | null;
  row_number?: number | null;
  language?: string | null;
}

export type FieldExtractionState =
  | "FILLED_HIGH"
  | "FILLED_MEDIUM"
  | "CONFLICTING"
  | "UNREADABLE"
  | "INVALID"
  | "NOT_PRESENT"
  | "NOT_APPLICABLE"
  | string;

export interface ExtractedField {
  name: string;
  value?: string | null;
  read1_value?: string | null;
  read2_value?: string | null;
  state: FieldExtractionState;
  confidence?: number | null;
  validator_error?: string | null;
  page_number: number;
  document_title: string;
}

export interface QuestionResponse {
  request_id: string;
  tenant_id: string;
  conversation_id: string;
  message_id: string;
  status: "answered" | "refusal" | "conflicting" | "safe_failure" | "clarify" | string;
  answer: string;
  citations: string[];
  evidence_type?: string | null;
  route?: string | null;
  answer_mode?: string | null;
  clarify_topics?: string[] | null;
  status_lines?: string[] | null;
  public_error_ref?: string | null;
  error_code?: string | null;
  sources?: SourceCitation[];
  conflict_sources?: SourceCitation[];
  extracted_fields?: ExtractedField[] | null;
}

export async function askQuestion(
  assistantId: string,
  questionText: string,
  conversationId?: string | null
): Promise<ApiResult<QuestionResponse>> {
  const normId =
    assistantId === "00000000-0000-0002-0000-000000000001"
      ? DEV_DEFAULT_ASSISTANT_ID
      : assistantId;

  let convId: string | null = null;
  if (conversationId !== undefined && conversationId !== null && conversationId.trim() !== "") {
    convId = conversationId;
  }

  return apiFetch<QuestionResponse>("/v1/questions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      assistant_id: normId,
      question_text: questionText,
      conversation_id: convId,
    }),
  });
}

export interface SessionDocumentResponse {
  session_document_id: string;
  conversation_id: string;
  file_name: string;
  page_count: number;
  status: string;
  extracted_fields: ExtractedField[];
  page_image_url: string;
}

export async function uploadSessionDocument(
  assistantId: string,
  conversationId: string,
  file: File
): Promise<ApiResult<SessionDocumentResponse>> {
  const normId =
    assistantId === "00000000-0000-0002-0000-000000000001"
      ? DEV_DEFAULT_ASSISTANT_ID
      : assistantId;

  const formData = new FormData();
  formData.append("file", file);

  return apiFetch<SessionDocumentResponse>(
    `/v1/assistants/${encodeURIComponent(normId)}/conversations/${encodeURIComponent(conversationId)}/documents`,
    {
      method: "POST",
      body: formData,
    }
  );
}

export interface FeedbackResponseData {
  id: string;
  message_id: string;
  user_id: string;
  rating: "up" | "down";
  reason: string | null;
  created_at: string;
  updated_at: string;
}

export async function submitFeedback(
  assistantId: string,
  messageId: string,
  rating: "up" | "down",
  reason?: string
): Promise<ApiResult<FeedbackResponseData>> {
  const normId =
    assistantId === "00000000-0000-0002-0000-000000000001"
      ? DEV_DEFAULT_ASSISTANT_ID
      : assistantId;

  const cleanReason = reason && reason.trim() !== "" ? reason.trim() : undefined;

  return apiFetch<FeedbackResponseData>(
    `/v1/assistants/${encodeURIComponent(normId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      method: "POST",
      body: JSON.stringify({
        rating,
        reason: cleanReason,
      }),
    }
  );
}

/**
 * Safely resolves a login redirect URL (STATUS.md UI Backlog & Open-Redirect hardening).
 * Accepts strictly relative paths starting with a single '/' and not '//' or '/\'.
 * Rejects external protocols, protocol-relative URLs, backslashes, and scheme bypass attempts.
 */
export function getSafeRedirectUrl(target: string | null | undefined): string {
  const defaultTarget = "/assistants";
  if (!target || typeof target !== "string") {
    return defaultTarget;
  }
  const trimmed = target.trim();
  if (trimmed === "" || trimmed === "/") {
    return defaultTarget;
  }
  // Must start with single slash, not protocol-relative (//) and not backslash (/\ or \)
  if (!trimmed.startsWith("/") || trimmed.startsWith("//") || trimmed.startsWith("/\\") || trimmed.startsWith("\\")) {
    return defaultTarget;
  }
  // Must not contain scheme delimiter or backslash
  if (trimmed.includes("://") || trimmed.includes(":") || trimmed.includes("\\")) {
    return defaultTarget;
  }
  return trimmed;
}
