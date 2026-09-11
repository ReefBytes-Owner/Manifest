#!/usr/bin/env bats

load '../test_helper/store_python.bash'

setup() {
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
}

@test "capability matrix has no unverified or blank cells" {
  # Correction 12 (C7k step 5b): store project-env's interpreter, never
  # `uv run` against this candidate.
  local python_bin
  python_bin="$(store_project_env_python)" || skip "store project-env unavailable (run: manifest provision)"
  run "$python_bin" "$REPO_ROOT/tools/render_plugin_capability_matrix.py" --check \
    --inspection "$REPO_ROOT/tests/fixtures/plugin_capability_inspection.json"
  [ "$status" -eq 0 ]
}

@test "all production adapters complete response-driven isolated-home lifecycles" {
  # Correction 12 (C7k step 5b): store project-env's pytest, never
  # `uv run pytest` against this candidate.
  local python_bin
  python_bin="$(store_project_env_python)" || skip "store project-env unavailable (run: manifest provision)"
  run "$python_bin" -m pytest \
    "$REPO_ROOT/tests/python/manifest_agent/test_native_adapter_integration.py" \
    -m 'not native' -q -p no:cacheprovider --rootdir "$REPO_ROOT"
  [ "$status" -eq 0 ]
}
