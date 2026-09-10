#!/bin/bash

# Install and platform helpers for bootstrap.sh. This file is sourced, not executed.

check_platform() {
    case "$PLATFORM" in
        macos)
            print_success "Running on macOS $(sw_vers -productVersion)"
            ;;
        linux)
            local version=""
            if [[ -f /etc/os-release ]]; then
                # /etc/os-release is a runtime-only distro file with no static
                # equivalent shellcheck can follow; content is host-specific.
                # shellcheck disable=SC1091
                . /etc/os-release
                version="$PRETTY_NAME"
            else
                version="$(uname -r)"
            fi
            print_success "Running on Linux: $version"
            if [[ -n "$PKG_MANAGER" ]]; then
                print_info "Package manager: $PKG_MANAGER"
            else
                print_warning "No supported package manager detected"
            fi
            ;;
        *)
            print_error "Unsupported platform: $(uname -s)"
            print_info "This script supports macOS and Linux"
            exit 1
            ;;
    esac
}

# Check and install Homebrew (macOS) or ensure package manager is available (Linux)
install_package_manager() {
    if [[ "$PLATFORM" == "macos" ]]; then
        print_step "Checking for Homebrew..."

        if command_exists brew; then
            print_success "Homebrew is installed"
            print_step "Updating Homebrew..."
            brew update --quiet
        else
            print_warning "Homebrew not found"
            if prompt_yes_no "Install Homebrew?"; then
                print_step "Installing Homebrew..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

                # Add Homebrew to PATH for Apple Silicon
                if [[ -f "/opt/homebrew/bin/brew" ]]; then
                    # shellcheck disable=SC1090
                    source <(/opt/homebrew/bin/brew shellenv)
                fi
                # Add Homebrew to PATH for Intel Mac
                if [[ -f "/usr/local/bin/brew" ]]; then
                    # shellcheck disable=SC1090
                    source <(/usr/local/bin/brew shellenv)
                fi
                print_success "Homebrew installed"
            else
                print_warning "Homebrew not installed - some installations may fail"
            fi
        fi
    elif [[ "$PLATFORM" == "linux" ]]; then
        print_step "Checking package manager..."

        if [[ -z "$PKG_MANAGER" ]]; then
            print_warning "No supported package manager found"
            print_info "Supported: apt, dnf, yum, pacman, zypper"
            print_info "You may need to install dependencies manually"
        else
            print_success "Package manager available: $PKG_MANAGER"

            # Update package lists
            case "$PKG_MANAGER" in
                apt)
                    if prompt_yes_no "Update apt package lists?"; then
                        print_step "Updating package lists..."
                        sudo apt-get update -qq
                    fi
                    ;;
                dnf | yum)
                    # dnf/yum auto-updates metadata
                    ;;
                pacman)
                    if prompt_yes_no "Sync pacman database?"; then
                        print_step "Syncing database..."
                        sudo pacman -Sy --noconfirm
                    fi
                    ;;
            esac
        fi
    fi
}

# Check Python installation (required for parallel_agent.py)
# Detects and prefers stable Python versions (>= 3.9, not alpha/beta/rc)
check_python() {
    print_step "Checking for Python..."

    # Find all Python installations (prefer specific stable versions)
    local python_candidates=(
        "/usr/local/bin/python3.14" # Homebrew Python 3.14 (latest stable)
        "/usr/local/bin/python3.13" # Homebrew Python 3.13
        "/usr/local/bin/python3.12" # Homebrew Python 3.12
        "/usr/bin/python3"          # macOS system Python (usually stable)
        "/usr/local/bin/python3"    # Homebrew Python (generic)
        "python3"                   # PATH python3
        "python"                    # PATH python
    )

    local best_python=""
    local best_version=""
    local best_score=0

    for py_cmd in "${python_candidates[@]}"; do
        # Check if command exists
        if ! command -v "$py_cmd" &> /dev/null; then
            continue
        fi

        # Get full version
        local version
        version=$($py_cmd --version 2>&1 | awk '{print $2}')
        if [[ -z "$version" ]]; then
            continue
        fi

        # Parse major.minor
        local major minor
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)

        # Skip Python 2.x
        if [[ "$major" -lt 3 ]]; then
            continue
        fi

        # Calculate score (prefer stable >= 3.9)
        local score=0

        # Prefer 3.9+ (modern Python with good library support)
        if [[ "$major" -eq 3 ]] && [[ "$minor" -ge 9 ]] && [[ "$minor" -le 20 ]]; then
            score=$((score + 100))
            # Bonus for newer stable versions (3.12+)
            if [[ "$minor" -ge 12 ]]; then
                score=$((score + 10))
            fi
        elif [[ "$major" -eq 3 ]] && [[ "$minor" -ge 7 ]]; then
            score=$((score + 50))
        fi

        # Penalize alpha/beta/rc versions heavily
        if [[ "$version" =~ (a|b|rc) ]]; then
            score=$((score - 1000))
        fi

        # Prefer /usr/bin over /usr/local (more stable on macOS)
        if [[ "$py_cmd" == "/usr/bin/python3" ]]; then
            score=$((score + 10))
        fi

        # Track best candidate
        if [[ $score -gt $best_score ]]; then
            best_score=$score
            best_python="$py_cmd"
            best_version="$version"
        fi
    done

    if [[ -n "$best_python" ]]; then
        export PYTHON_CMD="$best_python"
        print_success "Python is installed ($best_version)"

        # Warn about alpha/beta versions
        if [[ "$best_version" =~ (a|b|rc) ]]; then
            print_warning "Using pre-release Python version - some packages may fail to install"
            print_info "Consider installing a stable Python version for better compatibility"
        fi

        # Check for pip
        if $best_python -m pip --version &> /dev/null; then
            print_success "pip is available"
            return 0
        else
            print_warning "pip not found - Python packages cannot be installed"
            return 1
        fi
    else
        print_warning "Python not found"
        print_info "The parallel agent (parallel_agent.py) requires Python 3.9+"
        print_info ""
        print_info "To install Python:"
        if [[ "$PLATFORM" == "macos" ]]; then
            print_info "  macOS: brew install python3"
        else
            print_info "  Linux: Use your package manager (apt install python3, dnf install python3, etc.)"
        fi
        return 1
    fi
}

