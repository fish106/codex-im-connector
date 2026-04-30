# Codex IM Connector

[[English]](./README.md)

一个在 macOS 上运行的 IM 私聊机器人服务，用 Codex Python SDK 承接消息、管理线程与审批，并将结果流式回传到 IM。

## 1. 项目介绍

### 1.1 这是什么

`codex-im-connector` 是一个本地运行的 Python 服务。它本身不提供 Web UI，也不对外暴露 HTTP API，而是直接作为 macOS 后台服务运行，负责：

- 从 IM 私聊接收用户消息
- 把消息转发给 Codex App Server / Codex Python SDK
- 管理用户线程、默认模型、工作目录和审批状态
- 将模型输出以流式方式持续回传到 IM

当前主集成目标是飞书私聊机器人。

### 1.2 当前支持的核心能力

- 飞书私聊文本、图片输入
- Codex 线程管理
- 流式输出回传图文
- 审批提示与用户确认
- 模型与 reasoning effort 切换
- 当前线程重命名、compact、状态查看
- 工作目录切换与白名单校验
- macOS 后台运行与开机自动启动（通过 `launchd` 用户级 LaunchAgent）

已通过测试的版本：

- 飞书客户端 >= 7.66
- Codex cli >= 0.125.0

### 1.3 主要命令

当前机器人支持的主要命令包括：

- `/help`：查看帮助
- `/new [name]`：新建线程并切换
- `/list [search_term]`：查看远端线程列表
- `/resume <thread_id>`：恢复指定线程
- `/status`：查看当前线程信息和会话状态
- `/rename <name>`：重命名当前线程
- `/compact`：压缩当前线程上下文
- `/models`：查看可用模型
- `/model <name> <reasoning_effort>`：设置默认模型和推理强度
- `/cwd <path>`：更新后续新建线程的默认工作目录
- `/cwd --current <path>`：同时切换当前线程，并更新后续新建线程的默认工作目录
- `/stop`：中断当前运行中的任务
- `/steer <text>`：在不中断当前 turn 的情况下追加引导

## 2. 安装说明

### 2.1 前置条件

在运行安装脚本之前，请先准备以下环境：

- 一台 macOS 电脑
- 已安装 `git`
- 已安装或可安装 `curl`
- 一个可用的 Feishu 机器人应用
- 一个可用的 Codex CLI 可执行文件路径

> 当前仓库内的 `scripts/install.sh` 会自动处理 `uv` 和 Python 3.12，但不会替你安装 `git`。

### 2.2 安装 Codex CLI

本项目依赖本地的 Codex CLI 二进制，并要求你在安装时明确填写 `CODEX_BIN`。

请参考 OpenAI 官方 Codex CLI 文档：

