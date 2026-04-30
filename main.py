from __future__ import annotations

import asyncio
import signal
import threading
import time

from app import Application
from core.config import get_settings
from core.logging import setup_logging


def main() -> None:
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)

    app_loop = asyncio.new_event_loop()
    app = Application(settings=settings, loop=app_loop)
    started = threading.Event()
    shutdown_requested = threading.Event()

    def _loop_main() -> None:
        asyncio.set_event_loop(app_loop)
        app_loop.run_until_complete(app.start())
        started.set()
        app_loop.run_forever()

    loop_thread = threading.Thread(target=_loop_main, name="codex-im-app-loop", daemon=True)
    loop_thread.start()
    started.wait()

    def _shutdown(*_args) -> None:
        shutdown_requested.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        app.connector.start(app.handle_inbound, app.handle_approval_action, app.handle_thread_list_action)
        while not shutdown_requested.is_set():
            time.sleep(0.2)
    finally:
        app.connector.stop()
        future = asyncio.run_coroutine_threadsafe(app.shutdown(), app_loop)
        future.result(timeout=10)
        app_loop.call_soon_threadsafe(app_loop.stop)
        loop_thread.join(timeout=5)


if __name__ == "__main__":
    main()
