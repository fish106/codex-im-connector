from __future__ import annotations

from core.config import Settings


def test_allowed_cwd_roots_always_include_workspace_from_app_root(tmp_path):
    app_root = tmp_path / "app-root"
    allowed = tmp_path / "allowed"
    settings = Settings(
        APP_ROOT_PATH=str(app_root),
        CODEX_ALLOWED_CWD_ROOTS=f"[\"{allowed}\"]",
    )

    roots = settings.allowed_cwd_roots

    assert (app_root / "workspace").resolve() in roots
    assert allowed.resolve() in roots


def test_allowed_cwd_roots_deduplicate_workspace_root(tmp_path):
    app_root = tmp_path / "app-root"
    workspace = (app_root / "workspace").resolve()
    settings = Settings(
        APP_ROOT_PATH=str(app_root),
        CODEX_ALLOWED_CWD_ROOTS=f"[\"{workspace}\"]",
    )

    roots = settings.allowed_cwd_roots

    assert roots.count(workspace.resolve()) == 1


def test_paths_are_derived_from_app_root(tmp_path):
    app_root = tmp_path / "app-root"
    settings = Settings(APP_ROOT_PATH=str(app_root))

    assert settings.app_root_path == app_root.resolve()
    assert settings.codex_cwd_path == (app_root / "workspace").resolve()
    assert settings.sqlite_file_path == (app_root / "data" / "codex_im_connector.db").resolve()
