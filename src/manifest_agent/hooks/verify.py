"""`manifest hook verify <client>` mechanics: probe an installed client,
compare its protocol shape against the vendored fixture, and — only on an
exact match — promote the fixture to a verified, committed artifact.

No real client has ever been probed through this module. Every entry in
`config/hook-clients.json` ships `verified_version`/`verified_at`/
`protocol_probe_argv` null: the argv convention a real client would use to
emit its own event shape is unresolved (owner input required), so `verify`
can only ever report `unavailable` for the four real clients today. This
module is exercised end-to-end only against a fake client placed on a real
PATH in `tests/python/manifest_agent/hooks/test_hooks_verify.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..checks.process import run_argv

REPO_ROOT = Path(__file__).resolve().parents[3]
MATRIX_PATH = REPO_ROOT / "config" / "hook-clients.json"


def default_repo_root() -> Path:
    """`MANIFEST_HOOK_VERIFY_REPO_ROOT` lets tests point promotion writes and
    fixture lookups at an isolated tree; the repo default is this checkout."""
    override = os.environ.get("MANIFEST_HOOK_VERIFY_REPO_ROOT")
    return Path(override) if override else REPO_ROOT


def default_matrix_path() -> Path:
    """`MANIFEST_HOOK_CLIENTS_CONFIG` lets tests substitute the client
    matrix; the repo default is the committed `config/hook-clients.json`."""
    override = os.environ.get("MANIFEST_HOOK_CLIENTS_CONFIG")
    return Path(override) if override else MATRIX_PATH


Resolver = Callable[[str], "str | None"]

STATUS_VERIFIED = "verified"
STATUS_MISMATCH = "mismatch"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ClientEntry:
    """One `config/hook-clients.json` client, keyed by adapter CLIENT const."""

    key: str
    name: str
    executable_candidates: tuple[str, ...]
    version_argv: tuple[str, ...]
    version_pattern: str
    protocol_probe_argv: tuple[str, ...] | None
    verified_version: str | None
    verified_at: str | None
    fixture_dir: str
    model_labels: tuple[str, ...]


def load_matrix(matrix_path: Path = MATRIX_PATH) -> dict[str, ClientEntry]:
    """Parse `config/hook-clients.json` into `ClientEntry` records, keyed by
    the same name adapters use as `ReceiptInput.client` (e.g. `claude_code`)."""
    data = json.loads(matrix_path.read_text(encoding="utf-8"))
    entries: dict[str, ClientEntry] = {}
    for key, raw in data.get("clients", {}).items():
        probe = raw.get("protocol_probe_argv")
        entries[key] = ClientEntry(
            key=key,
            name=raw["name"],
            executable_candidates=tuple(raw["executable_candidates"]),
            version_argv=tuple(raw["version_argv"]),
            version_pattern=raw["version_pattern"],
            protocol_probe_argv=tuple(probe) if probe else None,
            verified_version=raw.get("verified_version"),
            verified_at=raw.get("verified_at"),
            fixture_dir=raw["fixture_dir"],
            model_labels=tuple(raw.get("model_labels", ())),
        )
    return entries


def default_resolver(path_env: str | None) -> Resolver:
    """An explicit, injectable resolver bound to one PATH string handed in by
    the caller — never a bare `os.environ["PATH"]` read buried in the probe
    logic below. Callers (the CLI, or a test) decide what PATH means."""

    def resolve(executable: str) -> str | None:
        return shutil.which(executable, path=path_env)

    return resolve


def resolve_executable(candidates: Sequence[str], resolver: Resolver) -> str | None:
    for candidate in candidates:
        found = resolver(candidate)
        if found:
            return found
    return None


@dataclass(frozen=True)
class VerifyConfig:
    repo_root: Path = field(default_factory=default_repo_root)
    matrix_path: Path = field(default_factory=default_matrix_path)
    resolver: Resolver = field(default_factory=lambda: default_resolver(None))
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class VerifyResult:
    status: str  # "verified" | "mismatch" | "unavailable"
    client: str
    executable: str | None
    version: str | None
    differences: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "client": self.client,
            "executable": self.executable,
            "version": self.version,
            "differences": list(self.differences),
            "reason": self.reason,
        }


def probe_version(
    executable: str, entry: ClientEntry, *, cwd: Path, timeout_seconds: float
) -> str | None:
    result = run_argv(
        (executable, *entry.version_argv),
        cwd=cwd,
        env={},
        timeout_seconds=timeout_seconds,
    )
    if result.timed_out or result.error:
        return None
    text = (result.stdout or "") + (result.stderr or "")
    match = re.search(entry.version_pattern, text)
    return match.group(1) if match else None


def probe_protocol(
    executable: str, entry: ClientEntry, *, cwd: Path, timeout_seconds: float
) -> dict | None:
    if not entry.protocol_probe_argv:
        return None
    result = run_argv(
        (executable, *entry.protocol_probe_argv),
        cwd=cwd,
        env={},
        timeout_seconds=timeout_seconds,
    )
    if result.timed_out or result.error:
        return None
    try:
        value = json.loads(result.stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _type_name(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def compare_shapes(expected: dict, actual: dict, *, prefix: str = "") -> list[str]:
    """Field-level differences between a vendored fixture and a live probe:
    missing fields, unexpected fields, and type mismatches — never a value
    diff, since fixture values are illustrative, not literal expectations."""
    differences: list[str] = []
    for key, expected_value in expected.items():
        path = f"{prefix}{key}"
        if key not in actual:
            differences.append(f"missing field {path!r}")
            continue
        actual_value = actual[key]
        expected_type = _type_name(expected_value)
        actual_type = _type_name(actual_value)
        if expected_type != actual_type:
            differences.append(
                f"field {path!r} expected type {expected_type}, got {actual_type}"
            )
        elif expected_type == "object":
            differences.extend(
                compare_shapes(expected_value, actual_value, prefix=f"{path}.")
            )
    for key in actual:
        if key not in expected:
            differences.append(f"unexpected field {prefix}{key!r}")
    return differences


def _primary_fixture(entry: ClientEntry, repo_root: Path) -> dict | None:
    directory = repo_root / entry.fixture_dir
    files = sorted(p for p in directory.glob("*.json"))
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8"))


def verify_client(
    client: str, entry: ClientEntry, config: VerifyConfig
) -> VerifyResult:
    """Probe, then compare -- the only place all three outcomes are decided."""
    executable = resolve_executable(entry.executable_candidates, config.resolver)
    if executable is None:
        return VerifyResult(
            STATUS_UNAVAILABLE,
            client,
            None,
            None,
            reason=f"no executable found among {list(entry.executable_candidates)}",
        )
    version = probe_version(
        executable, entry, cwd=config.repo_root, timeout_seconds=config.timeout_seconds
    )
    if version is None:
        return VerifyResult(
            STATUS_UNAVAILABLE,
            client,
            executable,
            None,
            reason="version probe produced no match for version_pattern",
        )
    if not entry.protocol_probe_argv:
        return VerifyResult(
            STATUS_UNAVAILABLE,
            client,
            executable,
            version,
            reason="no protocol_probe_argv configured (owner input required)",
        )
    expected = _primary_fixture(entry, config.repo_root)
    if expected is None:
        return VerifyResult(
            STATUS_UNAVAILABLE,
            client,
            executable,
            version,
            reason=f"no vendored fixture under {entry.fixture_dir}",
        )
    actual = probe_protocol(
        executable, entry, cwd=config.repo_root, timeout_seconds=config.timeout_seconds
    )
    if actual is None:
        return VerifyResult(
            STATUS_UNAVAILABLE,
            client,
            executable,
            version,
            reason="protocol probe produced no parseable JSON object",
        )
    differences = compare_shapes(expected, actual)
    if differences:
        return VerifyResult(
            STATUS_MISMATCH, client, executable, version, differences=tuple(differences)
        )
    return VerifyResult(STATUS_VERIFIED, client, executable, version)


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def promote(
    result: VerifyResult,
    entry: ClientEntry,
    config: VerifyConfig,
    *,
    now: str | None = None,
) -> Path:
    """On a `verified` result only: copy the fixture into a version-pinned,
    committable directory with a SOURCE.md recording how and when, then
    update the matrix's verified_version/verified_at/fixture_dir. This is the
    only writer of those three fields."""
    if result.status != STATUS_VERIFIED or not result.version:
        raise ValueError("promote requires a verified VerifyResult with a version")
    timestamp = now or _utc_now_iso()
    source_dir = config.repo_root / entry.fixture_dir
    target_dir = (
        config.repo_root / "tests" / "fixtures" / "hooks" / entry.name / result.version
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(source_dir.glob("*.json")):
        (target_dir / path.name).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    relative_target = target_dir.relative_to(config.repo_root).as_posix()
    source_md = (
        f"# {entry.name} hook fixtures -- source: verified\n\n"
        f"Verified by `manifest hook verify {result.client} --write` against the "
        f"installed executable `{result.executable}` (version `{result.version}`) "
        f"on {timestamp}.\n\n"
        f"The client's live protocol probe matched `{entry.fixture_dir}` with zero "
        "field-level differences (see `verify.compare_shapes`).\n"
    )
    (target_dir / "SOURCE.md").write_text(source_md, encoding="utf-8")
    _update_matrix(
        config.matrix_path, result.client, result.version, timestamp, relative_target
    )
    return target_dir


def _update_matrix(
    matrix_path: Path, client: str, version: str, timestamp: str, fixture_dir: str
) -> None:
    data = json.loads(matrix_path.read_text(encoding="utf-8"))
    data["clients"][client]["verified_version"] = version
    data["clients"][client]["verified_at"] = timestamp
    data["clients"][client]["fixture_dir"] = fixture_dir
    matrix_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def is_promotion_recorded(client: str, version: str, config: VerifyConfig) -> bool:
    """The single source of truth for whether `client_version_verified` may
    ever be `true` for this client+version. Every one of these independent
    signals must agree, or the answer is `False`:

    1. the matrix names THIS exact client key with THIS exact verified_version
       and a non-empty verified_at:
    2. `config.repo_root / entry.fixture_dir` exists and contains a SOURCE.md;
    3. that SOURCE.md's text names both this client and this version, and its
       header says "verified" rather than "unverified".

    Matching on the client key (not merely "some fixture dir exists") is what
    stops a verified result for one client from promoting a different one:
    client B's matrix entry never gets client A's verified_version/verified_at,
    and even if a SOURCE.md were manually copied across client directories, its
    embedded client name would not match the client key being checked.
    """
    try:
        entries = load_matrix(config.matrix_path)
    except (OSError, ValueError, KeyError):
        return False
    entry = entries.get(client)
    if entry is None or entry.name != client:
        return False
    if entry.verified_version != version or not entry.verified_at:
        return False
    source_doc = config.repo_root / entry.fixture_dir / "SOURCE.md"
    if not source_doc.is_file():
        return False
    try:
        text = source_doc.read_text(encoding="utf-8")
    except OSError:
        return False
    header = text.splitlines()[0].lower() if text.splitlines() else ""
    if "unverified" in header or "verified" not in header:
        return False
    return client in text and version in text
