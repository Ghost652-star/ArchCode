from pathlib import Path
from types import SimpleNamespace

from archcode.app import ArchCodeApp
from archcode.memory.instructions import InstructionDocumentLoader


def test_instruction_loader_uses_explicit_app_data_dir_for_user_rules(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    app_data_dir = tmp_path / "archcode-app" / ".archcode"
    project_root.mkdir(parents=True)
    app_data_dir.mkdir(parents=True)

    (project_root / "AGENTS.md").write_text("project rule", encoding="utf-8")
    (project_root / ".archcode").mkdir()
    (project_root / ".archcode" / "AGENTS.md").write_text(
        "local project rule", encoding="utf-8"
    )
    (app_data_dir / "AGENTS.md").write_text("user rule", encoding="utf-8")

    result = InstructionDocumentLoader(app_data_dir=app_data_dir).load(project_root)

    assert [source.path for source in result.loaded_sources] == [
        project_root / "AGENTS.md",
        project_root / ".archcode" / "AGENTS.md",
        app_data_dir / "AGENTS.md",
    ]
    assert "project rule" in result.compiled_text
    assert "local project rule" in result.compiled_text
    assert "user rule" in result.compiled_text


def test_app_without_project_work_dir_does_not_create_a_session(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    agent = SimpleNamespace(_work_dir=None)

    app = ArchCodeApp(agent=agent, model_name="test")

    assert app._session is None
    assert not (tmp_path / ".archcode" / "sessions").exists()
