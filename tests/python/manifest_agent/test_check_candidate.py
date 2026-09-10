"""Real Git contracts for complete disposable candidate materialization."""

import io
import os
import subprocess
import sys
import tarfile

import pytest


def git(root, *args, input=None):
    env = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    return subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *args,
        ],
        input=input,
        env=env,
        capture_output=True,
        check=True,
    ).stdout


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    for name in ("staged", "unstaged", "deleted", "mode", "head-only.txt"):
        (root / name).write_text("base\n")
    (root / ".gitignore").write_text(".apm/\ncache/\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD").decode().strip()
    (root / "head-only.txt").write_text("from current HEAD\n")
    git(root, "commit", "-qam", "head")
    (root / "staged").write_text("staged edit\n")
    git(root, "add", "staged")
    (root / "unstaged").write_text("unstaged edit\n")
    (root / "deleted").unlink()
    (root / "untracked.txt").write_text("untracked\n")
    (root / "line\nbreak").write_text("newline filename\n")
    (root / "mode").chmod(0o751)
    (root / "link").symlink_to("head-only.txt")
    return root, base


def materialize(source, tmp_path):
    from manifest_agent.checks import materialize_candidate

    return materialize_candidate(*source, tmp_path / "candidate")


def test_complete_candidate_preserves_source_and_current_head(source, tmp_path):
    root, base = source
    before = (root / ".git/index").read_bytes()
    status = git(root, "status", "--porcelain=v1", "-z")
    candidate = materialize(source, tmp_path)
    assert (root / ".git/index").read_bytes() == before
    assert git(root, "status", "--porcelain=v1", "-z") == status
    assert candidate.base_sha == base
    assert candidate.head_sha == git(root, "rev-parse", "HEAD").decode().strip()
    assert (candidate.root / "head-only.txt").read_text() == "from current HEAD\n"
    assert (candidate.root / "staged").read_text() == "staged edit\n"
    assert (candidate.root / "unstaged").read_text() == "unstaged edit\n"
    assert not (candidate.root / "deleted").exists()
    # "mode" was chmod'd 0o751 on disk AFTER the commit, with no `git add` to
    # stage it: Git still records it 100644 (C7f). The candidate must match
    # what Git recorded, not the source worktree's unstaged on-disk drift.
    assert (candidate.root / "mode").stat().st_mode & 0o777 == 0o644
    assert (
        git(candidate.root, "ls-files", "--stage", "mode")
        == f"100644 {git(root, 'rev-parse', 'HEAD:mode').decode().strip()} 0\tmode\n".encode()
    )
    assert os.readlink(candidate.root / "link") == "head-only.txt"
    assert (candidate.root / "staged").stat().st_ino != (root / "staged").stat().st_ino
    assert candidate.root.stat().st_mode & 0o777 == 0o700
    paths = git(candidate.root, "ls-files", "-z").split(b"\0")
    assert b"line\nbreak" in paths and b"untracked.txt" in paths
    assert b"deleted" not in paths
    archive = git(candidate.root, "archive", candidate.tree_sha)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        assert bundle.extractfile("head-only.txt").read() == b"from current HEAD\n"
        assert bundle.extractfile("untracked.txt").read() == b"untracked\n"
        assert "deleted" not in bundle.getnames()
    assert {"head-only.txt", "staged", "unstaged", "deleted", "untracked.txt"} <= set(
        candidate.changed_paths
    )
    for revision in (candidate.base_sha, candidate.head_sha):
        assert git(candidate.root, "cat-file", "-t", revision) == b"commit\n"
    assert not (candidate.root / ".git/objects/info/alternates").exists()


def test_candidate_file_modes_match_git_not_worktree_permission_drift(tmp_path):
    """A committed file's mode always matches Git's 100644/100755, even when
    the source worktree's on-disk permission bits disagree (C7f): a
    materialization bug once let a 100644 file's stray on-disk executable
    bit leak into the candidate, so hook.check-executables-have-shebangs
    flagged docs/SHARED_CHECKS.md as executable although git ls-files
    recorded 100644.
    """
    from manifest_agent.checks import materialize_candidate

    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "plain.txt").write_text("data\n")
    (root / "script.sh").write_text("#!/bin/sh\necho hi\n")
    (root / "script.sh").chmod(0o755)
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    assert git(root, "ls-files", "--stage", "plain.txt").split()[0] == b"100644"
    assert git(root, "ls-files", "--stage", "script.sh").split()[0] == b"100755"

    # Drift the on-disk bits away from what Git recorded, on BOTH files, with
    # nothing re-staged: a 100644 file gains a stray executable bit, and a
    # 100755 file loses its executable bit.
    (root / "plain.txt").chmod(0o755)
    (root / "script.sh").chmod(0o644)

    candidate = materialize_candidate(
        root, git(root, "rev-parse", "HEAD").decode().strip(), tmp_path / "candidate"
    )

    assert (candidate.root / "plain.txt").stat().st_mode & 0o777 == 0o644
    assert (candidate.root / "script.sh").stat().st_mode & 0o777 == 0o755
    assert (
        git(candidate.root, "ls-files", "--stage", "plain.txt").split()[0] == b"100644"
    )
    assert (
        git(candidate.root, "ls-files", "--stage", "script.sh").split()[0] == b"100755"
    )


