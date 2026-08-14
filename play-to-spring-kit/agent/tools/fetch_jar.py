"""Fetch, checksum-verify, and cache the dev-toolkit jar this kit depends on.

Mirrors play-to-springboot's ``scripts/tools/fetch_jar.py`` (same
``toolkit-release.json`` shape: ``{version, download_url, sha256}``, same
verify-cache-or-download-and-verify logic), pointed at
``play-to-spring-kit/lib/`` instead of a plugin data dir -- that's where
``agent/config.py``'s ``jar_path`` default and ``agent/tools/setup_ops.py``
already expect to find ``dev-toolkit-<version>.jar``.

This replaces vendoring the jar's bytes into git: the pinned sha256 in
``toolkit-release.json`` is the source of truth, and this module either
reuses a cache hit that matches it or downloads+verifies a fresh copy,
refusing to hand back anything that doesn't match.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_release(release_file: Path) -> dict[str, str]:
    if not release_file.is_file():
        raise SystemExit(f"ERROR: no release pin at {release_file}")
    data = json.loads(release_file.read_text(encoding="utf-8"))
    missing = [k for k in ("version", "download_url", "sha256") if not data.get(k)]
    if missing:
        raise SystemExit(
            f"ERROR: {release_file} is missing required field(s): {', '.join(missing)}"
        )
    return data


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
    except (urllib.error.URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"ERROR: failed to download dev-toolkit jar from {url}: {e}")
    tmp.replace(dest)


def fetch(release_file: Path, cache_dir: Path) -> Path:
    """Return a path to the pinned dev-toolkit jar, cached under cache_dir.

    Downloads (and checksum-verifies) only on a cache miss or mismatch.
    """
    release = load_release(release_file)
    version = release["version"]
    expected_sha256 = release["sha256"].lower()
    jar_path = cache_dir / f"dev-toolkit-{version}.jar"

    if jar_path.is_file():
        actual = sha256_of(jar_path)
        if actual == expected_sha256:
            return jar_path
        print(
            f"[warn] cached {jar_path} does not match pinned sha256 "
            f"(expected {expected_sha256}, got {actual}); re-downloading.",
            file=sys.stderr,
        )
        jar_path.unlink()

    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading dev-toolkit {version} from {release['download_url']}...", file=sys.stderr)
    download(release["download_url"], jar_path)

    actual = sha256_of(jar_path)
    if actual != expected_sha256:
        jar_path.unlink()
        raise SystemExit(
            f"ERROR: downloaded dev-toolkit-{version}.jar sha256 mismatch "
            f"(expected {expected_sha256}, got {actual}). Refusing to use an "
            f"unverified jar; deleted the download."
        )

    return jar_path
