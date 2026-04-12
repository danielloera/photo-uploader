"""
reprocess_exif.py

Reads photos from the Appwrite `photos_full_res` storage bucket, re-extracts
EXIF data using the improved pipeline from photo_uploader.py, and patches the
corresponding documents in the `metadata` collection.

Usage:
    python reprocess_exif.py              # reprocess all files
    python reprocess_exif.py --dry-run    # preview changes, no writes
    python reprocess_exif.py --file-id <id>           # single file
    python reprocess_exif.py --file-id <id> --dry-run

NOTE: Uses tables_db.client.call() directly to bypass a bug in the installed
Appwrite SDK where Row.$sequence is typed as str but the API returns an int,
causing RowList.model_validate() to fail on any non-empty table.
"""

import argparse
import sys
from io import BytesIO

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB
from appwrite.services.storage import Storage
from appwrite.query import Query
from PIL import Image

import secret

# ---------------------------------------------------------------------------
# Import shared EXIF utilities from photo_uploader
# ---------------------------------------------------------------------------
try:
    from photo_uploader import parse_exif, extract_metadata
except ImportError:
    print("ERROR: Could not import from photo_uploader.py — make sure it's in the same directory.")
    sys.exit(1)

PROJECT_ID    = '6643f12100122b48edf9'
ENDPOINT      = 'https://reatret.net/v1'
BUCKET_ID     = 'photos_full_res'
DATABASE_ID   = 'photos'
TABLE_ID      = 'metadata'
PAGE_LIMIT    = 100   # max items per Appwrite list call

EXIF_FIELDS = [
    'shutter_speed', 'focal_length', 'exposure_time', 'f_number',
    'iso', 'lens_make', 'lens_model', 'camera_make', 'camera_model',
    'date', 'width', 'height',
]


# ---------------------------------------------------------------------------
# Appwrite client
# ---------------------------------------------------------------------------

def make_client():
    client = Client()
    client.set_endpoint(ENDPOINT)
    client.set_project(PROJECT_ID)
    client.set_key(secret.api_key)
    return client


# ---------------------------------------------------------------------------
# Raw API helpers — bypass SDK Pydantic models entirely
#
# The installed SDK has type mismatches between its Pydantic models and what
# the server actually returns (e.g. Row.$sequence declared str, sent as int;
# File.encryption/compression declared required, not sent by server).
# We call client.call() directly and work with plain dicts throughout.
# ---------------------------------------------------------------------------

def raw_list_rows(tables_db, queries=None):
    """
    Call the list-rows endpoint directly, returning the raw dict response.
    Avoids RowList.model_validate() which crashes on the $sequence int/str mismatch.
    """
    api_path = f'/tablesdb/{DATABASE_ID}/tables/{TABLE_ID}/rows'
    api_params = {}
    if queries:
        api_params['queries'] = queries
    return tables_db.client.call('get', api_path, {}, api_params)


def raw_update_row(tables_db, row_id, data):
    """
    Call the update-row endpoint directly, returning the raw dict response.
    """
    api_path = f'/tablesdb/{DATABASE_ID}/tables/{TABLE_ID}/rows/{row_id}'
    return tables_db.client.call(
        'patch',
        api_path,
        {'content-type': 'application/json'},
        {'data': data},
    )


def raw_list_files(storage, bucket_id, queries=None):
    """
    Call the list-files endpoint directly, returning the raw dict response.
    Avoids FileList.model_validate() which crashes on missing encryption/compression fields.
    """
    api_path = f'/storage/buckets/{bucket_id}/files'
    api_params = {}
    if queries:
        api_params['queries'] = queries
    return storage.client.call('get', api_path, {}, api_params)


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

def list_all_files(storage, bucket_id):
    """Paginate through all files in a storage bucket."""
    files = []
    last_id = None
    while True:
        queries = [Query.limit(PAGE_LIMIT)]
        if last_id:
            queries.append(Query.cursor_after(last_id))
        page = raw_list_files(storage, bucket_id, queries=queries)
        batch = page.get('files', [])
        files.extend(batch)
        if len(batch) < PAGE_LIMIT:
            break
        last_id = batch[-1]['$id']
    return files


def list_all_rows(tables_db):
    """Paginate through all rows in the metadata table, returning plain dicts."""
    rows = []
    last_id = None
    while True:
        queries = [Query.limit(PAGE_LIMIT)]
        if last_id:
            queries.append(Query.cursor_after(last_id))
        page = raw_list_rows(tables_db, queries=[q for q in queries])
        batch = page.get('rows', [])
        rows.extend(batch)
        if len(batch) < PAGE_LIMIT:
            break
        last_id = batch[-1]['$id']
    return rows


