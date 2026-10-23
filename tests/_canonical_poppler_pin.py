"""Host gate for rows that accept only the Windows Poppler byte pin.

The predicate is the binding type. A resolved value that is not an exact
``PinnedPopplerRuntime`` records ``CANONICAL_POPPLER_PIN NOT_EXECUTED`` and
returns. The wrapper never skips. On a Windows host with the pin present the
wrapped test runs.
"""

from __future__ import annotations

import functools
import inspect
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from idis.parsers.poppler_runtime import (
    PinnedPopplerRuntime,
    PopplerRuntimeBindingError,
    resolve_pinned_poppler_runtime,
)

CANONICAL_POPPLER_PIN_ABSENT_LINE = "CANONICAL_POPPLER_PIN NOT_EXECUTED: Windows byte pin absent"
CANONICAL_POPPLER_PIN_PROPERTY = "canonical_poppler_pin"
CANONICAL_POPPLER_PIN_REQUIRE_ENV = "IDIS_REQUIRE_CANONICAL_POPPLER_PIN"
_PIN_IDENTITY_SHA256 = "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72"
_UNAVAILABLE = "POPPLER_RUNTIME_UNAVAILABLE"
_RECORD_PROPERTY: Callable[[str, object], None] | None = None
_PIN_PROBE: tuple[bool, BaseException | None] | None = None


class CanonicalPopplerPinGatedNodes(tuple[tuple[str, str], ...]):
    __doc__ = (
        "Windows-pin rows. The only acceptance evidence is pin identity\n"
        "8ebfb07d9a8e5e01c12cc2994837a122cc6c94a930c657e10ffc655db8f0cc72.\n"
        "\n"
        "CANONICAL_POPPLER_PIN rows count toward acceptance only from the owner's\n"
        "Windows present-mode run at the final head.\n"
        "A hosted NOT_EXECUTED result is never a pass.\n"
        "\n"
        "The predicate is the resolved binding type, never the identity value, so\n"
        "it covers the identity check at\n"
        "real_example_terminal_reconciliation.py:1458\n"
        "and the type check at pdf.py:906. The Linux identity is never admitted\n"
        "to terminal reconciliation. :268 and :1458 stay the Windows pin.\n"
    )

    def __new__(cls, values: tuple[tuple[str, str], ...]) -> CanonicalPopplerPinGatedNodes:
        return tuple.__new__(cls, values)


CANONICAL_POPPLER_PIN_GATED_NODES: tuple[tuple[str, str], ...] = CanonicalPopplerPinGatedNodes(
    (
        (
            "tests/slice100_task4/test_complete_pdf_page_census_red.py",
            "test_all_image_only_pdf_remains_reviewer_disposition_blocker",
        ),
        (
            "tests/slice100_task4/test_complete_pdf_page_census_red.py",
            "test_page_claimed_by_native_text_and_image_only_receipt_fails_closed",
        ),
        (
            "tests/slice100_task4/test_complete_pdf_page_census_red.py",
            "test_production_mixed_fixture_persists_complete_disjoint_census",
        ),
        (
            "tests/slice100_task4/test_pdf_page_ocr_merge.py",
            "test_read_only_census_derives_highest_fitting_rung_from_source_geometry",
        ),
        (
            "tests/slice100_task4/test_pdf_page_ocr_merge.py",
            "test_read_only_pdf_ocr_census_binds_required_files_by_content_hash",
        ),
        (
            "tests/slice100_task4/test_pdf_render_rounding_red.py",
            "test_real_poppler_half_pixel_matrix_emits_exact_v2_render_provenance",
        ),
        (
            "tests/slice100_task4/test_pdf_render_rounding_red.py",
            "test_worker_rejects_expected_dimension_mismatch_and_actual_resource_overrun",
        ),
        (
            "tests/slice100_task4/test_pdf_render_rounding_red.py",
            "test_real_rounding_artifact_succeeds_through_file_backed_worker",
        ),
    )
)


def _evaluate_pin_probe() -> tuple[bool, BaseException | None]:
    try:
        runtime = resolve_pinned_poppler_runtime()
    except PopplerRuntimeBindingError as exc:
        if exc.code == _UNAVAILABLE:
            return True, None
        return False, exc
    return type(runtime) is not PinnedPopplerRuntime, None


