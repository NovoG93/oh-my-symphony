"""Content-addressed-ish artifact storage for governed workflow nodes.

Every node's output lands on disk under `<root>/<run_id>/<node_id>/<file>`,
and only a row describing it — relative path, media type, size, sha256 —
goes into SQLite (`GovernedRunStore.record_artifact`). The database stays
small and the ledger stays verifiable: `verify()` re-hashes the bytes, so a
run whose workspace was edited behind Symphony's back fails integrity
instead of silently resuming.

Deliberate asymmetry, because it surprises everyone who reads both layers:
**content written here is NOT redacted.** `symphony.flow.redaction` runs at
the SQLite/UI boundary only. An artifact is the operator's primary evidence
— truncating or rewriting an agent transcript would destroy the very thing
they opened it to read — and it lives inside the same workspace the agent
already had write access to, so redacting it buys no containment. Previews
and event payloads derived *from* these bytes must still go through
`redact_and_cap` before they are persisted or rendered.

Path handling is hostile-input-first: filenames and ids come from workflow
YAML and from agent output, so every component is sanitized and every
resulting path is re-checked against `root` before it is touched.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..errors import ArtifactNotFound, SymphonyError, UnsafePath


# One artifact may not exceed this. Larger output is a bug (an agent
# dumping a binary, a runaway log) and the right answer is to fail loudly
# at the write, not to truncate — a half-written transcript that still
# hashes fine is worse evidence than no transcript.
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024

# Recognisable so `sweep_temp_files` can distinguish our orphans from a
# user's own dotfiles. `mkstemp` appends randomness to this.
TEMP_PREFIX = ".tmp-symphony-artifact-"

_MAX_COMPONENT_CHARS = 120
_MAX_SUFFIX_CHARS = 10
_HASH_CHUNK_BYTES = 1 << 16

_DEFAULT_FILENAME = "artifact"
_DEFAULT_RUN_ID = "unknown-run"
_DEFAULT_NODE_ID = "unknown-node"

# Everything outside this set — path separators, spaces, shell characters,
# non-ASCII — is replaced rather than dropped, so two distinct inputs stay
# distinct instead of colliding on the empty string.
_DISALLOWED_CHARS = re.compile(r"[^A-Za-z0-9._+@-]")
_DOT_RUN = re.compile(r"\.\.+")


@dataclass(frozen=True)
class StoredArtifact:
    """What the caller hands to `GovernedRunStore.record_artifact`."""

    relative_path: str
    absolute_path: Path
    media_type: str
    size_bytes: int
    sha256: str


def _cap_length(name: str, limit: int) -> str:
    """Truncate, keeping a short extension so media type stays guessable."""
    if len(name) <= limit:
        return name
    stem, dot, suffix = name.rpartition(".")
    if dot and 0 < len(suffix) <= _MAX_SUFFIX_CHARS:
        keep = max(1, limit - len(suffix) - 1)
        return f"{stem[:keep]}.{suffix}"
    return name[:limit]


def _sanitize_component(raw: str, *, fallback: str) -> str:
    """Reduce caller-supplied text to a single safe path component.

    Strips path separators, `..`, control characters and leading dots (a
    leading dot would hide the file and could imitate `TEMP_PREFIX`, making
    a real artifact look like a sweepable orphan).
    """
    text = "".join(ch if ch.isprintable() else "-" for ch in raw.strip())
    text = _DISALLOWED_CHARS.sub("-", text)
    text = _DOT_RUN.sub(".", text)
    text = _cap_length(text.strip("-. "), _MAX_COMPONENT_CHARS)
    return text.strip("-. ") or fallback


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class ArtifactStore:
    """Confined, atomic file storage rooted at one directory."""

    def __init__(self, root: Path) -> None:
        base = Path(root).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        # Resolve *after* creating: a root under a symlinked parent must
        # compare equal to the resolved paths every later check produces,
        # or every write would look like an escape.
        self._root = base.resolve()

    @property
    def root(self) -> Path:
        return self._root

    # --- path confinement --------------------------------------------------

    def _confine(self, candidate: Path, *, follow_final: bool) -> Path:
        """Return `candidate` proven to live under `root`.

        Two independent checks, because either alone has a hole: the
        lexical check catches `..` on paths that do not exist yet (where
        `resolve()` cannot help), and the symlink walk catches a component
        that exists and points out of the tree.
        """
        absolute = candidate if candidate.is_absolute() else self._root / candidate
        lexical = Path(os.path.normpath(str(absolute)))
        if not _is_within(lexical, self._root):
            raise UnsafePath(
                "artifact path escapes the artifact root",
                path=str(candidate),
                root=str(self._root),
            )
        self._reject_escaping_symlinks(lexical, include_final=follow_final)
        if not follow_final:
            return lexical
        resolved = lexical.resolve()
        if not _is_within(resolved, self._root):
            raise UnsafePath(
                "artifact path resolves outside the artifact root",
                path=str(candidate),
                resolved=str(resolved),
                root=str(self._root),
            )
        return resolved

    def _reject_escaping_symlinks(self, lexical: Path, *, include_final: bool) -> None:
        """Refuse any existing component that is a symlink leaving `root`.

        Walked component by component from the root down, so a symlink two
        directories up is caught even when the leaf does not exist yet.
        """
        parts = lexical.relative_to(self._root).parts
        current = self._root
        for index, part in enumerate(parts):
            current = current / part
            is_final = index == len(parts) - 1
            if not current.is_symlink():
                continue
            if is_final and not include_final:
                # Writes and deletes never go *through* a symlink, even one
                # that lands back inside the root: `os.replace` onto a link
                # and `rmtree` of a link are both surprises we do not want.
                raise UnsafePath(
                    "artifact destination is a symlink",
                    path=str(lexical),
                    root=str(self._root),
                )
            target = current.resolve()
            if not _is_within(target, self._root):
                raise UnsafePath(
                    "artifact path traverses a symlink pointing outside the root",
                    path=str(lexical),
                    link=str(current),
                    target=str(target),
                    root=str(self._root),
                )

    # --- directories -------------------------------------------------------

    def node_dir(self, run_id: str, node_id: str) -> Path:
        """Absolute directory for one node's output. Does not create it."""
        safe_run = _sanitize_component(run_id, fallback=_DEFAULT_RUN_ID)
        safe_node = _sanitize_component(node_id, fallback=_DEFAULT_NODE_ID)
        return self._confine(self._root / safe_run / safe_node, follow_final=False)

    def relative_node_dir(self, run_id: str, node_id: str) -> str:
        return self.node_dir(run_id, node_id).relative_to(self._root).as_posix()

    def _ensure_node_dir(self, run_id: str, node_id: str) -> Path:
        directory = self.node_dir(run_id, node_id)
        directory.mkdir(parents=True, exist_ok=True)
        # Re-check: mkdir raced with someone planting a symlink is
        # far-fetched, but the check is one stat and the failure mode is
        # writing agent output outside the run tree.
        return self._confine(directory, follow_final=False)

    # --- writes ------------------------------------------------------------

    def write_text(
        self,
        *,
        run_id: str,
        node_id: str,
        filename: str,
        content: str,
        media_type: str = "text/plain",
    ) -> StoredArtifact:
        """Persist UTF-8 text verbatim — see the module note on redaction."""
        return self._write(
            run_id=run_id,
            node_id=node_id,
            filename=filename,
            payload=content.encode("utf-8"),
            media_type=media_type,
        )

    def write_bytes(
        self,
        *,
        run_id: str,
        node_id: str,
        filename: str,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> StoredArtifact:
        return self._write(
            run_id=run_id,
            node_id=node_id,
            filename=filename,
            payload=content,
            media_type=media_type,
        )

    def _write(
        self,
        *,
        run_id: str,
        node_id: str,
        filename: str,
        payload: bytes,
        media_type: str,
    ) -> StoredArtifact:
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise SymphonyError(
                f"artifact {filename!r} is {len(payload)} bytes, over the "
                f"{MAX_ARTIFACT_BYTES}-byte limit",
                run_id=run_id,
                node_id=node_id,
                size_bytes=len(payload),
                limit=MAX_ARTIFACT_BYTES,
            )
        directory = self._ensure_node_dir(run_id, node_id)
        safe_name = _sanitize_component(filename, fallback=_DEFAULT_FILENAME)
        destination = self._confine(directory / safe_name, follow_final=False)

        # Atomic publish: readers (the web UI, integrity checks) either see
        # the previous bytes or the complete new ones, never a partial file.
        # `mkstemp` in the destination directory keeps the rename on one
        # filesystem, and creates the file 0600 — artifacts can quote
        # anything the agent saw, so a private mode is the right default.
        handle_fd, temp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=str(directory))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle_fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, destination)
        except BaseException:
            _unlink_quietly(temp_path)
            raise

        return StoredArtifact(
            relative_path=destination.relative_to(self._root).as_posix(),
            absolute_path=destination,
            media_type=media_type,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    # --- reads -------------------------------------------------------------

    def resolve(self, relative_path: str) -> Path:
        """Absolute path for a stored artifact.

        Raises `UnsafePath` when the path leaves the root (checked first,
        so a hostile path is reported as hostile rather than as missing)
        and `ArtifactNotFound` when nothing is there.
        """
        resolved = self._confine(Path(relative_path), follow_final=True)
        if not resolved.exists():
            raise ArtifactNotFound(
                "artifact is not on disk",
                relative_path=relative_path,
                root=str(self._root),
            )
        return resolved

    def verify(self, relative_path: str, expected_sha256: str) -> bool:
        """Whether the stored bytes still hash to `expected_sha256`.

        A missing artifact is a `False`, not an exception — the caller is
        asking a yes/no integrity question and both answers mean "do not
        resume". An escaping path still raises: that is a caller bug.
        """
        try:
            path = self.resolve(relative_path)
        except ArtifactNotFound:
            return False
        try:
            actual = _sha256_file(path)
        except OSError:
            return False
        return actual == expected_sha256.strip().lower()

    # --- maintenance -------------------------------------------------------

    def sweep_temp_files(self, *, older_than_seconds: float = 3600) -> int:
        """Delete orphaned `TEMP_PREFIX` files; return how many went.

        A crash between `mkstemp` and `os.replace` leaves one behind. The
        age floor keeps the sweep from racing a write that is in flight in
        another process.
        """
        cutoff = time.time() - max(0.0, older_than_seconds)
        removed = 0
        for dirpath, _dirnames, filenames in os.walk(self._root):
            for name in filenames:
                if not name.startswith(TEMP_PREFIX):
                    continue
                candidate = Path(dirpath) / name
                try:
                    if candidate.is_symlink() or candidate.stat().st_mtime > cutoff:
                        continue
                    candidate.unlink()
                except OSError:
                    continue
                removed += 1
        return removed

    def remove_run(self, run_id: str) -> int:
        """Delete one run's whole artifact tree; return bytes freed."""
        safe_run = _sanitize_component(run_id, fallback=_DEFAULT_RUN_ID)
        run_dir = self._confine(self._root / safe_run, follow_final=False)
        if run_dir == self._root:
            raise UnsafePath(
                "refusing to delete the artifact root itself",
                run_id=run_id,
                root=str(self._root),
            )
        if not run_dir.is_dir() or run_dir.is_symlink():
            return 0
        freed = self._tree_bytes(run_dir)
        shutil.rmtree(run_dir, ignore_errors=True)
        return freed

    @staticmethod
    def _tree_bytes(directory: Path) -> int:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(directory):
            for name in filenames:
                try:
                    # `lstat`: a symlink's own size, never its target's —
                    # the target may well be outside the tree.
                    total += (Path(dirpath) / name).lstat().st_size
                except OSError:
                    continue
        return total
