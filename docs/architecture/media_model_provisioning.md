# Media Model Provisioning

## Scope
Slice 40 keeps MP4 transcription private-gate-only and opt-in. It does not bundle,
commit, or download model files during normal CI.

## Supported Model Strategies
Preferred production strategy:
- provision a faster-whisper/CTranslate2 model directory outside the repository
- set `IDIS_MEDIA_STT_MODEL_PATH` to that local directory
- run the private gate with `--media-adapter faster-whisper --media-model-path <path>`

Optional explicit download/cache strategy:
- set `IDIS_MEDIA_STT_MODEL_NAME` to the approved model name
- set `IDIS_MEDIA_STT_ALLOW_DOWNLOAD=1`
- pass `--media-allow-model-download`
- use a pre-approved cache location outside the repository

normal CI must not download a Whisper model. CI may verify dependency imports, ffmpeg
provisioning, unavailable/failure/timeout handling, and injected-worker success paths.

## Safety Rules
- Do not commit model files.
- Do not place model caches under tracked repository paths.
- Do not print model paths in gate summaries.
- Do not print MP4 filenames, paths, frames, thumbnails, transcript text, or private content.
- Keep public upload and global parser dispatch unchanged; MP4 remains rejected outside the
  private gate.

## Private Gate Example
Use a pre-provisioned local model directory:

```powershell
$env:IDIS_MEDIA_STT_MODEL_PATH = "C:\Models\faster-whisper\tiny.en"
python scripts/run_real_example_gate.py --parse-supported --safe-summary `
  --ocr-enabled --media-enabled --media-adapter faster-whisper `
  --media-model-path $env:IDIS_MEDIA_STT_MODEL_PATH --no-progress
```

Use an explicitly approved named model download/cache:

```powershell
$env:IDIS_MEDIA_STT_MODEL_NAME = "tiny.en"
$env:IDIS_MEDIA_STT_ALLOW_DOWNLOAD = "1"
python scripts/run_real_example_gate.py --parse-supported --safe-summary `
  --media-enabled --media-adapter faster-whisper --media-model-name tiny.en `
  --media-allow-model-download --no-progress
```

If the local model path is missing or invalid, the private gate classifies MP4 media as
`media_transcription_unavailable` without reading MP4 bodies in the preflight path.
