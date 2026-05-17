# Media Model Provisioning

## Scope
Slice 40 keeps MP4 transcription private-gate-only and opt-in. Slice 41 adds local
operational bootstrap/probe tooling. Neither slice bundles, commits, or downloads model
files during normal CI.

## Supported Model Strategies
Preferred production strategy:
- provision a faster-whisper/CTranslate2 model directory outside the repository
- set `IDIS_MEDIA_STT_MODEL_PATH` to that local directory
- run the private gate with `--media-adapter faster-whisper --media-model-path <path>`
- validate the directory first with `scripts/bootstrap_faster_whisper_model.py`

Optional explicit download/cache strategy:
- set `IDIS_MEDIA_STT_MODEL_NAME` to the approved model name
- set `IDIS_MEDIA_STT_ALLOW_DOWNLOAD=1`
- pass `--media-allow-model-download`
- use a pre-approved cache location outside the repository
- pass `--allow-download` to the bootstrap command; without that flag it must not download

normal CI must not download a Whisper model. CI may verify dependency imports, ffmpeg
provisioning, unavailable/failure/timeout handling, and injected-worker success paths.

## Safety Rules
- Do not commit model files.
- Do not place model caches under tracked repository paths. The repository ignores
  `.local_models/`, `.local_media_models/`, `models/`, `var/media-models/`, and
  `.cache/faster-whisper/` for local-only operator use.
- Do not print model paths in gate summaries.
- Do not print MP4 filenames, paths, frames, thumbnails, transcript text, or private content.
- Keep public upload and global parser dispatch unchanged; MP4 remains rejected outside the
  private gate.

## Local Bootstrap And Probe
Validate an already-provisioned model directory without printing the path:

```powershell
python scripts/bootstrap_faster_whisper_model.py `
  --model-path $env:IDIS_MEDIA_STT_MODEL_PATH
```

If no local model is configured, the operational blocker is
`LOCAL_STT_MODEL_NOT_PROVISIONED`. Do not download automatically.

Bootstrap a named model only after explicit approval:

```powershell
python scripts/bootstrap_faster_whisper_model.py `
  --model-name tiny.en `
  --output-dir .local_media_models/tiny.en `
  --allow-download
```

The command returns path-free JSON containing only safe status fields such as
`LOCAL_MODEL_READY` or `LOCAL_STT_MODEL_NOT_PROVISIONED`.

When no CLI source is provided, the command reads `IDIS_MEDIA_STT_MODEL_PATH` and
`IDIS_MEDIA_STT_MODEL_NAME` from the environment. If neither is configured, it returns the
same `LOCAL_STT_MODEL_NOT_PROVISIONED` blocker. Explicit downloads inside the repository
must target one of the ignored local model/cache directories listed above. The bootstrap
command does not treat `IDIS_MEDIA_STT_ALLOW_DOWNLOAD=1` as a substitute for the
`--allow-download` CLI flag.

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
