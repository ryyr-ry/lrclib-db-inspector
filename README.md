# lrclib-db-inspector

Inspect LRCLIB database dump: schema, record count, field sizes, lyricsfile coverage, and estimate chunk count for the edge search API.

## Usage

Trigger the `Inspect LRCLIB DB Dump` workflow manually from the Actions tab.

Uses `fallocate --punch-hole` to reclaim disk space during decompression, allowing the ~42 GiB compressed database to be decompressed on a standard 84 GB GitHub Actions runner without running out of disk space.
