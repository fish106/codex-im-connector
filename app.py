from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from client.codex_runtime import CodexRuntime
from connector.feishu_connector import FeishuConnector
from core.config import Settings
from db.session import create_engine_and_session_factory, init_db
from model.connector_models import ApprovalActionEvent, InboundMessage, ThreadListActionEvent
from service.approval_service import ApprovalService
from service.pending_image_service import PendingImageService
from service.render_service import RenderService
from service.router_service import RouterService
from service.session_service import SessionService

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings, loop) -> None:
        self.settings = settings
        self.loop = loop
        self.engine, self.session_factory = create_engine_and_session_factory(settings.sqlite_url_resolved)
        self.session_service = SessionService(self.session_factory, default_cwd=str(settings.codex_cwd_path))
        self.approval_service = ApprovalService(self.session_factory, self.session_service)
        self.pending_image_service = PendingImageService(max_images_per_user=3)
        self.render_service = RenderService()
        self.runtime = CodexRuntime(
            settings=settings,
            session_service=self.session_service,
            approval_service=self.approval_service,
            loop=loop,
        )
        self.connector = FeishuConnector(settings=settings, app_loop=loop)
        self.router = RouterService(
            connector=self.connector,
            session_service=self.session_service,
            approval_service=self.approval_service,
            pending_image_service=self.pending_image_service,
            render_service=self.render_service,
            runtime=self.runtime,
        )

    async def start(self) -> None:
        self._ensure_directory_writable(self.settings.codex_cwd_path, label="APP_ROOT_PATH/workspace")
        logger.info("startup check passed: workspace is writable (%s)", self.settings.codex_cwd_path)
        sqlite_path = self.settings.sqlite_file_path
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_directory_writable(sqlite_path.parent, label="APP_ROOT_PATH/data")
        logger.info("startup check passed: data directory is writable (%s)", sqlite_path.parent)
        await init_db(self.engine)
        logger.info("startup step passed: database initialized")
        await self.runtime.start()
        logger.info("startup step passed: codex runtime initialized")
        logger.info("startup completed successfully")

    async def shutdown(self) -> None:
        await self.runtime.close()
        await self.engine.dispose()

    async def handle_inbound(self, inbound: InboundMessage) -> None:
        await self.router.handle_message(inbound)

    async def handle_approval_action(self, action_event: ApprovalActionEvent) -> None:
        await self.router.handle_approval_action(action_event)

    async def handle_thread_list_action(self, action_event: ThreadListActionEvent) -> None:
        await self.router.handle_thread_list_action(action_event)

    @staticmethod
    def _ensure_directory_writable(path: Path, *, label: str) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path, prefix=".cic-write-test-", delete=True):
                pass
        except OSError as exc:
            raise RuntimeError(
                f"{label} directory is not writable: {path}. "
                "If you are running inside a sandbox, use a path under the workspace or /tmp, "
                "or run the service from your normal terminal."
            ) from exc