def _canonical_pin_absent() -> bool:
    global _PIN_PROBE
    if _PIN_PROBE is None:
        _PIN_PROBE = _evaluate_pin_probe()
    absent, error = _PIN_PROBE
    if error is not None:
        raise error
    return absent


def _published_signature(test_fn: Callable[..., Any]) -> inspect.Signature:
    signature = inspect.signature(test_fn)
    if "record_property" in signature.parameters:
        return signature
    parameters = [
        *signature.parameters.values(),
        inspect.Parameter("record_property", inspect.Parameter.KEYWORD_ONLY),
    ]
    return signature.replace(parameters=parameters)


def _call_test(
    test_fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    accepts_recorder: bool,
) -> Any:
    call_kwargs = kwargs
    if not accepts_recorder:
        call_kwargs = {key: value for key, value in kwargs.items() if key != "record_property"}
    return test_fn(*args, **call_kwargs)


def requires_canonical_poppler_pin(test_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Run the test only when the resolver returns an exact Windows byte pin.

    Any other resolved type, or ``POPPLER_RUNTIME_UNAVAILABLE``, prints
    ``CANONICAL_POPPLER_PIN NOT_EXECUTED: Windows byte pin absent``, records
    that line on ``canonical_poppler_pin``, and returns.
    ``IDIS_REQUIRE_CANONICAL_POPPLER_PIN=1`` fails the node. Any other binding
    error fails the node. The wrapper never skips.
    """

    signature = inspect.signature(test_fn)
    accepts_recorder = "record_property" in signature.parameters

    @functools.wraps(test_fn)
    def _run(*args: Any, **kwargs: Any) -> Any:
        supplied = kwargs.get("record_property")
        recorder = supplied if callable(supplied) else _RECORD_PROPERTY
        if not _canonical_pin_absent():
            return _call_test(test_fn, args, kwargs, accepts_recorder=accepts_recorder)
        print(CANONICAL_POPPLER_PIN_ABSENT_LINE)
        if recorder is not None:
            recorder(CANONICAL_POPPLER_PIN_PROPERTY, CANONICAL_POPPLER_PIN_ABSENT_LINE)
        if os.environ.get(CANONICAL_POPPLER_PIN_REQUIRE_ENV) == "1":
            pytest.fail(CANONICAL_POPPLER_PIN_ABSENT_LINE, pytrace=False)
        return None

    _run.__signature__ = _published_signature(test_fn)  # type: ignore[attr-defined]
    # Pytest follows __wrapped__ back to the undecorated signature, which
    # would drop the injected record_property fixture.
    del _run.__wrapped__
    return _run


def pin_identity_sha256() -> str:
    """Return the Windows pin identity named as the only acceptance evidence."""

    return _PIN_IDENTITY_SHA256


def collected_canonical_poppler_pin_node_ids() -> tuple[str, ...]:
    """Return every collected parametrisation of the Windows-pin rows."""

    import io
    from contextlib import redirect_stderr, redirect_stdout

    wanted = set(CANONICAL_POPPLER_PIN_GATED_NODES)
    found: list[str] = []

    class _Collect:
        def pytest_collection_modifyitems(
            self, config: pytest.Config, items: list[pytest.Item]
        ) -> None:
            del config
            for item in items:
                node_id = item.nodeid.replace("\\", "/")
                head = node_id.split("[", 1)[0]
                key = (head.split("::", 1)[0], head.rsplit("::", 1)[-1])
                if key in wanted:
                    found.append(node_id)

    modules = sorted({module for module, _name in CANONICAL_POPPLER_PIN_GATED_NODES})
    stdout = io.StringIO()
    stderr = io.StringIO()
    root = Path(__file__).resolve().parents[1]
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = pytest.main(
            [
                "--rootdir",
                str(root),
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                *modules,
            ],
            plugins=[_Collect()],
        )
    if code != 0:
        sys.stderr.write(stderr.getvalue())
        sys.stderr.write(stdout.getvalue())
        raise SystemExit(code)
    return tuple(found)
