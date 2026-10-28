import atexit
import os
import shutil
import tempfile
import uuid
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from PIL import Image, ImageOps
from photo_uploader import (
    AppWriteHelper,
    PROJECT_ID,
    inspect_photo,
    is_valid_extension,
    publish_photo,
)

app = Flask(__name__)
app.config['UPLOADER_DEBUG'] = False

STAGING_DIR = tempfile.mkdtemp(prefix='photo_uploader_stage_')
STAGED_FILES = {}


def cleanup_staging_dir():
    if os.path.exists(STAGING_DIR):
        shutil.rmtree(STAGING_DIR, ignore_errors=True)


atexit.register(cleanup_staging_dir)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/stage', methods=['POST'])
def stage_photo():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    uploaded = request.files['file']
    original_name = uploaded.filename or ''
    base_name = os.path.basename(original_name)

    if not base_name or not is_valid_extension(base_name):
        return jsonify({'error': f'Unsupported file type: {base_name}'}), 400

    safe_name = secure_filename(base_name) or f'photo.{base_name.rsplit(".", 1)[-1].lower()}'
    temp_id = uuid.uuid4().hex
    staged_filename = f'{temp_id}_{safe_name}'
    staged_path = os.path.join(STAGING_DIR, staged_filename)

    try:
        uploaded.save(staged_path)
        info = inspect_photo(staged_path)
    except Exception as e:
        if os.path.exists(staged_path):
            try:
                os.remove(staged_path)
            except OSError:
                pass
        return jsonify({'error': f'Failed to inspect image {base_name}: {str(e)}'}), 400

    STAGED_FILES[temp_id] = {
        'path': staged_path,
        'filename': base_name,
        'width': info['width'],
        'height': info['height'],
        'exif': info['exif'],
    }

    return jsonify({
        'temp_id': temp_id,
        'filename': base_name,
        'width': info['width'],
        'height': info['height'],
        'exif': info['exif'],
        'preview_url': f'/api/preview/{temp_id}',
    })


@app.route('/api/preview/<temp_id>', methods=['GET'])
def preview_photo(temp_id):
    entry = STAGED_FILES.get(temp_id)
    if not entry or not os.path.exists(entry['path']):
        return jsonify({'error': 'Staged file not found'}), 404
    return send_file(entry['path'])


@app.route('/api/rotate', methods=['POST'])
def rotate_staged_photo():
    payload = request.get_json(silent=True) or {}
    temp_id = payload.get('temp_id')
    direction = payload.get('direction')  # 'left' (counter-clockwise) or 'right' (clockwise)

    if not temp_id or temp_id not in STAGED_FILES:
        return jsonify({'error': 'Staged photo not found'}), 404

    if direction not in ('left', 'right'):
        return jsonify({'error': 'Invalid direction, expected "left" or "right"'}), 400

    entry = STAGED_FILES[temp_id]
    staged_path = entry['path']

    if not os.path.exists(staged_path):
        return jsonify({'error': 'Staged photo file missing on disk'}), 404

    try:
        # Rotate image:
        # Pillow transpose:
        # - ROTATE_270 is 90 degrees counter-clockwise (Left) or ROTATE_90 is 90 degrees clockwise (Right)
        transpose_op = Image.Transpose.ROTATE_270 if direction == 'right' else Image.Transpose.ROTATE_90
        with Image.open(staged_path) as img:
            # First ensure any initial EXIF orientation is transposed into raw pixels
            img = ImageOps.exif_transpose(img)
            rotated = img.transpose(transpose_op)
            # Remove Orientation tag from exif if present so it doesn't double-orient
            exif = rotated.getexif()
            if 0x0112 in exif:
                del exif[0x0112]
            rotated.save(staged_path, exif=exif, quality=95)
            width, height = rotated.size

        entry['width'] = width
        entry['height'] = height
        return jsonify({
            'status': 'ok',
            'width': width,
            'height': height,
            'preview_url': f"/api/preview/{temp_id}?t={uuid.uuid4().hex}"
        })
    except Exception as e:
        return jsonify({'error': f'Failed to rotate image: {str(e)}'}), 500


@app.route('/api/stage/<temp_id>', methods=['DELETE'])
def remove_staged_photo(temp_id):
    entry = STAGED_FILES.pop(temp_id, None)
    if entry and os.path.exists(entry['path']):
        try:
            os.remove(entry['path'])
        except OSError:
            pass
    return jsonify({'status': 'ok'})


@app.route('/api/clear', methods=['POST'])
def clear_staged_photos():
    for temp_id in list(STAGED_FILES.keys()):
        entry = STAGED_FILES.pop(temp_id, None)
        if entry and os.path.exists(entry['path']):
            try:
                os.remove(entry['path'])
            except OSError:
                pass
    return jsonify({'status': 'ok'})


@app.route('/api/publish', methods=['POST'])
def publish_staged_photo():
    payload = request.get_json(silent=True) or {}
    temp_id = payload.get('temp_id')

    if not temp_id or temp_id not in STAGED_FILES:
        return jsonify({'error': 'Staged photo not found or already published'}), 404

    entry = STAGED_FILES[temp_id]
    staged_path = entry['path']
    if not os.path.exists(staged_path):
        STAGED_FILES.pop(temp_id, None)
        return jsonify({'error': 'Staged photo file missing on disk'}), 404

    try:
        client = AppWriteHelper(PROJECT_ID, debug=app.config.get('UPLOADER_DEBUG', False))
        result = publish_photo(client, staged_path, payload)
    except Exception as e:
        error_msg = getattr(e, 'message', None) or str(e)
        return jsonify({'error': error_msg}), 500

    # Clean up staged file after successful publish
    STAGED_FILES.pop(temp_id, None)
    if os.path.exists(staged_path):
        try:
            os.remove(staged_path)
        except OSError:
            pass

    return jsonify({
        'status': 'ok',
        'doc_id': result['doc_id'],
        'full_res_url': result['full_res_url'],
        'thumbnail_url': result['thumbnail_url'],
    })


def run_server(host='127.0.0.1', port=5000, debug=False):
    app.config['UPLOADER_DEBUG'] = debug
    url = f'http://{host}:{port}'
    print(f'Starting reatret.net Photo Uploader Web UI at {url}')
    app.run(host=host, port=port, debug=debug, use_reloader=False)
