#!/usr/bin/env python3
"""Validate or explicitly bootstrap a local faster-whisper model directory."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from idis.tools.media_model_bootstrap import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
