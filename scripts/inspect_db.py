#!/usr/bin/env python3
"""Inspect LRCLIB SQLite DB mounted via FUSE. Minimizes FUSE reads by using one connection."""

import sqlite3
import sys
import json
import time

DB_URI = sys.argv[1]
out = {}

conn = sqlite3.connect(DB_URI, uri=True)
conn.row_factory = sqlite3.Row

# 1. Schema (reads page 1 only — fast)
print("=== TABLES ===", flush=True)
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(json.dumps(tables), flush=True)
out["tables"] = tables

print("\n=== TRACKS SCHEMA ===", flush=True)
tracks_schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tracks'").fetchone()[0]
print(tracks_schema, flush=True)
out["tracks_schema"] = tracks_schema

print("\n=== LYRICS SCHEMA ===", flush=True)
lyrics_schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='lyrics'").fetchone()[0]
print(lyrics_schema, flush=True)
out["lyrics_schema"] = lyrics_schema

print("\n=== INDEXES ===", flush=True)
indexes = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
print(json.dumps(indexes), flush=True)
out["indexes"] = indexes

print("\n=== TRIGGERS ===", flush=True)
triggers = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()]
print(json.dumps(triggers), flush=True)
out["triggers"] = triggers

# 2. Sample records (LIMIT — reads a few pages only — fast)
print("\n=== SAMPLE RECORDS (tracks + lyrics join) ===", flush=True)
samples = []
for row in conn.execute("""
    SELECT t.id, t.name, t.artist_name, t.album_name, t.duration,
           t.last_lyrics_id,
           l.instrumental, l.has_lyricsfile, l.has_plain_lyrics, l.has_synced_lyrics,
           LENGTH(l.plain_lyrics) as plain_len,
           LENGTH(l.synced_lyrics) as synced_len,
           LENGTH(l.lyricsfile) as lyricsfile_len
    FROM tracks t
    LEFT JOIN lyrics l ON t.last_lyrics_id = l.id
    LIMIT 5
"""):
    samples.append(dict(row))
    print(json.dumps(dict(row)), flush=True)
out["samples"] = samples

print("\n=== SAMPLE LYRICSFILE (first 500 chars) ===", flush=True)
try:
    lf = conn.execute("SELECT SUBSTR(lyricsfile, 1, 500) FROM lyrics WHERE lyricsfile IS NOT NULL LIMIT 1").fetchone()
    if lf:
        print(lf[0], flush=True)
        out["sample_lyricsfile"] = lf[0]
except Exception as e:
    print(f"Error: {e}", flush=True)

# 3. SQLite header info (page size, page count — reads only page 1 header)
print("\n=== DB HEADER INFO ===", flush=True)
page_size = conn.execute("PRAGMA page_size").fetchone()[0]
page_count = conn.execute("PRAGMA page_count").fetchone()[0]
print(f"Page size: {page_size}", flush=True)
print(f"Page count: {page_count}", flush=True)
print(f"DB size: {page_size * page_count / 1073741824:.2f} GiB", flush=True)
out["page_size"] = page_size
out["page_count"] = page_count

# 4. Counts — single pass over tracks table using index if available
# COUNT(*) in SQLite can use the fastest covering index scan
print("\n=== COUNTS (may take a while through FUSE) ===", flush=True)
t0 = time.time()
track_count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
print(f"Track count: {track_count} ({time.time()-t0:.1f}s)", flush=True)
out["track_count"] = track_count

t0 = time.time()
lyrics_count = conn.execute("SELECT COUNT(*) FROM lyrics").fetchone()[0]
print(f"Lyrics count: {lyrics_count} ({time.time()-t0:.1f}s)", flush=True)
out["lyrics_count"] = lyrics_count

# These are fast if there are indexes on these boolean columns
t0 = time.time()
has_lyricsfile = conn.execute("SELECT COUNT(*) FROM lyrics WHERE has_lyricsfile = 1").fetchone()[0]
print(f"Has lyricsfile: {has_lyricsfile} ({time.time()-t0:.1f}s)", flush=True)
out["has_lyricsfile"] = has_lyricsfile

t0 = time.time()
has_plain = conn.execute("SELECT COUNT(*) FROM lyrics WHERE has_plain_lyrics = 1").fetchone()[0]
print(f"Has plain lyrics: {has_plain} ({time.time()-t0:.1f}s)", flush=True)
out["has_plain_lyrics"] = has_plain

t0 = time.time()
has_synced = conn.execute("SELECT COUNT(*) FROM lyrics WHERE has_synced_lyrics = 1").fetchone()[0]
print(f"Has synced lyrics: {has_synced} ({time.time()-t0:.1f}s)", flush=True)
out["has_synced_lyrics"] = has_synced

t0 = time.time()
instrumental = conn.execute("SELECT COUNT(*) FROM lyrics WHERE instrumental = 1").fetchone()[0]
print(f"Instrumental: {instrumental} ({time.time()-t0:.1f}s)", flush=True)
out["instrumental"] = instrumental

t0 = time.time()
tracks_with_lyrics = conn.execute("SELECT COUNT(*) FROM tracks WHERE last_lyrics_id IS NOT NULL").fetchone()[0]
print(f"Tracks with lyrics: {tracks_with_lyrics} ({time.time()-t0:.1f}s)", flush=True)
out["tracks_with_lyrics"] = tracks_with_lyrics

