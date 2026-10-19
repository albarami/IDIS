"""Task 4 RED contract for bounded, consensus-gated PDF OCR rescue."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import statistics
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image, ImageDraw, ImageFont, ImageOps

from idis.parsers import ocr
from idis.parsers import pdf as pdf_parser
from tests.slice100_task4.test_linux_poppler_parent_guard import (
    inject_inline_governed_child_seam,
)

_FICTIONAL_PHRASE = "fictional faint evidence alpha beta"
_POLICY_VERSION = "pdf-ocr-rescue-v1"
_ROTATIONS = (0, 90, 180, 270)
_TRANSFORMS = ("autocontrast-c0", "binary-192", "binary-224")
_PSMS = (6, 11)
_EXPECTED_MATRIX = tuple(
    (rotation, transform, psm)
    for rotation in _ROTATIONS
    for transform in _TRANSFORMS
    for psm in _PSMS
)
_FAINT_FIXTURE_SHA256 = "4dc70888cc83ccc9e060555e71a5a9ec818ab87d515bd9ea072fa8b357103f74"
_AGREEMENT_FLOOR_FIXTURE_SHA256 = "a56e3b508870cb2673cd00dde82970a16788e8b0e049418dba94535747997556"
_SHA256_KEYS = {
    "policy_sha256",
    "winner_confidence_values_sha256",
    "candidate_receipt_sha256",
    "normalized_page_text_sha256",
}
_RESCUE_PROVENANCE_KEYS = {
    "policy_version",
    "policy_sha256",
    "winner_rotation_degrees",
    "winner_transform_id",
    "winner_psm",
    "winner_word_count",
    "winner_mean_confidence",
    "winner_median_confidence",
    "winner_minimum_confidence",
    "winner_maximum_confidence",
    "winner_confidence_histogram_counts",
    "winner_confidence_values_sha256",
    "winner_fraction_at_or_above_0_50",
    "attempted_candidate_count",
    "supporting_candidate_count",
    "minimum_clique_agreement",
    "mean_clique_agreement",
    "candidate_receipt_sha256",
    "normalized_page_text_sha256",
}


class _CaptureQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def put(self, payload: dict[str, object]) -> None:
        self.items.append(payload)


class _Output:
    DICT = "dict"


class _PdfInfoNotInstalledError(Exception):
    pass


class _PdfPopplerTimeoutError(Exception):
    pass


class _TesseractError(Exception):
    pass


class _TesseractNotFoundError(Exception):
    pass


def _required_callable(name: str) -> Any:
    value = getattr(ocr, name, None)
    assert callable(value), f"idis.parsers.ocr must implement callable {name}"
    return value


def _required_error_type(name: str) -> type[BaseException]:
    value = getattr(ocr, name, None)
    assert isinstance(value, type) and issubclass(value, BaseException), (
        f"idis.parsers.ocr must implement exception {name}"
    )
    return value


def _empty_tesseract_payload() -> dict[str, object]:
    return {
        "text": [],
        "conf": [],
        "block_num": [],
        "par_num": [],
        "line_num": [],
        "word_num": [],
    }


def _image_pixels(image: Image.Image) -> tuple[int, ...]:
    flattened = getattr(image, "get_flattened_data", None)
    if callable(flattened):
        return tuple(flattened())
    return tuple(image.getdata())


def _text_payload(
    text: str,
    *,
    confidences: tuple[float, ...] | None = None,
) -> dict[str, object]:
    words = text.split()
    normalized_confidences = confidences or tuple(0.95 for _word in words)
    assert len(normalized_confidences) == len(words)
    return {
        "text": words,
        "conf": [str(value * 100.0) for value in normalized_confidences],
        "block_num": [1] * len(words),
        "par_num": [1] * len(words),
        "line_num": [1] * len(words),
        "word_num": list(range(1, len(words) + 1)),
    }


def _rotate(image: Image.Image, degrees: int) -> Image.Image:
    if degrees == 0:
        return image.copy()
    transpose = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }[degrees]
    return image.transpose(transpose)


def _expected_candidate_pixels(
    image: Image.Image,
    *,
    rotation: int,
    transform: str,
) -> tuple[tuple[int, int], tuple[int, ...]]:
    with _rotate(image.convert("L"), rotation) as rotated:
        if transform == "autocontrast-c0":
            transformed = ImageOps.autocontrast(rotated, cutoff=0)
        else:
            threshold = 192 if transform == "binary-192" else 224
            transformed = rotated.point(lambda pixel: 0 if pixel < threshold else 255)
        try:
            return transformed.size, _image_pixels(transformed)
        finally:
            transformed.close()


def _candidate_image(
    image: Image.Image,
    *,
    rotation: int,
    transform: str,
) -> Image.Image:
    with _rotate(image.convert("L"), rotation) as rotated:
        if transform == "autocontrast-c0":
            return ImageOps.autocontrast(rotated, cutoff=0)
        threshold = 192 if transform == "binary-192" else 224
        return rotated.point(lambda pixel: 0 if pixel < threshold else 255)


def _fixture_sha256(image: Image.Image) -> str:
    digest = hashlib.sha256()
    digest.update(image.mode.encode("ascii"))
    digest.update(b"\0")
    digest.update(image.width.to_bytes(4, "big"))
    digest.update(image.height.to_bytes(4, "big"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def _project_authored_faint_scan() -> Image.Image:
    image = Image.new("L", (1400, 240), color=248)
    ImageDraw.Draw(image).text(
        (60, 75),
        _FICTIONAL_PHRASE,
        font=ImageFont.load_default(size=52),
        fill=210,
    )
    return image


def _project_authored_agreement_floor_scan() -> Image.Image:
    dark_line = "amber cobalt delta ember forest galaxy"
    medium_line = "harbor island juniper kernel lantern meadow"
    threshold_line = "nectar orbit prairie quartz river summit"
    lines = [(dark_line, 150), *[(medium_line, 205)] * 6, *[(threshold_line, 223)] * 50]
    line_spacing = 58
    image = Image.new("L", (1800, 80 + len(lines) * line_spacing), color=225)
    draw = ImageDraw.Draw(image)
    # The black corner pixel pins autocontrast's lower endpoint without resembling text.
    draw.point((0, 0), fill=0)
    font = ImageFont.load_default(size=42)
    for line_number, (text, fill) in enumerate(lines):
        draw.text((40, 30 + line_number * line_spacing), text, font=font, fill=fill)
    return image


def _oracle_tokens(text: str) -> tuple[str, ...]:
    characters = [
        character if unicodedata.category(character).startswith(("L", "N")) else " "
        for character in unicodedata.normalize("NFKC", text).casefold()
    ]
    return tuple("".join(characters).split())


def _multiset_dice(left: tuple[str, ...], right: tuple[str, ...], *, width: int) -> float:
    left_counts = Counter(
        tuple(left[index : index + width]) for index in range(len(left) - width + 1)
    )
    right_counts = Counter(
        tuple(right[index : index + width]) for index in range(len(right) - width + 1)
    )
    if not left_counts or not right_counts:
        return 0.0
    overlap = sum(min(count, right_counts[gram]) for gram, count in left_counts.items())
    return 2.0 * overlap / (sum(left_counts.values()) + sum(right_counts.values()))


def _oracle_agreement(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    return min(
        _multiset_dice(left, right, width=1),
        _multiset_dice(left, right, width=2),
    )


def _oracle_quality_admitted(
    tokens: tuple[str, ...],
    confidences: tuple[float, ...],
) -> bool:
    if (
        len(tokens) < 4
        or sum(len(token) for token in tokens) < 16
        or len(confidences) != len(tokens)
        or not confidences
        or any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in confidences)
    ):
        return False
    return (
        math.fsum(confidences) / len(confidences) >= 0.60
        and statistics.median(confidences) >= 0.55
        and sum(value >= 0.50 for value in confidences) / len(confidences) >= 0.75
    )


def _real_tesseract_records(
    image: Image.Image,
    *,
    matrix: tuple[tuple[int, str, int], ...] = _EXPECTED_MATRIX,
) -> list[SimpleNamespace]:
    if shutil.which("tesseract") is None:
        pytest.skip("real local Tesseract binary is unavailable")
    # Import/runtime/language failures are real failures, not skip conditions.
    from pytesseract import Output, image_to_data

    records: list[SimpleNamespace] = []
    for ordinal, (rotation, transform, psm) in enumerate(matrix):
        with _candidate_image(image, rotation=rotation, transform=transform) as candidate_image:
            payload = image_to_data(
                candidate_image,
                lang="eng",
                config=f"--oem 1 --psm {psm}",
                output_type=Output.DICT,
                timeout=20,
            )
        words: list[str] = []
        confidences: list[float] = []
        for raw_word, raw_confidence in zip(payload["text"], payload["conf"], strict=True):
            word = str(raw_word).strip()
            if not word:
                continue
            words.append(word)
            confidences.append(float(raw_confidence) / 100.0)
        tokens = _oracle_tokens(" ".join(words))
        normalized_confidences = tuple(confidences)
        records.append(
            SimpleNamespace(
                matrix_ordinal=ordinal,
                rotation_degrees=rotation,
                transform_id=transform,
                psm=psm,
                tokens=tokens,
                word_confidences=normalized_confidences,
                quality_admitted=_oracle_quality_admitted(tokens, normalized_confidences),
            )
        )
    return records


def _qualifying_cliques(records: list[SimpleNamespace]) -> list[frozenset[int]]:
    qualifying: list[frozenset[int]] = []
    for rotation in _ROTATIONS:
        admitted = [
            record
            for record in records
            if record.rotation_degrees == rotation and record.quality_admitted
        ]
        for size in range(3, len(admitted) + 1):
            for members in combinations(admitted, size):
                if (
                    len({member.transform_id for member in members}) < 2
                    or {member.psm for member in members} != {6, 11}
                    or any(
                        _oracle_agreement(left.tokens, right.tokens) < 0.8
                        for left, right in combinations(members, 2)
                    )
                ):
                    continue
                qualifying.append(frozenset(member.matrix_ordinal for member in members))
    return [clique for clique in qualifying if not any(clique < other for other in qualifying)]


def _page_result(
    *,
    image: Image.Image,
    image_to_data: Any,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    return ocr._ocr_pdf_page_from_data(
        image=image,
        page_number=1,
        language="eng",
        deadline=time.monotonic() + timeout_seconds,
        image_to_data=image_to_data,
        output=_Output,
    )


def _provenance_mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(cast(Any, value))
    raise AssertionError("rescue provenance must be a mapping or immutable dataclass")


def _node(
    ordinal: int,
    *,
    rotation: int,
    transform: str,
    psm: int,
    mean_confidence: float = 0.9,
    high_confidence_fraction: float = 1.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        matrix_ordinal=ordinal,
        rotation_degrees=rotation,
        transform_id=transform,
        psm=psm,
        quality_admitted=True,
        mean_confidence=mean_confidence,
        fraction_at_or_above_0_50=high_confidence_fraction,
    )


def _pairwise_matrix(
    nodes: list[SimpleNamespace],
    *,
    edges: dict[tuple[int, int], float],
) -> dict[tuple[int, int], float]:
    values: dict[tuple[int, int], float] = {}
    for left, right in combinations(nodes, 2):
        key = tuple(sorted((left.matrix_ordinal, right.matrix_ordinal)))
        values[key] = edges.get(key, 0.0)
    return values


def _install_fake_file_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    convert_from_path: Any,
    image_to_data: Any,
) -> None:
    pdf2image = ModuleType("pdf2image")
    pdf2image.convert_from_path = convert_from_path  # type: ignore[attr-defined]
    pdf2image_exceptions = ModuleType("pdf2image.exceptions")
    pdf2image_exceptions.PDFInfoNotInstalledError = _PdfInfoNotInstalledError  # type: ignore[attr-defined]
    pdf2image_exceptions.PDFPopplerTimeoutError = _PdfPopplerTimeoutError  # type: ignore[attr-defined]
    pytesseract = ModuleType("pytesseract")
    pytesseract.Output = _Output  # type: ignore[attr-defined]
    pytesseract.TesseractError = _TesseractError  # type: ignore[attr-defined]
    pytesseract.TesseractNotFoundError = _TesseractNotFoundError  # type: ignore[attr-defined]
    pytesseract.image_to_data = image_to_data  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdf2image", pdf2image)
    monkeypatch.setitem(sys.modules, "pdf2image.exceptions", pdf2image_exceptions)
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)
    inject_inline_governed_child_seam(monkeypatch, ocr)


def test_rescue_matrix_is_exact_and_transforms_pixels_in_fixed_order() -> None:
    image = Image.new("L", (2, 3))
    image.putdata((10, 191, 192, 223, 224, 250))
    calls: list[dict[str, object]] = []

    def image_to_data(candidate_image: Image.Image, **kwargs: object) -> dict[str, object]:
        calls.append(
            {
                "mode": candidate_image.mode,
                "size": candidate_image.size,
                "pixels": _image_pixels(candidate_image),
                "kwargs": dict(kwargs),
            }
        )
        return _empty_tesseract_payload()

    no_consensus = _required_error_type("OcrRescueNoConsensusError")
    with pytest.raises(no_consensus):
        _page_result(image=image, image_to_data=image_to_data)

    assert len(calls) == 25
    primary, candidate_calls = calls[0], calls[1:]
    primary_kwargs = primary["kwargs"]
    assert isinstance(primary_kwargs, dict)
    assert "config" not in primary_kwargs
    assert len(candidate_calls) == len(_EXPECTED_MATRIX) == 24
    for call, (rotation, transform, psm) in zip(candidate_calls, _EXPECTED_MATRIX, strict=True):
        assert call["mode"] == "L"
        assert (call["size"], call["pixels"]) == _expected_candidate_pixels(
            image,
            rotation=rotation,
            transform=transform,
        )
        kwargs = call["kwargs"]
        assert isinstance(kwargs, dict)
        assert kwargs["lang"] == "eng"
        assert kwargs["output_type"] == _Output.DICT
        assert kwargs["config"] == f"--oem 1 --psm {psm}"

    timeouts: list[float] = []
    for call in calls:
        kwargs = call["kwargs"]
        assert isinstance(kwargs, dict)
        raw_timeout = kwargs["timeout"]
        assert isinstance(raw_timeout, (int, float))
        timeouts.append(float(raw_timeout))
    assert all(timeout > 0 for timeout in timeouts)
    assert all(later <= earlier for earlier, later in zip(timeouts, timeouts[1:], strict=False))


def test_project_authored_scan_generation_is_immutable() -> None:
    with _project_authored_faint_scan() as faint_scan:
        assert faint_scan.mode == "L"
        assert faint_scan.size == (1400, 240)
        assert _fixture_sha256(faint_scan) == _FAINT_FIXTURE_SHA256
    with _project_authored_agreement_floor_scan() as floor_scan:
        assert floor_scan.mode == "L"
        assert floor_scan.size == (1800, 3386)
        assert _fixture_sha256(floor_scan) == _AGREEMENT_FLOOR_FIXTURE_SHA256


def test_project_authored_faint_scan_forms_one_real_tesseract_rescue_clique() -> None:
    upright_matrix = tuple(candidate for candidate in _EXPECTED_MATRIX if candidate[0] == 0)
    with _project_authored_faint_scan() as image:
        records = _real_tesseract_records(image, matrix=upright_matrix)

    cliques = _qualifying_cliques(records)
    assert cliques == [frozenset({0, 1, 4, 5})]
    winners = [record for record in records if record.matrix_ordinal in cliques[0]]
    assert all(record.tokens == _oracle_tokens(_FICTIONAL_PHRASE) for record in winners)
    assert {record.transform_id for record in winners} == {
        "autocontrast-c0",
        "binary-224",
    }
    assert {record.psm for record in winners} == {6, 11}


def test_project_authored_floor_scan_real_tesseract_stays_below_reference_ceiling() -> None:
    with _project_authored_agreement_floor_scan() as image:
        records = _real_tesseract_records(image)

    assert _qualifying_cliques(records) == []
    cross_transform_agreements = [
        _oracle_agreement(left.tokens, right.tokens)
        for left, right in combinations(records, 2)
        if left.quality_admitted
        and right.quality_admitted
        and left.rotation_degrees == right.rotation_degrees
        and left.transform_id != right.transform_id
    ]
    assert cross_transform_agreements
    assert max(cross_transform_agreements) <= 0.25


def test_primary_nonempty_bypasses_rescue_and_keeps_standard_locator() -> None:
    calls: list[dict[str, object]] = []

    def image_to_data(_image: Image.Image, **kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return _text_payload(_FICTIONAL_PHRASE)

    with Image.new("L", (20, 20), color=255) as image:
        worker_page = _page_result(image=image, image_to_data=image_to_data)

    assert len(calls) == 1
    assert "config" not in calls[0]
    assert "rescue_provenance" not in worker_page
    pages = ocr._pages_from_worker_payload({"status": "success", "pages": [worker_page]})
    parsed = pdf_parser._parse_ocr_pages(
        page_count=1,
        max_pages=1,
        pages=pages,
        required_page_numbers={1},
        native_spans=[],
        native_text_length=0,
        native_text_page_count=0,
        blank_page_count=0,
    )
    assert parsed.success is True
    assert [span.locator for span in parsed.spans] == [{"page": 1, "line": 1, "source": "ocr"}]
    assert parsed.metadata.get("ocr_rescue_page_count", 0) == 0


@pytest.mark.parametrize(
    ("confidences", "expected_admitted"),
    [
        ((0.60, 0.60, 0.60, 0.60), True),
        ((0.50, 0.55, 0.55, 0.80), True),
        ((0.49, 0.55, 0.70, 0.90), True),
        ((0.599999, 0.599999, 0.599999, 0.599999), False),
        ((0.40, 0.54, 0.54, 1.0), False),
        ((0.49, 0.49, 0.92, 0.92), False),
    ],
)
def test_candidate_quality_confidence_boundaries(
    confidences: tuple[float, ...],
    expected_admitted: bool,
) -> None:
    # A pure factory seam is intentional: exact numerical RED boundaries must not depend
    # on a locally installed Tesseract version.
    make_candidate = _required_callable("_make_ocr_rescue_candidate")
    candidate = make_candidate(
        matrix_ordinal=0,
        rotation_degrees=0,
        transform_id="autocontrast-c0",
        psm=6,
        text="fictional evidence alpha beta",
        word_confidences=confidences,
    )
    assert candidate.quality_admitted is expected_admitted


@pytest.mark.parametrize(
    ("text", "confidences"),
    [
        ("x" * 65_537, (0.9,)),
        (" ".join("x" for _index in range(4_097)), tuple(0.9 for _index in range(4_097))),
        ("fictional evidence alpha beta", (0.9, 0.9, float("nan"), 0.9)),
        ("fictional evidence alpha beta", (0.9, 0.9, 1.01, 0.9)),
    ],
    ids=("character-limit", "token-limit", "nonfinite-confidence", "range-confidence"),
)
def test_oversize_or_invalid_confidence_is_fatal_not_a_completed_rejection(
    text: str,
    confidences: tuple[float, ...],
) -> None:
    make_candidate = _required_callable("_make_ocr_rescue_candidate")
    with pytest.raises(ocr.OcrError):
        make_candidate(
            matrix_ordinal=0,
            rotation_degrees=0,
            transform_id="autocontrast-c0",
            psm=6,
            text=text,
            word_confidences=confidences,
        )


def test_agreement_formula_unrounded_boundary_and_half_even_persistence() -> None:
    agreement = _required_callable("_ocr_rescue_agreement")
    is_edge = _required_callable("_is_ocr_rescue_agreement_edge")
    rounded = _required_callable("_round_ocr_rescue_agreement")

    assert agreement("alpha beta gamma delta", "alpha beta gamma delta") == 1.0
    assert agreement("a b c d e f", "a b c d e x") == pytest.approx(0.8)
    assert agreement("a b c d", "w x y z") == 0.0
    assert is_edge(0.799999) is False
    assert is_edge(0.800000) is True
    assert rounded(Decimal("0.8000005")) == Decimal("0.800000")
    assert rounded(Decimal("0.8000015")) == Decimal("0.800002")


@pytest.mark.parametrize(
    "nodes",
    [
        [
            _node(0, rotation=0, transform="autocontrast-c0", psm=6),
            _node(3, rotation=0, transform="binary-192", psm=11),
        ],
        [
            _node(0, rotation=0, transform="autocontrast-c0", psm=6),
            _node(1, rotation=0, transform="autocontrast-c0", psm=11),
            _node(2, rotation=0, transform="autocontrast-c0", psm=6),
        ],
        [
            _node(0, rotation=0, transform="autocontrast-c0", psm=6),
            _node(2, rotation=0, transform="binary-192", psm=6),
            _node(4, rotation=0, transform="binary-224", psm=6),
        ],
        [
            _node(0, rotation=0, transform="autocontrast-c0", psm=6),
            _node(1, rotation=90, transform="autocontrast-c0", psm=11),
            _node(2, rotation=0, transform="binary-192", psm=6),
        ],
    ],
    ids=("two-supporters", "one-transform", "one-psm", "mixed-rotation"),
)
def test_consensus_requires_three_same_rotation_cross_transform_both_psm_candidates(
    nodes: list[SimpleNamespace],
) -> None:
    select_consensus = _required_callable("_select_ocr_rescue_consensus")
    no_consensus = _required_error_type("OcrRescueNoConsensusError")
    edges = {
        tuple(sorted((left.matrix_ordinal, right.matrix_ordinal))): 0.95
        for left, right in combinations(nodes, 2)
    }
    with pytest.raises(no_consensus):
        select_consensus(nodes, pairwise_agreements=_pairwise_matrix(nodes, edges=edges))


@pytest.mark.parametrize("overlapping", [False, True], ids=("disjoint", "overlapping"))
def test_consensus_rejects_a_second_qualifying_near_tie(overlapping: bool) -> None:
    select_consensus = _required_callable("_select_ocr_rescue_consensus")
    no_consensus = _required_error_type("OcrRescueNoConsensusError")
    first = [
        _node(0, rotation=0, transform="autocontrast-c0", psm=6),
        _node(1, rotation=0, transform="autocontrast-c0", psm=11),
        _node(2, rotation=0, transform="binary-192", psm=6),
    ]
    if overlapping:
        second = [first[0], first[1], _node(4, rotation=0, transform="binary-224", psm=6)]
    else:
        second = [
            _node(6, rotation=90, transform="autocontrast-c0", psm=6),
            _node(7, rotation=90, transform="autocontrast-c0", psm=11),
            _node(8, rotation=90, transform="binary-192", psm=6),
        ]
    nodes = list({node.matrix_ordinal: node for node in [*first, *second]}.values())
    edges: dict[tuple[int, int], float] = {}
    for clique in (first, second):
        for left, right in combinations(clique, 2):
            edges[tuple(sorted((left.matrix_ordinal, right.matrix_ordinal)))] = 0.95

    with pytest.raises(no_consensus):
        select_consensus(nodes, pairwise_agreements=_pairwise_matrix(nodes, edges=edges))


def test_unique_clique_selects_one_real_candidate_deterministically() -> None:
    select_consensus = _required_callable("_select_ocr_rescue_consensus")
    nodes = [
        _node(0, rotation=0, transform="autocontrast-c0", psm=6),
        _node(1, rotation=0, transform="autocontrast-c0", psm=11),
        _node(2, rotation=0, transform="binary-192", psm=6),
        _node(4, rotation=0, transform="binary-224", psm=6),
    ]
    edges = {(0, 1): 0.90, (0, 2): 0.90, (1, 2): 0.85}
    decision = select_consensus(
        nodes,
        pairwise_agreements=_pairwise_matrix(nodes, edges=edges),
    )

    assert [node.matrix_ordinal for node in decision.supporting_candidates] == [0, 1, 2]
    assert decision.winner is nodes[0]
    assert decision.minimum_clique_agreement == pytest.approx(0.85)
    assert decision.mean_clique_agreement == pytest.approx((0.90 + 0.90 + 0.85) / 3)

    shuffled = list(reversed(nodes))
    repeated = select_consensus(
        shuffled,
        pairwise_agreements=_pairwise_matrix(shuffled, edges=edges),
    )
    assert repeated.winner.matrix_ordinal == 0
    assert [node.matrix_ordinal for node in repeated.supporting_candidates] == [0, 1, 2]


def test_rescued_page_emits_exact_immutable_provenance_and_rescue_locator() -> None:
    call_index = 0

    def image_to_data(_image: Image.Image, **_kwargs: object) -> dict[str, object]:
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            return _empty_tesseract_payload()
        candidate_ordinal = call_index - 2
        if candidate_ordinal in {0, 1, 2}:
            return _text_payload(_FICTIONAL_PHRASE)
        return _empty_tesseract_payload()

    with Image.new("L", (200, 80), color=245) as image:
        worker_page = _page_result(image=image, image_to_data=image_to_data)

    assert call_index == 25
    assert worker_page["text"] == _FICTIONAL_PHRASE
    provenance = _provenance_mapping(worker_page.get("rescue_provenance"))
    assert set(provenance) == _RESCUE_PROVENANCE_KEYS
    assert provenance["policy_version"] == _POLICY_VERSION
    assert provenance["winner_rotation_degrees"] == 0
    assert provenance["winner_transform_id"] == "autocontrast-c0"
    assert provenance["winner_psm"] == 6
    assert provenance["winner_word_count"] == 5
    assert provenance["attempted_candidate_count"] == 24
    assert provenance["supporting_candidate_count"] == 3
    assert provenance["minimum_clique_agreement"] == 1.0
    assert provenance["mean_clique_agreement"] == 1.0
    assert provenance["winner_mean_confidence"] == 0.95
    assert provenance["winner_median_confidence"] == 0.95
    assert provenance["winner_minimum_confidence"] == 0.95
    assert provenance["winner_maximum_confidence"] == 0.95
    assert provenance["winner_fraction_at_or_above_0_50"] == 1.0
    histogram = provenance["winner_confidence_histogram_counts"]
    assert isinstance(histogram, dict)
    assert all(isinstance(value, int) and value >= 0 for value in histogram.values())
    assert sum(histogram.values()) == 5
    for key in _SHA256_KEYS:
        value = provenance[key]
        assert isinstance(value, str) and len(value) == 64
        assert value == value.lower()
        int(value, 16)
    assert (
        provenance["normalized_page_text_sha256"]
        == hashlib.sha256(_FICTIONAL_PHRASE.encode("utf-8")).hexdigest()
    )

    pages = ocr._pages_from_worker_payload({"status": "success", "pages": [worker_page]})
    assert _provenance_mapping(pages[0].rescue_provenance) == provenance
    parsed = pdf_parser._parse_ocr_pages(
        page_count=1,
        max_pages=1,
        pages=pages,
        required_page_numbers={1},
        native_spans=[],
        native_text_length=0,
        native_text_page_count=0,
        blank_page_count=0,
    )
    assert parsed.success is True
    assert len(parsed.spans) == 1
    assert parsed.spans[0].locator == {
        "page": 1,
        "line": 1,
        "source": "ocr_rescue",
        **provenance,
    }
    assert parsed.metadata["ocr_rescue_page_count"] == 1
    assert parsed.metadata["ocr_rescue_span_count"] == 1
    receipt_sha256 = parsed.metadata["ocr_rescue_receipt_sha256"]
    assert isinstance(receipt_sha256, str) and len(receipt_sha256) == 64
    encoded = json.dumps(parsed.metadata, sort_keys=True)
    assert "candidate text" not in encoded
    assert "private path" not in encoded


def test_rescue_candidate_timeout_returns_no_partial_worker_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "fictional-timeout.pdf"
    source_path.write_bytes(b"fictional-pdf")
    calls = 0
    observed_timeouts: list[float] = []

    def convert_from_path(_path: str, **_kwargs: object) -> list[Image.Image]:
        return [Image.new("L", (32, 32), color=255)]

    def image_to_data(_image: Image.Image, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raw_timeout = kwargs["timeout"]
        assert isinstance(raw_timeout, (int, float))
        observed_timeouts.append(float(raw_timeout))
        if calls == 5:
            raise RuntimeError("Tesseract process timeout")
        return _empty_tesseract_payload()

    _install_fake_file_runtime(
        monkeypatch,
        convert_from_path=convert_from_path,
        image_to_data=image_to_data,
    )
    queue = _CaptureQueue()
    deadline = time.monotonic() + 2.0

    ocr._tesseract_pdf_file_ocr_worker(
        source_path,
        1,
        200,
        "eng",
        deadline,
        queue,
    )

    assert calls == 5
    assert queue.items == [{"status": "timeout"}]
    assert all(timeout > 0 for timeout in observed_timeouts)
    assert all(
        later <= earlier
        for earlier, later in zip(observed_timeouts, observed_timeouts[1:], strict=False)
    )
    assert time.monotonic() < deadline


def test_reference_low_agreement_range_cannot_form_a_rescue_clique() -> None:
    select_consensus = _required_callable("_select_ocr_rescue_consensus")
    no_consensus = _required_error_type("OcrRescueNoConsensusError")
    nodes = [
        _node(0, rotation=0, transform="autocontrast-c0", psm=6),
        _node(1, rotation=0, transform="autocontrast-c0", psm=11),
        _node(2, rotation=0, transform="binary-192", psm=6),
        _node(3, rotation=0, transform="binary-192", psm=11),
    ]
    safe_reference_values = (0.077, 0.10, 0.15, 0.20, 0.22, 0.25)
    edges = {
        tuple(sorted((left.matrix_ordinal, right.matrix_ordinal))): value
        for (left, right), value in zip(
            combinations(nodes, 2),
            safe_reference_values,
            strict=True,
        )
    }

    with pytest.raises(no_consensus):
        select_consensus(nodes, pairwise_agreements=_pairwise_matrix(nodes, edges=edges))

    assert max(edges.values()) == 0.25
    assert all(not math.isclose(value, 0.8) and value < 0.8 for value in edges.values())
