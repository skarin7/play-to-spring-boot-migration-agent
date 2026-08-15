"""tools/fetch_jar.py: cache/download/checksum-verify the pinned dev-toolkit jar.

Mirrors play-to-springboot's scripts/tools/test_tools.py::TestFetchJar --
same module, same guarantees, ported to pytest for this repo's test style.
"""

import hashlib
import json

import pytest

from agent.tools import fetch_jar


def _release_file(tmp_path, download_url, sha256, version="9.9.9"):
    release = tmp_path / "toolkit-release.json"
    release.write_text(
        json.dumps({"version": version, "download_url": download_url, "sha256": sha256}),
        encoding="utf-8",
    )
    return release


def test_cache_hit_skips_download(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    content = b"fake jar bytes"
    digest = hashlib.sha256(content).hexdigest()
    (cache_dir / "dev-toolkit-9.9.9.jar").write_bytes(content)
    # Unreachable URL proves the cache-hit path never calls download().
    release = _release_file(tmp_path, "http://127.0.0.1:1/unreachable", digest)
    jar_path = fetch_jar.fetch(release, cache_dir)
    assert jar_path == cache_dir / "dev-toolkit-9.9.9.jar"


def test_download_and_verify_success(tmp_path):
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.jar"
    content = b"a real-enough jar for this test"
    source.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    release = _release_file(tmp_path, source.resolve().as_uri(), digest)
    jar_path = fetch_jar.fetch(release, cache_dir)
    assert jar_path.read_bytes() == content


def test_checksum_mismatch_fails_loudly_and_does_not_leave_the_bad_jar(tmp_path):
    cache_dir = tmp_path / "cache"
    source = tmp_path / "source.jar"
    source.write_bytes(b"whatever bytes")
    wrong_sha = "0" * 64
    release = _release_file(tmp_path, source.resolve().as_uri(), wrong_sha)
    with pytest.raises(SystemExit):
        fetch_jar.fetch(release, cache_dir)
    assert not (cache_dir / "dev-toolkit-9.9.9.jar").exists()


def test_stale_cached_jar_is_redownloaded(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "dev-toolkit-9.9.9.jar").write_bytes(b"old wrong content")
    source = tmp_path / "source.jar"
    new_content = b"the correct new content"
    source.write_bytes(new_content)
    digest = hashlib.sha256(new_content).hexdigest()
    release = _release_file(tmp_path, source.resolve().as_uri(), digest)
    jar_path = fetch_jar.fetch(release, cache_dir)
    assert jar_path.read_bytes() == new_content


def test_missing_release_file_fails_loudly(tmp_path):
    with pytest.raises(SystemExit):
        fetch_jar.fetch(tmp_path / "nope.json", tmp_path / "cache")
