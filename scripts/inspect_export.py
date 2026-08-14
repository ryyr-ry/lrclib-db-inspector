#!/usr/bin/env python3
"""
Inspect LRCLIB SQLite DB through RapidgzipFile + APSW custom VFS.
No FUSE, no ratarmount, no disk decompression.
SQLite reads pages through a custom VFS backed by RapidgzipFile.
"""

import os
import sys
import json
import time
import struct
import apsw
from rapidgzip import RapidgzipFile

GZIP_PATH = sys.argv[1]


class GzipVFSFile(apsw.VFSFile):
    """VFS file handle backed by a shared RapidgzipFile.

    Since we don't inherit from any real filesystem, we pass base=""
    to VFSFile but never call super().__init__ which would try to open
    a real file. Instead we override all needed methods directly.
    """

    def __init__(self, gz_file, db_size):
        # Don't call super().__init__ — that triggers xOpen recursion.
        # We set attributes manually and override all VFSFile methods.
        self._gz = gz_file
        self._size = db_size

    def xRead(self, offset, amount):
        self._gz.seek(offset)
        data = self._gz.read(amount)
        if len(data) < amount:
            data += b"\x00" * (amount - len(data))
        return data

    def xFilesize(self):
        return self._size

    def xClose(self):
        pass

    def xDeviceCharacteristics(self):
        return apsw.SQLITE_IOCAP_IMMUTABLE

    def xSectorSize(self):
        return 4096

    def xLock(self, level):
        pass

    def xUnlock(self, level):
        pass

    def xCheckReservedLock(self):
        return False

    def xSync(self, flags):
        return True

    def xTruncate(self, size):
        pass

    def xWrite(self, offset, data):
        pass

    def xFileControl(self, op, arg):
        return False

    def xShmMap(self, *a):
        raise apsw.IOError("Shared memory not supported")

    def xShmBarrier(self):
        pass

    def xShmUnmap(self):
        pass


class GzipVFS(apsw.VFS):
    """Custom VFS that reads a SQLite database from a gzip file."""

    def __init__(self, gz_file, db_size, name="gzipvfs"):
        self._gz = gz_file
        self._size = db_size
        self.vfs_name = name
        super().__init__(name, base="")

    def xOpen(self, name, flags):
        return GzipVFSFile(self._gz, self._size)

    def xDelete(self, name, syncdir):
        pass

    def xAccess(self, name, flags):
        return True

    def xFullPathname(self, name):
        return name

    def xSleep(self, us):
        time.sleep(us / 1e6)
        return True

    def xCurrentTime(self):
        return time.time() / 86400.0 + 2440587.5

    def xGetSystemError(self, e):
        return (e, "")

    def xDlError(self):
        return ""

    def xRandom(self, n):
        return os.urandom(n)


