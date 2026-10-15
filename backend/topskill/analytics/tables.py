"""Warehouse tables of the creator analytics feature (SPEC §18.4).

Every table is a copy of one Firestore collection (or of the model messages of every conversation), keyed
by the Firestore doc id, partitioned by ``DATE(created_at)`` and clustered by ``owner_uid``. The physical
tables carry an ``fs_`` prefix; the model never sees those names. It only sees the CTE names in
:data:`TABLES` (``ledger``, ``rounds``, ...), and the server pre-filters every CTE to the asking creator
(``owner_uid = @uid``) before the model's SELECT runs (see :mod:`topskill.analytics.sql_guard`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ColumnType = Literal["STRING", "INT64", "BOOL", "TIMESTAMP", "STRING_ARRAY"]


@dataclass(frozen=True)
class Column:
    name: str
    type: ColumnType
    description: str = ""
    required: bool = False
    #: Hidden columns exist in the physical table but are dropped from the CTE the model queries.
    hidden: bool = False


@dataclass(frozen=True)
class TableSpec:
    cte: str  # name the model queries
    physical: str  # BigQuery table id inside the analytics dataset
    description: str
    columns: tuple[Column, ...]
    partition_field: str = "created_at"
    cluster_fields: tuple[str, ...] = ("owner_uid", "agent_id")

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def visible_columns(self) -> list[Column]:
        return [c for c in self.columns if not c.hidden]

    @property
    def hidden_names(self) -> list[str]:
        return [c.name for c in self.columns if c.hidden]


def _c(name: str, type_: ColumnType, description: str = "", **kw: bool) -> Column:
    return Column(name, type_, description, **kw)


_ID = _c("id", "STRING", "Firestore document id (unique).", required=True)
_OWNER = _c("owner_uid", "STRING", "Creator who owns the row (pre-filtered).", required=True, hidden=True)
_SYNCED = _c("synced_at", "TIMESTAMP", "When the row was copied to the warehouse.", hidden=True)


LEDGER = TableSpec(
    cte="ledger",
    physical="fs_ledger",
    description=(
        "Every entry of the creator's own wallet ledger (append-only). Income of the creator is "
        "category IN ('creator_income', 'a2a_income'): creator_income = paid visitor rounds on the web, "
        "a2a_income = calls paid by other AI agents over A2A. Spending by the creator is visitor_payment "
        "and a2a_payment (negative amounts). signup_grant and demo_topup are free credits, not income."
    ),
    columns=(
        _ID,
        _OWNER,
        _c("request_id", "STRING", "Idempotency key of the charge."),
        _c("kind", "STRING", "CREDIT | DEBIT | ADJUST (direction only; filter by category instead)."),
        _c(
            "category",
            "STRING",
            "signup_grant | demo_topup | visitor_payment | creator_income | a2a_payment | a2a_income "
            "| adjust",
        ),
        _c("amount_cents", "INT64", "Signed change of the balance in cents (100 cents = 1 credit)."),
        _c("balance_after_cents", "INT64", "Wallet balance after this entry, in cents."),
        _c("unit_price_cents", "INT64", "Price per round or per A2A call at charge time, in cents."),
        _c("billing_mode", "STRING", "per_round | a2a | NULL"),
        _c("counterparty_uid", "STRING", "Other party: the paying visitor/agent owner for income rows."),
        _c("agent_id", "STRING", "Agent the entry belongs to (NULL for grants/top-ups)."),
        _c("agent_slug", "STRING", "Agent URL slug."),
        _c("agent_title", "STRING", "Agent display name at charge time."),
        _c("conversation_id", "STRING", "Web conversation (NULL for A2A and grants)."),
        _c("mandate_id", "STRING", "A2A payment mandate id (A2A rows only)."),
        _c("a2a_call_id", "STRING", "A2A call id (A2A rows only); joins a2a_calls.id."),
        _c("round_index", "INT64", "Paid round number within the visitor's usage of the agent."),
        _c("model", "STRING", "Gemini model that answered."),
        _c("memo", "STRING", "Human-readable memo."),
        _c("created_at", "TIMESTAMP", "When the entry was written (UTC).", required=True),
        _SYNCED,
    ),
)

CONVERSATIONS = TableSpec(
    cte="conversations",
    physical="fs_conversations",
    description="Web chat conversations with the creator's agents (one row per conversation).",
    columns=(
        _ID,
        _OWNER,
        _c("agent_id", "STRING", "Agent id; joins agents.id."),
        _c("agent_slug", "STRING", "Agent URL slug."),
        _c("visitor_uid", "STRING", "Signed-in visitor (opaque id). Equal to the creator for own tests."),
        _c("channel", "STRING", "Always 'web'."),
        _c("title", "STRING", "First visitor message, at most 80 characters."),
        _c("rounds_ok", "INT64", "Successful rounds (question + answer) in the conversation."),
        _c("message_count", "INT64", "Stored messages (user + model)."),
        _c("created_at", "TIMESTAMP", "Conversation start (UTC).", required=True),
        _c("updated_at", "TIMESTAMP", "Last turn (UTC)."),
        _SYNCED,
    ),
)

ROUNDS = TableSpec(
    cte="rounds",
    physical="fs_rounds",
    description=(
        "One row per answered turn (model message) in a web conversation. status = 'ok' is a successful "
        "round; 'failed' and 'aborted' are never charged. charged_cents > 0 means the visitor paid; "
        "is_free = TRUE means a free round; counted = FALSE for the creator's own test turns and failures."
    ),
    columns=(
        _ID,
        _OWNER,
        _c("agent_id", "STRING", "Agent id; joins agents.id."),
        _c("agent_slug", "STRING", "Agent URL slug."),
        _c("conversation_id", "STRING", "Conversation id; joins conversations.id."),
        _c("visitor_uid", "STRING", "Visitor (opaque id)."),
        _c("by_owner", "BOOL", "TRUE when the creator tested their own agent."),
        _c("status", "STRING", "ok | failed | aborted"),
        _c("counted", "BOOL", "TRUE when the round counted for billing (free or paid)."),
        _c("is_free", "BOOL", "TRUE for a free round."),
        _c("round_index", "INT64", "Round number of this visitor with this agent (NULL if not counted)."),
        _c("charged_cents", "INT64", "What the visitor paid for this round, in cents (gross, before share)."),
        _c("model", "STRING", "Gemini model that answered."),
        _c("input_tokens", "INT64", "Prompt tokens."),
        _c("output_tokens", "INT64", "Answer tokens."),
        _c("tool_calls", "INT64", "Tools used in the turn (knowledge search, web search, records...)."),
        _c("knowledge_citations", "INT64", "Citations of the agent's knowledge documents."),
        _c("web_citations", "INT64", "Citations of web pages (Google Search grounding)."),
        _c("error_code", "STRING", "Error code of a failed turn."),
        _c("created_at", "TIMESTAMP", "When the answer was settled (UTC).", required=True),
        _SYNCED,
    ),
)

A2A_CALLS = TableSpec(
    cte="a2a_calls",
    physical="fs_a2a_calls",
    description=(
        "Calls made to the creator's agents by other AI agents over the A2A protocol. status: completed "
        "(charged), failed (not charged) or running. charged_cents is what the caller paid; "
        "creator_share_cents is what the creator earned."
    ),
    columns=(
        _ID,
        _OWNER,
        _c("agent_id", "STRING", "Agent id; joins agents.id."),
        _c("agent_slug", "STRING", "Agent URL slug."),
        _c("agent_title", "STRING", "Agent display name."),
        _c("payer_uid", "STRING", "Account that authorised the paying mandate (opaque id)."),
        _c("mandate_id", "STRING", "Payment mandate id."),
        _c("status", "STRING", "running | completed | failed"),
        _c("price_cents", "INT64", "Price snapshot of the call, in cents."),
        _c("charged_cents", "INT64", "Charged to the caller, in cents (0 when failed)."),
        _c("creator_share_cents", "INT64", "Creator's revenue share of the call, in cents."),
        _c("model", "STRING", "Gemini model that answered."),
        _c("error_code", "STRING", "Error code when failed."),
        _c("request_preview", "STRING", "First 120 characters of the caller's request."),
        _c("created_at", "TIMESTAMP", "When the call started (UTC).", required=True),
        _c("finished_at", "TIMESTAMP", "When the call settled (UTC)."),
        _SYNCED,
    ),
)

RECORDS = TableSpec(
    cte="records",
    physical="fs_records",
    description=(
        "Structured records (e.g. lead-intake forms) the agents filled while chatting, one per "
        "conversation. Field values are not copied (they may hold personal data); only which fields were "
        "filled."
    ),
    columns=(
        _ID,
        _OWNER,
        _c("agent_id", "STRING", "Agent id; joins agents.id."),
        _c("conversation_id", "STRING", "Conversation id; joins conversations.id."),
        _c("status", "STRING", "in_progress | completed"),
        _c("follow_status", "STRING", "Creator follow-up: new | following | done | invalid"),
        _c("fields_filled", "INT64", "Number of record fields with a value."),
        _c("filled_keys", "STRING_ARRAY", "Keys of the filled fields."),
        _c("has_summary", "BOOL", "TRUE when the record has a summary."),
        _c("created_at", "TIMESTAMP", "First save (UTC).", required=True),
        _c("updated_at", "TIMESTAMP", "Last change (UTC)."),
        _c("completed_at", "TIMESTAMP", "When the record was completed (UTC)."),
        _c("followed_at", "TIMESTAMP", "When the creator last changed follow_status (UTC)."),
        _SYNCED,
    ),
)

AGENTS = TableSpec(
    cte="agents",
    physical="fs_agents",
    description="The creator's agents (current settings).",
    columns=(
        _ID,
        _OWNER,
        _c("slug", "STRING", "URL slug."),
        _c("title", "STRING", "Display name."),
        _c("status", "STRING", "active | paused"),
        _c("listed", "BOOL", "Shown in the marketplace."),
        _c("deleted", "BOOL", "Deleted by the creator."),
        _c("pricing_mode", "STRING", "free | per_round"),
        _c("price_cents", "INT64", "Visitor price per round, in cents."),
        _c("free_rounds", "INT64", "Free rounds per visitor."),
        _c("a2a_enabled", "BOOL", "Callable by other agents over A2A."),
        _c("a2a_price_cents", "INT64", "Price per A2A call, in cents."),
        _c("model", "STRING", "Chat model."),
        _c("created_at", "TIMESTAMP", "Published at (UTC).", required=True),
        _c("updated_at", "TIMESTAMP", "Last settings change (UTC)."),
        _SYNCED,
    ),
    cluster_fields=("owner_uid",),
)

TABLES: tuple[TableSpec, ...] = (LEDGER, CONVERSATIONS, ROUNDS, A2A_CALLS, RECORDS, AGENTS)
TABLES_BY_CTE: dict[str, TableSpec] = {t.cte: t for t in TABLES}
CTE_NAMES: frozenset[str] = frozenset(TABLES_BY_CTE)
PHYSICAL_NAMES: frozenset[str] = frozenset(t.physical for t in TABLES)


def schema_prompt() -> str:
    """Text description of the queryable CTEs for the SQL-writing prompt."""
    blocks: list[str] = []
    for t in TABLES:
        lines = [f"TABLE {t.cte} -- {t.description}"]
        for c in t.visible_columns:
            type_ = "ARRAY<STRING>" if c.type == "STRING_ARRAY" else c.type
            lines.append(f"  {c.name} {type_}" + (f" -- {c.description}" if c.description else ""))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