t0 = time.time()
tracks_without_lyrics = conn.execute("SELECT COUNT(*) FROM tracks WHERE last_lyrics_id IS NULL").fetchone()[0]
print(f"Tracks without lyrics: {tracks_without_lyrics} ({time.time()-t0:.1f}s)", flush=True)
out["tracks_without_lyrics"] = tracks_without_lyrics

# 5. Field size stats (requires scan — may be slow)
print("\n=== FIELD SIZE STATS ===", flush=True)
t0 = time.time()
avg_plain = conn.execute("SELECT AVG(LENGTH(plain_lyrics)) FROM lyrics WHERE plain_lyrics IS NOT NULL").fetchone()[0]
print(f"Avg plain_lyrics length: {avg_plain:.1f} ({time.time()-t0:.1f}s)", flush=True)
out["avg_plain_lyrics_len"] = avg_plain

t0 = time.time()
avg_synced = conn.execute("SELECT AVG(LENGTH(synced_lyrics)) FROM lyrics WHERE synced_lyrics IS NOT NULL").fetchone()[0]
print(f"Avg synced_lyrics length: {avg_synced:.1f} ({time.time()-t0:.1f}s)", flush=True)
out["avg_synced_lyrics_len"] = avg_synced

t0 = time.time()
avg_lf = conn.execute("SELECT AVG(LENGTH(lyricsfile)) FROM lyrics WHERE lyricsfile IS NOT NULL").fetchone()[0]
print(f"Avg lyricsfile length: {avg_lf:.1f} ({time.time()-t0:.1f}s)", flush=True)
out["avg_lyricsfile_len"] = avg_lf

t0 = time.time()
max_plain = conn.execute("SELECT MAX(LENGTH(plain_lyrics)) FROM lyrics").fetchone()[0]
print(f"Max plain_lyrics length: {max_plain} ({time.time()-t0:.1f}s)", flush=True)
out["max_plain_lyrics_len"] = max_plain

t0 = time.time()
max_synced = conn.execute("SELECT MAX(LENGTH(synced_lyrics)) FROM lyrics").fetchone()[0]
print(f"Max synced_lyrics length: {max_synced} ({time.time()-t0:.1f)s}", flush=True)
out["max_synced_lyrics_len"] = max_synced

t0 = time.time()
max_lf = conn.execute("SELECT MAX(LENGTH(lyricsfile)) FROM lyrics").fetchone()[0]
print(f"Max lyricsfile length: {max_lf} ({time.time()-t0:.1f}s)", flush=True)
out["max_lyricsfile_len"] = max_lf

# 6. Edge cases
print("\n=== EDGE CASES ===", flush=True)
null_name = conn.execute("SELECT COUNT(*) FROM tracks WHERE name IS NULL").fetchone()[0]
print(f"Tracks with NULL name: {null_name}", flush=True)
out["null_name_tracks"] = null_name

null_artist = conn.execute("SELECT COUNT(*) FROM tracks WHERE artist_name IS NULL").fetchone()[0]
print(f"Tracks with NULL artist_name: {null_artist}", flush=True)
out["null_artist_tracks"] = null_artist

null_duration = conn.execute("SELECT COUNT(*) FROM tracks WHERE duration IS NULL").fetchone()[0]
print(f"Tracks with NULL duration: {null_duration}", flush=True)
out["null_duration_tracks"] = null_duration

dur_range = conn.execute("SELECT MIN(duration), MAX(duration), AVG(duration) FROM tracks WHERE duration IS NOT NULL").fetchone()
print(f"Duration range: min={dur_range[0]}, max={dur_range[1]}, avg={dur_range[2]:.1f}", flush=True)
out["duration_min"] = dur_range[0]
out["duration_max"] = dur_range[1]
out["duration_avg"] = dur_range[2]

# 7. JSON export size estimate
print("\n=== JSON EXPORT SIZE ESTIMATE ===", flush=True)
t0 = time.time()
total_bytes = conn.execute("""
    SELECT SUM(
        COALESCE(LENGTH(t.name),0) +
        COALESCE(LENGTH(t.name),0) +
        COALESCE(LENGTH(t.artist_name),0) +
        COALESCE(LENGTH(t.album_name),0) +
        8 +
        1 +
        COALESCE(LENGTH(l.plain_lyrics),0) +
        COALESCE(LENGTH(l.synced_lyrics),0) +
        COALESCE(LENGTH(l.lyricsfile),0)
    )
    FROM tracks t
    LEFT JOIN lyrics l ON t.last_lyrics_id = l.id
""").fetchone()[0]
print(f"Total API field bytes (raw): {total_bytes} ({time.time()-t0:.1f}s)", flush=True)
json_estimate = int(total_bytes * 1.4)
print(f"Estimated JSON size: {json_estimate} bytes ({json_estimate / 1073741824:.2f} GB)", flush=True)
print(f"Estimated files at 18 MiB each: {json_estimate // 18874368}", flush=True)
out["total_raw_bytes"] = total_bytes
out["json_estimate_bytes"] = json_estimate
out["estimated_chunk_files"] = json_estimate // 18874368

# 8. Write summary JSON
with open("db_inspection_report.json", "w") as f:
    json.dump(out, f, indent=2)
print("\n=== Report written to db_inspection_report.json ===", flush=True)

conn.close()
