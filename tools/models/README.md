# Worker venv for local OCR and speech-to-text

`mediatext.py` (repo root) reads collected photos and videos with Apple Vision (OCR) and mlx-whisper
(speech). It runs in its own venv so the app's `.venv` stays small and free of torch/MLX. The app shells out to

    tools/models/.venv/bin/python mediatext.py --list files.json -o <run folder> --derived <workspace>/derived

exactly like `mediascan.py`, as a local-lane job. It never touches the network.

## Setup (once)

    brew install ffmpeg
    python3.14 -m venv tools/models/.venv        # mlx 0.32.2 ships wheels for this Python on Apple silicon
    tools/models/.venv/bin/pip install -r tools/models/requirements.txt
    # exact resolved set: requirements.lock (pip install -r requirements.lock reproduces it)

Apple silicon macOS only. About 1.3 GB on disk (torch is a dependency of mlx-whisper).

## Whisper model: offline only

The worker sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` and resolves the model from the local
Hugging Face cache (`local_files_only`). It never downloads. If `mlx-community/whisper-large-v3-turbo` is missing
the run stops with a message. Fetch it once by hand (about 1.6 GB; the only step that uses the network):

    tools/models/.venv/bin/hf download mlx-community/whisper-large-v3-turbo

## OCR languages: what the bake-off showed

Apple Vision supports ar-SA, ars-SA, ru-RU, uk-UA, en-US (and more) here. With a list of languages, only the
FIRST one really drives recognition: `["ar-SA","ru-RU","uk-UA","en-US"]` gave byte-identical output to
`["ar-SA"]`, so Cyrillic came out as Latin look-alikes ("ToBOpuT flervoH" for "Говорит Легион"); listing Cyrillic first
destroys Arabic. Vision's automatic detection (`["auto"]`) reads Arabic, Cyrillic and Latin, even mixed in one picture,
at the same speed, so it is the default. Per-channel overrides (Settings) are available.

## Memory (8 GB Mac)

OCR hands Vision a CFData-backed bitmap (the plain data provider leaked every frame and
kernel-panicked an 8 GB Mac); `memory_guard()` restarts the worker above 1.5 GB (OCR) / 5 GB (speech) RSS and holds
while macOS reports under 5% free memory until 10%. MLX's cache is capped at 256 MB and cleared after every file.
Run one worker at a time (the local lane already does).