# Install Node.js (required for some CLIs)
install_node() {
    print_step "Checking for Node.js..."

    if command_exists node; then
        local node_version
        node_version=$(node --version)
        print_success "Node.js is installed ($node_version)"
    else
        print_warning "Node.js not found"

        if [[ "$PLATFORM" == "macos" ]]; then
            if command_exists brew && prompt_yes_no "Install Node.js via Homebrew?"; then
                print_step "Installing Node.js..."
                brew install node
                print_success "Node.js installed"
            else
                print_warning "Please install Node.js manually from https://nodejs.org"
            fi
        elif [[ "$PLATFORM" == "linux" ]]; then
            echo ""
            echo -e "${BOLD}Node.js Installation Options:${NC}"
            echo "  1. Use system package manager"
            echo "  2. Use NodeSource repository (recommended for latest LTS)"
            echo "  3. Skip (install manually later)"
            echo ""
            read -r -p "Choose option [1/2/3]: " node_choice

            case $node_choice in
                1)
                    print_step "Installing Node.js via $PKG_MANAGER..."
                    case "$PKG_MANAGER" in
                        apt)
                            sudo apt-get install -y nodejs npm
                            ;;
                        dnf)
                            sudo dnf install -y nodejs npm
                            ;;
                        yum)
                            sudo yum install -y nodejs npm
                            ;;
                        pacman)
                            sudo pacman -S --noconfirm nodejs npm
                            ;;
                        zypper)
                            sudo zypper install -y nodejs npm
                            ;;
                        *)
                            print_error "Package manager not supported for Node.js installation"
                            ;;
                    esac
                    print_success "Node.js installed"
                    ;;
                2)
                    print_step "Installing Node.js via NodeSource..."
                    if [[ "$PKG_MANAGER" == "apt" ]]; then
                        curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
                        sudo apt-get install -y nodejs
                    elif [[ "$PKG_MANAGER" == "dnf" || "$PKG_MANAGER" == "yum" ]]; then
                        curl -fsSL https://rpm.nodesource.com/setup_lts.x | sudo bash -
                        sudo "$PKG_MANAGER" install -y nodejs
                    else
                        print_warning "NodeSource not available for $PKG_MANAGER"
                        print_info "Please install Node.js manually from https://nodejs.org"
                    fi
                    ;;
                *)
                    print_warning "Node.js not installed - some CLI tools may not work"
                    ;;
            esac
        fi
    fi
}

# Install Claude Code CLI
install_claude() {
    if [[ "$ENABLE_CLAUDE" == false ]]; then
        print_info "Claude CLI is disabled - skipping installation"
        return 0
    fi

    print_step "Checking for Claude Code CLI..."

    if command_exists claude; then
        print_success "Claude Code CLI is installed"
        claude --version 2> /dev/null || true
    else
        print_warning "Claude Code CLI not found"
        echo ""
        echo -e "${BOLD}Claude Code CLI Installation Options:${NC}"
        echo "  1. npm install -g @anthropic-ai/claude-code"
        echo "  2. Download from https://claude.ai/code"
        echo ""

        if prompt_yes_no "Install Claude Code CLI via npm?"; then
            if command_exists npm; then
                print_step "Installing Claude Code CLI..."
                npm install -g @anthropic-ai/claude-code
                print_success "Claude Code CLI installed"
            else
                print_error "npm not found. Please install Node.js first."
                return 1
            fi
        else
            print_warning "Claude Code CLI not installed"
            if prompt_yes_no "Disable Claude in service configuration?"; then
                ENABLE_CLAUDE=false
            fi
        fi
    fi
}

# Install Gemini CLI
install_gemini() {
    if [[ "$ENABLE_GEMINI" == false ]]; then
        print_info "Gemini CLI is disabled - skipping installation"
        return 0
    fi

    print_step "Checking for Gemini CLI..."

    if command_exists gemini; then
        print_success "Gemini CLI is installed"
    else
        print_warning "Gemini CLI not found"
        echo ""
        echo -e "${BOLD}Gemini CLI Installation Options:${NC}"
        echo "  1. npm install -g @google/gemini-cli"
        echo "  2. See https://github.com/google-gemini/gemini-cli"
        echo ""

        if prompt_yes_no "Install Gemini CLI via npm?"; then
            if command_exists npm; then
                print_step "Installing Gemini CLI..."
                npm install -g @google/gemini-cli
                print_success "Gemini CLI installed"
            else
                print_error "npm not found. Please install Node.js first."
                return 1
            fi
        else
            print_warning "Gemini CLI not installed"
            if prompt_yes_no "Disable Gemini in service configuration?"; then
                ENABLE_GEMINI=false
            fi
        fi
    fi
}

