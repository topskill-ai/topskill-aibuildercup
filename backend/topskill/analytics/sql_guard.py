"""Guard and wrapper for model-written SQL (SPEC §18 analytics).

The model writes ONE GoogleSQL ``SELECT`` over the fixed CTE names of :mod:`.tables`. Before anything
reaches BigQuery the server:

1. :func:`check_sql` lexes the text (string literals are understood, so keywords inside strings are
   harmless) and rejects anything that is not a single read-only query over the CTEs: comments,
   semicolons between statements, backtick-quoted identifiers (the only way to spell ``project-id`` or an
   escaped name), query parameters and system variables (``@``), DML/DDL/scripting keywords, table-valued
   and model functions (``ML.*``, ``AI.*``, ``EXTERNAL_QUERY``, ``TABLE``, ``INFORMATION_SCHEMA`` ...), the
   physical table names and the dataset name, and unbalanced parentheses;
2. :func:`wrap_sql` prepends ``WITH <cte> AS (SELECT ... FROM `project.dataset.fs_x` WHERE owner_uid = @uid)``
   for every table, so the creator's own rows are the only rows the query can see;
3. the caller dry-runs the wrapped query and refuses it unless it is a ``SELECT`` whose referenced tables
   are a subset of the analytics tables and whose byte estimate is under the cap (see :mod:`.nlq`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from topskill.analytics.tables import PHYSICAL_NAMES, TABLES

MAX_SQL_CHARS = 4000

TokenKind = Literal["ident", "string", "number", "punct"]


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    pos: int


class SqlRejected(ValueError):
    """The SQL is not a single safe SELECT over the analytics CTEs. ``reason`` is a short machine code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# Words that never appear in a read-only query over our CTEs (compared case-insensitively against
# unquoted identifiers/keywords; text inside string literals is not inspected).
FORBIDDEN_WORDS: frozenset[str] = frozenset(
    {
        # DML / DDL / DCL
        "insert", "update", "delete", "merge", "create", "drop", "alter", "truncate", "grant", "revoke",
        "rename", "undrop", "export", "load", "copy", "clone", "snapshot",
        # scripting / sessions / procedures
        "declare", "set", "execute", "immediate", "call", "begin", "commit", "rollback", "transaction",
        "assert", "return", "raise", "loop", "while", "repeat", "procedure", "function", "temp",
        "temporary", "recursive",
        # table-valued / model / external access
        "table", "model", "external_query", "information_schema", "ml", "ai", "obj", "vector_search",
        "appends", "changes", "external_object_transform", "system_time", "session_user", "kms",
        "aead", "deterministic_encrypt", "deterministic_decrypt", "gap_fill",
    }
)  # fmt: skip

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_STRING_PREFIX_RE = re.compile(r"(?:[rR][bB]?|[bB][rR]?)?(?='|\")")


def _forbidden_names(dataset: str, project: str) -> frozenset[str]:
    names = {n.lower() for n in PHYSICAL_NAMES} | {dataset.lower()}
    names |= {part.lower() for part in re.split(r"[^A-Za-z0-9_]+", project) if part}
    return frozenset(names)


def _read_string(sql: str, i: int) -> int:
    """Index just past the string literal starting at ``sql[i]`` (prefix included). Raises if unterminated."""
    m = _STRING_PREFIX_RE.match(sql, i)
    prefix = m.group(0) if m else ""
    j = i + len(prefix)
    quote = sql[j]
    triple = sql.startswith(quote * 3, j)
    delim = quote * 3 if triple else quote
    j += len(delim)
    while j < len(sql):
        ch = sql[j]
        if ch == "\\":  # escape (raw strings also cannot end on a backslash-quoted delimiter)
            j += 2
            continue
        if not triple and ch == "\n":
            break
        if sql.startswith(delim, j):
            return j + len(delim)
        j += 1
    raise SqlRejected("unterminated_string", "The query has an unterminated string literal.")


