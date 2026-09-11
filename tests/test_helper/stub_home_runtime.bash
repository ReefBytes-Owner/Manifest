# stub_home_manifest_runtime — fake ~/.claude/.venv for legacy .py shims in bats.
#
# Resolution order for the `manifest`/`python` pair it symlinks in:
#   1. `command -v manifest` — under the store env, test.bats's path_prepend
#      puts `store:config-env/bin` on PATH, so this finds the store's
#      hash-verified `config-env` bundle with no extra setup. This is the
#      path the runner (and CI) actually take.
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
    manifest="$(command -v manifest || true)"
    if [[ -n "$manifest" ]]; then
        python_bin="$(dirname "$manifest")/python"
    else
        local venv_bin="$repo/configs/claude/.venv/bin"
        manifest="$venv_bin/manifest"
        python_bin="$venv_bin/python"
        if [[ ! -x "$manifest" ]]; then
            echo "stub_home_manifest_runtime: no 'manifest' on PATH and missing $manifest — run: uv sync --project configs/claude" >&2
            return 1
        fi
    fi
    mkdir -p "$home/.claude/.venv/bin" "$home/.local/bin"
    ln -sf "$manifest" "$home/.claude/.venv/bin/manifest"
    ln -sf "$python_bin" "$home/.claude/.venv/bin/python"
    printf '#!/bin/sh\nexit 0\n' > "$home/.local/bin/uv"
    chmod +x "$home/.local/bin/uv"
}