# Install Codex CLI
install_codex() {
    if [[ "$ENABLE_CODEX" == false ]]; then
        print_info "Codex CLI is disabled - skipping installation"
        return 0
    fi

    print_step "Checking for Codex CLI..."

    if command_exists codex; then
        print_success "Codex CLI is installed"
        codex --version 2> /dev/null || true
    else
        print_warning "Codex CLI not found"
        echo ""
        echo -e "${BOLD}Codex CLI Installation Options:${NC}"
        echo "  1. npm install -g @openai/codex"
        if [[ "$PLATFORM" == "macos" ]]; then
            echo "  2. brew install --cask codex"
        else
            echo "  2. See https://github.com/openai/codex"
        fi
        echo ""

        if prompt_yes_no "Install Codex CLI via npm?"; then
            if command_exists npm; then
                print_step "Installing Codex CLI..."
                npm install -g @openai/codex
                print_success "Codex CLI installed"
            else
                print_error "npm not found. Please install Node.js first."
                return 1
            fi
        else
            print_warning "Codex CLI not installed"
            if prompt_yes_no "Disable Codex in service configuration?"; then
                ENABLE_CODEX=false
            fi
        fi
    fi
}

# Install GitHub CLI
install_github_cli() {
    # Auto-detect: skip if already installed or disabled
    if [[ "$ENABLE_GH" == "auto" ]]; then
        if command_exists gh; then
            print_info "GitHub CLI (gh) is installed - enabling"
            ENABLE_GH=true
            return 0
        else
            print_info "GitHub CLI (gh) not found - skipping (auto-detect)"
            ENABLE_GH=false
            return 0
        fi
    fi

    if [[ "$ENABLE_GH" == false ]]; then
        print_info "GitHub CLI is disabled - skipping installation"
        return 0
    fi

    print_step "Checking for GitHub CLI (gh)..."

    if command_exists gh; then
        print_success "GitHub CLI (gh) is installed"
        gh --version 2> /dev/null || true
    else
        print_warning "GitHub CLI (gh) not found"
        echo ""
        echo -e "${BOLD}GitHub CLI Installation Options:${NC}"
        case "$PLATFORM" in
            macos)
                echo "  brew install gh"
                ;;
            linux)
                case "$PKG_MANAGER" in
                    apt)
                        echo "  sudo apt install gh"
                        ;;
                    dnf | yum)
                        echo "  sudo dnf install gh"
                        ;;
                    pacman)
                        echo "  sudo pacman -S github-cli"
                        ;;
                    *)
                        echo "  See https://cli.github.com/manual/installation"
                        ;;
                esac
                ;;
        esac
        echo ""

        if prompt_yes_no "Install GitHub CLI now?"; then
            case "$PLATFORM" in
                macos)
                    if command_exists brew; then
                        print_step "Installing GitHub CLI via Homebrew..."
                        brew install gh
                        print_success "GitHub CLI installed"
                    else
                        print_error "Homebrew not found. Please install Homebrew first."
                        return 1
                    fi
                    ;;
                linux)
                    case "$PKG_MANAGER" in
                        apt)
                            print_step "Installing GitHub CLI via apt..."
                            sudo apt update && sudo apt install -y gh
                            print_success "GitHub CLI installed"
                            ;;
                        dnf | yum)
                            print_step "Installing GitHub CLI via $PKG_MANAGER..."
                            sudo "$PKG_MANAGER" install -y gh
                            print_success "GitHub CLI installed"
                            ;;
                        pacman)
                            print_step "Installing GitHub CLI via pacman..."
                            sudo pacman -S --noconfirm github-cli
                            print_success "GitHub CLI installed"
                            ;;
                        *)
                            print_error "Package manager not supported. Please install manually: https://cli.github.com/manual/installation"
                            return 1
                            ;;
                    esac
                    ;;
            esac
        else
            print_warning "GitHub CLI not installed"
            if prompt_yes_no "Disable GitHub CLI in service configuration?"; then
                ENABLE_GH=false
            fi
        fi
    fi
}

