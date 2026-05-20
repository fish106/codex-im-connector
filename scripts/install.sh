#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/fish106/codex-im-connector.git"
LAUNCHD_LABEL="com.fish106.codex-im-connector"
DEFAULT_APP_ROOT="~/.cic"
LANG_CODE="en"
COLOR_RESET=$'\033[0m'
COLOR_RED=$'\033[31m'
COLOR_CYAN=$'\033[36m'

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
    zh:this_installer_only_supports_macos) echo "此安装脚本仅支持 macOS。" ;;
    en:this_installer_only_supports_macos) echo "This installer only supports macOS." ;;
    zh:do_not_run_with_sudo) echo "请不要使用 sudo 运行安装脚本。它会安装当前用户的 LaunchAgent，并写入当前用户目录。" ;;
    en:do_not_run_with_sudo) echo "Do not run the installer with sudo. It installs a LaunchAgent for the current user and writes into that user's home directory." ;;
    zh:interactive_tty_required) echo "安装脚本需要可交互终端。请不要使用会占用标准输入的方式运行；推荐使用 bash <(curl -fsSL .../install.sh)。" ;;
    en:interactive_tty_required) echo "The installer requires an interactive terminal. Do not run it in a way that consumes stdin; use bash <(curl -fsSL .../install.sh) instead." ;;
    zh:unsupported_sandbox_terminal) echo "检测到当前运行在受限沙箱终端中，无法向 macOS 注册 LaunchAgent。请改用 Terminal.app 或 iTerm2 运行安装脚本。" ;;
    en:unsupported_sandbox_terminal) echo "A restricted sandboxed terminal was detected. This environment cannot register a macOS LaunchAgent. Run the installer from Terminal.app or iTerm2 instead." ;;
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
    zh:codex_bin_must_be_executable) echo "CODEX_BIN 必须指向一个存在且可执行的文件。" ;;
    en:codex_bin_must_be_executable) echo "CODEX_BIN must point to an existing executable file." ;;
    zh:value_required) echo "该项为必填。" ;;
    en:value_required) echo "This value is required." ;;
    zh:repo_mismatch) echo "现有目录不是目标仓库：" ;;
    en:repo_mismatch) echo "Existing repository does not match target repository:" ;;
    zh:repo_has_local_changes) echo "现有仓库有本地修改，请先清理后再重装。" ;;
    en:repo_has_local_changes) echo "Existing repository has local changes. Clean it before reinstalling." ;;
    zh:repo_path_label) echo "仓库目录" ;;
    en:repo_path_label) echo "Repository path" ;;
    zh:updating_repo) echo "正在更新已存在的仓库..." ;;
    en:updating_repo) echo "Updating existing repository..." ;;
    zh:target_path_exists) echo "目标路径已存在且不是预期仓库：" ;;
    en:target_path_exists) echo "Target path already exists and is not the expected git repository:" ;;
    zh:cloning_repo) echo "正在克隆仓库到" ;;
    en:cloning_repo) echo "Cloning repository into" ;;
    zh:installing_dependencies) echo "正在使用 uv 安装 Python 依赖..." ;;
    en:installing_dependencies) echo "Installing Python dependencies with uv..." ;;
    zh:dependencies_may_take_minutes) echo "首次安装会联网下载依赖，可能需要几分钟，请耐心等待。" ;;
    en:dependencies_may_take_minutes) echo "The first installation may download dependencies from the network and can take a few minutes." ;;
    zh:plist_template_missing) echo "未找到 LaunchAgent 模板：" ;;
    en:plist_template_missing) echo "LaunchAgent template not found at" ;;
    zh:env_example_missing) echo "仓库中缺少 .env.example。" ;;
    en:env_example_missing) echo ".env.example not found in repository." ;;
    zh:allowed_effort_values) echo "允许的 reasoning effort 值：none, minimal, low, medium, high, xhigh。" ;;
    en:allowed_effort_values) echo "Allowed reasoning effort values: none, minimal, low, medium, high, xhigh." ;;
    zh:configuration_summary) echo "配置摘要：" ;;
    en:configuration_summary) echo "Configuration summary:" ;;
    zh:proceed_install) echo "确认继续安装吗？ [Y/n]: " ;;
    en:proceed_install) echo "Proceed with installation? [Y/n]: " ;;
    zh:installation_cancelled) echo "安装已取消。" ;;
    en:installation_cancelled) echo "Installation cancelled." ;;
    zh:venv_python_missing) echo "未找到虚拟环境 Python：" ;;
    en:venv_python_missing) echo "Expected virtualenv python not found at" ;;
    zh:dependency_sync_may_have_failed) echo "依赖安装可能失败了，请检查上面的 uv 输出。" ;;
    en:dependency_sync_may_have_failed) echo "Dependency installation may have failed. Check the uv output above." ;;
    zh:install_completed) echo "安装已完成。" ;;
    en:install_completed) echo "Installation completed successfully." ;;
    zh:launch_agent_start_failed) echo "后台服务启动失败，当前安装不可用。" ;;
    en:launch_agent_start_failed) echo "The background service failed to start. This installation is not usable yet." ;;
    zh:launch_agent_running) echo "后台服务已经成功启动，安装可正常使用。" ;;
    en:launch_agent_running) echo "The background service is running successfully. The installation is ready to use." ;;
    zh:launch_agent_login) echo "这是用户级 LaunchAgent，会在你登录后后台自动运行。" ;;
    en:launch_agent_login) echo "The LaunchAgent will run in the background after you log in." ;;
    zh:background_items_notice) echo "如果 macOS 显示后台项目提示，请到“系统设置 > 通用 > 登录项”中查看。" ;;
    en:background_items_notice) echo "If macOS shows a Background Items notice, review it in System Settings > General > Login Items." ;;
    zh:useful_commands) echo "常用命令：" ;;
    en:useful_commands) echo "Useful commands:" ;;
    zh:prompt_app_root) echo "APP_ROOT_PATH" ;;
    en:prompt_app_root) echo "APP_ROOT_PATH" ;;
    zh:prompt_codex_bin) echo "CODEX_BIN（本地 codex 可执行文件绝对路径）" ;;
    en:prompt_codex_bin) echo "CODEX_BIN (absolute path to your codex binary)" ;;
    zh:prompt_allowed_roots) echo "CODEX_ALLOWED_CWD_ROOTS（可选，逗号分隔路径）" ;;
    en:prompt_allowed_roots) echo "CODEX_ALLOWED_CWD_ROOTS (comma-separated paths, optional)" ;;
    zh:prompt_model) echo "CODEX_MODEL" ;;
    en:prompt_model) echo "CODEX_MODEL" ;;
    zh:prompt_effort) echo "CODEX_DEFAULT_REASONING_EFFORT" ;;
    en:prompt_effort) echo "CODEX_DEFAULT_REASONING_EFFORT" ;;
    zh:prompt_app_id) echo "FEISHU_APP_ID" ;;
    en:prompt_app_id) echo "FEISHU_APP_ID" ;;
    zh:prompt_app_secret) echo "FEISHU_APP_SECRET" ;;
    en:prompt_app_secret) echo "FEISHU_APP_SECRET" ;;
    zh:explain_app_root) echo "APP_ROOT_PATH 用来保存这个应用的所有本地数据，包括代码目录、workspace、数据库和日志。使用默认值请直接回车。" ;;
    en:explain_app_root) echo "APP_ROOT_PATH stores all local application data, including the code directory, workspace, database, and logs. Press Enter to use the default value." ;;
    zh:explain_codex_bin) echo "请填写本机 Codex CLI 可执行文件的绝对路径，例如 /opt/homebrew/bin/codex。此项必填，不能留空。" ;;
    en:explain_codex_bin) echo "Enter the absolute path to the local Codex CLI executable, for example /opt/homebrew/bin/codex. This value is required and cannot be empty." ;;
    zh:explain_allowed_roots) echo "可选填写额外允许的工作目录根路径，使用逗号分隔多个路径。留空则只允许 APP_ROOT_PATH/workspace 及其子目录。" ;;
    en:explain_allowed_roots) echo "Optionally enter additional allowed working-directory root paths, separated by commas. Leave empty to allow only APP_ROOT_PATH/workspace and its subdirectories." ;;
    zh:explain_model) echo "可选设置默认模型。直接回车将使用 .env.example 中的默认值。" ;;
    en:explain_model) echo "Optionally set the default model. Press Enter to use the default value from .env.example." ;;
    zh:explain_effort) echo "可选设置默认推理强度。允许值：none, minimal, low, medium, high, xhigh。直接回车使用默认值。" ;;
    en:explain_effort) echo "Optionally set the default reasoning effort. Allowed values: none, minimal, low, medium, high, xhigh. Press Enter to use the default value." ;;
    zh:explain_app_id) echo "请输入飞书应用的 App ID。此项必填。" ;;
    en:explain_app_id) echo "Enter the Feishu application's App ID. This value is required." ;;
    zh:explain_app_secret) echo "请输入飞书应用的 App Secret。此项必填，安装脚本会写入 .env。" ;;
    en:explain_app_secret) echo "Enter the Feishu application's App Secret. This value is required and will be written to .env by the installer." ;;
    zh:summary_app_root) echo "APP_ROOT_PATH" ;;
    en:summary_app_root) echo "APP_ROOT_PATH" ;;
    zh:summary_workspace) echo "派生 workspace" ;;
    en:summary_workspace) echo "Derived workspace" ;;
    zh:summary_sqlite) echo "派生 SQLite 路径" ;;
    en:summary_sqlite) echo "Derived SQLite path" ;;
    zh:summary_codex_bin) echo "CODEX_BIN" ;;
    en:summary_codex_bin) echo "CODEX_BIN" ;;
    zh:summary_allowed_roots) echo "CODEX_ALLOWED_CWD_ROOTS" ;;
    en:summary_allowed_roots) echo "CODEX_ALLOWED_CWD_ROOTS" ;;
    zh:summary_model) echo "CODEX_MODEL" ;;
    en:summary_model) echo "CODEX_MODEL" ;;
    zh:summary_effort) echo "CODEX_DEFAULT_REASONING_EFFORT" ;;
    en:summary_effort) echo "CODEX_DEFAULT_REASONING_EFFORT" ;;
    zh:summary_app_id) echo "FEISHU_APP_ID" ;;
    en:summary_app_id) echo "FEISHU_APP_ID" ;;
    zh:summary_app_secret) echo "FEISHU_APP_SECRET" ;;
    en:summary_app_secret) echo "FEISHU_APP_SECRET" ;;
    *) echo "$key" ;;
  esac
}

