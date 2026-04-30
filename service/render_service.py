from __future__ import annotations

from typing import Sequence

from model.connector_models import ApprovalPrompt, AvailableModelInfo, CurrentThreadInfo, ThreadRecord


class RenderService:
    def stream_placeholder(self) -> str:
        return "正在处理，请稍候。"

    def help_message(self) -> str:
        return (
            "### 可用命令\n"
            "- `/help` 查看帮助\n"
            "- `/new [name]` 新建线程并切换\n"
            "- `/list [search_term]` 查看远端线程\n"
            "- `/resume <thread_id>` 切换线程\n"
            "- `/status` 查看当前线程信息和会话状态\n"
            "- `/rename <name>` 重命名当前线程\n"
            "- `/compact` 压缩当前线程上下文\n"
            "- `/cwd <path>` 更新后续新建线程的默认工作目录\n"
            "- `/cwd --current <path>` 同时切换当前线程，并更新后续新建线程的默认工作目录\n"
            "- `/stop` 中断当前运行中的任务\n"
            "- `/steer <text>` 不打断并追加一段引导\n"
            "- `/models` 查看可用模型\n"
            "- `/model <name> <reasoning_effort>` 设置模型与推理强度"
        )

    def approval_message(self, prompt: ApprovalPrompt) -> str:
        return (
            "### 请确认操作\n"
            f"- 描述：{prompt.reason or '无'}\n"
            f"- 命令：`{prompt.command or '无'}`\n\n"
        )

    def approval_inline_message(self, current_markdown: str, prompt: ApprovalPrompt) -> str:
        base = (current_markdown or "").rstrip()
        if not base:
            base = self.stream_placeholder()
        return f"{base}\n\n---\n\n{self.approval_message(prompt)}"

    def busy_message(self) -> str:
        return "当前已有任务正在运行，请等待完成、审批，或发送 `/stop`。"

    def status_message(
        self,
        thread_info: CurrentThreadInfo,
        current_turn_id: str | None,
        waiting_for_approval: bool,
        is_busy: bool,
        default_cwd: str | None,
        current_model: str | None,
        current_reasoning_effort: str | None,
    ) -> str:
        return (
            "### 当前线程\n"
            f"- ID：<font color='blue'>{thread_info.thread_id}</font>\n"
            f"- 名称：<font color='blue'>{thread_info.name or '未命名线程'}</font>\n"
            f"- Source：<font color='blue'>{thread_info.source}</font>\n"
            f"- Model Provider：<font color='blue'>{thread_info.model_provider}</font>\n"
            f"- Model：<font color='blue'>{current_model or '无'}</font>\n"
            f"- Effort：<font color='blue'>{current_reasoning_effort or '无'}</font>\n"
            f"- CWD：<font color='blue'>{thread_info.cwd}</font>\n"
            f"- Preview：<font color='blue'>{thread_info.preview or '无'}</font>\n"
            f"- Created At：<font color='blue'>{thread_info.created_at}</font>\n"
            f"- Updated At：<font color='blue'>{thread_info.updated_at}</font>\n\n"
            "### 会话状态\n"
            f"- 当前 Turn：<font color='blue'>{current_turn_id or '无'}</font>\n"
            f"- 默认工作目录：<font color='blue'>{default_cwd or '无'}</font>\n"
            f"- 忙碌中：<font color='blue'>{'是' if is_busy else '否'}</font>\n"
            f"- 待审批：<font color='blue'>{'是' if waiting_for_approval else '否'}</font>"
        )

    def threads_message(self, threads: Sequence[ThreadRecord], current_thread_id: str | None) -> str:
        if not threads:
            return "当前还没有可用线程。"
        lines = ["### 可用线程列表"]
        for record in threads:
            marker = " (当前)" if record.thread_id == current_thread_id else ""
            title = record.name or "未命名线程"
            lines.append(f"- `{record.thread_id}` {title}{marker}")
        return "\n".join(lines)

    def new_thread_message(self, thread_id: str, name: str | None) -> str:
        display_name = name or "未命名线程"
        return f"已创建并切换到新线程：`{thread_id}`，名称：{display_name}"

    def resumed_thread_message(self, thread_id: str, name: str | None) -> str:
        display_name = name or "未命名线程"
        return f"已切换到线程：`{thread_id}`，名称：{display_name}"

    def recreated_thread_message(self, thread_id: str) -> str:
        return (
            "之前保存的线程已不可用，已自动创建新线程继续处理。\n\n"
            f"新线程：`{thread_id}`"
        )

    def approved_message(self) -> str:
        return "已同意本次操作，继续执行。"

    def approved_stream_placeholder(self) -> str:
        return "已同意本次操作，继续执行。"

    def rejected_message(self) -> str:
        return "已拒绝本次操作，按你的新消息继续处理。"

    def stop_message(self) -> str:
        return "已请求中断当前任务。"

    def models_message(
        self,
        models: Sequence[AvailableModelInfo],
        current_model_id: str | None = None,
        current_reasoning_effort: str | None = None,
    ) -> str:
        if not models:
            return "当前没有可用模型。"
        lines = ["### 可用模型"]
        for model in models:
            default_tag = " (默认)" if model.is_default else ""
            current_tag = "（当前）" if current_model_id == model.model_id else ""
            supported_parts = []
            for effort in model.supported_reasoning_efforts:
                effort_current_tag = "（当前）" if current_model_id == model.model_id and current_reasoning_effort == effort else ""
                supported_parts.append(f"<font color='blue'>{effort}</font>{effort_current_tag}")
            supported = ", ".join(supported_parts)
            lines.append(
                f"- `{model.model_id}` {default_tag}{current_tag}: {supported}"
            )
        return "\n".join(lines)

    def model_usage_message(self) -> str:
        return "请使用 `/model <name> <reasoning_effort>`。"

    def model_updated_message(self, model_name: str, reasoning_effort: str) -> str:
        return f"已设置默认模型为 `{model_name}`，默认 reasoning_effort 为 `{reasoning_effort}`。"

    def rename_usage_message(self) -> str:
        return "请使用 `/rename <name>`。"

    def renamed_thread_message(self, thread_id: str, name: str | None) -> str:
        display_name = name or "未命名线程"
        return f"已重命名当前线程：<font color='blue'>{thread_id}</font>\n\n新的名称：<font color='blue'>{display_name}</font>"

    def compact_started_message(self) -> str:
        return "已发起当前线程的 compact。"

    def compact_completed_message(self, thread_id: str) -> str:
        return f"当前线程 compact 已完成：`{thread_id}`。"

    def compact_failed_message(self, thread_id: str, message: str) -> str:
        return f"当前线程 compact 失败：`{thread_id}`。\n原因：{message}"

    def cwd_usage_message(self) -> str:
        return "请使用 `/cwd <path>` 或 `/cwd --current <path>`。"

    def cwd_updated_message(self, cwd: str) -> str:
        return f"已切换当前线程，并更新后续新建线程的默认工作目录：<font color='blue'>{cwd}</font>"

    def cwd_default_only_message(self, cwd: str) -> str:
        return f"已更新后续新建线程的默认工作目录：<font color='blue'>{cwd}</font>"

    def cwd_updated_thread_unavailable_message(self, cwd: str) -> str:
        return (
            f"已更新后续新建线程的默认工作目录：<font color='blue'>{cwd}</font>\n\n"
            "但当前线程不可用，请使用 `/resume` 或 `/new`。"
        )

    def cwd_invalid_message(self, message: str) -> str:
        return f"工作目录切换失败：{message}"

    def invalid_reasoning_effort_message(self, reasoning_effort: str) -> str:
        return (
            f"不支持的 reasoning_effort：`{reasoning_effort}`。\n"
            "可选值：`none`, `minimal`, `low`, `medium`, `high`, `xhigh`。"
        )

    def steer_usage_message(self) -> str:
        return "请使用 `/steer <text>` 来追加引导内容。"

    def steer_success_message(self) -> str:
        return "已追加引导内容，当前任务继续执行。"

    def steer_unavailable_message(self) -> str:
        return "当前没有可引导的进行中任务，或该任务当前不可 steer。"

    def no_pending_approval_message(self) -> str:
        return "当前没有待审批项。"

    def thread_list_state_expired_message(self) -> str:
        return "列表状态已失效，请重新执行 `/list`。"

    def pending_image_received_message(self, count: int) -> str:
        return f"已收到第 <font color='blue'>{count}</font> 张图片，请继续发送你想让我做什么。"

    def pending_images_limit_message(self, limit: int) -> str:
        return f"当前最多暂存 <font color='blue'>{limit}</font> 张图片，请先发送文字说明。"

    def image_download_failed_message(self, message: str) -> str:
        return f"图片处理失败：{message}"

    def image_output_send_failed_message(self, message: str) -> str:
        return f"图片输出发送失败：{message}"

    def image_not_supported_during_approval_message(self) -> str:
        return "当前审批处理中，暂不支持发送图片，请先完成审批。"

    def unsupported_message(self) -> str:
        return "当前支持飞书私聊文本消息与单张图片消息。"

    def error_message(self, message: str) -> str:
        return f"处理失败：{message}"

    def unknown_command_message(self) -> str:
        return "未知命令。\n\n" + self.help_message()

    def no_thread_message(self) -> str:
        return "当前没有活跃线程，请先发送普通消息或使用 `/new`。"

    def no_available_thread_message(self) -> str:
        return "当前还没有可用线程。"

    def invalid_resume_message(self) -> str:
        return "无法找到要切换的线程，请使用 `/resume <thread_id>`，并先用 `/list [search_term]` 查看可用线程。"

    def current_thread_unavailable_message(self) -> str:
        return "当前线程不可用，请使用 `/resume` 或 `/new`。"

    def turn_cancelled_message(self) -> str:
        return "本次操作已取消。"

    def turn_completed_fallback(self, status: str) -> str:
        if status == "completed":
            return "已完成。"
        if status == "interrupted":
            return "任务已中断。"
        return "任务已结束。"