# Install GitLab CLI
install_gitlab_cli() {
    # Auto-detect: skip if already installed or disabled
    if [[ "$ENABLE_GLAB" == "auto" ]]; then
        if command_exists glab; then
            print_info "GitLab CLI (glab) is installed - enabling"
            ENABLE_GLAB=true
            return 0
        else
            print_info "GitLab CLI (glab) not found - skipping (auto-detect)"
            ENABLE_GLAB=false
            return 0
        fi
    fi

    if [[ "$ENABLE_GLAB" == false ]]; then
        print_info "GitLab CLI is disabled - skipping installation"
        return 0
    fi

    print_step "Checking for GitLab CLI (glab)..."

    if command_exists glab; then
        print_success "GitLab CLI (glab) is installed"
        glab --version 2> /dev/null || true
    else
        print_warning "GitLab CLI (glab) not found"
        echo ""
        echo -e "${BOLD}GitLab CLI Installation Options:${NC}"
        case "$PLATFORM" in
            macos)
                echo "  brew install glab"
                ;;
            linux)
                case "$PKG_MANAGER" in
                    apt)
                        echo "  sudo apt install glab"
                        ;;
                    dnf | yum)
                        echo "  sudo dnf install glab"
                        ;;
                    pacman)
                        echo "  sudo pacman -S glab"
                        ;;
                    *)
                        echo "  See https://gitlab.com/gitlab-org/cli"
                        ;;
                esac
                ;;
        esac
        echo ""

        if prompt_yes_no "Install GitLab CLI now?"; then
            case "$PLATFORM" in
                macos)
                    if command_exists brew; then
                        print_step "Installing GitLab CLI via Homebrew..."
                        brew install glab
                        print_success "GitLab CLI installed"
                    else
                        print_error "Homebrew not found. Please install Homebrew first."
                        return 1
                    fi
                    ;;
                linux)
                    case "$PKG_MANAGER" in
                        apt)
                            print_step "Installing GitLab CLI via apt..."
                            sudo apt update && sudo apt install -y glab
                            print_success "GitLab CLI installed"
                            ;;
                        dnf | yum)
                            print_step "Installing GitLab CLI via $PKG_MANAGER..."
                            sudo "$PKG_MANAGER" install -y glab
                            print_success "GitLab CLI installed"
                            ;;
                        pacman)
                            print_step "Installing GitLab CLI via pacman..."
                            sudo pacman -S --noconfirm glab
                            print_success "GitLab CLI installed"
                            ;;
                        *)
                            print_error "Package manager not supported. Please install manually: https://gitlab.com/gitlab-org/cli"
                            return 1
                            ;;
                    esac
                    ;;
            esac
        else
            print_warning "GitLab CLI not installed"
            if prompt_yes_no "Disable GitLab CLI in service configuration?"; then
                ENABLE_GLAB=false
            fi
        fi
    fi
}

# Check for jq (required by git_ops.sh)
check_jq() {
    print_step "Checking for jq (required by git_ops.sh)..."

    if command_exists jq; then
        print_success "jq is installed"
    else
        print_warning "jq not found"
        echo ""
        echo -e "${BOLD}jq Installation Options:${NC}"
        case "$PLATFORM" in
            macos)
                echo "  brew install jq"
                ;;
            linux)
                case "$PKG_MANAGER" in
                    apt)
                        echo "  sudo apt install jq"
                        ;;
                    dnf | yum)
                        echo "  sudo dnf install jq"
                        ;;
                    pacman)
                        echo "  sudo pacman -S jq"
                        ;;
                    zypper)
                        echo "  sudo zypper install jq"
                        ;;
                    *)
                        echo "  See https://stedolan.github.io/jq/"
                        ;;
                esac
                ;;
        esac
        echo ""

        if prompt_yes_no "Install jq now?"; then
            case "$PLATFORM" in
                macos)
                    if command_exists brew; then
                        print_step "Installing jq via Homebrew..."
                        brew install jq
                        print_success "jq installed"
                    else
                        print_error "Homebrew not found."
                        return 1
                    fi
                    ;;
                linux)
                    case "$PKG_MANAGER" in
                        apt)
                            print_step "Installing jq via apt..."
                            sudo apt update && sudo apt install -y jq
                            print_success "jq installed"
                            ;;
                        dnf | yum)
                            print_step "Installing jq via $PKG_MANAGER..."
                            sudo "$PKG_MANAGER" install -y jq
                            print_success "jq installed"
                            ;;
                        pacman)
                            print_step "Installing jq via pacman..."
                            sudo pacman -S --noconfirm jq
                            print_success "jq installed"
                            ;;
                        zypper)
                            print_step "Installing jq via zypper..."
                            sudo zypper install -y jq
                            print_success "jq installed"
                            ;;
                        *)
                            print_error "Package manager not supported."
                            return 1
                            ;;
                    esac
                    ;;
            esac
        else
            print_warning "jq not installed - git_ops.sh may have limited functionality"
        fi
    fi
}

# Ensure rsync is available — the config/skill deploy in deploy.sh + common.sh
# uses it (the config-tree copy and the skill copy). Best-effort auto-install;
# non-fatal: deploy_home_skills already has a cp fallback, but the config-tree
# rsync (with --exclude) prefers rsync, so we try to provide it. Every path
# returns 0 so the unguarded caller is never aborted under set -e.
check_rsync() {
    if command_exists rsync; then
        print_success "rsync is installed"
        return 0
    fi

    print_step "Installing rsync (used by config/skill deploy)..."

    case "$PLATFORM" in
        macos)
            if command_exists brew && brew install rsync; then
                print_success "rsync installed"
                return 0
            fi
            ;;
        linux)
            case "$PKG_MANAGER" in
                apt)
                    if sudo apt-get update -qq && sudo apt-get install -y -qq rsync; then
                        print_success "rsync installed"
                        return 0
                    fi
                    ;;
                dnf | yum)
                    if sudo "$PKG_MANAGER" install -y rsync; then
                        print_success "rsync installed"
                        return 0
                    fi
                    ;;
                pacman)
                    if sudo pacman -S --noconfirm rsync; then
                        print_success "rsync installed"
                        return 0
                    fi
                    ;;
                zypper)
                    if sudo zypper install -y rsync; then
                        print_success "rsync installed"
                        return 0
                    fi
                    ;;
            esac
            ;;
    esac

    print_warning "Could not install rsync automatically; skill deploy will fall back to cp. Install rsync for the full config-tree sync."
    return 0
}

