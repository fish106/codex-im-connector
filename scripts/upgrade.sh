#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/fish106/codex-im-connector.git"
LAUNCHD_LABEL_DEFAULT="com.fish106.codex-im-connector"
DEFAULT_APP_ROOT="~/.cic"
LANG_CODE="en"
COLOR_RESET=$'\033[0m'
COLOR_RED=$'\033[31m'
COLOR_CYAN=$'\033[36m'
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"

detect_lang() {
  local locale="${LC_ALL:-${LC_MESSAGES:-${LANG:-en_US.UTF-8}}}"
  case "$locale" in
    zh*|ZH*) LANG_CODE="zh" ;;
    *) LANG_CODE="en" ;;
  esac
}

tr() {
  local key="$1"
  case "$LANG_CODE:$key" in
    zh:this_upgrader_only_supports_macos) echo "此升级脚本仅支持 macOS。" ;;
    en:this_upgrader_only_supports_macos) echo "This upgrade script only supports macOS." ;;
    zh:git_required) echo "需要先安装 git。请先安装 Xcode Command Line Tools 或 git。" ;;
    en:git_required) echo "git is required. Install Xcode Command Line Tools or git first." ;;
    zh:uv_not_found_installing) echo "未找到 uv，正在安装 uv..." ;;
    en:uv_not_found_installing) echo "uv not found. Installing uv..." ;;
    zh:curl_required_for_uv) echo "自动安装 uv 需要 curl。" ;;
    en:curl_required_for_uv) echo "curl is required to install uv automatically." ;;
    zh:uv_install_failed) echo "uv 安装失败。" ;;
    en:uv_install_failed) echo "uv installation failed." ;;
    zh:ensuring_python) echo "正在通过 uv 确保 Python 3.12 可用..." ;;
    en:ensuring_python) echo "Ensuring Python 3.12 is available through uv..." ;;
    zh:python_not_found) echo "未能找到可用的 Python 3.12 运行时。" ;;
    en:python_not_found) echo "Failed to locate a usable Python 3.12 runtime." ;;
    zh:install_meta_missing) echo "未找到安装元数据文件。" ;;
    en:install_meta_missing) echo "Install metadata file not found." ;;
    zh:install_meta_hint) echo "如果你安装时使用了非默认 APP_ROOT_PATH，请在执行脚本前导出 APP_ROOT_PATH，或直接运行本机源码目录中的 scripts/upgrade.sh。" ;;
    en:install_meta_hint) echo "If you installed with a non-default APP_ROOT_PATH, export APP_ROOT_PATH before running this script, or run scripts/upgrade.sh from the local repository checkout." ;;
    zh:repo_dir_missing) echo "安装元数据中的仓库目录不存在：" ;;
    en:repo_dir_missing) echo "Repository directory from install metadata does not exist:" ;;
    zh:repo_mismatch) echo "现有目录不是目标仓库：" ;;
    en:repo_mismatch) echo "Existing repository does not match target repository:" ;;
    zh:repo_has_local_changes) echo "现有仓库有本地修改，请先清理后再升级。" ;;
    en:repo_has_local_changes) echo "Existing repository has local changes. Clean it before upgrading." ;;
    zh:repo_path_label) echo "仓库目录" ;;
    en:repo_path_label) echo "Repository path" ;;
    zh:updating_repo) echo "正在更新仓库代码..." ;;
    en:updating_repo) echo "Updating repository..." ;;
    zh:installing_dependencies) echo "正在使用 uv 同步 Python 依赖..." ;;
    en:installing_dependencies) echo "Syncing Python dependencies with uv..." ;;
    zh:dependencies_may_take_minutes) echo "升级时如果需要更新依赖，可能会联网下载几分钟，请耐心等待。" ;;
    en:dependencies_may_take_minutes) echo "If dependency updates are needed, the upgrade may download packages from the network for a few minutes." ;;
    zh:plist_template_missing) echo "未找到 LaunchAgent 模板：" ;;
    en:plist_template_missing) echo "LaunchAgent template not found at" ;;
    zh:venv_python_missing) echo "未找到虚拟环境 Python：" ;;
    en:venv_python_missing) echo "Expected virtualenv python not found at" ;;
    zh:dependency_sync_may_have_failed) echo "依赖同步可能失败了，请检查上面的 uv 输出。" ;;
    en:dependency_sync_may_have_failed) echo "Dependency sync may have failed. Check the uv output above." ;;
    zh:upgrading_summary) echo "将保留现有 .env 配置，只更新代码、依赖并重启后台服务。" ;;
    en:upgrading_summary) echo "The existing .env configuration will be preserved. Only code, dependencies, and the background service will be updated." ;;
    zh:upgrade_completed) echo "升级已完成。" ;;
    en:upgrade_completed) echo "Upgrade completed successfully." ;;
    zh:useful_commands) echo "常用命令：" ;;
    en:useful_commands) echo "Useful commands:" ;;
    zh:configuration_preserved) echo "现有 .env 配置未被改写。" ;;
    en:configuration_preserved) echo "The existing .env file was not rewritten." ;;
    *) echo "$key" ;;
  esac
}

