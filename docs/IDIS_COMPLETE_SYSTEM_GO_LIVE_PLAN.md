# IDIS Complete System Go-Live Plan

## Current Main Baseline
After Slice 37, `origin/main` is:

`481e549def5cb9ef42469e7980c99d06f8968cec`

## Completed Real-Example Gate Slices
- Slice 29 completed: private real-example gate harness.
- Slice 30 completed: encrypted PDF handling and safe classification.
- Slice 31 completed: no-text PDF classification as OCR-required.
- Slice 32 completed: OCR adapter interface and controlled execution boundary.
- Slice 33 completed: real Tesseract OCR adapter.
- Slice 34 completed: image OCR handling.
- Slice 35 completed: HTML/TXT parsing for private gate coverage.
- Slice 36 completed: residual PDF OCR completion/readiness.
- Slice 37 completed: private media readiness boundary.

## Current Aggregate Real-Example Blockers
- `.mp4|media_transcription_unavailable: 5`
- `.mp4|file_too_large: 3`
- `.pdf|ocr_no_text_extracted: 2`

## Slice 38 Decision
Slice 38 decision: real MP4 transcription deferred pending media/STT provisioning decision.

Reasoning:
- local ffmpeg/ffprobe unavailable
- Docker/CI do not provision media dependencies
- STT engine/model/runtime decision is not made
- Whisper/model provisioning is larger than a safe slice

Slice 38 therefore remains a documentation, planning, and control slice. It does not add
ffmpeg, Whisper, cloud STT APIs, real transcription, public upload expansion, or global
parser registry dispatch.

## Next Planned Slice
Next planned slice: media transcription provisioning implementation, after choosing runtime.

The next slice should start from exact `origin/main`, run read-only aggregate diagnostics,
choose one media/STT runtime, and only then add an opt-in private-gate implementation with
bounded file size, duration, runtime, tenant isolation, audit artifacts, and deterministic
media-segment provenance.