# Install the cursor-agent CLI (headless Cursor agent used by parallel_agent.py)
check_cursor() {
    if [[ "$ENABLE_CURSOR" == false ]]; then
        print_info "Cursor is disabled - skipping installation"
        return 0
    fi

    print_step "Checking for cursor-agent CLI..."

    if command_exists cursor-agent || [[ -f "$HOME/.local/bin/cursor-agent" ]]; then
        print_success "cursor-agent is installed"
        return 0
    fi

    print_warning "cursor-agent CLI not found"
    echo ""
    echo -e "${BOLD}cursor-agent Installation:${NC}"
    echo "  curl https://cursor.com/install -fsS | bash"
    echo ""

    if prompt_yes_no "Install cursor-agent now?"; then
        if curl https://cursor.com/install -fsS | bash; then
            if command_exists cursor-agent || [[ -f "$HOME/.local/bin/cursor-agent" ]]; then
                print_success "cursor-agent installed"
                print_info "Authenticate with: cursor-agent login  (or set CURSOR_API_KEY)"
            else
                print_warning "cursor-agent installed but not yet on PATH (restart your shell)"
            fi
        else
            print_warning "cursor-agent installation failed"
            if prompt_yes_no "Disable Cursor in service configuration?"; then
                ENABLE_CURSOR=false
            fi
        fi
    else
        print_warning "cursor-agent not installed"
        if prompt_yes_no "Disable Cursor in service configuration?"; then
            ENABLE_CURSOR=false
        fi
    fi
}

# Install the Devin CLI (Cognition's headless coding agent, `devin`).
# Opt-in: only runs when --enable-devin / services.yml turned it on.
# Homebrew cask first (the vendor's documented macOS path, and the only one
# that gives brew the uninstall record); the vendor install script is the
# fallback for Linux and brew-less machines.
check_devin() {
    if [[ "${ENABLE_DEVIN:-false}" == false ]]; then
        print_info "Devin is disabled - skipping installation"
        return 0
    fi

    print_step "Checking for Devin CLI..."

    if command_exists devin || [[ -x "$HOME/.local/bin/devin" ]]; then
        print_success "Devin CLI is installed"
        return 0
    fi

    print_warning "Devin CLI not found"
    echo ""
    echo -e "${BOLD}Devin CLI Installation:${NC}"
    if command_exists brew; then
        echo "  brew install --cask devin-cli"
    else
        echo "  curl -fsSL https://cli.devin.ai/install.sh | bash"
    fi
    echo ""

    if prompt_yes_no "Install the Devin CLI now?"; then
        local installed=false
        if command_exists brew; then
            brew install --cask devin-cli && installed=true
        else
            curl -fsSL https://cli.devin.ai/install.sh | bash && installed=true
        fi

        if [[ "$installed" == true ]] && { command_exists devin || [[ -x "$HOME/.local/bin/devin" ]]; }; then
            print_success "Devin CLI installed"
            print_info "Authenticate with: devin auth login"
        else
            print_warning "Devin CLI installation failed"
            if prompt_yes_no "Disable Devin in service configuration?"; then
                ENABLE_DEVIN=false
            fi
        fi
    else
        print_warning "Devin CLI not installed"
        if prompt_yes_no "Disable Devin in service configuration?"; then
            ENABLE_DEVIN=false
        fi
    fi
}

# Pinned to the exact uv release already attested (real download, sha256-verified)
# for the toolchain store at config/toolchain.lock.json's "uv" entry (C7). Bumping
# this requires re-verifying the new release's published checksums, same as there.
UV_INSTALLER_PINNED_VERSION="0.12.6"

# GitHub release target triple for this host, or empty when uv publishes no
# release for it (uname reports something this bootstrap does not recognize).
_uv_release_target() {
    local os arch
    os="$(uname -s)"
    arch="$(uname -m)"
    case "$os-$arch" in
        Darwin-arm64) echo "aarch64-apple-darwin" ;;
        Darwin-x86_64) echo "x86_64-apple-darwin" ;;
        Linux-x86_64) echo "x86_64-unknown-linux-gnu" ;;
        Linux-aarch64 | Linux-arm64) echo "aarch64-unknown-linux-gnu" ;;
        *) echo "" ;;
    esac
}