log() {
  printf '[upgrade] %s\n' "$*"
}

hint() {
  printf '[upgrade] %b%s%b\n' "$COLOR_CYAN" "$*" "$COLOR_RESET"
}

die() {
  printf '[upgrade] %bERROR: %s%b\n' "$COLOR_RED" "$*" "$COLOR_RESET" >&2
  exit 1
}

require_macos() {
  [[ "$(uname -s)" == "Darwin" ]] || die "$(tr this_upgrader_only_supports_macos)"
}

require_git() {
  command -v git >/dev/null 2>&1 || die "$(tr git_required)"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi

  log "$(tr uv_not_found_installing)"
  command -v curl >/dev/null 2>&1 || die "$(tr curl_required_for_uv)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  hash -r
  command -v uv >/dev/null 2>&1 || die "$(tr uv_install_failed)"
}

ensure_python() {
  log "$(tr ensuring_python)"
  uv python install 3.12 >/dev/null
  BOOTSTRAP_PYTHON="$(uv python find 3.12)"
  [[ -n "${BOOTSTRAP_PYTHON:-}" && -x "$BOOTSTRAP_PYTHON" ]] || die "$(tr python_not_found)"
}

normalize_path() {
  "$BOOTSTRAP_PYTHON" - "$1" <<'PY'
from pathlib import Path
import sys

raw = sys.argv[1]
path = Path(raw).expanduser()
if not path.is_absolute():
    path = (Path.cwd() / path).resolve()
else:
    path = path.resolve()
print(path)
PY
}

read_meta_value() {
  "$BOOTSTRAP_PYTHON" - "$1" "$2" <<'PY'
from pathlib import Path
import sys

meta_path = Path(sys.argv[1])
key = sys.argv[2]
for line in meta_path.read_text(encoding="utf-8").splitlines():
    if "=" not in line:
        continue
    current_key, current_value = line.split("=", 1)
    if current_key == key:
        print(current_value)
        break
PY
}

load_install_meta() {
  local candidates=()

  if [[ -n "${APP_ROOT_PATH:-}" ]]; then
    candidates+=("$(normalize_path "$APP_ROOT_PATH")")
  fi

  if [[ -n "$SCRIPT_SOURCE" && -f "$SCRIPT_SOURCE" ]]; then
    local script_dir repo_root inferred_app_root
    script_dir="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd -P)"
    repo_root="$(cd "$script_dir/.." && pwd -P)"
    if [[ -d "$repo_root/.git" ]]; then
      inferred_app_root="$(cd "$repo_root/../.." && pwd -P)"
      candidates+=("$inferred_app_root")
    fi
  fi

  candidates+=("$(normalize_path "$DEFAULT_APP_ROOT")")

  local candidate
  for candidate in "${candidates[@]}"; do
    INSTALL_META_PATH="$candidate/install-meta.env"
    if [[ -f "$INSTALL_META_PATH" ]]; then
      APP_ROOT_PATH="$candidate"
      break
    fi
  done

  if [[ -z "${APP_ROOT_PATH:-}" || ! -f "${INSTALL_META_PATH:-}" ]]; then
    local requested_root="${APP_ROOT_PATH:-$(normalize_path "$DEFAULT_APP_ROOT")}"
    die "$(tr install_meta_missing) $requested_root/install-meta.env
$(tr install_meta_hint)"
  fi

  REPO_DIR="$(read_meta_value "$INSTALL_META_PATH" "REPO_DIR")"
  PLIST_PATH="$(read_meta_value "$INSTALL_META_PATH" "PLIST_PATH")"
  LAUNCHD_LABEL="$(read_meta_value "$INSTALL_META_PATH" "LAUNCHD_LABEL")"

  [[ -n "${REPO_DIR:-}" ]] || REPO_DIR="$APP_ROOT_PATH/src/codex-im-connector"
  [[ -n "${PLIST_PATH:-}" ]] || PLIST_PATH="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL_DEFAULT}.plist"
  [[ -n "${LAUNCHD_LABEL:-}" ]] || LAUNCHD_LABEL="$LAUNCHD_LABEL_DEFAULT"

  STDOUT_LOG="$APP_ROOT_PATH/logs/stdout.log"
  STDERR_LOG="$APP_ROOT_PATH/logs/stderr.log"
}

