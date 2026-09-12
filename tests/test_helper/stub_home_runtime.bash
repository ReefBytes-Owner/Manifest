# stub_home_manifest_runtime — fake ~/.claude/.venv for legacy .py shims in bats.
#
# Resolution order for the `manifest`/`python` pair it symlinks in:
#   1. `$MANIFEST_TOOLCHAIN_STORE`'s `config-env` bundle (the store's
#      hash-verified `configs/claude` install, whose `[project.scripts]
#      manifest` entry point exists at `bin/manifest` -- see
#      toolchain_provision.py's `config-env` case). Resolved via the store's
#      own `manifest.json` index rather than `test.bats`'s `path_prepend`:
#      `path_prepend` verification always probes a bundle's `bin/python`
#      (toolchain_path_prepend.py::bundle_primary_relative), but config-env's
#      lock entry only declares `bin/manifest` as a console script, so a
#      `store:config-env/bin` path_prepend entry BLOCKs every preflight
#      (caught by test_toolchain_c7i_functional.py). This needs no such
#      verification -- it is a bats test fixture, not a check's own argv.
#   2. `$repo/configs/claude/.venv/bin/manifest` — a manually built venv for
#      local, non-store development (`uv sync --project configs/claude`).
stub_home_manifest_runtime() {
    local repo="${1:-${REPO_ROOT:-}}"
    [[ -n "$repo" ]] || {
        echo "stub_home_manifest_runtime: REPO_ROOT unset" >&2
        return 1
    }
    local home="${HOME:?}"
    local manifest python_bin
    manifest="$(_stub_home_store_manifest_path)"
    if [[ -n "$manifest" && -x "$manifest" ]]; then
        python_bin="$(dirname "$manifest")/python"
    else
        local venv_bin="$repo/configs/claude/.venv/bin"
        manifest="$venv_bin/manifest"
        python_bin="$venv_bin/python"
        if [[ ! -x "$manifest" ]]; then
            echo "stub_home_manifest_runtime: no store config-env manifest and missing $manifest — run: uv sync --project configs/claude" >&2
            return 1
        fi
    fi
    mkdir -p "$home/.claude/.venv/bin" "$home/.local/bin"
    ln -sf "$manifest" "$home/.claude/.venv/bin/manifest"
    ln -sf "$python_bin" "$home/.claude/.venv/bin/python"
    printf '#!/bin/sh\nexit 0\n' > "$home/.local/bin/uv"
    chmod +x "$home/.local/bin/uv"
}

# _stub_home_store_manifest_path — absolute path to config-env's `bin/manifest`
# inside $MANIFEST_TOOLCHAIN_STORE, or empty if the store/bundle is absent.
# Reads the store's own manifest.json index (not a hash-verified resolve --
# see the note above for why a lighter lookup is fine here).
_stub_home_store_manifest_path() {
    [[ -n "${MANIFEST_TOOLCHAIN_STORE:-}" ]] || return 0
    local index="$MANIFEST_TOOLCHAIN_STORE/manifest.json"
    [[ -f "$index" ]] || return 0
    python3 - "$index" "$MANIFEST_TOOLCHAIN_STORE" << 'PY'
import json
import sys

index_path, store_root = sys.argv[1], sys.argv[2]
with open(index_path, encoding="utf-8") as handle:
    manifest = json.load(handle)
relative = (
    manifest.get("tools", {}).get("config-env", {}).get("executables", {}).get("bin/manifest", {}).get("path")
)
if relative:
    print(f"{store_root}/{relative}")
PY
}