# Download the pinned uv release tarball AND its separately-published `.sha256`
# sidecar (both real GitHub release assets, fetched independently of each
# other), verify the tarball's digest against the sidecar's stated value, and
# only then extract `uv`/`uvx` into ~/.local/bin. Replaces piping
# astral.sh/uv/install.sh into `sh` (CON-013: download, verify a checksum,
# then run) with the same verify-then-execute shape config/toolchain.lock.json
# already uses for the toolchain store's copy of this exact uv release.
install_uv_verified_release() {
    local target sha_tool
    target="$(_uv_release_target)"
    if [[ -z "$target" ]]; then
        print_warning "uv: no known release target for $(uname -s)/$(uname -m)"
        return 1
    fi
    if command_exists sha256sum; then
        sha_tool="sha256sum"
    elif command_exists shasum; then
        sha_tool="shasum -a 256"
    else
        print_warning "uv: cannot verify a checksum without sha256sum or shasum"
        return 1
    fi

    local base="https://github.com/astral-sh/uv/releases/download/${UV_INSTALLER_PINNED_VERSION}"
    local archive="uv-${target}.tar.gz"
    local workdir
    workdir="$(mktemp -d)" || return 1
    # shellcheck disable=SC2064 # workdir is fixed at trap-set time, not runtime
    trap "rm -rf '$workdir'" RETURN

    if ! curl -fsSL -o "$workdir/$archive" "$base/$archive"; then
        print_warning "uv: could not download $archive"
        return 1
    fi
    if ! curl -fsSL -o "$workdir/$archive.sha256" "$base/$archive.sha256"; then
        print_warning "uv: could not download $archive.sha256"
        return 1
    fi

    local expected actual
    expected="$(awk '{print $1}' "$workdir/$archive.sha256")"
    actual="$(cd "$workdir" && $sha_tool "$archive" | awk '{print $1}')"
    if [[ -z "$expected" || "$actual" != "$expected" ]]; then
        print_warning "uv: checksum mismatch for $archive (want $expected, got $actual)"
        return 1
    fi

    if ! tar -xzf "$workdir/$archive" -C "$workdir"; then
        print_warning "uv: could not extract $archive"
        return 1
    fi
    mkdir -p "$HOME/.local/bin"
    local extracted="$workdir/uv-${target}"
    if [[ ! -x "$extracted/uv" ]]; then
        print_warning "uv: verified archive did not contain uv/$target"
        return 1
    fi
    install -m 755 "$extracted/uv" "$HOME/.local/bin/uv"
    [[ -x "$extracted/uvx" ]] && install -m 755 "$extracted/uvx" "$HOME/.local/bin/uvx"
    return 0
}

# Idempotent and existence-guarded (Principle V): no-op if uv is already available,
# even when it lives at ~/.local/bin and is not yet on this shell's PATH. Prefers a
# package manager, falling back to a portable pip --user install (Python is a prereq).
check_uv() {
    if command_exists uv || [[ -x "$HOME/.local/bin/uv" ]]; then
        print_success "uv is installed"
        return 0
    fi

    print_step "Installing uv (Python tool installer)..."

    case "$PLATFORM" in
        macos)
            if command_exists brew && brew install uv; then
                print_success "uv installed via Homebrew"
                return 0
            fi
            ;;
        linux)
            # Only pacman reliably packages uv; apt/dnf/yum/zypper do not, so those
            # fall through to the portable pip path below.
            if [[ "$PKG_MANAGER" == "pacman" ]] && sudo pacman -S --noconfirm uv; then
                print_success "uv installed via pacman"
                return 0
            fi
            ;;
    esac

    # Portable fallback 1: a real download of the pinned uv release, verified
    # against its own published checksum, then extracted straight into
    # ~/.local/bin (already on the framework PATH) with no Python/pip -- so it
    # still works on PEP 668 externally-managed interpreters (default
    # Debian/Ubuntu, Homebrew Python) where `pip install --user` is blocked.
    if command_exists curl && install_uv_verified_release; then
        if command_exists uv || [[ -x "$HOME/.local/bin/uv" ]]; then
            print_success "uv installed via the verified release download"
            return 0
        fi
    fi

    # Portable fallback 2: pip --user, for environments that have Python 3 but no
    # curl. May fail on PEP 668 interpreters; callers handle the failure.
    if check_python; then
        local python_cmd="${PYTHON_CMD:-python3}"
        if $python_cmd -m pip install --user --prefer-binary uv; then
            print_success "uv installed via pip --user"
            return 0
        fi
    fi

    print_warning "Could not install uv automatically; see https://docs.astral.sh/uv/"
    return 1
}

# The pinned-apm-wheel install path was removed by spec 674 Phase 5 (T5.4).
# It existed to install the tool that owned ~/.claude/skills; skills now ship
# as plugin bundles and apm owns nothing. Its integrity suites
# (apm_binary_integrity, apm_supply_chain, apm_upgrade_gate) were retired in
# the same commit -- keeping the code without them would have left an
# unguarded network install path, which is strictly worse than either.
# Rollback does NOT need it: apm_ungate_domain.sh and sync-skills.sh invoke
# no apm binary (verified, not assumed).

# Read the three optional-group toggles out of the deployed services.yml in ONE
# probe. Echoes "<smoke> <browser_use> <claude>" as 0/1 flags; returns non-zero
# when no interpreter could parse the file at all.
#
# Three separate `python3 -c … | grep -q 1` probes used to make a probe failure
# (system python3 without PyYAML) indistinguishable from "service disabled": an
# enabled smoke/browser-use service silently got no deps and only failed later,
# inside the runtime. Now a failure is a distinct outcome the caller reports.
read_service_group_flags() {
    local services_yml="$1" target_dir="$2"
    local py
    # The runtime's own interpreter first: it always has PyYAML once synced, so a
    # host python3 without PyYAML stops mattering after the first bootstrap.
    for py in "$target_dir/.venv/bin/python3" "$target_dir/.venv/bin/python" python3; do
        if [[ "$py" == python3 ]]; then
            command_exists python3 || continue
        else
            [[ -x "$py" ]] || continue
        fi
        "$py" - "$services_yml" << 'PY' 2> /dev/null && return 0
import sys

try:
    import yaml
except ImportError:
    sys.exit(2)

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
except (OSError, yaml.YAMLError):
    sys.exit(3)

services = data.get("services") if isinstance(data, dict) else None
services = services if isinstance(services, dict) else {}


def on(name: str) -> str:
    svc = services.get(name)
    return "1" if isinstance(svc, dict) and svc.get("enabled") else "0"


print(on("smoke"), on("browser_use"), on("claude"))
PY
    done
    return 1
}

