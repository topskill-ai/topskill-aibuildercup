// Wire types copied verbatim from docs/SPEC.md §6.3 — do not edit here; change the SPEC first.

export type Role = "visitor" | "creator";
export type AccentColor = "amber" | "blue" | "emerald" | "rose" | "violet";
export type PricingMode = "free" | "per_round";
export type FieldType = "single_choice" | "multi_choice" | "number" | "boolean" | "text" | "long_text";

export interface Me { uid: string; email: string | null; display_name: string; photo_url: string | null;
  role: Role; creator_profile: { headline: string; bio: string } | null; created_at: string }

export interface Wallet { balance_cents: number; currency: "credits" }
export type LedgerCategory = "signup_grant" | "demo_topup" | "visitor_payment" | "creator_income"
  | "a2a_payment" | "a2a_income" | "adjust";
export interface LedgerEntry { id: string; request_id: string; kind: "CREDIT" | "DEBIT" | "ADJUST";
  category: LedgerCategory; amount_cents: number; balance_after_cents: number;
  unit_price_cents: number | null; billing_mode: "per_round" | "a2a" | null;
  counterparty_uid: string | null; counterparty_name: string | null;
  agent_id: string | null; agent_slug: string | null; agent_title: string | null;
  conversation_id: string | null; mandate_id: string | null; a2a_call_id: string | null;
  round_index: number | null; memo: string; created_at: string }
export interface Page<T> { items: T[]; next_cursor: string | null }

export interface StarterQuestion { label: string; prompt: string }
export interface Capability { icon: string; name: string; description: string }
export interface Pricing { mode: PricingMode; price_cents: number; free_rounds: number;
  a2a_enabled: boolean; a2a_price_cents: number }
export interface RecordOption { key: string; label: string }
export interface RecordField { key: string; label: string; type: FieldType; description: string;
  options: RecordOption[] | null; required: boolean; pii: boolean; show_in_table: boolean;
  min: number | null; max: number | null }
export interface RecordSchema { version: 1; scope: "session"; fields: RecordField[] }
export interface AgentConfig { soul: string; model: string; temperature: number | null;
  web_search_enabled: boolean; skill_ids: string[]; record_schema: RecordSchema | null;
  /** P2 guard (SPEC §18 "Guard"): Model Armor screening; applies live (not versioned). */
  safety_screening: boolean }

export interface SkillFile { path: string; size: number; mime: string; is_text: boolean }
export interface Skill { id: string; slug: string; name: string; description: string;
  body_chars: number; files: SkillFile[]; created_at: string; body_preview?: string }

export interface AgentStats { rounds: number; paid_rounds: number; conversations: number;
  visitor_gross_cents: number; creator_earned_cents: number; a2a_calls: number;
  a2a_gross_cents: number; records: number; last_activity_at: string | null;
  /** P2 guard (SPEC §18 "Guard"); optional so older payloads still type-check. */
  guard?: GuardStats }
export interface AgentUrls { public_page: string; a2a_card: string; a2a_endpoint: string }
export interface Agent { id: string; slug: string; status: "active" | "paused"; listed: boolean;
  title: string; tagline: string; description: string; avatar_id: string | null; avatar_url: string | null;
  greeting: string; input_placeholder: string; starter_questions: StarterQuestion[] | null;
  capabilities: Capability[]; accent_color: AccentColor; creator_name: string; creator_headline: string;
  pricing: Pricing; config: AgentConfig; config_dirty: boolean; active_version: number; version_count: number;
  skills: Skill[]; knowledge: { files: number; ready: number; processing: number; failed: number };
  stats: AgentStats; urls: AgentUrls; created_at: string; updated_at: string;
  /** P2 guard (SPEC §18 "Guard"): screening is effectively running (flag on, deployment has it, healthy). */
  guard_active?: boolean }
export interface AgentSummary { id: string; slug: string; title: string; tagline: string;
  avatar_url: string | null; accent_color: AccentColor; status: "active" | "paused"; listed: boolean;
  pricing: Pricing; active_version: number; config_dirty: boolean; stats: AgentStats; updated_at: string }