@pytest.mark.parametrize("target", ["/tmp", "../outside", "bridge/secret"])
def test_unsafe_and_transitive_symlinks_block(source, tmp_path, target):
    from manifest_agent.checks import CandidateBlockedError

    (source[0] / "link").unlink()
    (source[0] / "link").symlink_to(target)
    if target.startswith("bridge"):
        (source[0] / "bridge").symlink_to("../outside")
    with pytest.raises(CandidateBlockedError):
        materialize(source, tmp_path)


def test_special_file_blocks_without_hanging(source, tmp_path):
    from manifest_agent.checks import CandidateBlockedError

    os.mkfifo(source[0] / "pipe")
    with pytest.raises(CandidateBlockedError):
        materialize(source, tmp_path)


@pytest.mark.parametrize(
    "case", ["inside", "nonempty", "symlink-parent", "not-git", "bad-base"]
)
def test_invalid_materialization_boundaries_block(source, tmp_path, case):
    from manifest_agent.checks import CandidateBlockedError, materialize_candidate

    root, base = source
    destination = tmp_path / "candidate"
    if case == "inside":
        destination = root / "candidate"
    elif case == "nonempty":
        destination.mkdir()
        (destination / "keep").write_text("keep")
    elif case == "symlink-parent":
        (tmp_path / "alias").symlink_to(root, target_is_directory=True)
        destination = tmp_path / "alias/candidate"
    elif case == "not-git":
        root = tmp_path
    else:
        base = "missing-revision"
    with pytest.raises(CandidateBlockedError):
        materialize_candidate(root, base, destination)


def test_conflicted_index_blocks(source, tmp_path):
    from manifest_agent.checks import CandidateBlockedError

    root, _ = source
    blob = git(root, "hash-object", "-w", "--stdin", input=b"conflict").strip()
    git(
        root,
        "update-index",
        "--index-info",
        input=b"0 "
        + b"0" * 40
        + b"\tstaged\n100644 "
        + blob
        + b" 1\tstaged\n100644 "
        + blob
        + b" 2\tstaged\n",
    )
    with pytest.raises(CandidateBlockedError):
        materialize(source, tmp_path)


@pytest.mark.parametrize("change", ["bytes", "mode", "link", "add", "remove", "index"])
def test_digest_tracks_all_source_identities(source, change):
    from manifest_agent.checks import candidate_digest

    root, _ = source
    before = candidate_digest(root)
    if change == "bytes":
        (root / "staged").write_text("different\n")
    elif change == "mode":
        (root / "staged").chmod(0o600)
    elif change == "link":
        (root / "link").unlink()
        (root / "link").symlink_to("staged")
    elif change == "add":
        (root / "added").write_text("new")
    elif change == "remove":
        (root / "untracked.txt").unlink()
    else:
        git(root, "add", "unstaged")
    assert candidate_digest(root) != before


@pytest.mark.parametrize("state", ["clean", "dirty", "missing", "wrong-pin"])
def test_required_submodule_preserves_gitlink_and_blocks_invalid_state(
    source, tmp_path, state
):
    from manifest_agent.checks import CandidateBlockedError

    root, _ = source
    module = root / "module"
    module.mkdir()
    git(module, "init", "-q")
    (module / "module.txt").write_text("pinned content")
    git(module, "add", ".")
    git(module, "commit", "-qm", "module")
    pin = git(module, "rev-parse", "HEAD").decode().strip()
    git(root, "update-index", "--add", "--cacheinfo", "160000", pin, "module")
    (root / ".gitmodules").write_text(
        '[submodule "module"]\npath = module\nurl = https://invalid.example/never-fetch\n'
    )
    git(root, "add", ".gitmodules")
    if state == "dirty":
        (module / "module.txt").write_text("dirty")
    elif state == "missing":
        (module / ".git").rename(tmp_path / "module-git")
    elif state == "wrong-pin":
        (module / "module.txt").write_text("next")
        git(module, "commit", "-qam", "next")
    if state != "clean":
        with pytest.raises(CandidateBlockedError):
            materialize(source, tmp_path)
        return
    candidate = materialize(source, tmp_path)
    assert (candidate.root / "module/module.txt").read_text() == "pinned content"
    assert not (candidate.root / "module/.git").exists()
    assert (
        git(candidate.root, "ls-files", "--stage", "module")
        == f"160000 {pin} 0\tmodule\n".encode()
    )