def tokenize(sql: str) -> list[Token]:
    """Lex GoogleSQL just enough to tell code from literals. Rejects comments and quoted identifiers."""
    tokens: list[Token] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if sql.startswith("--", i) or ch == "#" or sql.startswith("/*", i):
            raise SqlRejected("comment", "Comments are not allowed in the query.")
        if ch == "`":
            raise SqlRejected("quoted_identifier", "Backtick-quoted names are not allowed.")
        m = _STRING_PREFIX_RE.match(sql, i)
        if m is not None and i + len(m.group(0)) < n and sql[i + len(m.group(0))] in "'\"":
            end = _read_string(sql, i)
            tokens.append(Token("string", sql[i:end], i))
            i = end
            continue
        if ch.isalpha() or ch == "_":
            m = _IDENT_RE.match(sql, i)
            assert m is not None
            tokens.append(Token("ident", m.group(0), i))
            i = m.end()
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            m = _NUMBER_RE.match(sql, i)
            assert m is not None
            tokens.append(Token("number", m.group(0), i))
            i = m.end()
            continue
        if not ch.isascii():
            raise SqlRejected("bad_character", "The query contains an unexpected character.")
        tokens.append(Token("punct", ch, i))
        i += 1
    return tokens


def check_sql(sql: str, *, dataset: str, project: str) -> str:
    """Validate model SQL; returns it normalised (trimmed, trailing semicolons removed) or raises
    :class:`SqlRejected`."""
    text = (sql or "").strip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    if not text:
        raise SqlRejected("empty", "The query is empty.")
    if len(text) > MAX_SQL_CHARS:
        raise SqlRejected("too_long", "The query is too long.")
    tokens = tokenize(text)
    forbidden_names = _forbidden_names(dataset, project)
    lead = next((t for t in tokens if t.text != "("), None)
    if lead is None or lead.kind != "ident" or lead.text.lower() not in ("select", "with"):
        raise SqlRejected("not_select", "Only SELECT queries are allowed.")
    depth = 0
    for tok in tokens:
        if tok.kind == "punct":
            if tok.text == ";":
                raise SqlRejected("multiple_statements", "Only one statement is allowed.")
            if tok.text == "@":
                raise SqlRejected("parameter", "Query parameters and system variables are not allowed.")
            if tok.text in "{}\\$?":
                raise SqlRejected("bad_character", "The query contains an unexpected character.")
            if tok.text == "(":
                depth += 1
            elif tok.text == ")":
                depth -= 1
                if depth < 0:
                    raise SqlRejected("unbalanced", "The query has unbalanced parentheses.")
            continue
        if tok.kind != "ident":
            continue
        word = tok.text.lower()
        if word in FORBIDDEN_WORDS:
            raise SqlRejected("forbidden_keyword", f"'{tok.text.upper()}' is not allowed in the query.")
        if word in forbidden_names or word.startswith("fs_") or word.startswith("topskill_analytics"):
            raise SqlRejected("table_reference", "Query only the provided tables, by their short names.")
    if depth != 0:
        raise SqlRejected("unbalanced", "The query has unbalanced parentheses.")
    return text


def cte_block(*, project: str, dataset: str) -> str:
    """``ledger AS (...), conversations AS (...), ...`` – each pre-filtered to ``owner_uid = @uid``."""
    parts: list[str] = []
    for t in TABLES:
        hidden = ", ".join(t.hidden_names)
        select = f"SELECT * EXCEPT ({hidden})" if hidden else "SELECT *"
        parts.append(f"{t.cte} AS ({select} FROM `{project}.{dataset}.{t.physical}` WHERE owner_uid = @uid)")
    return ",\n  ".join(parts)


def wrap_sql(checked_sql: str, *, project: str, dataset: str) -> str:
    """Prepend the owner-filtered CTEs to a query that passed :func:`check_sql`.

    A query that starts with its own ``WITH`` has its CTE list appended after ours (``WITH ours, theirs
    SELECT ...``), so ORDER BY / LIMIT of the outer query keep their meaning. A CTE of the model that reuses
    one of our names is a duplicate alias, which BigQuery rejects.
    """
    ctes = cte_block(project=project, dataset=dataset)
    body = checked_sql.strip()
    m = re.match(r"(?is)^with\s+", body)
    if m:
        return f"WITH\n  {ctes},\n{body[m.end() :]}"
    return f"WITH\n  {ctes}\n{body}"


def allowed_tables(*, project: str, dataset: str) -> frozenset[str]:
    """``project.dataset.table`` ids the wrapped query may reference (checked after the dry run)."""
    return frozenset(f"{project}.{dataset}.{t.physical}" for t in TABLES)