log() {
  printf '[install] %s\n' "$*"
}

hint() {
  printf '[install] %b%s%b\n' "$COLOR_CYAN" "$*" "$COLOR_RESET"
}

die() {
  printf '[install] %bERROR: %s%b\n' "$COLOR_RED" "$*" "$COLOR_RESET" >&2
  exit 1
}

require_macos() {
  [[ "$(uname -s)" == "Darwin" ]] || die "$(tr this_installer_only_supports_macos)"
}

require_non_root() {
  [[ "$(id -u)" -ne 0 ]] || die "$(tr do_not_run_with_sudo)"
}

require_interactive_tty() {
  [[ -r /dev/tty ]] || die "$(tr interactive_tty_required)"
}

require_non_sandboxed_terminal() {
  if [[ -n "${CODEX_SANDBOX:-}" || "${__CFBundleIdentifier:-}" == "com.openai.codex" ]]; then
    die "$(tr unsupported_sandbox_terminal)"
  fi
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

normalize_existing_executable() {
  local input="$1"
  local normalized
  normalized="$(normalize_path "$input")"
  [[ -f "$normalized" && -x "$normalized" ]] || die "$(tr codex_bin_must_be_executable)"
  printf '%s\n' "$normalized"
}

json_array_from_csv() {
  "$BOOTSTRAP_PYTHON" - "$1" <<'PY'
import json
import sys
from pathlib import Path

raw = sys.argv[1].strip()
if not raw:
    print("[]")
    raise SystemExit(0)

parts = [item.strip() for item in raw.split(",") if item.strip()]
items = []
for part in parts:
    path = Path(part).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    items.append(str(path))
print(json.dumps(items))
PY
}

get_env_value() {
  local key="$1"
  local file="$2"
  grep -E "^${key}=" "$file" | head -n 1 | cut -d= -f2-
}

set_env_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  "$BOOTSTRAP_PYTHON" - "$file" "$key" "$value" <<'PY'
from pathlib import Path
import sys

file_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = file_path.read_text(encoding="utf-8").splitlines()
updated = False
for index, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[index] = f"{key}={value}"
        updated = True
        break
if not updated:
    lines.append(f"{key}={value}")
file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

mask_secret() {
  local value="$1"
  local length="${#value}"
  if (( length <= 4 )); then
    printf '****\n'
    return
  fi
  printf '%s****%s\n' "${value:0:2}" "${value: -2}"
}

prompt_required() {
  local prompt="$1"
  local value=""
  while true; do
    read -r -p "$prompt: " value < /dev/tty
    if [[ -n "${value// }" ]]; then
      printf '%s\n' "$value"
      return
    fi
    log "$(tr value_required)"
  done
}

prompt_optional() {
  local prompt="$1"
  local default_value="$2"
  local value=""
  read -r -p "$prompt [$default_value]: " value < /dev/tty
  if [[ -z "${value}" ]]; then
    printf '%s\n' "$default_value"
  else
    printf '%s\n' "$value"
  fi
}

prepare_directories() {
  mkdir -p "$APP_ROOT_PATH" "$APP_ROOT_PATH/src" "$APP_ROOT_PATH/workspace" "$APP_ROOT_PATH/data" "$APP_ROOT_PATH/logs"
}

clone_or_update_repo() {
  if [[ -d "$REPO_DIR/.git" ]]; then
    local remote_url
    remote_url="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
    if [[ "$remote_url" != "$REPO_URL" && "$remote_url" != "${REPO_URL%.git}" ]]; then
      die "$(tr repo_mismatch) $REPO_DIR"
    fi
    if ! git -C "$REPO_DIR" diff --quiet || ! git -C "$REPO_DIR" diff --cached --quiet; then
      die "$(tr repo_has_local_changes)
      $(tr repo_path_label): $REPO_DIR"
    fi
    log "$(tr updating_repo)"
    git -C "$REPO_DIR" fetch --all --prune
    local default_branch
    default_branch="$(git -C "$REPO_DIR" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')"
    default_branch="${default_branch:-main}"
    git -C "$REPO_DIR" checkout "$default_branch"
    git -C "$REPO_DIR" pull --ff-only origin "$default_branch"
    return
  fi

  if [[ -e "$REPO_DIR" ]]; then
    die "$(tr target_path_exists) $REPO_DIR"
  fi

  log "$(tr cloning_repo) $REPO_DIR..."
  git clone "$REPO_URL" "$REPO_DIR"
}

sync_dependencies() {
  log "$(tr installing_dependencies)"
  log "$(tr dependencies_may_take_minutes)"
  uv sync --project "$REPO_DIR" --python "$BOOTSTRAP_PYTHON"
}

write_install_meta() {
  cat > "$APP_ROOT_PATH/install-meta.env" <<EOF
APP_ROOT_PATH=$APP_ROOT_PATH
REPO_DIR=$REPO_DIR
PLIST_PATH=$PLIST_PATH
LAUNCHD_LABEL=$LAUNCHD_LABEL
EOF
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

install_launch_agent() {
  local domain="gui/$(id -u)"
  local service_target="$domain/$LAUNCHD_LABEL"

  mkdir -p "$(dirname "$PLIST_PATH")"
  render_launch_agent_plist
  launchctl bootout "$domain" "$PLIST_PATH" >/dev/null 2>&1 || true
  launchctl enable "$service_target" >/dev/null 2>&1 || true

  if ! launchctl bootstrap "$domain" "$PLIST_PATH"; then
    if launchctl print "$service_target" >/dev/null 2>&1; then
      log "$(tr launch_agent_running)"
    else
      launchctl load -w "$PLIST_PATH" >/dev/null 2>&1 || true
      launchctl enable "$service_target" >/dev/null 2>&1 || true
      if ! launchctl print "$service_target" >/dev/null 2>&1; then
        die "$(tr launch_agent_start_failed)"
      fi
      log "$(tr launch_agent_running)"
    fi
  fi

  launchctl enable "$service_target"
  launchctl kickstart -k "$service_target" >/dev/null 2>&1 || true
}

main() {
  detect_lang
  require_macos
  require_non_root
  require_interactive_tty
  require_non_sandboxed_terminal
  require_git
  ensure_uv
  ensure_python

  local app_root_input
  hint "$(tr explain_app_root)"
  app_root_input="$(prompt_optional "$(tr prompt_app_root)" "$DEFAULT_APP_ROOT")"
  APP_ROOT_PATH="$(normalize_path "$app_root_input")"
  REPO_DIR="$APP_ROOT_PATH/src/codex-im-connector"
  STDOUT_LOG="$APP_ROOT_PATH/logs/stdout.log"
  STDERR_LOG="$APP_ROOT_PATH/logs/stderr.log"
  PLIST_PATH="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"

  prepare_directories
  clone_or_update_repo
  sync_dependencies

  local env_example="$REPO_DIR/.env.example"
  local env_file="$REPO_DIR/.env"
  [[ -f "$env_example" ]] || die "$(tr env_example_missing)"
  cp "$env_example" "$env_file"

  local default_model
  default_model="$(get_env_value "CODEX_MODEL" "$env_example")"
  local default_effort
  default_effort="$(get_env_value "CODEX_DEFAULT_REASONING_EFFORT" "$env_example")"

  local codex_bin_input
  hint "$(tr explain_codex_bin)"
  codex_bin_input="$(prompt_required "$(tr prompt_codex_bin)")"
  local codex_bin
  codex_bin="$(normalize_existing_executable "$codex_bin_input")"

  local cwd_roots_input
  hint "$(tr explain_allowed_roots)"
  read -r -p "$(tr prompt_allowed_roots): " cwd_roots_input < /dev/tty
  local allowed_roots_json
  allowed_roots_json="$(json_array_from_csv "$cwd_roots_input")"

  local model_input
  hint "$(tr explain_model)"
  model_input="$(prompt_optional "$(tr prompt_model)" "$default_model")"

  local effort_input
  hint "$(tr explain_effort)"
  while true; do
    effort_input="$(prompt_optional "$(tr prompt_effort)" "$default_effort")"
    case "$effort_input" in
      none|minimal|low|medium|high|xhigh) break ;;
      *) log "$(tr allowed_effort_values)" ;;
    esac
  done

  local feishu_app_id
  hint "$(tr explain_app_id)"
  feishu_app_id="$(prompt_required "$(tr prompt_app_id)")"
  local feishu_app_secret
  hint "$(tr explain_app_secret)"
  feishu_app_secret="$(prompt_required "$(tr prompt_app_secret)")"

  log "$(tr configuration_summary)"
  printf '  %s: %s\n' "$(tr summary_app_root)" "$APP_ROOT_PATH"
  printf '  %s: %s\n' "$(tr summary_workspace)" "$APP_ROOT_PATH/workspace"
  printf '  %s: %s\n' "$(tr summary_sqlite)" "$APP_ROOT_PATH/data/codex_im_connector.db"
  printf '  %s: %s\n' "$(tr summary_codex_bin)" "$codex_bin"
  printf '  %s: %s\n' "$(tr summary_allowed_roots)" "$allowed_roots_json"
  printf '  %s: %s\n' "$(tr summary_model)" "$model_input"
  printf '  %s: %s\n' "$(tr summary_effort)" "$effort_input"
  printf '  %s: %s\n' "$(tr summary_app_id)" "$feishu_app_id"
  printf '  %s: %s\n' "$(tr summary_app_secret)" "$(mask_secret "$feishu_app_secret")"

  local confirm
  read -r -p "$(tr proceed_install)" confirm < /dev/tty
  if [[ -n "$confirm" && ! "$confirm" =~ ^[Yy]$ ]]; then
    die "$(tr installation_cancelled)"
  fi

  set_env_value "$env_file" "APP_ROOT_PATH" "$APP_ROOT_PATH"
  set_env_value "$env_file" "CODEX_BIN" "$codex_bin"
  set_env_value "$env_file" "CODEX_ALLOWED_CWD_ROOTS" "$allowed_roots_json"
  set_env_value "$env_file" "CODEX_MODEL" "$model_input"
  set_env_value "$env_file" "CODEX_DEFAULT_REASONING_EFFORT" "$effort_input"
  set_env_value "$env_file" "FEISHU_APP_ID" "$feishu_app_id"
  set_env_value "$env_file" "FEISHU_APP_SECRET" "$feishu_app_secret"

  write_install_meta
  PYTHON_BIN="$REPO_DIR/.venv/bin/python"
  [[ -x "$PYTHON_BIN" ]] || die "$(tr venv_python_missing) $PYTHON_BIN
  $(tr dependency_sync_may_have_failed)"

  install_launch_agent

  log "$(tr install_completed)"
  log "$(tr launch_agent_login)"
  log "$(tr background_items_notice)"
  log "$(tr useful_commands)"
  printf '  launchctl print gui/%s/%s\n' "$(id -u)" "$LAUNCHD_LABEL"
  printf '  tail -f %s\n' "$STDERR_LOG"
}

main "$@"