# ---------------------------------------------------------------------------
# Row index: file_id → row dict
# ---------------------------------------------------------------------------

def build_row_index(rows):
    """
    Parse the file ID out of each row's full_res_url and return a
    dict mapping  file_id → row dict.

    URL format:
      https://reatret.net/v1/storage/buckets/photos_full_res/files/<FILE_ID>/view?project=...
    """
    index = {}
    for row in rows:
        url = row.get('full_res_url', '') or ''
        try:
            file_id = url.split('/files/')[1].split('/')[0]
            index[file_id] = row
        except (IndexError, AttributeError):
            print(f"  [warn] could not parse file_id from URL: {url!r} (row {row.get('$id')})")
    return index


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

def download_file(storage, bucket_id, file_id):
    """Download a storage file and return it as a PIL Image."""
    file_bytes = storage.get_file_download(bucket_id=bucket_id, file_id=file_id)
    image = Image.open(BytesIO(file_bytes))
    image.load()   # force decode before BytesIO can be GC'd
    return image


# ---------------------------------------------------------------------------
# Core reprocess logic
# ---------------------------------------------------------------------------

def reprocess_file(file_id, row, storage, tables_db, dry_run):
    """Download one file, re-extract EXIF, and patch its row."""
    row_id = row['$id']
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing file: {file_id}")
    print(f"  row_id : {row_id}")
    print(f"  title  : {row.get('title') or '(no title)'}")

    # --- Download & parse ---
    try:
        image = download_file(storage, BUCKET_ID, file_id)
    except Exception as e:
        print(f"  [error] download failed: {e}")
        return False

    width, height = image.size
    exif = parse_exif(image)
    meta = extract_metadata(exif)
    meta['width'] = width
    meta['height'] = height

    # --- Show diff vs stored values ---
    changed_fields = {}
    unchanged_fields = []
    for field in EXIF_FIELDS:
        new_val = meta.get(field)
        old_val = row.get(field)
        if new_val != old_val:
            changed_fields[field] = {'old': old_val, 'new': new_val}
        else:
            unchanged_fields.append(field)

    if not changed_fields:
        print("  [skip] no changes detected")
        return True

    print(f"  [diff] {len(changed_fields)} field(s) will change:")
    for field, diff in changed_fields.items():
        print(f"         {field}: {diff['old']!r}  →  {diff['new']!r}")

    if unchanged_fields:
        print(f"  [same] {len(unchanged_fields)} field(s) unchanged: {', '.join(unchanged_fields)}")

    # --- Patch row ---
    if not dry_run:
        try:
            raw_update_row(tables_db, row_id, {k: meta[k] for k in EXIF_FIELDS})
            print("  [ok] row updated")
        except Exception as e:
            print(f"  [error] update failed: {e}")
            return False
    else:
        print("  [dry-run] skipping write")

    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Reprocess EXIF data for photos in Appwrite.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without writing to Appwrite.')
    parser.add_argument('--file-id', type=str, default=None,
                        help='Reprocess a single file by its Appwrite storage ID.')
    args = parser.parse_args()

    client = make_client()
    storage = Storage(client)
    tables_db = TablesDB(client)

    if args.dry_run:
        print("=== DRY RUN MODE — no rows will be modified ===\n")

    # --- Fetch rows and build lookup index ---
    print("Fetching rows from metadata table...")
    rows = list_all_rows(tables_db)
    print(f"  {len(rows)} row(s) found")

    row_index = build_row_index(rows)
    print(f"  {len(row_index)} row(s) indexed by file_id")

    # --- Determine which files to process ---
    if args.file_id:
        file_ids_to_process = [args.file_id]
    else:
        print("\nFetching file list from storage bucket...")
        files = list_all_files(storage, BUCKET_ID)
        print(f"  {len(files)} file(s) found in bucket")
        file_ids_to_process = [f['$id'] for f in files]

    # --- Process ---
    ok = 0
    skipped = 0
    errors = 0

    for file_id in file_ids_to_process:
        row = row_index.get(file_id)
        if not row:
            print(f"\n[warn] no row found for file_id={file_id} — skipping")
            skipped += 1
            continue

        success = reprocess_file(file_id, row, storage, tables_db, dry_run=args.dry_run)
        if success:
            ok += 1
        else:
            errors += 1

    # --- Summary ---
    print(f"\n{'='*50}")
    print(f"Done.  processed={ok}  skipped={skipped}  errors={errors}")
    if args.dry_run:
        print("(dry run — no changes were written)")


if __name__ == '__main__':
    main()
