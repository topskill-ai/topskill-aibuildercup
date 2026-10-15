"""SQL guard: only one read-only SELECT over the CTE names gets through (SPEC §18 analytics)."""

from __future__ import annotations

import pytest

from topskill.analytics import sql_guard
from topskill.analytics.sql_guard import SqlRejected, check_sql, tokenize, wrap_sql
from topskill.analytics.tables import TABLES

pytestmark = pytest.mark.unit

DS = "topskill_analytics"
PROJECT = "top-skill"


def ok(sql: str) -> str:
    return check_sql(sql, dataset=DS, project=PROJECT)


def rejected(sql: str) -> str:
    with pytest.raises(SqlRejected) as info:
        check_sql(sql, dataset=DS, project=PROJECT)
    return info.value.reason


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT agent_title, SUM(amount_cents) AS income_cents FROM ledger "
        "WHERE category = 'a2a_income' GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
        "select count(*) as n from rounds where status = 'ok'",
        "WITH w AS (SELECT * FROM a2a_calls WHERE status = 'failed') SELECT error_code, COUNT(*) AS n FROM w "
        "GROUP BY error_code",
        "(SELECT 1 AS x)",
        "SELECT DATE(created_at) AS day, SUM(amount_cents) AS income_cents FROM ledger "
        "WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY) GROUP BY day ORDER BY day",
        "SELECT a.title, COUNT(r.id) AS rounds FROM agents AS a LEFT JOIN rounds AS r ON r.agent_id = a.id "
        "GROUP BY a.title",
        # keywords inside string literals are data, not code
        "SELECT * FROM ledger WHERE memo = 'DROP TABLE x; -- @uid fs_ledger `top-skill`'",
        "SELECT * FROM ledger WHERE memo LIKE \"%delete%\" AND memo != r'\\d+' AND memo != '''a\nb'''",
        "SELECT * FROM ledger WHERE memo = 'it\\'s fine'",
        "SELECT * REPLACE (UPPER(memo) AS memo) FROM ledger",
        "SELECT 1;",
        "SELECT 1 ;  ;\n",
        "SELECT ARRAY_LENGTH(filled_keys) AS n FROM records",
        # ``model`` is a column; the project id's parts are ordinary names
        "SELECT model, COUNT(*) AS n FROM rounds GROUP BY model",
        "SELECT r.model FROM rounds AS r",
        "WITH top AS (SELECT 1 AS n) SELECT * FROM top",
        "WITH top AS (SELECT 1 AS n) SELECT top.n FROM top",
        "SELECT title AS skill FROM agents",
        "SELECT top - skill AS d FROM (SELECT 2 AS top, 1 AS skill)",
    ],
)
def test_accepts_plain_selects(sql: str) -> None:
    out = ok(sql)
    assert not out.endswith(";")


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        # stacked statements and scripting
        ("SELECT 1; DROP TABLE ledger", "multiple_statements"),
        ("SELECT 1; SELECT * FROM `top-skill.topskill_analytics.fs_ledger`", "quoted_identifier"),
        ("DECLARE x INT64 DEFAULT 1", "not_select"),
        ("BEGIN SELECT 1; END", "not_select"),
        ("EXECUTE IMMEDIATE 'SELECT * FROM topskill_analytics.fs_ledger'", "not_select"),
        ("SELECT 1 FROM ledger WHERE EXISTS (SELECT 1) AND 1 = 1 UNION ALL SELECT 1 /* x */", "comment"),
        # DML / DDL
        ("DELETE FROM ledger WHERE TRUE", "not_select"),
        ("UPDATE ledger SET amount_cents = 0 WHERE TRUE", "not_select"),
        ("INSERT INTO ledger (id) VALUES ('x')", "not_select"),
        ("MERGE ledger T USING ledger S ON T.id = S.id WHEN MATCHED THEN DELETE", "not_select"),
        ("CREATE TEMP TABLE t AS SELECT 1", "not_select"),
        ("SELECT * FROM ledger WHERE id IN (SELECT id FROM ledger) AND x = (DELETE)", "forbidden_keyword"),
        ("WITH x AS (SELECT 1) DELETE FROM ledger WHERE TRUE", "forbidden_keyword"),
        ("WITH RECURSIVE x AS (SELECT 1) SELECT * FROM x", "forbidden_keyword"),
        # direct dataset / table references that would bypass the owner filter
        ("SELECT * FROM topskill_analytics.fs_ledger", "table_reference"),
        ("SELECT * FROM fs_ledger", "table_reference"),
        ("SELECT * FROM FS_LEDGER", "table_reference"),
        ("SELECT * FROM TopSkill_Analytics.fs_rounds", "table_reference"),
        ("SELECT * FROM topskill_analytics_dev.fs_ledger", "table_reference"),
        ("SELECT * FROM top-skill.topskill_analytics.fs_ledger", "table_reference"),
        ("SELECT * FROM top-skill.other_dataset.secrets", "table_reference"),
        ("SELECT * FROM TOP-SKILL.other_dataset.secrets", "table_reference"),
        ("SELECT * FROM ledger AS l JOIN top-skill.ds.t AS x ON TRUE", "table_reference"),
        ("SELECT * FROM `top-skill.topskill_analytics.fs_ledger`", "quoted_identifier"),
        ("SELECT * FROM `topskill_analytics`.`fs_ledger`", "quoted_identifier"),
        ("SELECT * FROM `\\x74opskill_analytics`.ledger", "quoted_identifier"),
        ("SELECT * FROM region-asia-southeast1.INFORMATION_SCHEMA.JOBS", "forbidden_keyword"),
        ("SELECT * FROM ledger, INFORMATION_SCHEMA.TABLES", "forbidden_keyword"),
        ("SELECT * FROM ledger FOR SYSTEM_TIME AS OF CURRENT_TIMESTAMP()", "forbidden_keyword"),
        # table-valued / model / external functions
        ("SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1')", "forbidden_keyword"),
        ("SELECT * FROM ML.PREDICT(MODEL m, (SELECT 1))", "forbidden_keyword"),
        ("SELECT AI.GENERATE('x') AS y", "forbidden_keyword"),
        ("SELECT * FROM APPENDS(TABLE ledger)", "forbidden_keyword"),
        ("SELECT * FROM VECTOR_SEARCH(TABLE ledger, 'x', (SELECT 1))", "forbidden_keyword"),
        ("SELECT SESSION_USER() AS who", "forbidden_keyword"),
        # parameters and system variables (the server owns @uid)
        ("SELECT * FROM ledger WHERE @uid = @uid", "parameter"),
        ("SELECT @@project_id AS p", "parameter"),
        ("SELECT * FROM ledger WHERE id = ?", "bad_character"),
        # comments (also #legacySQL dialect switches)
        ("#legacySQL\nSELECT * FROM [top-skill:topskill_analytics.fs_ledger]", "comment"),
        ("SELECT 1 -- ) , x AS (SELECT * FROM fs_ledger", "comment"),
        ("SELECT 1 # trailing", "comment"),
        # breaking out of the wrapper
        ("SELECT 1) , evil AS (SELECT 2", "unbalanced"),
        ("SELECT (1", "unbalanced"),
        # odd input
        ("", "empty"),
        ("   ;  ", "empty"),
        ("SELECT 'unterminated", "unterminated_string"),
        ("SELECT * FROM ledger WHERE title = ＇x＇", "bad_character"),
        ("SELECT * FROM ledger$20261015", "bad_character"),
        ("VALUES (1)", "not_select"),
        ("(DELETE FROM ledger WHERE TRUE)", "not_select"),
        ("SELECT " + "1 + " * 2000 + "1", "too_long"),
    ],
)
def test_rejects_unsafe_sql(sql: str, reason: str) -> None:
    assert rejected(sql) == reason