validate_repo() {
  [[ -d "$REPO_DIR/.git" ]] || die "$(tr repo_dir_missing) $REPO_DIR"

  local remote_url
  remote_url="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
  if [[ "$remote_url" != "$REPO_URL" && "$remote_url" != "${REPO_URL%.git}" ]]; then
    die "$(tr repo_mismatch) $REPO_DIR"
  fi

  if [[ -n "$(git -C "$REPO_DIR" status --porcelain --untracked-files=normal)" ]]; then
    die "$(tr repo_has_local_changes)
$(tr repo_path_label): $REPO_DIR"
  fi
}

update_repo() {
  log "$(tr updating_repo)"
  git -C "$REPO_DIR" fetch --all --prune
  local default_branch
  default_branch="$(git -C "$REPO_DIR" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')"
  default_branch="${default_branch:-main}"
  git -C "$REPO_DIR" checkout "$default_branch"
  git -C "$REPO_DIR" pull --ff-only origin "$default_branch"
}

sync_dependencies() {
  log "$(tr installing_dependencies)"
  log "$(tr dependencies_may_take_minutes)"
  uv sync --project "$REPO_DIR" --python "$BOOTSTRAP_PYTHON"
}

render_launch_agent_plist() {
  local template_path="$REPO_DIR/scripts/com.fish106.codex-im-connector.plist.template"
  [[ -f "$template_path" ]] || die "$(tr plist_template_missing) $template_path"

  "$BOOTSTRAP_PYTHON" - "$template_path" "$PLIST_PATH" "$LAUNCHD_LABEL" "$PYTHON_BIN" "$REPO_DIR" "$STDOUT_LOG" "$STDERR_LOG" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
plist_path = Path(sys.argv[2])
label = sys.argv[3]
python_bin = sys.argv[4]
repo_dir = sys.argv[5]
stdout_log = sys.argv[6]
stderr_log = sys.argv[7]

content = template_path.read_text(encoding="utf-8")
content = content.replace("__LAUNCHD_LABEL__", label)
content = content.replace("__PYTHON_BIN__", python_bin)
content = content.replace("__REPO_DIR__", repo_dir)
content = content.replace("__STDOUT_LOG__", stdout_log)
content = content.replace("__STDERR_LOG__", stderr_log)
plist_path.write_text(content, encoding="utf-8")
PY
}

restart_launch_agent() {
  mkdir -p "$(dirname "$PLIST_PATH")" "$APP_ROOT_PATH/logs"
  render_launch_agent_plist
  launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
  launchctl enable "gui/$(id -u)/$LAUNCHD_LABEL"
  launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
}

main() {
  detect_lang
  require_macos
  require_git
  ensure_uv
  ensure_python
  load_install_meta

  hint "$(tr upgrading_summary)"
  validate_repo
  update_repo
  sync_dependencies

  PYTHON_BIN="$REPO_DIR/.venv/bin/python"
  [[ -x "$PYTHON_BIN" ]] || die "$(tr venv_python_missing) $PYTHON_BIN
$(tr dependency_sync_may_have_failed)"

  restart_launch_agent

  log "$(tr upgrade_completed)"
  log "$(tr configuration_preserved)"
  log "$(tr useful_commands)"
  printf '  launchctl print gui/%s/%s\n' "$(id -u)" "$LAUNCHD_LABEL"
  printf '  tail -f %s\n' "$STDERR_LOG"
}

main "$@"
