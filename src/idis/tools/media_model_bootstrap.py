"""Safe local faster-whisper model bootstrap and validation tooling."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from idis.parsers.media import (
    FasterWhisperMediaConfig,
    FasterWhisperModelStatus,
    probe_faster_whisper_model,
)

TOOL_NAME = "faster_whisper_model_bootstrap"
REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_MODEL_READY_REASON = "LOCAL_MODEL_READY"
LOCAL_STT_MODEL_NOT_PROVISIONED_REASON = "LOCAL_STT_MODEL_NOT_PROVISIONED"
DOWNLOAD_BLOCKED_IN_CI_REASON = "MODEL_DOWNLOAD_BLOCKED_IN_CI"
DOWNLOAD_FAILED_REASON = "MODEL_DOWNLOAD_FAILED"
INVALID_ARGUMENTS_REASON = "INVALID_ARGUMENTS"
UNSAFE_MODEL_OUTPUT_DIR_REASON = "UNSAFE_MODEL_OUTPUT_DIR"
DEFAULT_COMPUTE_TYPE = "int8"
IGNORED_REPO_MODEL_ROOTS = (
    Path(".local_models"),
    Path(".local_media_models"),
    Path("models"),
    Path("var") / "media-models",
    Path(".cache") / "faster-whisper",
)

ModelLoader = Callable[[str, Path, str], Path | None]


@dataclass(frozen=True, slots=True)
class FasterWhisperModelBootstrapOptions:
    """Inputs for local faster-whisper model validation or explicit bootstrap."""

    model_path: Path | None = None
    model_name: str | None = None
    output_dir: Path | None = None
    allow_download: bool = False
    compute_type: str = DEFAULT_COMPUTE_TYPE


@dataclass(frozen=True, slots=True)
class FasterWhisperModelBootstrapResult:
    """Path-free operational result for local model checks."""

    status: str
    reason_code: str
    can_attempt: bool
    download_attempted: bool

    def to_safe_dict(self) -> dict[str, object]:
        """Return JSON-safe output with no filesystem paths or model contents."""
        return {
            "tool": TOOL_NAME,
            "safe_summary": True,
            "status": self.status,
            "reason_code": self.reason_code,
            "can_attempt": self.can_attempt,
            "download_attempted": self.download_attempted,
        }


def bootstrap_faster_whisper_model(
    options: FasterWhisperModelBootstrapOptions,
    *,
    model_loader: ModelLoader | None = None,
) -> FasterWhisperModelBootstrapResult:
    """Validate or explicitly bootstrap a local faster-whisper model directory.

    Args:
        options: Model path/name/download controls. Paths are never returned.
        model_loader: Optional injected downloader used by tests.

    Returns:
        A path-free status suitable for aggregate-only logs.
    """
    if options.model_path is not None:
        return _probe_local_model(options.model_path)

    if options.model_name is None:
        return _blocked(LOCAL_STT_MODEL_NOT_PROVISIONED_REASON, download_attempted=False)

    if not options.allow_download:
        return _blocked(LOCAL_STT_MODEL_NOT_PROVISIONED_REASON, download_attempted=False)

    if options.output_dir is None:
        return _blocked(INVALID_ARGUMENTS_REASON, download_attempted=False)
    if not _safe_download_destination(options.output_dir):
        return _blocked(UNSAFE_MODEL_OUTPUT_DIR_REASON, download_attempted=False)

    if _running_in_ci():
        return _blocked(DOWNLOAD_BLOCKED_IN_CI_REASON, download_attempted=False)

    loader = model_loader or _download_faster_whisper_model
    try:
        downloaded_path = loader(options.model_name, options.output_dir, options.compute_type)
    except Exception:
        return _blocked(DOWNLOAD_FAILED_REASON, download_attempted=True)

    model_path = downloaded_path or options.output_dir
    if not _safe_download_destination(model_path) or not _path_is_within(
        child=model_path,
        parent=options.output_dir,
    ):
        return _blocked(UNSAFE_MODEL_OUTPUT_DIR_REASON, download_attempted=True)

    result = _probe_local_model(model_path, download_attempted=True)
    if result.can_attempt:
        return result
    return _blocked(LOCAL_STT_MODEL_NOT_PROVISIONED_REASON, download_attempted=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the faster-whisper model bootstrap CLI."""
    parser = argparse.ArgumentParser(
        description="Validate or explicitly bootstrap a local faster-whisper model directory.",
    )
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--model-path")
    source.add_argument("--model-name")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--compute-type", default=DEFAULT_COMPUTE_TYPE)
    args = parser.parse_args(argv)
    model_path = _path_arg_or_env(args.model_path, "IDIS_MEDIA_STT_MODEL_PATH")
    model_name = _string_arg_or_env(args.model_name, "IDIS_MEDIA_STT_MODEL_NAME")

    result = bootstrap_faster_whisper_model(
        FasterWhisperModelBootstrapOptions(
            model_path=model_path,
            model_name=model_name,
            output_dir=args.output_dir,
            allow_download=args.allow_download,
            compute_type=args.compute_type,
        )
    )
    print(json.dumps(result.to_safe_dict(), sort_keys=True, indent=2))
    return 0 if result.can_attempt else 1


