"""Task 4 RED contract for durable faster-whisper runtime provenance.

No real media inference runs here.  Spawn-safe workers exercise the production
process boundary while binding every successful local-model result to content
and configuration fingerprints that are safe to persist in document JSONB.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

import idis.parsers.media as media_module
from idis.parsers.base import ParseErrorCode
from idis.parsers.media import (
    FasterWhisperMediaAdapter,
    FasterWhisperMediaConfig,
    MediaConfig,
    parse_media,
)
from idis.services.ingestion import IngestionContext, IngestionService
from idis.services.ingestion.service import RouteValidatedSha256
from idis.services.media_health import _fingerprint_local_model
from idis.storage.compliant_store import ComplianceEnforcedStore
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.slice100_task4.test_terminal_reconciliation import (
    EXPECTED_COMPANY_COUNTS,
    EXPECTED_FORMAT_COUNTS,
    EXPECTED_LARGE_FILES,
    EXPECTED_OCR_FILES,
    EXPECTED_PDF_ENCRYPTION_DISPOSITIONS,
    EXPECTED_STT_FILES,
    EXPECTED_TOTAL_FILES,
    FIFTY_MIB,
    _expected_pdf_ocr_hashes,
    _source_companies,
)

_TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
_DEAL_ID = UUID("33333333-3333-4333-8333-333333333333")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVENANCE_KEY = "media_runtime_provenance"
EXPECTED_MEDIA_RUNTIME_PROVENANCE = {
    "schema_version": "faster-whisper-runtime-provenance-v2",
    "adapter": "faster-whisper",
    "adapter_contract_version": "idis-faster-whisper-adapter-v1",
    "runtime_package_version": "fictional-version-1",
    "model_source": "local",
    "model_files_sha256": hashlib.sha256(b"fictional-model").hexdigest(),
    "cpu_threads": 4,
    "num_workers": 1,
    "config_sha256": hashlib.sha256(b"fictional-config").hexdigest(),
}


def _success_worker(
    source: object,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del source, config, timeout_seconds
    queue.put(
        {
            "status": "success",
            "segments": [{"start_ms": 0, "end_ms": 900, "text": "fictional provenance transcript"}],
        }
    )


def _mutating_worker(
    source: object,
    config: FasterWhisperMediaConfig,
    timeout_seconds: float,
    queue: Any,
) -> None:
    del source, timeout_seconds
    assert config.model_path is not None
    (Path(config.model_path) / "model.bin").write_bytes(b"mutated-during-inference")
    queue.put(
        {
            "status": "success",
            "segments": [{"start_ms": 0, "end_ms": 900, "text": "must not persist"}],
        }
    )


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "operator-private-model-location"
    model.mkdir()
    (model / "config.json").write_text('{"model":"fictional"}', encoding="utf-8")
    (model / "model.bin").write_bytes(b"fictional-model-content")
    (model / "vocabulary.txt").write_text("fictional vocabulary", encoding="utf-8")
    return model


def _adapter(
    model: Path,
    *,
    worker: Any = _success_worker,
    duration_probe: Any | None = None,
    compute_type: str = "int8",
) -> FasterWhisperMediaAdapter:
    return FasterWhisperMediaAdapter(
        config=FasterWhisperMediaConfig(
            model_path=str(model),
            allow_model_download=False,
            language="en",
            compute_type=compute_type,
            max_duration_seconds=10,
        ),
        binary_resolver=lambda _name: "fictional-binary",
        duration_probe=duration_probe or (lambda _source, _config, _timeout: 0.9),
        worker_target=worker,
    )


def _parse(model: Path, *, adapter: FasterWhisperMediaAdapter | None = None) -> Any:
    return parse_media(
        b"fictional-private-media",
        media_config=MediaConfig(
            enabled=True,
            adapter=adapter or _adapter(model),
            timeout_seconds=2,
        ),
    )


def test_local_model_success_emits_health_compatible_safe_runtime_provenance(
    tmp_path: Path,
) -> None:
    model = _model_dir(tmp_path)
    result = _parse(model)

    assert result.success is True
    provenance = result.metadata[_PROVENANCE_KEY]
    _path_sha256, health_model_files_sha256 = _fingerprint_local_model(model)
    assert provenance == {
        "schema_version": "faster-whisper-runtime-provenance-v2",
        "adapter": "faster-whisper",
        "adapter_contract_version": "idis-faster-whisper-adapter-v1",
        "runtime_package_version": importlib.metadata.version("faster-whisper"),
        "model_source": "local",
        "model_files_sha256": health_model_files_sha256,
        "cpu_threads": 4,
        "num_workers": 1,
        "config_sha256": provenance["config_sha256"],
    }
    assert _SHA256_RE.fullmatch(provenance["config_sha256"])

    encoded = json.dumps(provenance, sort_keys=True)
    assert str(model) not in encoded
    assert "operator-private-model-location" not in encoded
    assert "model.bin" not in encoded
    assert "fictional-model-content" not in encoded
    assert "fictional provenance transcript" not in encoded


def test_faster_whisper_receipt_carries_pinned_thread_configuration(tmp_path: Path) -> None:
    result = _parse(_model_dir(tmp_path))

    assert result.success is True
    receipt = result.metadata[_PROVENANCE_KEY]
    assert type(receipt["cpu_threads"]) is int
    assert type(receipt["num_workers"]) is int
    assert receipt["cpu_threads"] == 4
    assert receipt["num_workers"] == 1


def test_worker_passes_pinned_thread_configuration_to_whisper_constructor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    constructor_kwargs: dict[str, object] = {}

    class _Segment:
        start = 0.0
        end = 0.9
        text = "fictional pinned-thread transcript"

    class _WhisperModel:
        def __init__(self, _model_ref: object, **kwargs: object) -> None:
            constructor_kwargs.update(kwargs)

        def transcribe(self, _source: object, **_kwargs: object) -> tuple[list[_Segment], object]:
            return [_Segment()], object()

    class _Queue:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def put(self, payload: dict[str, object]) -> None:
            self.payloads.append(payload)

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=_WhisperModel),
    )
    source = tmp_path / "fictional-private-video.mp4"
    source.write_bytes(b"fictional-private-media")
    queue = _Queue()

    media_module._faster_whisper_worker(
        source,
        FasterWhisperMediaConfig(
            model_path=str(_model_dir(tmp_path)),
            allow_model_download=False,
            language="en",
            compute_type="int8",
            max_duration_seconds=10,
        ),
        2,
        queue,
    )

    assert queue.payloads == [
        {
            "status": "success",
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 900,
                    "text": "fictional pinned-thread transcript",
                }
            ],
        }
    ]
    assert constructor_kwargs["cpu_threads"] == 4
    assert constructor_kwargs["num_workers"] == 1


def test_config_fingerprint_changes_when_admitted_inference_config_changes(tmp_path: Path) -> None:
    model = _model_dir(tmp_path)

    int8 = _parse(model, adapter=_adapter(model, compute_type="int8"))
    float32 = _parse(model, adapter=_adapter(model, compute_type="float32"))

    assert int8.success is True
    assert float32.success is True
    assert (
        int8.metadata[_PROVENANCE_KEY]["config_sha256"]
        != float32.metadata[_PROVENANCE_KEY]["config_sha256"]
    )
    assert (
        int8.metadata[_PROVENANCE_KEY]["model_files_sha256"]
        == float32.metadata[_PROVENANCE_KEY]["model_files_sha256"]
    )


def test_config_fingerprint_changes_when_pinned_thread_configuration_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = _model_dir(tmp_path)
    baseline = _parse(model)

    monkeypatch.setattr(media_module, "FASTER_WHISPER_CPU_THREADS", 5, raising=False)
    cpu_thread_change = _parse(model)
    monkeypatch.setattr(media_module, "FASTER_WHISPER_CPU_THREADS", 4)
    monkeypatch.setattr(media_module, "FASTER_WHISPER_NUM_WORKERS", 2, raising=False)
    worker_change = _parse(model)

    assert baseline.success is True
    assert cpu_thread_change.success is True
    assert worker_change.success is True
    baseline_sha256 = baseline.metadata[_PROVENANCE_KEY]["config_sha256"]
    assert cpu_thread_change.metadata[_PROVENANCE_KEY]["config_sha256"] != baseline_sha256
    assert worker_change.metadata[_PROVENANCE_KEY]["config_sha256"] != baseline_sha256


def test_model_mutation_between_admission_and_inference_fails_closed(tmp_path: Path) -> None:
    model = _model_dir(tmp_path)

    def remove_required_model_file(
        _source: object,
        _config: FasterWhisperMediaConfig,
        _timeout: float,
    ) -> float:
        (model / "config.json").unlink()
        return 0.9

    result = _parse(model, adapter=_adapter(model, duration_probe=remove_required_model_file))

    assert result.success is False
    assert [error.code for error in result.errors] == [ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED]
    assert _PROVENANCE_KEY not in result.metadata


def test_model_mutation_during_inference_rejects_completed_looking_output(tmp_path: Path) -> None:
    model = _model_dir(tmp_path)

    result = _parse(model, adapter=_adapter(model, worker=_mutating_worker))

    assert result.success is False
    assert result.spans == []
    assert [error.code for error in result.errors] == [ParseErrorCode.MEDIA_TRANSCRIPTION_FAILED]
    assert _PROVENANCE_KEY not in result.metadata


def test_ingestion_persists_runtime_provenance_in_existing_document_jsonb(tmp_path: Path) -> None:
    model = _model_dir(tmp_path)
    payload = b"fictional-private-media"
    source_path = tmp_path / "fictional-private-video.mp4"
    source_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    service = IngestionService(
        compliant_store=ComplianceEnforcedStore(
            inner_store=FilesystemObjectStore(base_dir=tmp_path / "store")
        ),
        media_config=MediaConfig(enabled=True, adapter=_adapter(model), timeout_seconds=2),
    )
    result = service.ingest_file(
        ctx=IngestionContext(
            tenant_id=_TENANT_ID,
            actor_id="task4-test",
            request_id="task4-media-provenance",
        ),
        deal_id=_DEAL_ID,
        filename="fictional-private-video.mp4",
        media_type="video/mp4",
        source_path=source_path,
        size_bytes=len(payload),
        validated_sha256=RouteValidatedSha256.from_digest(
            actual_sha256=digest,
            expected_sha256=digest,
        ),
    )

    assert result.success is True
    assert result.document_id is not None
    document = service.get_document(_TENANT_ID, result.document_id)
    assert document is not None
    provenance = document.metadata[_PROVENANCE_KEY]
    assert provenance["adapter"] == "faster-whisper"
    assert _SHA256_RE.fullmatch(provenance["model_files_sha256"])
    assert _SHA256_RE.fullmatch(provenance["config_sha256"])
    assert json.loads(json.dumps(provenance)) == provenance
    assert str(model) not in json.dumps(document.metadata, sort_keys=True)


def _reconcile_media_provenance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from idis.evaluation.real_example_terminal_reconciliation import (
        reconcile_terminal_ingestion,
    )

    return reconcile_terminal_ingestion(
        source_companies=_source_companies(),
        terminal_rows=rows,
        expected_company_counts=EXPECTED_COMPANY_COUNTS,
        expected_format_counts=EXPECTED_FORMAT_COUNTS,
        expected_total_files=EXPECTED_TOTAL_FILES,
        expected_ocr_files=EXPECTED_OCR_FILES,
        expected_stt_files=EXPECTED_STT_FILES,
        expected_large_files=EXPECTED_LARGE_FILES,
        large_file_threshold_bytes=FIFTY_MIB,
        expected_pdf_encryption_dispositions=EXPECTED_PDF_ENCRYPTION_DISPOSITIONS,
        expected_media_runtime_provenance=EXPECTED_MEDIA_RUNTIME_PROVENANCE,
        expected_pdf_ocr_content_sha256s=_expected_pdf_ocr_hashes(_source_companies()),
    )


def _rows_with_media_provenance() -> list[dict[str, Any]]:
    from tests.slice100_task4.test_media_dual_track_terminal import _dual_track_rows

    _companies, rows = _dual_track_rows(lambda _index: (True, False))
    for row in rows:
        if row["extension"] == "mp4":
            row[_PROVENANCE_KEY] = copy.deepcopy(EXPECTED_MEDIA_RUNTIME_PROVENANCE)
    return rows


def test_all_media_terminal_rows_bind_to_one_admitted_runtime_provenance() -> None:
    summary = _reconcile_media_provenance(_rows_with_media_provenance())

    assert _SHA256_RE.fullmatch(summary["media_runtime_provenance_sha256"])
    assert "runtime_package_version" not in summary


def test_missing_or_mismatched_media_runtime_provenance_blocks_reconciliation() -> None:
    import pytest

    from idis.evaluation.real_example_terminal_reconciliation import (
        TerminalIngestionReconciliationError,
    )

    rows = _rows_with_media_provenance()
    media_rows = [row for row in rows if row["extension"] == "mp4"]
    del media_rows[0][_PROVENANCE_KEY]
    media_rows[1][_PROVENANCE_KEY]["model_files_sha256"] = "0" * 64

    with pytest.raises(TerminalIngestionReconciliationError):
        _reconcile_media_provenance(rows)