export interface AgentDraft { title: string; tagline: string; description: string; soul: string;
  greeting: string; input_placeholder: string; starter_questions: StarterQuestion[];
  capabilities: Capability[]; accent_color: AccentColor;
  pricing: { mode: PricingMode; price_cents: number; free_rounds: number; rationale: string };
  record_fields: RecordField[]; web_search_recommended: boolean; avatar_prompt: string; model_used: string }

export interface VersionSummary { n: number; created_at: string; note: string; model: string;
  web_search_enabled: boolean; skills: { name: string; skill_id: string }[]; records_enabled: boolean; active: boolean }

export interface PublicCreator { uid: string; name: string; headline: string }
export interface PublicAgentCard { slug: string; title: string; tagline: string; avatar_url: string | null;
  accent_color: AccentColor; creator: PublicCreator; pricing: { mode: PricingMode; price_cents: number; free_rounds: number };
  features: { knowledge: boolean; web_search: boolean; records: boolean; a2a: boolean;
    /** P2 guard (SPEC §18 "Guard"): "Protected by Model Armor" badge. */
    safety_screening?: boolean };
  stats: { conversations: number; a2a_calls: number } }
export interface PublicAgent extends PublicAgentCard { description: string; greeting: string;
  input_placeholder: string; starter_questions: StarterQuestion[]; capabilities: Capability[];
  a2a: { enabled: boolean; price_cents: number; card_url: string; endpoint: string };
  status: "active" }

export interface BillingStatus { billing_mode: PricingMode; price_cents: number; free_rounds: number;
  is_owner: boolean; used_rounds: number; free_rounds_left: number | null;
  next_round_charge_cents: number; balance_cents: number; can_afford_next_round: boolean;
  owner_available: boolean }

export interface Citation { n: number; kind: "knowledge" | "web"; title: string; uri: string | null;
  domain: string | null; file_id: string | null; page_start: number | null; page_end: number | null;
  snippet: string | null }
export interface ToolLine { id: string; tool: string; kind: ToolKind; title: string; target: string;
  status: "running" | "completed" | "error" | "interrupted" }
export type ToolKind = "search" | "web" | "skill" | "record" | "a2a" | "other";
export interface ChatMessage { id: string; role: "user" | "model"; text: string;
  status: "ok" | "failed" | "aborted"; turn_id: string; citations: Citation[];
  search_entry_point_html: string | null; tools: ToolLine[];
  charge: { charged_cents: number; free: boolean; round_index: number | null } | null; created_at: string }
export interface ConversationOut { id: string; agent_slug: string; title: string; rounds_ok: number;
  created_at: string; updated_at: string; messages: ChatMessage[] }
export interface ConversationSummary { id: string; visitor_name: string; title: string; rounds_ok: number;
  message_count: number; created_at: string; updated_at: string }

export interface KnowledgeFile { file_id: string; filename: string; mime: string; size: number;
  status: "processing" | "ready" | "failed" | "unreadable"; error: string | null; pages: number | null;
  created_at: string; updated_at: string }
export interface Passage { n: number; file_id: string; filename: string; text: string;
  page_start: number | null; page_end: number | null; score: number | null }

export interface RecordValue { value: string | number | boolean | string[] | null; evidence: string | null;
  updated_at: string; source: "tool" | "extraction" }
export type FollowStatus = "new" | "following" | "done" | "invalid";
export interface RecordRow { id: string; conversation_id: string; visitor_name: string;
  visitor_email_masked: string; version: number; status: "in_progress" | "completed";
  summary: string | null; completed_at: string | null; data: Record<string, RecordValue>;
  follow_status: FollowStatus; follow_note: string; followed_at: string | null;
  created_at: string; updated_at: string }

export interface Mandate { id: string; audience: string; intent: string; payer_agent: string | null;
  max_amount_cents: number; budget_cents: number; spent_cents: number; calls: number;
  status: "active" | "revoked" | "expired"; issued_at: string; expires_at: string }
export interface A2ACall { id: string; mandate_id: string; agent_slug: string; agent_title: string;
  status: "running" | "completed" | "failed"; price_cents: number; charged_cents: number;
  creator_share_cents: number; request_preview: string; answer_preview: string;
  created_at: string; finished_at: string | null }
export interface DirectoryAgent { slug: string; name: string; tagline: string; description: string;
  card_url: string; endpoint: string; price_cents: number; tags: string[]; examples: string[] }

