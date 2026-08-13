# lrclib-db-inspector

Inspect LRCLIB database dump: schema, record count, field sizes, lyricsfile coverage, and estimate chunk count for the edge search API.

## Usage

Trigger the `Inspect LRCLIB DB Dump` workflow manually from the Actions tab.

The workflow downloads the ~42 GiB compressed SQLite dump, decompresses it, and inspects:
- Full schema (tables, indexes, triggers, FTS)
- Record counts (tracks, lyrics, lyricsfile coverage)
- Field sizes (plain_lyrics, synced_lyrics, lyricsfile)
- Estimated JSON export size and chunk count for the edge API

Results are printed to the workflow log and uploaded as an artifact.
