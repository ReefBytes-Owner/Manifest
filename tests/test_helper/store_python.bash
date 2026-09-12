# store_project_env_python — resolve the store's project-env `bin/python`.
#
# Correction 12 (C7k step 5b), phase-3-5-decisions.md: a test that needs the
# repository's own Python (with its project dependencies installed) must
# never invoke `uv run`/`uv sync` against the candidate under test -- with
# `store:uv/bin` on test.bats's PATH (C7k step 5), such a call succeeds and
# materializes a real `.venv` inside the candidate, which the runner's
# candidate-identity walk then correctly BLOCKs on every later check
# ("unsafe symlink: .venv/bin/python"). The fix is the same lookup C7k step
# 5 introduced for config-env (see stub_home_runtime.bash's
# `_stub_home_store_manifest_path`): read the store's own `manifest.json`
# index for the already-provisioned, hash-verified `project-env` bundle's
# `bin/python`, and use that interpreter directly. No new environment is
# created, nothing is synced, nothing is written into the candidate.
#
# Prints the absolute path to project-env's `bin/python` on stdout and
# returns 0, or prints nothing and returns 1 if the store/bundle is absent
# (the caller decides whether that is a skip or a failure).
store_project_env_python() {
    [[ -n "${MANIFEST_TOOLCHAIN_STORE:-}" ]] || return 1
    local index="$MANIFEST_TOOLCHAIN_STORE/manifest.json"
    [[ -f "$index" ]] || return 1
    local python_bin
    python_bin="$(
        python3 - "$index" "$MANIFEST_TOOLCHAIN_STORE" << 'PY'
import json
import sys

index_path, store_root = sys.argv[1], sys.argv[2]
with open(index_path, encoding="utf-8") as handle:
    manifest = json.load(handle)
relative = (
    manifest.get("tools", {})
    .get("project-env", {})
    .get("executables", {})
    .get("bin/python", {})
    .get("path")
)
if relative:
    print(f"{store_root}/{relative}")
PY
    )"
    [[ -n "$python_bin" && -x "$python_bin" ]] || return 1
    printf '%s\n' "$python_bin"
}