export interface Dashboard { balance_cents: number; earned_total_cents: number; earned_today_cents: number;
  earned_7d_cents: number; visitor_income_cents: number; a2a_income_cents: number;
  paid_rounds: number; a2a_calls: number; agents: AgentSummary[]; recent_income: LedgerEntry[] }
export interface ApiErrorBody { code: string; message: string; trace_id: string; detail: Record<string, unknown> | null }

export interface Meta { chat_models: string[]; default_chat_model: string; revenue_share_bp: number;
  signup_grant_cents: number; demo_topup_cents: number; demo_topup_daily_limit: number;
  a2a_mandate_defaults: { max_amount_cents: number; budget_cents: number; ttl_minutes: number;
    max_budget_cents: number; max_ttl_minutes: number } }

// ---- SSE payloads (backend mirror: topskill/events.py; emit only via sse_event(name, Model)) ----
export interface StatusData { state: "ready"; conversation_id: string | null; turn_id: string;
  request_id: string; model: string }
export interface TextDeltaData { delta: string }
export interface ToolStartData { id: string; tool: string; kind: ToolKind; title: string; target: string }
export interface ToolEndData { id: string; tool: string; status: "completed" | "error" }
export interface CitationsData { sources: Citation[]; search_entry_point_html: string | null; web_search_queries: string[] }
export interface StreamErrorData { code: string; message: string; trace_id: string }
export interface DoneUsage { request_id: string; input_tokens: number | null; output_tokens: number | null; model: string | null }
export interface DoneBilling { charged_cents: number; free: boolean; round_index: number | null;
  free_rounds_left: number | null; balance_cents: number }
export interface DoneData { conversation_id: string | null; message_id: string | null; turn_id: string;
  outcome: "ok" | "failed" | "aborted"; usage: DoneUsage | null; billing: DoneBilling | null }
export type ChatStreamEvent =
  | { event: "status"; data: StatusData }
  | { event: "text_delta"; data: TextDeltaData }
  | { event: "tool_start"; data: ToolStartData }
  | { event: "tool_end"; data: ToolEndData }
  | { event: "citations"; data: CitationsData }
  | { event: "error"; data: StreamErrorData }
  | { event: "done"; data: DoneData }
  | { event: "guard"; data: GuardData };

export interface DiscoveredAgent { slug: string; name: string; tagline: string; price_cents: number; card_url: string }
export interface A2ADiscoverData { query: string; agents: DiscoveredAgent[] }
export interface A2ACallData { call_id: string | null; slug: string; agent_name: string; endpoint: string;
  price_cents: number; request_preview: string }
export interface A2AResultData { call_id: string | null; slug: string; status: "completed" | "failed" | "rejected";
  charged_cents: number; creator_share_cents: number; answer_preview: string; error_code: string | null;
  payer_balance_cents: number; mandate_spent_cents: number; mandate_budget_cents: number }
export interface MandateCounters { id: string; spent_cents: number; budget_cents: number; calls: number }
export interface AssistantDoneData extends DoneData { billing: null; mandate: MandateCounters }
export type AssistantStreamEvent =
  | { event: "status"; data: StatusData }            // conversation_id: null
  | { event: "text_delta"; data: TextDeltaData }
  | { event: "tool_start"; data: ToolStartData }     // kind "a2a" for calls, "search" for discovery
  | { event: "tool_end"; data: ToolEndData }
  | { event: "a2a_discover"; data: A2ADiscoverData }
  | { event: "a2a_call"; data: A2ACallData }
  | { event: "a2a_result"; data: A2AResultData }
  | { event: "error"; data: StreamErrorData }
  | { event: "done"; data: AssistantDoneData };

// ---- P2 guard: Model Armor (SPEC §18 "Guard") ----
export interface GuardStats { prompts_screened: number; prompts_blocked: number; prompts_redacted: number;
  answers_screened: number; answers_blocked: number; answers_redacted: number; answers_flagged: number;
  failed_open: number }
/** SSE `guard`: the visitor message (`prompt`) or the final answer (`answer`) was blocked or redacted. */
export interface GuardData { target: "prompt" | "answer"; action: "blocked" | "redacted"; categories: string[];
  text: string }
