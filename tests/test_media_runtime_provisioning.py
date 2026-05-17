"""Tests for media runtime and model provisioning controls."""

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "bootstrap_faster_whisper_model.py"


def test_python_dependency_declares_faster_whisper() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "faster-whisper==1.2.1" in pyproject


def test_dockerfile_provisions_ffmpeg_for_builder_and_runtime() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.count("ffmpeg") >= 2


def test_ci_check_job_provisions_ffmpeg_without_model_download() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "ffmpeg" in workflow
    assert "IDIS_RUN_REAL_MEDIA_STT=1" not in workflow
    assert "IDIS_MEDIA_STT_MODEL_PATH" not in workflow
    assert "IDIS_MEDIA_STT_ALLOW_DOWNLOAD" not in workflow


def test_media_model_provisioning_docs_are_ci_safe_and_private() -> None:
    docs = (REPO_ROOT / "docs" / "architecture" / "media_model_provisioning.md").read_text(
        encoding="utf-8"
    )

    required = [
        "IDIS_MEDIA_STT_MODEL_PATH",
        "IDIS_MEDIA_STT_MODEL_NAME",
        "IDIS_MEDIA_STT_ALLOW_DOWNLOAD=1",
        "normal CI must not download",
        "Do not commit model files",
        "private gate",
        "--media-model-path",
        "--media-allow-model-download",
        "LOCAL_STT_MODEL_NOT_PROVISIONED",
        "scripts/bootstrap_faster_whisper_model.py",
    ]
    for phrase in required:
        assert phrase in docs