@pytest.mark.parametrize("change", ["add", "remove", "bytes", "mode", "link", "index"])
def test_concurrent_source_change_blocks_after_copy(
    source, tmp_path, monkeypatch, change
):
    import manifest_agent.checks.candidate as implementation

    original = implementation._copy

    def copy_then_change(root, destination, files):
        original(root, destination, files)
        if change == "add":
            (root / "arrived\nlate").write_text("late")
        elif change == "remove":
            (root / "untracked.txt").unlink()
        elif change == "bytes":
            (root / "staged").write_text("late")
        elif change == "mode":
            (root / "staged").chmod(0o600)
        elif change == "link":
            (root / "link").unlink()
            (root / "link").symlink_to("staged")
        else:
            git(root, "add", "unstaged")

    monkeypatch.setattr(implementation, "_copy", copy_then_change)
    with pytest.raises(implementation.CandidateBlockedError):
        materialize(source, tmp_path)


def test_digest_rejects_unreadable_files(source):
    from manifest_agent.checks import CandidateBlockedError, candidate_digest

    path = source[0] / "staged"
    path.chmod(0)
    try:
        with pytest.raises(CandidateBlockedError):
            candidate_digest(source[0])
    finally:
        path.chmod(0o644)


def test_ambient_git_configuration_cannot_redirect_source(
    source, tmp_path, monkeypatch
):
    from manifest_agent.checks import candidate_digest

    before = candidate_digest(source[0])
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "foreign-index"))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign-git"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    assert candidate_digest(source[0]) == before
    materialize(source, tmp_path)
    assert not (tmp_path / "foreign-index").exists()


def test_candidate_archive_does_not_hide_export_ignored_inputs(source, tmp_path):
    (source[0] / ".gitattributes").write_text("head-only.txt export-ignore\n")
    candidate = materialize(source, tmp_path)
    with tarfile.open(
        fileobj=io.BytesIO(git(candidate.root, "archive", candidate.tree_sha))
    ) as bundle:
        assert bundle.extractfile("head-only.txt").read() == b"from current HEAD\n"


def test_missing_nested_submodule_blocks_even_when_git_status_ignores_it(
    source, tmp_path
):
    from manifest_agent.checks import CandidateBlockedError

    root = source[0]
    module = root / "module"
    module.mkdir()
    git(module, "init", "-q")
    (module / "file").write_text("module")
    git(module, "add", ".")
    git(module, "commit", "-qm", "module")
    pin = git(module, "rev-parse", "HEAD").decode().strip()
    git(module, "update-index", "--add", "--cacheinfo", "160000", pin, "nested")
    (module / ".gitmodules").write_text(
        '[submodule "nested"]\npath = nested\nurl = https://invalid.example/never\nignore = all\n'
    )
    git(module, "add", ".gitmodules")
    git(module, "commit", "-qm", "nested pin")
    pin = git(module, "rev-parse", "HEAD").decode().strip()
    git(root, "update-index", "--add", "--cacheinfo", "160000", pin, "module")
    with pytest.raises(CandidateBlockedError):
        materialize(source, tmp_path)


@pytest.mark.parametrize("missing", [False, True])
def test_skip_worktree_inputs_block_before_candidate_construction(
    source, tmp_path, missing
):
    from manifest_agent.checks import CandidateBlockedError

    root = source[0]
    git(root, "update-index", "--skip-worktree", "head-only.txt")
    if missing:
        (root / "head-only.txt").unlink()
    before = (root / ".git/index").read_bytes()
    with pytest.raises(CandidateBlockedError, match=r"skip-worktree|sparse"):
        materialize(source, tmp_path)
    assert (root / ".git/index").read_bytes() == before
    assert not (tmp_path / "candidate").exists()


def test_internal_git_disables_transport_even_if_source_config_allows_it(
    source, tmp_path
):
    from manifest_agent.checks.process import git_output

    helper = tmp_path / "transport.py"
    marker = tmp_path / "transport-started"
    helper.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(1)\n"
    )
    helper.chmod(0o700)
    git(source[0], "config", "protocol.ext.allow", "always")
    with pytest.raises(subprocess.CalledProcessError):
        git_output(source[0], "ls-remote", f"ext::{helper}")
    assert not marker.exists()


def test_missing_promisor_objects_block_without_lazy_fetch(source, tmp_path):
    from manifest_agent.checks import CandidateBlockedError

    root = source[0]
    helper = tmp_path / "promisor.py"
    marker = tmp_path / "lazy-fetch-started"
    helper.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(1)\n"
    )
    helper.chmod(0o700)
    git(root, "config", "remote.origin.url", f"ext::{helper}")
    git(root, "config", "remote.origin.promisor", "true")
    git(root, "config", "protocol.ext.allow", "always")
    blob = git(root, "rev-parse", "HEAD:deleted").decode().strip()
    (root / ".git/objects" / blob[:2] / blob[2:]).rename(tmp_path / "withheld-object")
    with pytest.raises(CandidateBlockedError):
        materialize(source, tmp_path)
    assert not marker.exists()
