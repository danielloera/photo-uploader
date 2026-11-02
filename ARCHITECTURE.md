# reatret.net Photo Uploader Architecture

This document describes the design, components, data flow, and APIs of the `photo-uploader` project for future reference and edits.

---

## 1. Overview & Purpose

The Photo Uploader is a lightweight local web application designed to prepare, inspect, edit, and publish photos to [Appwrite](https://appwrite.io) for `reatret.net`.

### Key Characteristics:
- **Zero-clutter, lightweight stack**: Built using Python's standard library, Flask, Pillow, and vanilla frontend (HTML, CSS, JS) without heavy frontend frameworks or build steps.
- **Drag-and-Drop Batch Staging**: Supports dropping entire folders (with recursive folder traversal) or multiple files (`.jpg`, `.jpeg`, `.png`).
- **Deferred Uploading**: Uploads to production Appwrite Storage and Database **only after** clicking **Publish**.
- **Film & Digital Workflows**: Supports digital EXIF extraction and overrides, as well as film rolls with batch metadata (stock type, unique sequential timestamps for chronological order).
- **In-Browser Image Manipulation**: Real-time previews, full-size lightbox, and in-place 90° rotation (Left / Right) to correct landscape/portrait orientations prior to publishing.

---

## 2. Directory & File Structure

```
photo-uploader/
├── photo_uploader.py      # Core business logic: Appwrite SDK client, EXIF parsing, thumbnailing, publishing
├── web_ui.py              # Flask server routes: staging, rotation, previews, publishing
├── reprocess_exif.py      # Standalone utility: re-reads Appwrite storage and updates EXIF metadata in database
├── secret.py              # (Git-ignored) Appwrite API key configuration
├── requirements.txt       # Project dependencies (appwrite, Pillow, flask)
├── run.sh                 # Local launcher script (handles venv activation & execution)
├── photo-uploader.desktop # Desktop entry launcher shortcut for KDE / FreeDesktop
├── templates/
│   └── index.html         # Main UI markup
└── static/
    ├── style.css          # Dark-theme UI styles & responsive CSS grid
    └── app.js             # Client-side state, folder traversal, drag-and-drop, API interactions
```

---

## 3. Component Details

### `photo_uploader.py`
Contains core backend processing shared between the Web UI and standalone scripts:
- **`AppWriteHelper`**: Wraps the Appwrite Python SDK client:
  - `upload_file(bucket, file_path)`: Uploads a file via chunked multipart upload with public read permission (`read("any")`).
  - `create_doc(data)`: Creates a new document in database `photos`, collection `metadata`.
  - `check_bucket(bucket_id)`: Logs bucket configuration in debug mode.
- **EXIF Extraction & Parsing**:
  - `parse_exif(image)`: Reads raw tags and IFDs (including GPS and Exif IFD).
  - `extract_metadata(exif)`: Pulls exposure time, focal length, aperture ($f$-stop), ISO, camera make/model, lens make/model, date, and handles APEX shutter speed/aperture fallbacks.
  - `normalize_exif_payload(raw_exif)`: Normalizes UI-edited EXIF strings/floats/integers for document insertion.
- **Image Operations**:
  - `inspect_photo(file_path)`: Opens image with Pillow, normalizes orientation (`ImageOps.exif_transpose`), reads dimensions (`width`, `height`), and returns EXIF.
  - `publish_photo(client, file_path, metadata)`:
    1. Uploads original full-resolution photo to Appwrite bucket `photos_full_res`.
    2. Resizes transposed image to $1/4$ size using `Image.LANCZOS`, saves optimized JPEG thumbnail (`quality=70`), and uploads to `photos_thumbnail`.
    3. Builds metadata document payload and inserts into `metadata` collection.
    4. Automatically removes temporary thumbnail file.
- **CLI Entrypoint (`__main__`)**:
  - Parses `--host`, `--port`, `--debug` and invokes `web_ui.run_server()`.

### `web_ui.py`
A Flask application that handles local file lifecycle:
- **Staging Directory**: Creates a temporary directory via `tempfile.mkdtemp(prefix='photo_uploader_stage_')` registered for cleanup on shutdown via `atexit`.
- **API Endpoints**:
  - `GET /`: Renders `index.html`.
  - `POST /api/stage`: Accepts multipart file upload, validates extension, saves to local staging dir, inspects metadata, and returns `{ temp_id, filename, width, height, exif, preview_url }`.
  - `GET /api/preview/<temp_id>`: Streams the staged image to the browser for previews and lightbox.
  - `POST /api/rotate`: In-place image rotation (`direction: "left" | "right"`). Flattens EXIF orientation, transposes 90°, rewrites file, and returns updated dimensions and fresh preview cache-buster.
  - `DELETE /api/stage/<temp_id>`: Removes a staged file when excluded/deleted from UI.
  - `POST /api/clear`: Removes all staged files from disk and memory.
  - `POST /api/publish`: Takes photo metadata and `temp_id`, invokes `publish_photo`, removes the staged file on disk upon success, and returns Appwrite URLs and document details.

### Frontend (`templates/index.html`, `static/style.css`, `static/app.js`)
- **Folder Traversal**: Uses `webkitGetAsEntry()` / directory reader API to recursively gather `.jpg`, `.jpeg`, and `.png` files when a folder is dragged into the browser.
- **Responsive Grid**: Cards adapt across desktop and mobile screens (`repeat(auto-fill, minmax(360px, 1fr))`).
- **Batch Operations**:
  - **Set All Digital**: Toggles all non-published photos to Digital mode, displaying editable EXIF fields.
  - **Set All Film**: Toggles all non-published photos to Film mode and sets distinct, sequentially incremented timestamps (1 second apart) to maintain chronological timeline order across the roll.
  - **Batch Film Type**: Applies film stock string (e.g. `Kodak Portra 400`) across all film photos.
- **Sequential Publishing**: The frontend iterates through remaining photos one by one via `POST /api/publish`, updating individual status badges (`Staged` → `Uploading...` → `Published ✓` or `Error`) and providing real-time progress.

---

## 4. Appwrite Data Model

### Storage Buckets
- `photos_full_res`: Stores original high-resolution uploaded images.
- `photos_thumbnail`: Stores $1/4$ scale resized thumbnail images.

### Database: `photos` / Collection: `metadata`

#### Common Fields:
- `id` (String): Cleaned lowercase alphanumeric and underscore ID generated from Title (e.g., `dannys_photo`), or unique ID fallback.
- `title` (String): User-provided photo title.
- `description` (String): User-provided photo description.
- `width` (Integer): Final image width after orientation adjustments.
- `height` (Integer): Final image height after orientation adjustments.
- `full_res_url` (String): Public view URL for full resolution file.
- `thumbnail_url` (String): Public view URL for thumbnail file.
- `is_film` (Boolean): `true` for film photos, `false` for digital.

#### Film-Specific Fields (`is_film: true`):
- `film_type` (String): Film stock name (defaults to `"Will update later"` if left blank).
- `date` (String): Timestamp in format `YYYY:MM:DD HH:MM:SS`.

#### Digital-Specific Fields (`is_film: false`):
- `camera_make` (String | null)
- `camera_model` (String | null)
- `lens_make` (String | null)
- `lens_model` (String | null)
- `focal_length` (Float | null)
- `f_number` (Float | null)
- `exposure_time` (Float | null)
- `shutter_speed` (Float | null)
- `iso` (Integer | null)
- `date` (String | null)

---

## 5. Running the Application

### Using `run.sh`
```bash
./run.sh
```
Automatically ensures virtual environment dependencies are set up, starts the server, and opens the Web UI in your default browser at the expected local IP and port.

### Passing Options
```bash
./run.sh --port 8080 --debug
```
- `--host`: Bind address (default: `127.0.0.1`).
- `--port`: Port number (default: `5000`).
- `--debug`: Enables Flask debug output and Appwrite bucket debug checks.