def _probe_local_model(
    model_path: Path,
    *,
    download_attempted: bool = False,
) -> FasterWhisperModelBootstrapResult:
    probe = probe_faster_whisper_model(
        FasterWhisperMediaConfig(
            model_path=str(model_path),
            allow_model_download=False,
        )
    )
    if probe.status == FasterWhisperModelStatus.LOCAL_MODEL_READY and probe.can_attempt:
        return FasterWhisperModelBootstrapResult(
            status="ready",
            reason_code=LOCAL_MODEL_READY_REASON,
            can_attempt=True,
            download_attempted=download_attempted,
        )
    return _blocked(
        LOCAL_STT_MODEL_NOT_PROVISIONED_REASON,
        download_attempted=download_attempted,
    )


def _blocked(reason_code: str, *, download_attempted: bool) -> FasterWhisperModelBootstrapResult:
    return FasterWhisperModelBootstrapResult(
        status="blocked",
        reason_code=reason_code,
        can_attempt=False,
        download_attempted=download_attempted,
    )


def _download_faster_whisper_model(
    model_name: str,
    destination: Path,
    compute_type: str,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with _suppress_output():
        from faster_whisper.utils import download_model

        downloaded_path = download_model(
            model_name,
            output_dir=str(destination),
            local_files_only=False,
        )
    del compute_type
    return Path(downloaded_path)


def _running_in_ci() -> bool:
    return any(os.environ.get(name) for name in ("CI", "GITHUB_ACTIONS"))


def _safe_download_destination(destination: Path) -> bool:
    resolved_destination = destination.expanduser().resolve(strict=False)
    repo_root = REPO_ROOT.resolve(strict=False)
    try:
        relative_destination = resolved_destination.relative_to(repo_root)
    except ValueError:
        return True
    if not relative_destination.parts:
        return False
    return any(
        relative_destination == root or root in relative_destination.parents
        for root in IGNORED_REPO_MODEL_ROOTS
    )


def _path_is_within(*, child: Path, parent: Path) -> bool:
    resolved_child = child.expanduser().resolve(strict=False)
    resolved_parent = parent.expanduser().resolve(strict=False)
    try:
        resolved_child.relative_to(resolved_parent)
    except ValueError:
        return False
    return True


def _path_arg_or_env(raw_value: str | None, env_name: str) -> Path | None:
    value = _string_arg_or_env(raw_value, env_name)
    return Path(value) if value is not None else None


def _string_arg_or_env(raw_value: str | None, env_name: str) -> str | None:
    value = raw_value if raw_value is not None else os.environ.get(env_name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _suppress_output() -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def suppress() -> Iterator[None]:
        with (
            open(os.devnull, "w", encoding="utf-8") as devnull,
            contextlib.redirect_stdout(devnull),
            contextlib.redirect_stderr(devnull),
        ):
            yield

    return suppress()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