- [Codex CLI 文档](https://developers.openai.com/codex/cli)

建议你先完成以下事项：

1. 按照官方文档安装 Codex CLI
2. 确认 `codex` 命令可执行
3. 记录实际二进制路径，例如：
   - `/opt/homebrew/bin/codex`
4. 按照官方文档完成登录或 API 凭证配置

安装脚本要求：

- `CODEX_BIN` 必须是本地存在且可执行的文件
- 脚本不会为这个配置提供默认值

你可以在终端中先手工确认：

```bash
which codex
codex --help
```

### 2.3 在飞书开放平台创建智能体应用

1. 登录[飞书开放平台开发者后台](https://open.feishu.cn/app)
2. 直接创建飞书智能体应用
![飞书智能体应用创建入口](./docs/img-1.png)
3. 获取 App ID 和 App Secret

> 如果发送消息后机器人没有响应，请优先检查机器人的消息接收、卡片交互、消息发送/更新、图片上传下载相关权限

### 2.4 飞书应用的 App ID 和 App Secret

本项目安装时需要你填写：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

请注意：

- 两者都是必填项
- 安装脚本会把它们写入项目的 `.env`
- `FEISHU_APP_SECRET` 属于敏感信息，不要提交到版本库

### 2.5 使用安装脚本

你可以直接使用一键安装脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/fish106/codex-im-connector/main/scripts/install.sh | bash
```

安装脚本会引导你完成以下内容：

1. 选择 `APP_ROOT_PATH`
   - 默认值：`~/.cic`
2. 拉取或更新代码到：
   - `APP_ROOT_PATH/src/codex-im-connector`
3. 自动安装 Python 依赖
4. 引导填写配置项
5. 生成 `.env`
6. 生成并安装 `launchd` 的 LaunchAgent
7. 后台启动服务

### 2.6 安装完成后服务如何运行

安装完成后，服务会以 macOS 用户级 `LaunchAgent` 的方式运行：

- 安装位置：
  - `~/Library/LaunchAgents/com.fish106.codex-im-connector.plist`
- 登录后自动启动
- 后台持续运行

默认目录结构为：

- `APP_ROOT_PATH/workspace`：默认工作目录
- `APP_ROOT_PATH/data`：SQLite 数据目录
- `APP_ROOT_PATH/logs`：日志目录
- `APP_ROOT_PATH/src/codex-im-connector`：代码目录

### 2.7 如何检查服务状态

可以使用以下命令检查：

```bash
launchctl print gui/$(id -u)/com.fish106.codex-im-connector
tail -f ~/.cic/logs/stderr.log
tail -f ~/.cic/logs/stdout.log
```

如果你使用了自定义 `APP_ROOT_PATH`，请把上面命令中的 `~/.cic` 替换成你的实际路径。

### 2.8 安装后如何修改配置

安装完成后，项目的运行配置保存在：

- `APP_ROOT_PATH/src/codex-im-connector/.env`

你可以直接编辑这个文件来修改配置，例如：

- `CODEX_BIN`
- `CODEX_ALLOWED_CWD_ROOTS`
- `CODEX_MODEL`
- `CODEX_DEFAULT_REASONING_EFFORT`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

请注意：

- **安装完成后不要修改 `APP_ROOT_PATH`**
- `APP_ROOT_PATH` 决定了代码目录、数据库目录、日志目录、workspace 目录以及 `launchd` 运行路径
- 如果你需要更换 `APP_ROOT_PATH`，推荐的做法是：
  1. 先卸载当前安装
  2. 再重新执行安装脚本并选择新的 `APP_ROOT_PATH`

### 2.9 修改配置后如何重启服务

修改 `.env` 后，需要重启 macOS 后台服务让配置生效。

可以使用以下命令：

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.fish106.codex-im-connector.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fish106.codex-im-connector.plist
```

或者使用：

```bash
launchctl kickstart -k gui/$(id -u)/com.fish106.codex-im-connector
```

建议修改配置后：

1. 先执行 `bootout + bootstrap`
2. 再检查日志确认服务已正常启动

例如：

```bash
tail -f APP_ROOT_PATH/logs/stderr.log
tail -f APP_ROOT_PATH/logs/stdout.log
```

请把上面的 `APP_ROOT_PATH` 替换成你的实际安装路径。

### 2.10 如何升级

如果你只是想升级到最新代码并重启服务，不需要重新填写配置，也不需要重写 `.env`。

默认安装路径为 `~/.cic` 时，可以直接执行：

```bash
curl -fsSL https://raw.githubusercontent.com/fish106/codex-im-connector/main/scripts/upgrade.sh | bash
```

如果你安装时使用了自定义 `APP_ROOT_PATH`，请先导出环境变量，再执行升级命令：

```bash
export APP_ROOT_PATH=/your/app/root
curl -fsSL https://raw.githubusercontent.com/fish106/codex-im-connector/main/scripts/upgrade.sh | bash
```

升级脚本会执行以下操作：

- 读取现有安装元数据
- 拉取仓库最新代码
- 使用 `uv sync` 同步依赖
- 重建并重启 `launchd` 后台服务

它不会执行以下操作：

- 不会重新询问配置
- 不会覆盖现有 `.env`
- 不会修改 `APP_ROOT_PATH`

### 2.11 如何卸载

你也可以直接安装路径里面的卸载脚本：

```bash
bash APP_ROOT_PATH/src/codex-im-connector/scripts/uninstall.sh
```
请把上面的 `APP_ROOT_PATH` 替换成你的实际安装路径。

卸载脚本会：

- 停止并移除 LaunchAgent
- 删除代码目录
- 询问是否删除整个 `APP_ROOT_PATH`

## 3. 项目架构与开发说明

### 3.1 目录结构

当前主要目录如下：

- `client/`：Codex App Server / SDK 运行时封装
- `connector/`：IM 平台连接器抽象与飞书实现
- `core/`：配置与日志初始化
- `db/`：SQLAlchemy 模型与数据库初始化
- `model/`：Pydantic 数据模型
- `service/`：路由、审批、渲染、session 管理等业务逻辑
- `scripts/`：安装、卸载与 LaunchAgent 模板
- `tests/unit/`：单元测试

### 3.2 运行架构

整体链路可以概括为：

1. `main.py` 启动应用
2. `app.py` 组装数据库、session service、approval service、runtime、connector、router
3. `FeishuConnector` 通过 WebSocket 接收飞书事件
4. `RouterService` 解析命令、审批和普通消息
5. `CodexRuntime` 调用 Codex App Server / AsyncCodex
6. 输出通过 connector 流式回传到飞书

### 3.3 开发依赖与运行方式

项目使用：

- Python 3.12
- `uv`
- `sqlalchemy`
- `aiosqlite`
- `pydantic-settings`
- `lark-oapi`
- `openai-codex-app-server-sdk`

本地开发常用命令示例：

```bash
uv sync --extra dev
uv run python main.py
uv run pytest
```