# Recreate a venv whose interpreter no longer works. A Python upgrade (or a tree
# copied from another home) leaves the directory populated but every console
# script pointing at a dead absolute path, and `uv sync` then fails in ways that
# read like a lockfile problem.
heal_broken_home_venv() {
    local venv="$1"
    [[ "$venv" == */.venv ]] || return 0
    [[ -d "$venv" ]] || return 0
    local py
    for py in "$venv/bin/python3" "$venv/bin/python"; do
        if [[ -x "$py" ]] && "$py" -c "" 2> /dev/null; then
            return 0
        fi
    done
    print_warning "Home venv interpreter is unusable — recreating $venv"
    rm -rf "$venv"
}

# Install ~/.local/bin/manifest defensively. Everything here answers a way the
# destination can already be occupied: by our own current copy (nothing to do),
# by a stale copy (replace), by a symlink (never write THROUGH it — that silently
# overwrites whatever it points at), by another tool's `manifest` (back it up
# rather than clobber), or by a directory (refuse and say so).
install_manifest_wrapper() {
    local src="$SCRIPT_DIR/configs/claude/scripts/manifest-cli.sh"
    local bin_dir="$HOME/.local/bin" dest="$HOME/.local/bin/manifest"

    if [[ ! -f "$src" ]]; then
        print_error "manifest wrapper source is missing ($src) — incomplete checkout?"
        return 1
    fi
    if ! mkdir -p "$bin_dir" 2> /dev/null; then
        print_error "Cannot create $bin_dir — manifest CLI not installed"
        return 1
    fi
    if [[ ! -w "$bin_dir" ]]; then
        print_error "$bin_dir is not writable — manifest CLI not installed"
        return 1
    fi
    if [[ -d "$dest" && ! -L "$dest" ]]; then
        print_error "$dest is a directory — remove it, then re-run ./bootstrap.sh"
        return 1
    fi

    # A bootstrap killed mid-install leaves manifest.tmp.<pid> behind; ~/.local/bin
    # is on PATH, so that litter is a stale executable sitting next to the wrapper.
    rm -f "$bin_dir"/manifest.tmp.* 2> /dev/null || true

    if [[ -L "$dest" ]]; then
        local link_target
        link_target="$(readlink "$dest" 2> /dev/null)" || link_target="?"
        print_warning "Replacing symlink $dest -> $link_target with a managed copy"
        rm -f "$dest" || {
            print_error "Could not remove symlink $dest"
            return 1
        }
    elif [[ -f "$dest" ]] && ! grep -q 'manifest-cli-wrapper' "$dest" 2> /dev/null; then
        # Not ours: a same-named CLI from another project, or a hand-written script.
        local backup="$dest.pre-manifest.bak"
        if [[ -e "$backup" ]]; then
            print_warning "$dest is not Manifest's wrapper; existing backup kept at $backup"
        else
            print_warning "$dest was not installed by Manifest — backing it up to $backup"
            cp -p "$dest" "$backup" 2> /dev/null || print_warning "Could not back up $dest"
        fi
    fi

    if cmp -s "$src" "$dest" 2> /dev/null; then
        # Already installed and byte-identical: heal a lost +x bit and stop.
        [[ -x "$dest" ]] || chmod +x "$dest" 2> /dev/null || true
        print_info "manifest CLI already current at $dest"
        return 0
    fi

    # Write to a sibling temp file and rename: an interrupted or short copy must
    # never become the wrapper every skill and hook invokes.
    local tmp="$dest.tmp.$$"
    if ! cp "$src" "$tmp" 2> /dev/null; then
        rm -f "$tmp"
        print_error "Could not write $tmp — manifest CLI not installed"
        return 1
    fi
    if ! chmod +x "$tmp" || ! mv -f "$tmp" "$dest"; then
        rm -f "$tmp"
        print_error "Could not install $dest — manifest CLI not installed"
        return 1
    fi
    # Verify what landed instead of trusting cp's exit status (full disk, quota).
    if ! cmp -s "$src" "$dest" || [[ ! -x "$dest" ]]; then
        print_error "$dest did not verify after install — re-run ./bootstrap.sh"
        return 1
    fi
    print_success "manifest CLI installed at $dest"
    return 0
}

