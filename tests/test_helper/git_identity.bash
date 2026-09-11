# git_identity.bash — Correction 17 (phase-3-5-decisions.md, C7r fix 2):
# shared git-identity fixture for bats suites whose fixtures create real
# git commits.
#
# Under the real runner a check body's HOME is redirected to a fresh, empty
# per-check scratch directory (toolchain_cache.scratch_home_environment) --
# there is no `~/.gitconfig`, and the environment forwarded to the body does
# not carry the operator's global git identity (`ENVIRONMENT_KEYS` in
# `src/manifest_agent/checks/cli.py` never forwards it). A fixture that
# relies on an ambient global identity, or on git falling back to reading
# `$HOME/.gitconfig`/the system config for `user.name`/`user.email`, only
# ever worked by accident under a developer's real HOME. This pins identity
# explicitly (`GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_NAME`/
# `GIT_COMMITTER_EMAIL` -- consulted by `git commit`/`git var` directly, no
# config read required) AND isolates every other git config lookup
# (`GIT_CONFIG_NOSYSTEM=1` skips `/etc/gitconfig`; `GIT_CONFIG_GLOBAL`
# points at an empty file under the sandbox, never `$HOME/.gitconfig`) so
# the suite neither depends on nor fights the operator's real git config,
# signing setup, or `safe.directory` allowlist.
#
# Convention: `load '../test_helper/git_identity.bash'` and call
# `git_identity_begin <sandbox_dir>` from setup() / `git_identity_end` from
# teardown() -- same pattern as `isolated_home.bash`.

# git_identity_begin <sandbox_dir> — export a deterministic, isolated git
# identity and config search path rooted under <sandbox_dir>. Call from
# setup() before any fixture `git init`/`git commit`.
git_identity_begin() {
    local sandbox="$1"
    export GIT_AUTHOR_NAME="manifest-test"
    export GIT_AUTHOR_EMAIL="manifest-test@example.invalid"
    export GIT_COMMITTER_NAME="manifest-test"
    export GIT_COMMITTER_EMAIL="manifest-test@example.invalid"
    export GIT_CONFIG_NOSYSTEM=1
    export GIT_CONFIG_GLOBAL="$sandbox/git-identity-global-config"
    : > "$GIT_CONFIG_GLOBAL"
}

# git_identity_end — unset every variable git_identity_begin exported. Call
# from teardown() so an identity pinned for one test never leaks into the
# next bats process bats may reuse.
git_identity_end() {
    unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL \
        GIT_CONFIG_NOSYSTEM GIT_CONFIG_GLOBAL
}
