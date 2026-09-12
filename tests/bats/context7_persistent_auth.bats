#!/usr/bin/env bats

load '../test_helper/bats-support/load'
load '../test_helper/bats-assert/load'

REPO_ROOT="$BATS_TEST_DIRNAME/../.."

setup() {
    export BATS_TMPDIR="${BATS_TMPDIR:-/tmp}"
    SANDBOX=$(mktemp -d "$BATS_TMPDIR/context7_auth.XXXXXX")
    export HOME="$SANDBOX/home"
    # Correction 16 rule 2: configure_context7_auth.py's credential_path()
    # honors $XDG_CONFIG_HOME over $HOME/.config when set. Pin it into this
    # test's own sandbox explicitly -- never rely on it being unset -- so a
    # stray ambient XDG_CONFIG_HOME can never point credential reads at the
    # developer's real ~/.config/context7/credentials.json.
    export XDG_CONFIG_HOME="$HOME/.config"
    export SCRIPT_DIR="$REPO_ROOT"
    export MCP_STUB_LOG="$SANDBOX/mcp.log"
    mkdir -p "$HOME/.config/context7" "$SANDBOX/bin"
    printf '{"access_token":"ctx7sk-test-secret-must-not-leak","token_type":"bearer"}\n' > "$HOME/.config/context7/credentials.json"
    chmod 600 "$HOME/.config/context7/credentials.json"
    mkdir -p "$HOME/.cursor" "$HOME/.codex"
    printf '%s\n' '{"mcpServers":{"private":{"url":"https://private.example/mcp"}}}' > "$HOME/.cursor/mcp.json"
    printf '%s\n' '[mcp_servers.private]' 'url = "https://private.example/mcp"' > "$HOME/.codex/config.toml"
    : > "$MCP_STUB_LOG"

    cat > "$SANDBOX/bin/npx" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$MCP_STUB_LOG"
STUB
    chmod +x "$SANDBOX/bin/npx"
    export PATH="$SANDBOX/bin:$PATH"

    print_step() { :; }
    print_success() { :; }
    print_info() { :; }
    print_warning() { echo "WARN: $*"; }
    print_error() { echo "ERR: $*"; }
    print_header() { :; }
    command_exists() { command -v "$1" > /dev/null 2>&1; }

    # shellcheck disable=SC1090
    source "$REPO_ROOT/bootstrap/lib/mcp.sh"
}

teardown() {
    [[ -n "${SANDBOX:-}" && -d "$SANDBOX" ]] && rm -rf "$SANDBOX"
}

@test "Context7 uses one pinned device-OAuth login for every enabled harness" {
    ENABLE_CLAUDE=true
    ENABLE_CURSOR=true
    ENABLE_CODEX=true
    ENABLE_GEMINI=true
    ENABLE_ANTIGRAVITY=true
    ENABLE_DEVIN=false

    run configure_context7_persistent_auth
    assert_success
    run cat "$MCP_STUB_LOG"
    assert_line --index 0 "--yes ctx7@0.5.10 login"
    [ "${#lines[@]}" -eq 1 ]
    refute_output --partial "ctx7sk-test-secret-must-not-leak"
    run python3 -c "
import json
from pathlib import Path
h=Path('$HOME')
assert json.load(open(h/'.claude.json'))['mcpServers']['context7']['headers']['Authorization'].startswith('Bearer ')
assert json.load(open(h/'.cursor/mcp.json'))['mcpServers']['context7']['url'] == 'https://mcp.context7.com/mcp'
assert json.load(open(h/'.cursor/mcp.json'))['mcpServers']['private']['url'] == 'https://private.example/mcp'
assert json.load(open(h/'.gemini/settings.json'))['mcpServers']['context7']['httpUrl'] == 'https://mcp.context7.com/mcp'
assert json.load(open(h/'.gemini/config/mcp_config.json'))['mcpServers']['context7']['serverUrl'] == 'https://mcp.context7.com/mcp'
assert '[mcp_servers.context7.http_headers]' in (h/'.codex/config.toml').read_text()
assert '[mcp_servers.private]' in (h/'.codex/config.toml').read_text()
print('all-harnesses-configured')"
    assert_output "all-harnesses-configured"
}

@test "Context7 never falls back to generic native-OAuth registration" {
    parse_mcp_registry() {
        MCP_SERVER_NAMES=(context7)
        MCP_SERVER_URLS=(https://mcp.context7.com/mcp)
        MCP_SERVER_TRANSPORTS=(http)
        MCP_SERVER_PURPOSES=(docs)
    }
    prompt_mcp_selection() { MCP_SELECTED_INDICES=(0); }
    install_claude_mcp_server() { echo "GENERIC claude $*" >> "$MCP_STUB_LOG"; }
    install_gemini_mcp_server() { echo "GENERIC gemini $*" >> "$MCP_STUB_LOG"; }
    install_codex_mcp_server() { echo "GENERIC codex $*" >> "$MCP_STUB_LOG"; }
    configure_cursor_mcp_config() { echo "GENERIC cursor" >> "$MCP_STUB_LOG"; }
    ENABLE_CLAUDE=true
    ENABLE_CURSOR=true
    ENABLE_CODEX=true
    ENABLE_GEMINI=true
    ENABLE_ANTIGRAVITY=false
    ENABLE_DEVIN=false
    FORCE=true

    run install_mcp_servers
    assert_success
    run cat "$MCP_STUB_LOG"
    assert_line --index 0 "--yes ctx7@0.5.10 login"
    refute_output --partial "GENERIC"
}

@test "MCP default merge migrates only legacy Context7 OAuth entries" {
    src="$SANDBOX/defaults.json"
    tgt="$SANDBOX/user.json"
    cat > "$src" <<'EOF'
{"mcpServers":{"context7":{"url":"https://mcp.context7.com/mcp"}}}
EOF
    cat > "$tgt" <<'EOF'
{"mcpServers":{"context7":{"url":"https://mcp.context7.com/mcp/oauth","type":"http"},"private":{"url":"https://private.example/mcp"}}}
EOF

    run python3 "$REPO_ROOT/configs/claude/scripts/merge_mcp_defaults.py" "$src" "$tgt"
    assert_success
    run python3 -c "
import json
d=json.load(open('$tgt'))['mcpServers']
assert d['context7'] == {'url': 'https://mcp.context7.com/mcp'}
assert d['private'] == {'url': 'https://private.example/mcp'}
print('legacy-migrated')"
    assert_output "legacy-migrated"
}

@test "MCP default merge preserves authenticated Context7 headers" {
    src="$SANDBOX/defaults.json"
    tgt="$SANDBOX/user.json"
    cat > "$src" <<'EOF'
{"mcpServers":{"context7":{"url":"https://mcp.context7.com/mcp"}}}
EOF
    cat > "$tgt" <<'EOF'
{"mcpServers":{"context7":{"url":"https://mcp.context7.com/mcp","headers":{"Authorization":"Bearer ctx7sk-test-secret-must-not-leak"}}}}
EOF

    run python3 "$REPO_ROOT/configs/claude/scripts/merge_mcp_defaults.py" "$src" "$tgt"
    assert_success
    refute_output --partial "ctx7sk-test-secret-must-not-leak"
    run python3 -c "
import json
d=json.load(open('$tgt'))['mcpServers']['context7']
assert d['headers']['Authorization'] == 'Bearer ctx7sk-test-secret-must-not-leak'
print('auth-preserved')"
    assert_output "auth-preserved"
}
