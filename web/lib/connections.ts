/**
 * Live / Demo Connection Management
 * Strict adherence to docs/ui/CONNECTION_MAP.md and UI-1 Scope.
 */

export type DataSourceMode = "live" | "demo";

export const REQUIRED_DATA_SOURCE_ERROR =
  "NEXT_PUBLIC_DATA_SOURCE is required and must be either 'live' or 'demo'.";

/**
 * Returns the current global data source mode from NEXT_PUBLIC_DATA_SOURCE.
 * Throws a fixed error if missing, empty, or unknown (no default).
 */
export function getDataSourceMode(): DataSourceMode {
  const val = process.env.NEXT_PUBLIC_DATA_SOURCE;
  if (!val || (val !== "live" && val !== "demo")) {
    throw new Error(REQUIRED_DATA_SOURCE_ERROR);
  }
  return val;
}

export type FeatureKey =
  | "auth_login"
  | "auth_me"
  | "auth_logout"
  | "knowledge_list"
  | "knowledge_upload"
  | "knowledge_status"
  | "knowledge_retire"
  | "knowledge_review"
  | "knowledge_image"
  | "knowledge_accept"
  | "knowledge_exclude"
  | "knowledge_promote"
  | "knowledge_retry"
  | "chat_ask"
  | "chat_citations"
  | "assistant_picker"
  | "assistants_list"
  | "assistant_create"
  | "assistant_delete"
  | "workspace_members"
  | "instructions"
  | "guardrails"
  | "actions"
  | "conversations"
  | "assistant_settings"
  | "conversation_history"
  | "streaming_status"
  | "visual_thumbnail"
  | "source_language"
  | "pdf_viewer"
  | "refusal_presentation"
  | "conflict_presentation"
  | "clarify_presentation"
  | "general_task_draft"
  | "safe_failure_presentation"
  | "session_doc_upload"
  | "extraction_table"
  | "field_state_badges"
  | "feedback_controls"
  | "chat_rate_limit";

/**
 * Explicit map from feature to data source ("live" | "demo"),
 * matching docs/ui/CONNECTION_MAP.md.
 * Today live: login, knowledge list/upload/review, ask question.
 * Everything else: demo.
 */
export const FEATURE_CONNECTIONS: Record<FeatureKey, "live" | "demo"> = {
  // Live features today per CONNECTION_MAP.md
  auth_login: "live",
  auth_me: "live",
  auth_logout: "live",
  knowledge_list: "live",
  knowledge_upload: "live",
  knowledge_status: "live",
  knowledge_retire: "live",
  knowledge_review: "live",
  knowledge_image: "live",
  knowledge_accept: "live",
  knowledge_exclude: "live",
  knowledge_promote: "live",
  knowledge_retry: "live",
  chat_ask: "live",
  chat_citations: "live",

  // Demo features today (unbuilt or simulated backend paths)
  assistant_picker: "demo",
  assistants_list: "demo",
  assistant_create: "demo",
  assistant_delete: "demo",
  workspace_members: "demo",
  instructions: "demo",
  guardrails: "demo",
  actions: "demo",
  conversations: "demo",
  assistant_settings: "demo",
  conversation_history: "demo",
  streaming_status: "demo",
  visual_thumbnail: "demo",
  source_language: "demo",
  pdf_viewer: "demo",
  refusal_presentation: "demo",
  conflict_presentation: "demo",
  clarify_presentation: "demo",
  general_task_draft: "demo",
  safe_failure_presentation: "demo",
  session_doc_upload: "demo",
  extraction_table: "demo",
  field_state_badges: "demo",
  feedback_controls: "demo",
  chat_rate_limit: "demo",
};

/**
 * Resolves the effective data source for a given feature.
 * If the global NEXT_PUBLIC_DATA_SOURCE is "demo", returns "demo".
 * If "live", returns the feature's status from FEATURE_CONNECTIONS.
 */
export function getFeatureDataSource(feature: FeatureKey): "live" | "demo" {
  const globalMode = getDataSourceMode();
  if (globalMode === "demo") {
    return "demo";
  }
  return FEATURE_CONNECTIONS[feature];
}
