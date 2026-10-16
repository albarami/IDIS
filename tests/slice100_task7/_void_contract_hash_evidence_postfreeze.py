"""Void identifiers admitted to the blocklist after the signed v16 census.

The pre-freeze evidence module is byte-pinned by the signed census receipt and
must remain immutable.  This append-only companion records documentary
predecessors discovered during the post-freeze lifecycle audit.
"""

from __future__ import annotations

from tests.slice100_task7._void_contract_hash_evidence import VOID_CONTRACT_HASHES

RAW_LITERAL = "RAW_LITERAL"
FILE_SHA256 = "FILE_SHA256"

VOID_V5_DIAGNOSTIC_ARTIFACT_SHA256 = (
    "2bd17c8ba1558409f3deeec4ab1387e7b14d543adfd4091fba259e4eab12f147"
)
VOID_V5_DIAGNOSTIC_FIXTURE_SHA256 = (
    "16c6194f7483dfb1ff64a7d4c042cf2df652b4adf379f09305e108f607c785db"
)
VOID_V5_DIAGNOSTIC_CONTRACT_SHA256 = (
    "704409435701c1919727c76ed2889116c12702ae3c34922d21f35636c0b7d9e1"
)
VOID_V19_CANDIDATE_ARTIFACT_SHA256 = (
    "21bb93882a2d316bbf899cdae282c231b99a1aeea2239c2966954bfa7f4789c4"
)
VOID_V19_PROSPECTIVE_FROZEN_ARTIFACT_SHA256 = (
    "91a6d99322d7c2dd8fba862db9d4fe858e89f933cc22c1e126fdb9c5ec1dd76b"
)
VOID_V19_PROSPECTIVE_CONTRACT_SHA256 = (
    "47945b910257381adbf0b895d6596730762828592041c7bb1c4086bb5b25035b"
)

POSTFREEZE_VOID_CONTRACT_HASHES = (
    VOID_V5_DIAGNOSTIC_ARTIFACT_SHA256,
    VOID_V5_DIAGNOSTIC_FIXTURE_SHA256,
    VOID_V5_DIAGNOSTIC_CONTRACT_SHA256,
    VOID_V19_CANDIDATE_ARTIFACT_SHA256,
    VOID_V19_PROSPECTIVE_FROZEN_ARTIFACT_SHA256,
    VOID_V19_PROSPECTIVE_CONTRACT_SHA256,
)
POSTFREEZE_VOID_HASHES = tuple(value.encode("ascii") for value in POSTFREEZE_VOID_CONTRACT_HASHES)

POSTFREEZE_VOID_HASH_EVIDENCE_MATRIX = (
    (
        VOID_V5_DIAGNOSTIC_ARTIFACT_SHA256,
        "tests/slice100_task7/_void_contract_hash_evidence_postfreeze.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V5_DIAGNOSTIC_FIXTURE_SHA256,
        "tests/slice100_task7/_void_contract_hash_evidence_postfreeze.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V5_DIAGNOSTIC_CONTRACT_SHA256,
        "tests/slice100_task7/_void_contract_hash_evidence_postfreeze.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "tests/slice100_task7/_void_contract_hash_evidence_postfreeze.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_PROSPECTIVE_FROZEN_ARTIFACT_SHA256,
        "tests/slice100_task7/_void_contract_hash_evidence_postfreeze.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_PROSPECTIVE_CONTRACT_SHA256,
        "tests/slice100_task7/_void_contract_hash_evidence_postfreeze.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V5_DIAGNOSTIC_ARTIFACT_SHA256,
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V5_DIAGNOSTIC_FIXTURE_SHA256,
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V5_DIAGNOSTIC_CONTRACT_SHA256,
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        RAW_LITERAL,
        2,
    ),
    (
        VOID_V19_PROSPECTIVE_FROZEN_ARTIFACT_SHA256,
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_PROSPECTIVE_CONTRACT_SHA256,
        "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V5_DIAGNOSTIC_ARTIFACT_SHA256,
        "docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V5_DIAGNOSTIC_FIXTURE_SHA256,
        "docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V5_DIAGNOSTIC_CONTRACT_SHA256,
        "docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md",
        RAW_LITERAL,
        2,
    ),
    (
        VOID_V19_PROSPECTIVE_FROZEN_ARTIFACT_SHA256,
        "docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_PROSPECTIVE_CONTRACT_SHA256,
        "docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "scripts/authorize_task7_v19_contract_publication.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "scripts/generate_task7_v19_contract_fixture.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "scripts/run_task7_v19_current_guard_census.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "scripts/run_task7_v19_signed_census.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "tests/slice100_task7/test_authorize_task7_v19_contract_publication_red.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "tests/slice100_task7/test_migration_0038_v19_authority_red.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "tests/slice100_task7/test_run_task7_v19_census_red.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "tests/slice100_task7/test_run_task7_v19_signed_census.py",
        RAW_LITERAL,
        1,
    ),
    (
        VOID_V19_CANDIDATE_ARTIFACT_SHA256,
        "contracts/full_strict_v19_reviewed_preimage_v1.json",
        FILE_SHA256,
        1,
    ),
)

