"""GL-12 slice 2: harness deal creation sends an explicit API-valid stage.

The operator or the case supplies the stage. A missing or invalid stage fails
closed with a stable code before any request. There is no default.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from idis.evaluation.types import (
    HARNESS_DEAL_STAGE_INVALID,
    HARNESS_DEAL_STAGE_REQUIRED,
    HarnessContractError,
    deal_stage_for_request,
)

_INVALID_STAGES = ("series_a", "LATER_STAGE", "", None)


def _assert_safe_code(exc: pytest.ExceptionInfo[HarnessContractError], code: str) -> None:
    assert exc.value.code == code
    assert str(exc.value) == code
    for leaked in ("series_a", "LATER_STAGE", "not-a-stage"):
        assert leaked not in str(exc.value)
        assert leaked not in repr(exc.value)


def test_deal_stage_for_request_accepts_only_exact_openapi_values() -> None:
    assert deal_stage_for_request("SEED") == "SEED"
    assert deal_stage_for_request("LATER") == "LATER"
    assert deal_stage_for_request("OTHER") == "OTHER"


@pytest.mark.parametrize("value", _INVALID_STAGES)
def test_deal_stage_for_request_rejects_missing_and_inexact_values(value: object) -> None:
    expected = HARNESS_DEAL_STAGE_REQUIRED if value in ("", None) else HARNESS_DEAL_STAGE_INVALID
    with pytest.raises(HarnessContractError) as exc:
        deal_stage_for_request(value)
    _assert_safe_code(exc, expected)


def test_deal_stage_for_request_reads_the_openapi_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    def spec() -> dict[str, Any]:
        return {"components": {"schemas": {"DealStage": {"enum": ["SEED"]}}}}

    monkeypatch.setattr("idis.evaluation.types.load_openapi_spec", spec)
    assert deal_stage_for_request("SEED") == "SEED"
    with pytest.raises(HarnessContractError) as exc:
        deal_stage_for_request("SERIES_A")
    _assert_safe_code(exc, HARNESS_DEAL_STAGE_INVALID)


def test_real_example_stage_option_has_no_default() -> None:
    from idis.evaluation.real_example_run_harness import RealExampleFullRunHarnessOptions

    field = next(
        item
        for item in dataclasses.fields(RealExampleFullRunHarnessOptions)
        if item.name == "deal_stage"
    )
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING


def test_real_example_deal_request_carries_supplied_stage(tmp_path: Path) -> None:
    from idis.evaluation.real_example_run_harness import (
        RealExampleFullRunHarnessOptions,
        run_real_example_full_run_harness,
    )
    from tests.test_real_example_run_harness import _FakeApiClient

    root = tmp_path / "room"
    root.mkdir()
    (root / "private.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")
    client = _FakeApiClient()
    run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(
            root=root,
            deal_stage="SEED",
            api_client=client,
        )
    )
    assert client.deal_requests == [
        {
            "name": "Private real_example controlled run",
            "company_name": "Private real_example company",
            "stage": "SEED",
        }
    ]


@pytest.mark.parametrize("stage", ["LATER", "OTHER"])
def test_real_example_later_and_other_still_reach_deal_creation(tmp_path: Path, stage: str) -> None:
    from idis.evaluation.real_example_run_harness import (
        RealExampleFullRunHarnessOptions,
        run_real_example_full_run_harness,
    )
    from tests.test_real_example_run_harness import _FakeApiClient

    root = tmp_path / "room"
    root.mkdir()
    (root / "private.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")
    client = _FakeApiClient()
    run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, deal_stage=stage, api_client=client)
    )
    assert client.created_deals == 1
    assert client.deal_requests[0]["stage"] == stage


@pytest.mark.parametrize("stage", _INVALID_STAGES)
def test_real_example_missing_or_invalid_stage_sends_no_request(
    tmp_path: Path, stage: object
) -> None:
    from idis.evaluation.real_example_run_harness import (
        RealExampleFullRunHarnessOptions,
        run_real_example_full_run_harness,
    )
    from tests.test_real_example_run_harness import _FakeApiClient

    client = _FakeApiClient()
    expected = HARNESS_DEAL_STAGE_REQUIRED if stage in ("", None) else HARNESS_DEAL_STAGE_INVALID
    with pytest.raises(HarnessContractError) as exc:
        run_real_example_full_run_harness(
            RealExampleFullRunHarnessOptions(
                root=tmp_path / "missing-room",
                deal_stage=stage,  # type: ignore[arg-type]
                api_client=client,
            )
        )
    _assert_safe_code(exc, expected)
    assert client.created_deals == 0
    assert client.deal_requests == []


def test_gdbs_and_data_room_and_synthetic_stage_parameters_have_no_default() -> None:
    from idis.evaluation.data_room_harness import run_data_room_harness
    from idis.evaluation.harness import run_suite
    from idis.evaluation.real_example_source_package import prepare_two_company_source_packages
    from idis.evaluation.synthetic_strict_runtime_rehearsal import (
        build_bounded_synthetic_api_run_rehearsal,
        build_bounded_synthetic_api_upload_rehearsal,
        build_bounded_synthetic_full_execution_rehearsal,
        build_bounded_synthetic_package_surface_rehearsal,
    )

    functions = (
        run_suite,
        run_data_room_harness,
        prepare_two_company_source_packages,
        build_bounded_synthetic_api_upload_rehearsal,
        build_bounded_synthetic_api_run_rehearsal,
        build_bounded_synthetic_full_execution_rehearsal,
        build_bounded_synthetic_package_surface_rehearsal,
    )
    names = {
        run_suite: "deal_stage",
        run_data_room_harness: "stage",
        prepare_two_company_source_packages: "deal_stage",
    }
    for function in functions:
        parameter_name = names.get(function, "deal_stage")
        parameter = inspect.signature(function).parameters[parameter_name]
        assert parameter.default is inspect.Parameter.empty


def test_gdbs_execute_case_sends_supplied_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    from idis.evaluation.harness import _execute_case
    from idis.evaluation.types import GdbsCase

    bodies: list[dict[str, Any]] = []

    def mock_send(self: httpx.Client, request: httpx.Request, **kwargs: object) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v1/deals") and request.method == "POST":
            bodies.append(json.loads(request.content))
            return httpx.Response(status_code=201, json={"deal_id": "deal-1"}, request=request)
        if "/runs" in url and request.method == "POST":
            return httpx.Response(
                status_code=202,
                json={"run_id": "run-1", "status": "SUCCEEDED", "steps": []},
                request=request,
            )
        return httpx.Response(status_code=404, request=request)

    monkeypatch.setattr(httpx.Client, "send", mock_send)
    case = GdbsCase(
        case_id="case-1",
        deal_id="00000000-0000-0000-0000-000000000001",
        deal_key="deal_001",
        scenario="clean",
        directory="deals/missing",
        description="",
    )
    _execute_case(
        case,
        base_url="http://127.0.0.1:9",
        api_key=None,
        dataset_root=Path("tests/fixtures/gdbs_mini"),
        deal_stage="GROWTH",
    )
    assert bodies == [{"name": "deal_001", "company_name": "deal_001", "stage": "GROWTH"}]


@pytest.mark.parametrize("stage", _INVALID_STAGES)
def test_gdbs_execute_rejects_stage_before_any_request(
    monkeypatch: pytest.MonkeyPatch, stage: object
) -> None:
    from idis.evaluation.harness import run_suite

    requests: list[str] = []

    def mock_send(self: httpx.Client, request: httpx.Request, **kwargs: object) -> httpx.Response:
        requests.append(str(request.url))
        raise AssertionError("stage validation must happen before HTTP")

    monkeypatch.setattr(httpx.Client, "send", mock_send)
    expected = HARNESS_DEAL_STAGE_REQUIRED if stage in ("", None) else HARNESS_DEAL_STAGE_INVALID
    with pytest.raises(HarnessContractError) as exc:
        run_suite(
            Path("tests/fixtures/missing-gdbs"),
            "gdbs-s",
            deal_stage=stage,  # type: ignore[arg-type]
            mode="execute",
            base_url="http://127.0.0.1:9",
        )
    _assert_safe_code(exc, expected)
    assert requests == []


def test_gdbs_cli_stage_option_has_no_default() -> None:
    import argparse

    from idis.cli import create_parser

    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["test", "gdbs-s", "--dataset", "datasets/gdbs_full"])
    args = parser.parse_args(
        ["test", "gdbs-s", "--dataset", "datasets/gdbs_full", "--deal-stage", "SEED"]
    )
    assert args.deal_stage == "SEED"
    action = _argument(parser, "deal_stage")
    assert action is not None
    assert action.required is True
    assert action.default in (None, argparse.SUPPRESS)


def test_data_room_metadata_carries_supplied_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from idis.evaluation.data_room_harness import run_data_room_harness

    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> Any:
        captured["deal_metadata"] = kwargs["deal_metadata"]
        raise RuntimeError("stop-after-deal-metadata")

    monkeypatch.setattr("idis.evaluation.data_room_harness.build_run_context", capture)
    with pytest.raises(RuntimeError, match="stop-after-deal-metadata"):
        run_data_room_harness(
            data_room_root=tmp_path / "missing-room",
            stage="SERIES_A",
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            company_name="Acme Corp",
        )
    assert captured["deal_metadata"]["stage"] == "SERIES_A"


@pytest.mark.parametrize("stage", _INVALID_STAGES)
def test_data_room_missing_or_invalid_stage_builds_no_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: object
) -> None:
    from idis.evaluation.data_room_harness import run_data_room_harness

    calls: list[str] = []

    def capture(**_kwargs: Any) -> Any:
        calls.append("context")
        raise AssertionError("stage validation must happen before the run context")

    monkeypatch.setattr("idis.evaluation.data_room_harness.build_run_context", capture)
    expected = HARNESS_DEAL_STAGE_REQUIRED if stage in ("", None) else HARNESS_DEAL_STAGE_INVALID
    with pytest.raises(HarnessContractError) as exc:
        run_data_room_harness(
            data_room_root=tmp_path / "missing-room",
            stage=stage,  # type: ignore[arg-type]
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="22222222-2222-2222-2222-222222222222",
            company_name="Acme Corp",
        )
    _assert_safe_code(exc, expected)
    assert calls == []


def test_data_room_cli_stage_option_is_required() -> None:
    from idis.evaluation.data_room_harness import main

    with pytest.raises(SystemExit):
        main(
            [
                "--data-room-root",
                "missing",
                "--tenant-id",
                "11111111-1111-1111-1111-111111111111",
                "--deal-id",
                "22222222-2222-2222-2222-222222222222",
                "--company-name",
                "Acme Corp",
            ]
        )


def test_synthetic_upload_sends_supplied_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    bodies: list[dict[str, Any]] = []
    original_post = rehearsal.TestClient.post

    def recording_post(self: Any, url: str, *args: Any, **kwargs: Any) -> Any:
        if url == "/v1/deals":
            bodies.append(dict(kwargs.get("json") or {}))
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(rehearsal.TestClient, "post", recording_post)
    rehearsal.build_bounded_synthetic_api_upload_rehearsal(
        dataset_root=Path("datasets/gdbs_full"),
        env={},
        max_cases=1,
        allow_synthetic_api_upload=True,
        deal_stage="SERIES_B",
        object_store_base_dir=tmp_path / "objects",
    )
    assert bodies
    assert all(body.get("stage") == "SERIES_B" for body in bodies)


@pytest.mark.parametrize("stage", _INVALID_STAGES)
def test_synthetic_upload_rejects_stage_before_any_request(
    monkeypatch: pytest.MonkeyPatch, stage: object
) -> None:
    from idis.evaluation import synthetic_strict_runtime_rehearsal as rehearsal

    constructed: list[str] = []

    def refuse_client(*_args: Any, **_kwargs: Any) -> Any:
        constructed.append("client")
        raise AssertionError("stage validation must happen before the API client")

    monkeypatch.setattr(rehearsal.TestClient, "__init__", refuse_client)
    expected = HARNESS_DEAL_STAGE_REQUIRED if stage in ("", None) else HARNESS_DEAL_STAGE_INVALID
    with pytest.raises(HarnessContractError) as exc:
        rehearsal.build_bounded_synthetic_api_upload_rehearsal(
            dataset_root=Path("missing-dataset"),
            env={},
            max_cases=1,
            allow_synthetic_api_upload=True,
            deal_stage=stage,  # type: ignore[arg-type]
        )
    _assert_safe_code(exc, expected)
    assert constructed == []


def test_source_package_deal_request_carries_supplied_stage(tmp_path: Path) -> None:
    from idis.evaluation.real_example_source_package import prepare_two_company_source_packages
    from tests.slice100_task3._support import (
        FakePreparationApi,
        build_source_fixture,
        contract_kwargs,
    )

    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi()
    prepare_two_company_source_packages(
        api_client=api,
        api_key="test-secret",
        ledger_path=tmp_path / "ledger.jsonl",
        deal_stage="SERIES_B",
        **contract_kwargs(fixture),
    )
    deal_posts = [
        call for call in api.calls if call["method"] == "POST" and call["url"] == "/v1/deals"
    ]
    assert len(deal_posts) == 2
    assert all(call["json"]["stage"] == "SERIES_B" for call in deal_posts)


@pytest.mark.parametrize("stage", _INVALID_STAGES)
def test_source_package_rejects_stage_before_any_request(tmp_path: Path, stage: object) -> None:
    from idis.evaluation.real_example_source_package import prepare_two_company_source_packages
    from tests.slice100_task3._support import FakePreparationApi

    api = FakePreparationApi()
    expected = HARNESS_DEAL_STAGE_REQUIRED if stage in ("", None) else HARNESS_DEAL_STAGE_INVALID
    with pytest.raises(HarnessContractError) as exc:
        prepare_two_company_source_packages(
            api_client=api,
            api_key="test-secret",
            ledger_path=tmp_path / "ledger.jsonl",
            deal_stage=stage,  # type: ignore[arg-type]
            root=tmp_path / "missing-source",
        )
    _assert_safe_code(exc, expected)
    assert api.calls == []


def _argument(parser: Any, dest: str) -> Any:
    for action in parser._actions:
        if action.dest == dest:
            return action
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                found = _argument(subparser, dest)
                if found is not None:
                    return found
    return None


_OPENAPI_UNAVAILABLE = "HARNESS_OPENAPI_UNAVAILABLE"
_SECRET_OPENAPI_PATH = "C:/very/secret/spec.yaml"


def _fail_openapi_load() -> dict[str, Any]:
    from idis.api.openapi_loader import OpenAPILoadError

    raise OpenAPILoadError("boom", path=_SECRET_OPENAPI_PATH)


def _patch_openapi_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("idis.evaluation.types.load_openapi_spec", _fail_openapi_load)


def _load_gate3() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "gates" / "gate_3_gdbs_f.py"
    spec = importlib.util.spec_from_file_location("gl12_s2c_gate_3_gdbs_f", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_stderr_is_only_the_code(captured: Any, code: str) -> None:
    assert captured.err == f"{code}\n"
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert "secret" not in captured.err
    assert "secret" not in captured.out


def test_deal_stage_for_request_converts_openapi_load_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_openapi_load(monkeypatch)
    with pytest.raises(HarnessContractError) as exc:
        deal_stage_for_request("SEED")
    assert exc.value.code == _OPENAPI_UNAVAILABLE
    assert "secret" not in str(exc.value)
    assert "secret" not in repr(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_gdbs_cli_openapi_load_failure_prints_only_the_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from idis.cli import cmd_test_gdbs, create_parser

    _patch_openapi_load(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        ["test", "gdbs-s", "--dataset", "datasets/gdbs_full", "--deal-stage", "SEED"]
    )
    exit_code = cmd_test_gdbs(args)
    _assert_stderr_is_only_the_code(capsys.readouterr(), _OPENAPI_UNAVAILABLE)
    assert exit_code == 1


def test_gate3_execute_openapi_load_failure_prints_only_the_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    import sys

    gate = _load_gate3()
    monkeypatch.setattr(gate, "RESULTS_PATH", tmp_path)
    _patch_openapi_load(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["gate_3_gdbs_f.py", "--execute", "--deal-stage", "SEED"])
    exit_code = gate.main()
    _assert_stderr_is_only_the_code(capsys.readouterr(), _OPENAPI_UNAVAILABLE)
    assert exit_code == 1

    monkeypatch.setattr(sys, "argv", ["gate_3_gdbs_f.py", "--status"])
    status_code = gate.main()
    status = capsys.readouterr()
    assert status_code == 2
    assert "Traceback" not in status.out
    assert "Traceback" not in status.err
    assert "secret" not in status.out
    assert "secret" not in status.err
    assert _OPENAPI_UNAVAILABLE not in status.err


def test_gate3_status_next_steps_match_blocked_json_and_name_deal_stage() -> None:
    gate = _load_gate3()
    report_steps = gate.run_gate_3_blocked()["next_steps"]
    status_path = (
        Path(__file__).resolve().parents[1] / "docs" / "gates" / "gate_3_blocked_status.json"
    )
    document = json.loads(status_path.read_text(encoding="utf-8"))
    assert report_steps == document["next_steps"]
    assert "--deal-stage" in report_steps[-1]
