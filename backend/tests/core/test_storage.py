from __future__ import annotations

from pathlib import Path

import pytest

from topskill.errors import ApiError
from topskill.storage import BlobStore, LocalBlobStore, get_blob_store, normalize_path

pytestmark = pytest.mark.unit


async def test_local_roundtrip(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path)
    assert isinstance(store, BlobStore)
    await store.put("skills/skl_1/SKILL.md", b"hello", "text/markdown")
    await store.put("skills/skl_1/references/a.md", b"a", "text/markdown")
    await store.put("skills/skl_2/SKILL.md", b"other", "text/markdown")
    assert await store.get("skills/skl_1/SKILL.md") == b"hello"
    await store.put("skills/skl_1/SKILL.md", b"v2", "text/markdown")
    assert await store.get("skills/skl_1/SKILL.md") == b"v2"
    await store.delete("skills/skl_1/references/a.md")
    await store.delete("skills/skl_1/references/a.md")  # missing is fine
    with pytest.raises(ApiError) as err:
        await store.get("skills/skl_1/references/a.md")
    assert err.value.status == 404 and err.value.code == "not_found"
    await store.delete_prefix("skills/skl_1/")
    with pytest.raises(ApiError):
        await store.get("skills/skl_1/SKILL.md")
    assert await store.get("skills/skl_2/SKILL.md") == b"other"


@pytest.mark.parametrize("bad", ["/etc/passwd", "../x", "a/../../x", "a\\b", "", "a//b", "./a"])
async def test_paths_are_validated(tmp_path: Path, bad: str) -> None:
    store = LocalBlobStore(tmp_path)
    with pytest.raises(ValueError):
        await store.put(bad, b"x", "text/plain")
    with pytest.raises(ValueError):
        normalize_path(bad)


def test_default_store_is_local_in_tests() -> None:
    assert isinstance(get_blob_store(), LocalBlobStore)
