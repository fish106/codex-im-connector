# Codex IM Connector

[[中文]](./README_ZH.md)

A private-chat IM bot service that runs on macOS, uses the Codex Python SDK to handle messages, manage threads and approvals, and stream results back to IM.

## 1. Overview

### 1.1 What it is

`codex-im-connector` is a locally running Python service. It does not provide a web UI or expose an external HTTP API. Instead, it runs directly as a macOS background service and is responsible for:

- Receiving private chat messages from IM
- Forwarding messages to the Codex App Server / Codex Python SDK
- Managing user threads, default models, working directories, and approval state
- Streaming model output back to IM

The current primary integration target is a Feishu/Lark private chat bot.

### 1.2 Core capabilities

- Feishu/Lark private chat text and image input
- Codex thread management
- Streaming text output and image result delivery
- Approval prompts and user confirmation
- Model and reasoning effort switching
- Current thread rename, compact, and status inspection
- Working directory switching with allowlist validation
- macOS background service with auto-start on login through a user-level `launchd` LaunchAgent

Tested versions:

- Feishu client >= 7.66
- Codex CLI >= 0.125.0

### 1.3 Main commands

The bot currently supports these commands:

- `/help`: Show help
- `/new [name]`: Create a new thread and switch to it
- `/list [search_term]`: List remote threads
- `/resume <thread_id>`: Resume a specific thread
- `/status`: Show current thread info and session state
- `/rename <name>`: Rename the current thread
- `/compact`: Compact the current thread context
- `/models`: List available models
- `/model <name> <reasoning_effort>`: Set the default model and reasoning effort
- `/cwd <path>`: Update the default working directory for future new threads
- `/cwd --current <path>`: Switch the current thread and also update the default working directory for future new threads
- `/stop`: Interrupt the current running task
- `/steer <text>`: Add steering text without interrupting the current turn

## 2. Installation

### 2.1 Prerequisites

Before running the installer, prepare the following:

- A macOS machine
- `git` installed
- `curl` installed or available
- A usable Feishu bot application
- A usable local Codex CLI binary path

> The repository `scripts/install.sh` will install `uv` and Python 3.12 automatically, but it will not install `git` for you.

### 2.2 Install Codex CLI

This project depends on a local Codex CLI binary, and the installer requires you to provide `CODEX_BIN`.

See the official OpenAI Codex CLI documentation:

- [Codex CLI documentation](https://developers.openai.com/codex/cli)

Recommended preparation:

1. Install Codex CLI by following the official documentation
2. Confirm that the `codex` command works
3. Record the actual binary path, for example:
   - `/opt/homebrew/bin/codex`
4. Complete login or API credential setup as required by the official documentation

Installer requirements:

- `CODEX_BIN` must point to an existing executable file
- The installer does not provide a default value for this field

You can verify it manually in Terminal first:

```bash
which codex
codex --help
```

### 2.3 Create a Feishu/Lark agent app

1. Sign in to the [Feishu Open Platform developer console](https://open.feishu.cn/app)
2. Create a Feishu/Lark agent app directly
![Feishu/Lark agent app entry](./docs/img-1.png)
3. Retrieve the App ID and App Secret

> If the bot does not respond after you send a message, first check message receiving, card interaction, message send/update, and image upload/download permissions.

### 2.4 Feishu/Lark App ID and App Secret

You must provide these during installation:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

Notes:

- Both are required
- The installer writes them into `.env`
- `FEISHU_APP_SECRET` is sensitive and should never be committed to version control

### 2.5 Run the installer

You can use the one-command installer directly:

```bash
curl -fsSL https://raw.githubusercontent.com/fish106/codex-im-connector/main/scripts/install.sh | bash
```

The installer will guide you through:

1. Choosing `APP_ROOT_PATH`
   - Default: `~/.cic`
2. Cloning or updating the code into:
   - `APP_ROOT_PATH/src/codex-im-connector`
3. Installing Python dependencies automatically
4. Filling in the required configuration
5. Generating `.env`
6. Creating and installing the `launchd` LaunchAgent
7. Starting the service in the background

### 2.6 How the service runs after installation

After installation, the service runs as a macOS user-level `LaunchAgent`:

- Installed at:
  - `~/Library/LaunchAgents/com.fish106.codex-im-connector.plist`
- Starts automatically after login
- Keeps running in the background

Default directory layout:

- `APP_ROOT_PATH/workspace`: default working directory
- `APP_ROOT_PATH/data`: SQLite data directory
- `APP_ROOT_PATH/logs`: log directory
- `APP_ROOT_PATH/src/codex-im-connector`: source code directory

### 2.7 Check service status

Use these commands:

```bash
launchctl print gui/$(id -u)/com.fish106.codex-im-connector
tail -f ~/.cic/logs/stderr.log
tail -f ~/.cic/logs/stdout.log
```

If you use a custom `APP_ROOT_PATH`, replace `~/.cic` with your actual path.

### 2.8 Modify configuration after installation

The runtime configuration is stored at:

- `APP_ROOT_PATH/src/codex-im-connector/.env`

You can edit this file directly, for example:

- `CODEX_BIN`
- `CODEX_ALLOWED_CWD_ROOTS`
- `CODEX_MODEL`
- `CODEX_DEFAULT_REASONING_EFFORT`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

Important:

- **Do not change `APP_ROOT_PATH` after installation**
- `APP_ROOT_PATH` determines the code path, database path, log path, workspace path, and `launchd` runtime path
- If you need a different `APP_ROOT_PATH`, the recommended flow is:
  1. Uninstall the current installation
  2. Run the installer again and choose the new `APP_ROOT_PATH`

### 2.9 Restart the service after changing configuration

After editing `.env`, restart the macOS background service so the new configuration takes effect.

You can use:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.fish106.codex-im-connector.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fish106.codex-im-connector.plist
```

Or:

```bash
launchctl kickstart -k gui/$(id -u)/com.fish106.codex-im-connector
```

Recommended:

1. Run `bootout + bootstrap`
2. Check the logs to confirm the service started correctly

Example:

```bash
tail -f APP_ROOT_PATH/logs/stderr.log
tail -f APP_ROOT_PATH/logs/stdout.log
```

Replace `APP_ROOT_PATH` with your actual installation path.

### 2.10 Upgrade

If you only want to upgrade to the latest code and restart the service, you do not need to re-enter configuration and `.env` will not be rewritten.

If you used the default installation path `~/.cic`, run:

```bash
curl -fsSL https://raw.githubusercontent.com/fish106/codex-im-connector/main/scripts/upgrade.sh | bash
```

If you used a custom `APP_ROOT_PATH`, export it first and then run the upgrade command:

```bash
export APP_ROOT_PATH=/your/app/root
curl -fsSL https://raw.githubusercontent.com/fish106/codex-im-connector/main/scripts/upgrade.sh | bash
```

The upgrade script will:

- Read the existing installation metadata
- Pull the latest repository code
- Run `uv sync` to update dependencies
- Rebuild and restart the `launchd` background service

It will not:

- Ask for configuration again
- Overwrite your existing `.env`
- Change `APP_ROOT_PATH`

### 2.11 Uninstall

You can run the uninstall script from the installation path:

```bash
bash APP_ROOT_PATH/src/codex-im-connector/scripts/uninstall.sh
```

Replace `APP_ROOT_PATH` with your actual installation path.

The uninstall script will:

- Stop and remove the LaunchAgent
- Remove the source code directory
- Ask whether to delete the entire `APP_ROOT_PATH`

## 3. Architecture and Development

### 3.1 Directory structure

Main directories:

- `client/`: Codex App Server / SDK runtime wrapper
- `connector/`: IM platform connector abstractions and Feishu implementation
- `core/`: configuration and logging initialization
- `db/`: SQLAlchemy models and database initialization
- `model/`: Pydantic data models
- `service/`: routing, approvals, rendering, and session management
- `scripts/`: install, uninstall, and LaunchAgent template
- `tests/unit/`: unit tests

### 3.2 Runtime architecture

The main flow is:

1. `main.py` starts the application
2. `app.py` wires together the database, session service, approval service, runtime, connector, and router
3. `FeishuConnector` receives Feishu events over WebSocket
4. `RouterService` handles commands, approvals, and normal messages
5. `CodexRuntime` calls the Codex App Server / AsyncCodex
6. Output is streamed back to Feishu through the connector

### 3.3 Development dependencies and local run

The project uses:

- Python 3.12
- `uv`
- `sqlalchemy`
- `aiosqlite`
- `pydantic-settings`
- `lark-oapi`
- `openai-codex-app-server-sdk`

Common local development commands:

```bash
uv sync --extra dev
uv run python main.py
uv run pytest
```
