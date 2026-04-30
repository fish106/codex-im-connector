#!/usr/bin/env bash
set -euo pipefail

LAUNCHD_LABEL="com.fish106.codex-im-connector"
DEFAULT_APP_ROOT="$HOME/.cic"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
LANG_CODE="en"

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
    zh:this_uninstaller_only_supports_macos) echo "此卸载脚本仅支持 macOS。" ;;
    en:this_uninstaller_only_supports_macos) echo "This uninstaller only supports macOS." ;;
    zh:python3_required) echo "运行卸载脚本需要 python3。" ;;
    en:python3_required) echo "python3 is required to run the uninstaller." ;;
    zh:prompt_app_root) echo "APP_ROOT_PATH" ;;
    en:prompt_app_root) echo "APP_ROOT_PATH" ;;
    zh:prompt_delete_data) echo "是否删除 APP_ROOT_PATH 下的全部数据（包括配置文件、数据库、workspace、日志等）？删除后将无法恢复" ;;
    en:prompt_delete_data) echo "Delete all data under APP_ROOT_PATH (including configuration, database, workspace, logs, and related files)? This action cannot be undone" ;;
    zh:removed_app_root) echo "已删除 APP_ROOT_PATH：" ;;
    en:removed_app_root) echo "Removed APP_ROOT_PATH:" ;;
    zh:kept_app_root) echo "已保留 APP_ROOT_PATH：" ;;
    en:kept_app_root) echo "Kept APP_ROOT_PATH:" ;;
    zh:uninstall_completed) echo "卸载已完成。" ;;
    en:uninstall_completed) echo "Uninstall completed." ;;
    *) echo "$key" ;;
  esac
}

log() {
  printf '[uninstall] %s\n' "$*"
}

die() {
  printf '[uninstall] ERROR: %s\n' "$*" >&2
  exit 1
}

normalize_path() {
  python3 - "$1" <<'PY'
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

load_install_meta() {
  local inferred_app_root
  inferred_app_root="$(cd "$REPO_ROOT/../.." && pwd -P)"
  if [[ -f "$inferred_app_root/install-meta.env" ]]; then
    # shellcheck disable=SC1090
    source "$inferred_app_root/install-meta.env"
    return
  fi

  if [[ -f "$DEFAULT_APP_ROOT/install-meta.env" ]]; then
    # shellcheck disable=SC1091
    source "$DEFAULT_APP_ROOT/install-meta.env"
    return
  fi

  local input
  read -r -p "$(tr prompt_app_root) [$DEFAULT_APP_ROOT]: " input
  if [[ -z "$input" ]]; then
    input="$DEFAULT_APP_ROOT"
  fi

  APP_ROOT_PATH="$(normalize_path "$input")"
  REPO_DIR="$APP_ROOT_PATH/src/codex-im-connector"
  PLIST_PATH="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
}

remove_launch_agent() {
  if [[ -f "$PLIST_PATH" ]]; then
    launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
    launchctl disable "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST_PATH"
  fi
}

main() {
  detect_lang
  [[ "$(uname -s)" == "Darwin" ]] || die "$(tr this_uninstaller_only_supports_macos)"
  command -v python3 >/dev/null 2>&1 || die "$(tr python3_required)"

  load_install_meta

  remove_launch_agent

  if [[ -n "${REPO_DIR:-}" && -d "$REPO_DIR" ]]; then
    rm -rf "$REPO_DIR"
  fi

  if [[ -n "${APP_ROOT_PATH:-}" && -f "$APP_ROOT_PATH/install-meta.env" ]]; then
    rm -f "$APP_ROOT_PATH/install-meta.env"
  fi

  local delete_data
  read -r -p "$(tr prompt_delete_data) (${APP_ROOT_PATH})? [y/N]: " delete_data
  if [[ "$delete_data" =~ ^[Yy]$ ]]; then
    rm -rf "$APP_ROOT_PATH"
    log "$(tr removed_app_root) $APP_ROOT_PATH"
  else
    log "$(tr kept_app_root) $APP_ROOT_PATH"
  fi

  log "$(tr uninstall_completed)"
}

main "$@"
