# Media Transcription Provisioning Decision

## Status
Decision recorded in Slice 38. Real MP4 transcription remains deferred until the media
runtime is explicitly approved and provisioned.

## Current Aggregate Blockers
- `.mp4|media_transcription_unavailable: 5`
- `.mp4|file_too_large: 3`
- `.pdf|ocr_no_text_extracted: 2`

These are aggregate-only counts from the private real-example gate. No MP4 filenames,
paths, thumbnails, frame data, transcript text, or content are required to make this
decision.

## Decision
Do not implement real MP4 transcription in Slice 38. Keep the current private-gate media
boundary honest: MP4 files are either explicitly blocked as
`media_transcription_unavailable` or deferred as `file_too_large`.

## Why Slice 38 Does Not Implement Real Transcription
- local ffmpeg/ffprobe unavailable
- Docker/CI do not provision media dependencies
- STT engine/model/runtime decision is not made
- Whisper/model provisioning is larger than a safe slice

Adding only `ffmpeg` would solve container demuxing but not speech-to-text. Adding an STT
engine or model also changes runtime footprint, privacy posture, CI setup, deployment
artifacts, and operational ownership. That decision needs its own approved provisioning
slice before any adapter can honestly claim parsed MP4 success.

## Approved Future Options
- local ffmpeg + faster-whisper/whisper.cpp
- cloud STT provider with BYOK/privacy constraints
- human-supplied transcripts as first-class documents

## Required Production Constraints
- opt-in private gate first
- no public upload expansion until approved
- bounded file size/duration/runtime
- no raw transcript leakage in logs/gate summaries
- tenant isolation and audit artifacts
- deterministic provenance from media segment to claim/evidence

## Exact Next Implementation Slice Recommendation
Next implementation slice: media transcription provisioning implementation, after choosing runtime.

The next slice should begin read-only and aggregate-only from the current `origin/main`.
It should choose one runtime path, then add RED tests for dependency probes, timeout
handling, process cleanup, safe unavailable/failure classifications, safe TIMECODE spans,
ledger policy invalidation, and aggregate-only private gate output before touching
production code.