def main():
    print(f"Opening RapidgzipFile: {GZIP_PATH}", flush=True)
    t0 = time.time()
    gz = RapidgzipFile(GZIP_PATH, parallelization=os.cpu_count())

    # Read SQLite header (first 100 bytes) to get page_size and page_count
    header = gz.read(100)
    if header[:16] != b"SQLite format 3\x00":
        print(f"ERROR: Not a SQLite database. Header: {header[:16]}", flush=True)
        sys.exit(1)

    page_size = struct.unpack(">H", header[16:18])[0]
    if page_size == 1:
        page_size = 65536
    page_count = struct.unpack(">I", header[28:32])[0]
    db_size = page_size * page_count

    print(
        f"Opened in {time.time()-t0:.1f}s | "
        f"page_size={page_size} page_count={page_count} "
        f"db_size={db_size} ({db_size/1073741824:.2f} GiB)",
        flush=True,
    )

    gz.seek(0)

    # Register custom VFS and connect
    vfs = GzipVFS(gz, db_size)
    conn = apsw.Connection("dummy", vfs=vfs.vfs_name, flags=apsw.SQLITE_OPEN_READONLY)
    cur = conn.cursor()

    report = {}

    # --- 1. Schema (reads page 1 only — instant) ---
    print("\n=== TABLES ===", flush=True)
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print(json.dumps(tables), flush=True)
    report["tables"] = tables

    print("\n=== TRACKS SCHEMA ===", flush=True)
    tracks_schema = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tracks'"
    ).fetchone()[0]
    print(tracks_schema, flush=True)
    report["tracks_schema"] = tracks_schema

    print("\n=== LYRICS SCHEMA ===", flush=True)
    lyrics_schema = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='lyrics'"
    ).fetchone()[0]
    print(lyrics_schema, flush=True)
    report["lyrics_schema"] = lyrics_schema

    print("\n=== INDEXES ===", flush=True)
    indexes = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='index'")]
    print(json.dumps(indexes), flush=True)
    report["indexes"] = indexes

    # --- 2. Sample records (LIMIT 5 — reads a few pages) ---
    print("\n=== SAMPLE RECORDS ===", flush=True)
    samples = []
    for row in cur.execute(
        """
        SELECT t.id, t.name, t.artist_name, t.album_name, t.duration,
               t.last_lyrics_id,
               l.instrumental, l.has_lyricsfile, l.has_plain_lyrics, l.has_synced_lyrics,
               LENGTH(l.plain_lyrics) as plain_len,
               LENGTH(l.synced_lyrics) as synced_len,
               LENGTH(l.lyricsfile) as lyricsfile_len
        FROM tracks t
        LEFT JOIN lyrics l ON t.last_lyrics_id = l.id
        LIMIT 5
    """
    ):
        rec = dict(zip([d[0] for d in cur.description], row))
        samples.append(rec)
        print(json.dumps(rec), flush=True)
    report["samples"] = samples

    print("\n=== SAMPLE LYRICSFILE (first 500 chars) ===", flush=True)
    try:
        lf = cur.execute(
            "SELECT SUBSTR(lyricsfile, 1, 500) FROM lyrics WHERE lyricsfile IS NOT NULL LIMIT 1"
        ).fetchone()
        if lf:
            print(lf[0], flush=True)
            report["sample_lyricsfile"] = lf[0]
    except Exception as e:
        print(f"Error: {e}", flush=True)

    # --- 3. Full table scan: tracks (sequential B-tree leaf page read) ---
    print("\n=== FULL SCAN: TRACKS ===", flush=True)
    t0 = time.time()

    track_count = 0
    tracks_with_lyrics = 0
    tracks_without_lyrics = 0
    null_name = 0
    null_artist = 0
    null_duration = 0
    min_duration = None
    max_duration = None
    sum_duration = 0
    count_duration = 0

    for row in cur.execute(
        "SELECT id, name, artist_name, album_name, duration, last_lyrics_id FROM tracks ORDER BY id"
    ):
        track_count += 1
        if row[1] is None:
            null_name += 1
        if row[2] is None:
            null_artist += 1
        if row[4] is None:
            null_duration += 1
        else:
            if min_duration is None or row[4] < min_duration:
                min_duration = row[4]
            if max_duration is None or row[4] > max_duration:
                max_duration = row[4]
            sum_duration += row[4]
            count_duration += 1
        if row[5] is not None:
            tracks_with_lyrics += 1
        else:
            tracks_without_lyrics += 1
        if track_count % 200000 == 0:
            print(f"  tracks: {track_count} ({time.time()-t0:.1f}s)", flush=True)

    tracks_time = time.time() - t0
    print(f"Tracks done: {track_count} in {tracks_time:.1f}s", flush=True)
    report["track_count"] = track_count
    report["tracks_with_lyrics"] = tracks_with_lyrics
    report["tracks_without_lyrics"] = tracks_without_lyrics
    report["null_name_tracks"] = null_name
    report["null_artist_tracks"] = null_artist
    report["null_duration_tracks"] = null_duration
    report["duration_min"] = min_duration
    report["duration_max"] = max_duration
    report["duration_avg"] = sum_duration / count_duration if count_duration else 0
    report["tracks_scan_time_s"] = tracks_time

    # --- 4. Full table scan: lyrics (sequential B-tree leaf page read) ---
    print("\n=== FULL SCAN: LYRICS ===", flush=True)
    t1 = time.time()

    lyrics_count = 0
    has_plain = 0
    has_synced = 0
    has_lyricsfile = 0
    instrumental_count = 0
    total_plain_len = 0
    total_synced_len = 0
    total_lyricsfile_len = 0
    max_plain_len = 0
    max_synced_len = 0
    max_lyricsfile_len = 0
    count_plain = 0
    count_synced = 0
    count_lyricsfile = 0

    for row in cur.execute(
        """
        SELECT id, plain_lyrics, synced_lyrics, lyricsfile,
               instrumental, has_plain_lyrics, has_synced_lyrics, has_lyricsfile
        FROM lyrics ORDER BY id
    """
    ):
        lyrics_count += 1
        if row[1] is not None:
            l = len(row[1])
            total_plain_len += l
            if l > max_plain_len:
                max_plain_len = l
            count_plain += 1
        if row[2] is not None:
            l = len(row[2])
            total_synced_len += l
            if l > max_synced_len:
                max_synced_len = l
            count_synced += 1
        if row[3] is not None:
            l = len(row[3])
            total_lyricsfile_len += l
            if l > max_lyricsfile_len:
                max_lyricsfile_len = l
            count_lyricsfile += 1
        if row[4]:
            instrumental_count += 1
        if row[5]:
            has_plain += 1
        if row[6]:
            has_synced += 1
        if row[7]:
            has_lyricsfile += 1
        if lyrics_count % 200000 == 0:
            print(f"  lyrics: {lyrics_count} ({time.time()-t1:.1f}s)", flush=True)

    lyrics_time = time.time() - t1
    print(f"Lyrics done: {lyrics_count} in {lyrics_time:.1f}s", flush=True)

    report["lyrics_count"] = lyrics_count
    report["has_plain_lyrics"] = has_plain
    report["has_synced_lyrics"] = has_synced
    report["has_lyricsfile"] = has_lyricsfile
    report["instrumental"] = instrumental_count
    report["avg_plain_lyrics_len"] = total_plain_len / count_plain if count_plain else 0
    report["avg_synced_lyrics_len"] = total_synced_len / count_synced if count_synced else 0
    report["avg_lyricsfile_len"] = total_lyricsfile_len / count_lyricsfile if count_lyricsfile else 0
    report["max_plain_lyrics_len"] = max_plain_len
    report["max_synced_lyrics_len"] = max_synced_len
    report["max_lyricsfile_len"] = max_lyricsfile_len
    report["total_plain_bytes"] = total_plain_len
    report["total_synced_bytes"] = total_synced_len
    report["total_lyricsfile_bytes"] = total_lyricsfile_len
    report["lyrics_scan_time_s"] = lyrics_time

    # --- 5. JSON export size estimate ---
    print("\n=== JSON EXPORT SIZE ESTIMATE ===", flush=True)
    total_raw = total_plain_len + total_synced_len + total_lyricsfile_len
    track_metadata_bytes = track_count * 150
    total_with_metadata = total_raw + track_metadata_bytes
    json_overhead_factor = 1.3
    json_estimate = int(total_with_metadata * json_overhead_factor)

    total_no_lyricsfile = total_plain_len + total_synced_len + track_metadata_bytes
    json_estimate_no_lf = int(total_no_lyricsfile * json_overhead_factor)

    print(f"Total raw text bytes: {total_raw}", flush=True)
    print(f"  plain: {total_plain_len} ({total_plain_len/1073741824:.2f} GiB)", flush=True)
    print(f"  synced: {total_synced_len} ({total_synced_len/1073741824:.2f} GiB)", flush=True)
    print(f"  lyricsfile: {total_lyricsfile_len} ({total_lyricsfile_len/1073741824:.2f} GiB)", flush=True)
    print(f"JSON estimate (all fields): {json_estimate} bytes ({json_estimate/1073741824:.2f} GiB)", flush=True)
    print(f"JSON estimate (no lyricsfile): {json_estimate_no_lf} bytes ({json_estimate_no_lf/1073741824:.2f} GiB)", flush=True)
    print(f"Chunk files at 18 MiB: {json_estimate // 18874368}", flush=True)
    print(f"Chunk files (no lyricsfile) at 18 MiB: {json_estimate_no_lf // 18874368}", flush=True)

    report["json_estimate_all"] = json_estimate
    report["json_estimate_no_lyricsfile"] = json_estimate_no_lf
    report["estimated_chunk_files"] = json_estimate // 18874368
    report["estimated_chunk_files_no_lyricsfile"] = json_estimate_no_lf // 18874368

    # --- 6. Summary ---
    total_time = time.time() - t0
    print(f"\n=== SUMMARY ===", flush=True)
    print(f"Total time: {total_time:.1f}s", flush=True)
    print(f"Tracks: {track_count} ({tracks_time:.1f}s)", flush=True)
    print(f"Lyrics: {lyrics_count} ({lyrics_time:.1f}s)", flush=True)
    print(f"Page size: {page_size}, Page count: {page_count}", flush=True)
    report["page_size"] = page_size
    report["page_count"] = page_count
    report["db_size_bytes"] = db_size
    report["total_time_s"] = total_time

    with open("db_inspection_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nReport written to db_inspection_report.json", flush=True)

    conn.close()
    gz.close()


if __name__ == "__main__":
    main()
