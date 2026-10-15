"""``/api/skills`` on the emulator: folder, zip and single-file uploads, ownership, previews."""

from __future__ import annotations

import httpx
import pytest

from tests.publish.helpers import SAMPLE_SKILL_DIR, creator, folder_files, folder_zip, visitor
from topskill.skills import load_skill_refs, read_skill_body, read_skill_file

pytestmark = pytest.mark.emulator


async def test_folder_upload_strips_top_dir_and_stores_files(client: httpx.AsyncClient) -> None:
    uid, headers = creator()
    resp = await client.post("/api/skills", files=folder_files(), headers=headers)
    assert resp.status_code == 201, resp.text
    skill = resp.json()
    assert set(skill) == {"id", "slug", "name", "description", "body_chars", "files", "created_at"}
    assert skill["id"].startswith("skl_") and skill["slug"] == "sample-skill"
    assert skill["name"] == "Sample Skill"
    assert skill["description"] == "A small travel-insurance playbook used by the publish tests."
    assert [f["path"] for f in skill["files"]] == ["SKILL.md", "references/levels.md"]
    assert all(f["is_text"] for f in skill["files"]) and skill["created_at"].endswith("Z")
    assert skill["body_chars"] > 50

    refs = await load_skill_refs(uid, [skill["id"]])
    assert (await read_skill_body(refs[0])).startswith("# Travel insurance helper")
    levels = await read_skill_file(refs[0], "references/levels.md")
    assert levels == (SAMPLE_SKILL_DIR / "references" / "levels.md").read_bytes()


async def test_zip_and_single_file_uploads(client: httpx.AsyncClient) -> None:
    _, headers = creator()
    resp = await client.post(
        "/api/skills", files={"file": ("pkg.zip", folder_zip(), "application/zip")}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["slug"] == "sample-skill"

    resp = await client.post(
        "/api/skills",
        files=[("files", ("SKILL.md", b"---\nname: Quick Helper\n---\nAnswer briefly.", "text/markdown"))],
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Quick Helper" and resp.json()["slug"] == "quick-helper"


@pytest.mark.parametrize(
    "files",
    [
        [("files", ("notes.md", b"no skill here", "text/markdown"))],
        [("files", ("../SKILL.md", b"x", "text/markdown"))],
        {"file": ("broken.zip", b"PK\x03\x04garbage", "application/zip")},
    ],
)
async def test_invalid_packages_rejected(client: httpx.AsyncClient, files) -> None:
    _, headers = creator()
    resp = await client.post("/api/skills", files=files, headers=headers)
    assert resp.status_code == 400 and resp.json()["code"] == "skill_invalid", resp.text
    assert resp.json()["trace_id"]


async def test_no_files_and_oversized_rejected(client: httpx.AsyncClient) -> None:
    _, headers = creator()
    resp = await client.post("/api/skills", data={"x": "1"}, files={"other": ("a", b"b")}, headers=headers)
    assert resp.status_code == 400 and resp.json()["code"] == "skill_invalid"
    big = [
        ("files", ("SKILL.md", b"x", "text/markdown")),
        ("files", ("big.bin", b"0" * (2 * 1024 * 1024 + 1))),
    ]
    resp = await client.post("/api/skills", files=big, headers=headers)
    assert resp.status_code == 413 and resp.json()["code"] == "payload_too_large"


async def test_list_get_and_ownership(client: httpx.AsyncClient) -> None:
    _, headers = creator()
    first = (await client.post("/api/skills", files=folder_files(), headers=headers)).json()
    second = (await client.post("/api/skills", files=folder_files(), headers=headers)).json()
    listed = (await client.get("/api/skills", headers=headers)).json()["items"]
    assert [s["id"] for s in listed] == [second["id"], first["id"]]
    assert "body_preview" not in listed[0]

    detail = (await client.get(f"/api/skills/{first['id']}", headers=headers)).json()
    assert detail["body_preview"].startswith("# Travel insurance helper")
    assert "---" not in detail["body_preview"][:3]

    _, other = creator()
    resp = await client.get(f"/api/skills/{first['id']}", headers=other)
    assert resp.status_code == 404 and resp.json()["code"] == "skill_not_found"
    assert (await client.get("/api/skills", headers=other)).json()["items"] == []


async def test_creator_only(client: httpx.AsyncClient) -> None:
    _, headers = visitor()
    resp = await client.post("/api/skills", files=folder_files(), headers=headers)
    assert resp.status_code == 403 and resp.json()["code"] == "creator_required"
    assert (await client.get("/api/skills")).status_code == 401