def test_tokenizer_keeps_string_literals_whole() -> None:
    toks = tokenize("SELECT 'a;b' AS x, b\"c\" FROM ledger")
    kinds = [(t.kind, t.text) for t in toks]
    assert ("string", "'a;b'") in kinds
    assert ("string", 'b"c"') in kinds


def test_wrap_prefilters_every_table_by_uid_parameter() -> None:
    wrapped = wrap_sql(ok("SELECT COUNT(*) AS n FROM rounds"), project=PROJECT, dataset=DS)
    assert wrapped.startswith("WITH\n")
    for t in TABLES:
        assert f"{t.cte} AS (SELECT * EXCEPT (owner_uid, synced_at) FROM `{PROJECT}.{DS}.{t.physical}` " in (
            wrapped
        )
    assert wrapped.count("WHERE owner_uid = @uid") == len(TABLES)
    assert wrapped.rstrip().endswith("SELECT COUNT(*) AS n FROM rounds")


def test_wrap_merges_the_models_own_with_clause() -> None:
    sql = ok("WITH w AS (SELECT * FROM ledger) SELECT COUNT(*) AS n FROM w ORDER BY n")
    wrapped = wrap_sql(sql, project=PROJECT, dataset=DS)
    assert wrapped.count("WITH") == 1
    assert (
        "WHERE owner_uid = @uid),\nw AS (SELECT * FROM ledger) SELECT COUNT(*) AS n FROM w ORDER BY n"
        in wrapped
    )


def test_allowed_tables_are_exactly_the_physical_tables() -> None:
    allowed = sql_guard.allowed_tables(project=PROJECT, dataset=DS)
    assert allowed == {f"{PROJECT}.{DS}.{t.physical}" for t in TABLES}