# Record install provenance outside ~/.claude so it survives that tree being
# deleted — which is exactly when the wrapper needs to name the clone to re-run.
write_runtime_stamp() {
    local target_dir="$1" groups="$2"
    local state_root="${MANIFEST_STATE_ROOT:-$HOME/.manifest}"
    mkdir -p "$state_root" 2> /dev/null || return 0
    local tmp="$state_root/runtime.env.tmp.$$"
    {
        echo "clone_path=$SCRIPT_DIR"
        echo "runtime_root=$target_dir"
        echo "groups=$groups"
        echo "wrapper=$HOME/.local/bin/manifest"
        echo "synced_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$tmp" 2> /dev/null || {
        rm -f "$tmp"
        return 0
    }
    mv -f "$tmp" "$state_root/runtime.env" 2> /dev/null || rm -f "$tmp"
    return 0
}

# Sync the home Python runtime via uv (replaces pip install --user for parallel_agent deps).
# Reads deployed services.yml for optional dependency groups, runs uv sync, optionally
# installs Playwright Chromium for smoke, and deploys ~/.local/bin/manifest wrapper.
#
# Fail-open by design: every failure below warns and returns 0 so one broken
# runtime cannot abort a bootstrap that still has assistants to configure. The
# runtime's own `manifest doctor` and env-check are the fail-closed gates.
uv_sync_home_runtime() {
    local target_dir="${TARGET_DIR:-$HOME/.claude}"
    local uv_bin=""
    if command_exists uv; then
        uv_bin="$(command -v uv)"
    elif [[ -x "$HOME/.local/bin/uv" ]]; then
        uv_bin="$HOME/.local/bin/uv"
    else
        print_warning "uv not found — skipping home runtime sync"
        return 0
    fi

    # uv sync needs both deploy artifacts. Without this check the failure surfaces
    # as a raw uv error about a missing project, blaming the wrong thing.
    local artifact
    for artifact in pyproject.toml uv.lock; do
        if [[ ! -f "$target_dir/$artifact" ]]; then
            print_error "$target_dir/$artifact is missing — deploy is incomplete, skipping home runtime sync"
            return 0
        fi
    done

    local -a group_flags=()
    local install_playwright=false
    local groups_csv="core"
    local services_yml="$target_dir/config/services.yml"
    local flags="" smoke_on=0 browser_on=0 claude_on=0

    if [[ ! -f "$services_yml" ]]; then
        print_warning "$services_yml not found — syncing core runtime only (no optional groups)"
    elif flags="$(read_service_group_flags "$services_yml" "$target_dir")"; then
        read -r smoke_on browser_on claude_on <<< "$flags" || true
    else
        # No interpreter could parse services.yml. Preserving the previous sync's
        # group selection beats silently downgrading an enabled service to core.
        local prev_groups=""
        prev_groups="$(sed -n 's/^groups=//p' "${MANIFEST_STATE_ROOT:-$HOME/.manifest}/runtime.env" 2> /dev/null | tail -n 1)" || prev_groups=""
        print_warning "Could not read $services_yml (unreadable, not valid YAML, or no interpreter with PyYAML) — optional groups unresolved"
        case "$prev_groups" in
            *smoke-agent*) browser_on=1 ;;
        esac
        case "$prev_groups" in
            *smoke*) smoke_on=1 ;;
        esac
        case "$prev_groups" in
            *claude*) claude_on=1 ;;
        esac
        if [[ -n "$prev_groups" && "$prev_groups" != "core" ]]; then
            print_warning "Reusing the previous sync's groups ($prev_groups) from the runtime stamp"
        fi
    fi

    # browser-use implies smoke (its deps layer on top), so fold the two toggles
    # into one group list. Deduplicated because groups_csv is recorded in the
    # runtime stamp and read back by the unresolved-services path above.
    if [[ "$browser_on" == 1 ]]; then
        smoke_on=1
    fi
    if [[ "$smoke_on" == 1 ]]; then
        group_flags+=(--group smoke)
        install_playwright=true
        groups_csv="$groups_csv,smoke"
    fi
    if [[ "$browser_on" == 1 ]]; then
        group_flags+=(--group smoke-agent)
        groups_csv="$groups_csv,smoke-agent"
    fi
    if [[ "$claude_on" == 1 ]]; then
        group_flags+=(--group claude)
        groups_csv="$groups_csv,claude"
    fi

    heal_broken_home_venv "$target_dir/.venv"

    print_step "Syncing home Python runtime (uv)..."
    if ! "$uv_bin" sync --project "$target_dir" "${group_flags[@]+"${group_flags[@]}"}"; then
        print_warning "uv sync failed — parallel agent may be unavailable"
        if [[ -x "$HOME/.local/bin/manifest" ]]; then
            print_warning "Existing manifest CLI kept, but its runtime may be stale — fix uv and re-run ./bootstrap.sh"
        fi
        return 0
    fi

    # uv can exit 0 with a venv that lacks the console script (e.g. a pyproject
    # that stopped declaring the package). Warn, but still install the wrapper:
    # its runtime check names this state precisely, which beats `command not found`.
    if [[ ! -e "$target_dir/.venv/bin/manifest" ]]; then
        print_warning "uv sync completed but $target_dir/.venv/bin/manifest is missing — check $target_dir/pyproject.toml"
    fi

    if [[ "$install_playwright" == true ]]; then
        if [[ -x "$target_dir/.venv/bin/playwright" ]]; then
            "$target_dir/.venv/bin/playwright" install chromium || print_warning "playwright install chromium failed"
        else
            print_warning "smoke is enabled but $target_dir/.venv/bin/playwright is missing — skipping browser install"
        fi
    fi

    ensure_local_bin_on_path
    install_manifest_wrapper || true
    write_runtime_stamp "$target_dir" "$groups_csv"
    print_success "Home runtime synced (groups: $groups_csv)"
}
