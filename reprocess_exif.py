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
"""

import argparse
import os
import sys
import tempfile
import urllib.request
from io import BytesIO

from appwrite.client import Client
from appwrite.services.databases import Databases
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

PROJECT_ID      = '6643f12100122b48edf9'
ENDPOINT        = 'https://reatret.net/v1'
BUCKET_ID       = 'photos_full_res'
DATABASE_ID     = 'photos'
COLLECTION_ID   = 'metadata'
PAGE_LIMIT      = 100   # max items per Appwrite list call

EXIF_FIELDS = [
    'shutter_speed', 'focal_length', 'exposure_time', 'f_number',
    'iso', 'lens_make', 'lens_model', 'camera_make', 'camera_model',
    'date', 'gps_latitude', 'gps_longitude',
    'width', 'height',
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
        page = storage.list_files(bucket_id=bucket_id, queries=queries)
        batch = page.get('files', [])
        files.extend(batch)
        if len(batch) < PAGE_LIMIT:
            break
        last_id = batch[-1]['$id']
    return files


def list_all_docs(databases, database_id, collection_id):
    """Paginate through all documents in a collection."""
    docs = []
    last_id = None
    while True:
        queries = [Query.limit(PAGE_LIMIT)]
        if last_id:
            queries.append(Query.cursor_after(last_id))
        page = databases.list_documents(
            database_id=database_id,
            collection_id=collection_id,
            queries=queries,
        )
        batch = page.get('documents', [])
        docs.extend(batch)
        if len(batch) < PAGE_LIMIT:
            break
        last_id = batch[-1]['$id']
    return docs


# ---------------------------------------------------------------------------
# Document index: file_id → doc
# ---------------------------------------------------------------------------

def build_doc_index(docs):
    """
    Parse the file ID out of each document's full_res_url and return a
    dict mapping  file_id → document.

    URL format:
      https://reatret.net/v1/storage/buckets/photos_full_res/files/<FILE_ID>/view?project=...
    """
    index = {}
    for doc in docs:
        url = doc.get('full_res_url', '')
        try:
            # Everything between '/files/' and '/view'
            file_id = url.split('/files/')[1].split('/')[0]
            index[file_id] = doc
        except (IndexError, AttributeError):
            print(f"  [warn] could not parse file_id from URL: {url!r} (doc {doc['$id']})")
    return index


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

def download_file(storage, bucket_id, file_id):
    """
    Download a storage file and return it as a PIL Image.
    Uses a NamedTemporaryFile so Pillow can open it safely.
    """
    file_bytes = storage.get_file_download(bucket_id=bucket_id, file_id=file_id)
    # Appwrite SDK returns bytes directly
    image = Image.open(BytesIO(file_bytes))
    image.load()   # force decode before BytesIO can be GC'd
    return image


# ---------------------------------------------------------------------------
# Core reprocess logic
# ---------------------------------------------------------------------------

def reprocess_file(file_id, doc, storage, databases, dry_run):
    """Download one file, re-extract EXIF, and patch its document."""
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing file: {file_id}")
    print(f"  doc_id : {doc['$id']}")
    print(f"  title  : {doc.get('title', '(no title)')}")

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
        old_val = doc.get(field)
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

    # --- Patch document ---
    if not dry_run:
        try:
            databases.update_document(
                database_id=DATABASE_ID,
                collection_id=COLLECTION_ID,
                document_id=doc['$id'],
                data={k: meta[k] for k in EXIF_FIELDS},
            )
            print(f"  [ok] document updated")
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
    databases = Databases(client)

    if args.dry_run:
        print("=== DRY RUN MODE — no documents will be modified ===\n")

    # --- Fetch documents and build lookup index ---
    print("Fetching documents from metadata collection...")
    docs = list_all_docs(databases, DATABASE_ID, COLLECTION_ID)
    print(f"  {len(docs)} document(s) found")

    doc_index = build_doc_index(docs)
    print(f"  {len(doc_index)} document(s) indexed by file_id")

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
        doc = doc_index.get(file_id)
        if not doc:
            print(f"\n[warn] no document found for file_id={file_id} — skipping")
            skipped += 1
            continue

        success = reprocess_file(file_id, doc, storage, databases, dry_run=args.dry_run)
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