_MASTER_PLAN_BASE_COUNTS = (
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    2,
    2,
    2,
    2,
    2,
    1,
    1,
    1,
    1,
    1,
    1,
    2,
    1,
    1,
    2,
    1,
    1,
    1,
    1,
    1,
)
_DESIGN_PLAN_BASE_COUNTS = (
    1,
    1,
    2,
    2,
    2,
    2,
    2,
    2,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    2,
    2,
    1,
    0,
    0,
    0,
    0,
    0,
    1,
    1,
    1,
    1,
    1,
    1,
    2,
    1,
    1,
    1,
    1,
    1,
)
_BASE_ARTIFACT_FILE_DIGEST_PATHS = (
    (VOID_CONTRACT_HASHES[0], "contracts/full_strict_v2_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[2], "contracts/full_strict_v3_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[5], "contracts/full_strict_v4_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[8], "contracts/full_strict_v5_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[11], "contracts/full_strict_v6_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[14], "contracts/full_strict_v7_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[17], "contracts/full_strict_v8_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[18], "contracts/full_strict_v9_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[19], "contracts/full_strict_v10_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[20], "contracts/full_strict_v11_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[26], "contracts/full_strict_v12_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[29], "contracts/full_strict_v13_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[32], "contracts/full_strict_v14_reviewed_preimage_v1.json"),
    (VOID_CONTRACT_HASHES[35], "contracts/full_strict_v15_reviewed_preimage_v1.json"),
)

BASE_VOID_HASH_EVIDENCE_MATRIX = (
    *(
        (
            hash_value,
            "tests/slice100_task7/_void_contract_hash_evidence.py",
            RAW_LITERAL,
            1,
        )
        for hash_value in VOID_CONTRACT_HASHES
    ),
    *(
        (
            hash_value,
            "docs/plans/2026-07-15-slice100-strict-real-example-live-dry-run.md",
            RAW_LITERAL,
            expected_count,
        )
        for hash_value, expected_count in zip(
            VOID_CONTRACT_HASHES,
            _MASTER_PLAN_BASE_COUNTS,
            strict=True,
        )
        if expected_count
    ),
    *(
        (
            hash_value,
            "docs/plans/2026-07-21-slice100-task7-receipt-edge-schema-design.md",
            RAW_LITERAL,
            expected_count,
        )
        for hash_value, expected_count in zip(
            VOID_CONTRACT_HASHES,
            _DESIGN_PLAN_BASE_COUNTS,
            strict=True,
        )
        if expected_count
    ),
    (
        VOID_CONTRACT_HASHES[10],
        "tests/slice100_task7/_full_strict_v6_usage_authority.py",
        RAW_LITERAL,
        1,
    ),
    *((hash_value, path, FILE_SHA256, 1) for hash_value, path in _BASE_ARTIFACT_FILE_DIGEST_PATHS),
)

VOID_HASH_EVIDENCE_MATRIX = (
    *BASE_VOID_HASH_EVIDENCE_MATRIX,
    *POSTFREEZE_VOID_HASH_EVIDENCE_MATRIX,
)