def test_local_model_bootstrap_command_defaults_to_no_download(capsys: Any) -> None:
    assert MODEL_BOOTSTRAP_SCRIPT.exists()
    from idis.tools.media_model_bootstrap import main

    exit_code = main(["--model-name", "tiny.en"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["safe_summary"] is True
    assert output["status"] == "blocked"
    assert output["reason_code"] == "LOCAL_STT_MODEL_NOT_PROVISIONED"
    assert output["download_attempted"] is False


def test_local_model_bootstrap_script_runs_without_pythonpath() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(MODEL_BOOTSTRAP_SCRIPT), "--model-name", "tiny.en"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)

    assert result.returncode == 1
    assert output["reason_code"] == "LOCAL_STT_MODEL_NOT_PROVISIONED"
    assert "ModuleNotFoundError" not in result.stderr


def test_local_model_bootstrap_no_env_returns_blocker_json() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("IDIS_MEDIA_STT_MODEL_PATH", None)
    env.pop("IDIS_MEDIA_STT_MODEL_NAME", None)
    env.pop("IDIS_MEDIA_STT_ALLOW_DOWNLOAD", None)

    result = subprocess.run(
        [sys.executable, str(MODEL_BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    output = json.loads(result.stdout)

    assert result.returncode == 1
    assert output["safe_summary"] is True
    assert output["status"] == "blocked"
    assert output["reason_code"] == "LOCAL_STT_MODEL_NOT_PROVISIONED"
    assert result.stderr == ""


def test_local_model_validation_reports_ready_without_printing_path(
    tmp_path: Path,
    capsys: Any,
) -> None:
    from idis.tools.media_model_bootstrap import main

    model_path = tmp_path / "confidential-local-model"
    model_path.mkdir()
    (model_path / "model.bin").write_text("synthetic model", encoding="utf-8")
    (model_path / "config.json").write_text("{}", encoding="utf-8")

    exit_code = main(["--model-path", str(model_path)])
    output_text = capsys.readouterr().out
    output = json.loads(output_text)

    assert exit_code == 0
    assert output["safe_summary"] is True
    assert output["status"] == "ready"
    assert output["reason_code"] == "LOCAL_MODEL_READY"
    assert output["can_attempt"] is True
    assert str(model_path) not in output_text
    assert "confidential-local-model" not in output_text


def test_model_bootstrap_download_requires_explicit_allow_download(tmp_path: Path) -> None:
    from idis.tools.media_model_bootstrap import (
        FasterWhisperModelBootstrapOptions,
        bootstrap_faster_whisper_model,
    )

    loader_called = False

    def unexpected_loader(*_: object) -> None:
        nonlocal loader_called
        loader_called = True

    result = bootstrap_faster_whisper_model(
        FasterWhisperModelBootstrapOptions(
            model_name="tiny.en",
            output_dir=tmp_path / "local-media-models" / "tiny.en",
            allow_download=False,
        ),
        model_loader=unexpected_loader,
    )

    assert result.status == "blocked"
    assert result.reason_code == "LOCAL_STT_MODEL_NOT_PROVISIONED"
    assert result.download_attempted is False
    assert loader_called is False


def test_model_bootstrap_env_download_policy_still_requires_cli_flag(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from idis.tools import media_model_bootstrap

    loader_called = False

    def unexpected_loader(*_: object) -> None:
        nonlocal loader_called
        loader_called = True
        raise AssertionError("env download policy must not replace --allow-download")

    monkeypatch.setenv("IDIS_MEDIA_STT_MODEL_NAME", "tiny.en")
    monkeypatch.setenv("IDIS_MEDIA_STT_ALLOW_DOWNLOAD", "1")
    monkeypatch.setattr(media_model_bootstrap, "_download_faster_whisper_model", unexpected_loader)

    exit_code = media_model_bootstrap.main(
        ["--output-dir", str(tmp_path / "local-media-models" / "tiny.en")]
    )

    assert exit_code == 1
    assert loader_called is False


def test_model_bootstrap_rejects_repo_internal_unignored_download_dir(
    monkeypatch: Any,
) -> None:
    from idis.tools.media_model_bootstrap import (
        FasterWhisperModelBootstrapOptions,
        bootstrap_faster_whisper_model,
    )

    loader_called = False

    def unexpected_loader(*_: object) -> None:
        nonlocal loader_called
        loader_called = True

    monkeypatch.chdir(REPO_ROOT)
    result = bootstrap_faster_whisper_model(
        FasterWhisperModelBootstrapOptions(
            model_name="tiny.en",
            output_dir=Path("unsafe-model-cache") / "tiny.en",
            allow_download=True,
        ),
        model_loader=unexpected_loader,
    )

    assert result.status == "blocked"
    assert result.reason_code == "UNSAFE_MODEL_OUTPUT_DIR"
    assert result.download_attempted is False
    assert loader_called is False


def test_model_bootstrap_rejects_absolute_repo_internal_unignored_download_dir(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from idis.tools.media_model_bootstrap import (
        FasterWhisperModelBootstrapOptions,
        bootstrap_faster_whisper_model,
    )

    loader_called = False

    def unexpected_loader(*_: object) -> None:
        nonlocal loader_called
        loader_called = True

    monkeypatch.chdir(tmp_path)
    result = bootstrap_faster_whisper_model(
        FasterWhisperModelBootstrapOptions(
            model_name="tiny.en",
            output_dir=REPO_ROOT / "unsafe-absolute-model-cache" / "tiny.en",
            allow_download=True,
        ),
        model_loader=unexpected_loader,
    )

    assert result.status == "blocked"
    assert result.reason_code == "UNSAFE_MODEL_OUTPUT_DIR"
    assert result.download_attempted is False
    assert loader_called is False


def test_model_bootstrap_ci_blocks_real_download_attempt(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from idis.tools.media_model_bootstrap import (
        FasterWhisperModelBootstrapOptions,
        bootstrap_faster_whisper_model,
    )

    loader_called = False

    def unexpected_loader(*_: object) -> None:
        nonlocal loader_called
        loader_called = True

    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = bootstrap_faster_whisper_model(
        FasterWhisperModelBootstrapOptions(
            model_name="tiny.en",
            output_dir=tmp_path / "local-media-models" / "tiny.en",
            allow_download=True,
        ),
        model_loader=unexpected_loader,
    )

    assert result.status == "blocked"
    assert result.reason_code == "MODEL_DOWNLOAD_BLOCKED_IN_CI"
    assert result.download_attempted is False
    assert loader_called is False


def test_model_bootstrap_allow_download_invokes_loader_and_revalidates(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from idis.tools.media_model_bootstrap import (
        FasterWhisperModelBootstrapOptions,
        bootstrap_faster_whisper_model,
    )

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    output_dir = tmp_path / "local-media-models" / "tiny.en"

    def synthetic_loader(model_name: str, destination: Path, compute_type: str) -> None:
        assert model_name == "tiny.en"
        assert compute_type == "int8"
        destination.mkdir(parents=True)
        (destination / "model.bin").write_text("synthetic model", encoding="utf-8")
        (destination / "config.json").write_text("{}", encoding="utf-8")

    result = bootstrap_faster_whisper_model(
        FasterWhisperModelBootstrapOptions(
            model_name="tiny.en",
            output_dir=output_dir,
            allow_download=True,
        ),
        model_loader=synthetic_loader,
    )

    assert result.status == "ready"
    assert result.reason_code == "LOCAL_MODEL_READY"
    assert result.can_attempt is True
    assert result.download_attempted is True
    assert str(output_dir) not in json.dumps(result.to_safe_dict(), sort_keys=True)


def test_model_bootstrap_revalidates_downloaded_model_path_returned_by_loader(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from idis.tools.media_model_bootstrap import (
        FasterWhisperModelBootstrapOptions,
        bootstrap_faster_whisper_model,
    )

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    output_dir = tmp_path / "local-media-models"
    downloaded_path = output_dir / "snapshot" / "tiny.en"

    def synthetic_loader(model_name: str, destination: Path, compute_type: str) -> Path:
        del model_name, destination, compute_type
        downloaded_path.mkdir(parents=True)
        (downloaded_path / "model.bin").write_text("synthetic model", encoding="utf-8")
        (downloaded_path / "config.json").write_text("{}", encoding="utf-8")
        return downloaded_path

    result = bootstrap_faster_whisper_model(
        FasterWhisperModelBootstrapOptions(
            model_name="tiny.en",
            output_dir=output_dir,
            allow_download=True,
        ),
        model_loader=synthetic_loader,
    )

    assert result.status == "ready"
    assert result.reason_code == "LOCAL_MODEL_READY"
    assert str(downloaded_path) not in json.dumps(result.to_safe_dict(), sort_keys=True)


def test_model_bootstrap_rejects_unsafe_downloaded_model_path_returned_by_loader(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from idis.tools.media_model_bootstrap import (
        FasterWhisperModelBootstrapOptions,
        bootstrap_faster_whisper_model,
    )

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    output_dir = tmp_path / "local-media-models"
    unsafe_returned_path = REPO_ROOT / "unsafe-returned-model-cache" / "tiny.en"
    try:
        unsafe_returned_path.mkdir(parents=True, exist_ok=True)
        (unsafe_returned_path / "model.bin").write_text("synthetic model", encoding="utf-8")
        (unsafe_returned_path / "config.json").write_text("{}", encoding="utf-8")

        def unsafe_loader(model_name: str, destination: Path, compute_type: str) -> Path:
            del model_name, destination, compute_type
            return unsafe_returned_path

        result = bootstrap_faster_whisper_model(
            FasterWhisperModelBootstrapOptions(
                model_name="tiny.en",
                output_dir=output_dir,
                allow_download=True,
            ),
            model_loader=unsafe_loader,
        )

        assert result.status == "blocked"
        assert result.reason_code == "UNSAFE_MODEL_OUTPUT_DIR"
        assert result.can_attempt is False
        assert str(unsafe_returned_path) not in json.dumps(result.to_safe_dict(), sort_keys=True)
    finally:
        with contextlib.suppress(OSError):
            (unsafe_returned_path / "model.bin").unlink()
        with contextlib.suppress(OSError):
            (unsafe_returned_path / "config.json").unlink()
        with contextlib.suppress(OSError):
            unsafe_returned_path.rmdir()
        with contextlib.suppress(OSError):
            unsafe_returned_path.parent.rmdir()


def test_gitignore_excludes_local_media_model_directories() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    required_patterns = [
        ".local_models/",
        ".local_media_models/",
        "models/",
        "var/media-models/",
        ".cache/faster-whisper/",
    ]
    for pattern in required_patterns:
        assert pattern in gitignore


def test_no_tracked_faster_whisper_model_files() -> None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked_files = result.stdout.splitlines()

    assert not any(Path(path).name == "model.bin" for path in tracked_files)
    assert not any(path.startswith("models/") for path in tracked_files)
    assert not any(path.startswith(".local_models/") for path in tracked_files)
    assert not any(path.startswith(".local_media_models/") for path in tracked_files)
    assert not any(path.startswith("var/media-models/") for path in tracked_files)
