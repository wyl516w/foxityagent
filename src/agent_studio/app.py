from __future__ import annotations

import sys

from PySide6 import QtWidgets

from agent_studio.core.config import AppConfig
from agent_studio.core.state import SharedState
from agent_studio.services.backend_server import BackendServer
from agent_studio.storage.sqlite_store import SQLiteStore
from agent_studio.ui.main_window import MainWindow


def main() -> int:
    config = AppConfig()
    store = SQLiteStore(
        database_path=config.database_path,
        event_retention_limit=config.event_retention_limit,
    )
    store.initialize()
    state = SharedState(config=config, store=store)
    backend = BackendServer(config=config, state=state)
    backend.start()

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(config.app_name)
    window = MainWindow(config=config, state=state, backend=backend)
    window.show()

    exit_code = app.exec()
    backend.stop()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
