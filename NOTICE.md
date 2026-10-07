# Third-party notices

Telegram Investigator bundles or builds on the following. Their licences apply to those parts.

## Data

**`investigator/data/gazetteer/dictionary.db`** is a derivative database. It is built by `investigator/gazetteer_build.py` from:

- **OpenStreetMap** data, © OpenStreetMap contributors, available under the
  [Open Database License 1.0 (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).
  See <https://www.openstreetmap.org/copyright>. The extracts came from Geofabrik (download.geofabrik.de).
  *Share-alike:* anyone who distributes `dictionary.db`, or another database derived from it,
  must offer that database under the ODbL, keep this attribution, and not add technical restrictions.
  The build script is in this repository. The raw inputs (about 1.1 GB) are not: the download sources are listed in `investigator/data/gazetteer/README.md`.
- **GeoNames** gazetteer data (<https://www.geonames.org>), licensed under
  [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
- **Curated place lists and lexicons** in `investigator/data/gazetteer/curated/` and `investigator/data/lexicon/`,
  written for this project. They carry the same licence as the project.

The exact input files, their SHA-256 hashes and the build date are recorded in
`investigator/data/gazetteer/README.md` and in the database's `meta` table.

**Lexicons and curated places are not included in this repository.** Put your own in `investigator/data/lexicon/`
and `investigator/data/gazetteer/curated/` and rebuild `dictionary.db` (see `investigator/data/gazetteer/BUILD.md`).
A lexicon may deliberately include slurs or propaganda vocabulary so investigators can find that language; mark
such terms in the entry's `notes`.

## Software shipped in the built UI (`investigator/web/assets`)

- **Svelte**: MIT License, © 2016–present Svelte contributors. <https://github.com/sveltejs/svelte>
- **d3-force** (and its d3 dependencies d3-dispatch, d3-quadtree, d3-timer): ISC License, © 2010–2021 Mike Bostock.
  <https://github.com/d3/d3-force>

## Python dependencies (installed by the user, not bundled)

- **App** (`requirements.txt`):
  - FastAPI: MIT
  - Uvicorn: BSD-3-Clause
  - Telethon: MIT
  - cryptg: CC0 1.0
- **OCR/speech worker** (`tools/models/requirements.txt`):
  - MLX: MIT
  - mlx-whisper: MIT
  - ocrmac: MIT
  - Pillow: MIT-CMU
  - NumPy: BSD-3-Clause
  - PyObjC: MIT
  - PyTorch: BSD-3-Clause
- **Model:** the Whisper model weights (`mlx-community/whisper-large-v3-turbo`, converted from OpenAI Whisper) are MIT-licensed.
  They are downloaded by the user and not bundled.
