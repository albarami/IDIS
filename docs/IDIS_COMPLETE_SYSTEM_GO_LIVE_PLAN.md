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

## Slice 39 And 40 Media Runtime Status
Slice 39 completed the opt-in private-gate `faster-whisper` runtime boundary. It added
ffmpeg provisioning, the Python runtime dependency, structured media adapter outcomes, and
kept public upload/global parser dispatch unchanged.

Slice 40 model provisioning policy:
- preferred: provide a local faster-whisper model directory with `IDIS_MEDIA_STT_MODEL_PATH`
- optional: allow named-model download/cache only with `IDIS_MEDIA_STT_ALLOW_DOWNLOAD=1`
- normal CI must not download a Whisper model
- private gate summaries must remain aggregate-only and must not print model paths,
  filenames, transcripts, frames, thumbnails, or private content

Until a valid local model path or explicit download/cache policy is configured, the
remaining MP4s are expected to stay `media_transcription_unavailable`.
