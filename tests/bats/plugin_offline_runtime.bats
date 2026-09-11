#!/usr/bin/env bats

load '../test_helper/store_python.bash'

setup() {
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
}

@test "bundle runtime path gate accepts local bundles without network tooling" {
  # Correction 12 (C7k step 5b): this used to hard-code $REPO_ROOT/.venv,
  # which only existed because an earlier test (plugin_migration.bats /
  # plugin_native_parity.bats) incidentally `uv run` a .venv into the
  # candidate before this test ran -- an undeclared ordering dependency on
  # a bug those tests have since stopped committing. Resolve the store's
  # own project-env interpreter instead, same as every other fixed test.
  local python_bin
  python_bin="$(store_project_env_python)" || skip "store project-env unavailable (run: manifest provision)"
  local fixture_bin
  fixture_bin="$(mktemp -d "${BATS_TMPDIR:-/tmp}/manifest-offline.XXXXXX")"
  trap 'rm -rf "$fixture_bin"' RETURN
  for command in curl npm npx uv uvx; do
    printf '#!/usr/bin/env sh\necho network disabled >&2\nexit 127\n' > "$fixture_bin/$command"
    chmod +x "$fixture_bin/$command"
  done
  run env PATH="$fixture_bin:/usr/bin:/bin" UV_NO_NETWORK=1 \
    "$python_bin" "$REPO_ROOT/tools/check_plugin_runtime_paths.py"
  [ "$status" -eq 0 ]
}
