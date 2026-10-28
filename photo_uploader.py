from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.storage import Storage
from appwrite.id import ID
from appwrite.input_file import InputFile
import os
from os import remove
from os.path import isfile
from datetime import datetime
import re
from PIL import Image, ImageOps, ExifTags
import argparse
import math
import secret

PROJECT_ID = '6643f12100122b48edf9'
VALID_EXTENSIONS = {'jpg', 'jpeg', 'png'}
IFD_CODE_LOOKUP = {i.value: i.name for i in ExifTags.IFD}


class AppWriteHelper:
    def __init__(self, project_id=PROJECT_ID, debug=False):
        client = Client()
        client.set_endpoint('https://reatret.net/v1')
        client.set_project(project_id)
        client.set_key(secret.api_key)
        self.project_id = project_id
        self.databases = Databases(client)
        self.storage = Storage(client)
        self.debug = debug

    def check_bucket(self, bucket_id):
        if not self.debug:
            return None
        try:
            api_path = f'/storage/buckets/{bucket_id}'
            bucket = self.storage.client.call(
                'get',
                api_path,
                {'X-Appwrite-Project': self.project_id},
            )
            print(f"  [debug] Bucket {bucket_id}: max file size = {bucket['maximumFileSize']}")
            return bucket
        except Exception as e:
            print(f"  [debug] Could not get bucket {bucket_id}: {e}")
            return None

    def upload_file(self, bucket, file_path):
        try:
            file_id = ID.unique()
            api_path = f'/storage/buckets/{bucket}/files'
            api_params = {
                'fileId': file_id,
                'file': InputFile.from_path(file_path),
                'permissions': ["read(\"any\")"]
            }
            result = self.storage.client.chunked_upload(
                api_path,
                {
                    'X-Appwrite-Project': self.project_id,
                    'content-type': 'multipart/form-data',
                    'accept': 'application/json',
                },
                api_params,
                'file',
                None,
                file_id,
            )
            print(f'uploaded: {file_path}')
            upload_id = result['$id']
            return f'https://reatret.net/v1/storage/buckets/{bucket}/files/{upload_id}/view?project={self.project_id}'
        except Exception as e:
            print(f'  [error] upload failed for {file_path}')
            if self.debug:
                if hasattr(e, 'message'):
                    print(f'  [error] message: {e.message}')
                if hasattr(e, 'response'):
                    print(f'  [error] response: {e.response}')
            raise e

    def create_doc(self, data):
        api_path = '/databases/photos/collections/metadata/documents'
        api_params = {
            'documentId': ID.unique(),
            'data': data,
            'permissions': ["read(\"any\")"]
        }
        return self.databases.client.call(
            'post',
            api_path,
            {
                'X-Appwrite-Project': self.project_id,
                'content-type': 'application/json',
                'accept': 'application/json',
            },
            api_params,
        )


# ---------------------------------------------------------------------------
# EXIF value coercion helpers
# ---------------------------------------------------------------------------

def filter_float(val):
    """Convert EXIF value to float, handling IFDRational, tuples, and APEX."""
    if val is None or val == '':
        return None
    try:
        # IFDRational / Fraction — safe to cast directly
        return float(val)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def filter_str(val):
    """Convert EXIF value to a clean string, stripping null bytes."""
    if val is None:
        return None
    try:
        # '\u0000' is the actual null character; r'\u0000' is a literal backslash sequence
        return str(val).replace('\x00', '').replace('\u0000', '').strip()
    except Exception:
        return None


def filter_int(val):
    """Convert EXIF value to int, handling tuples (e.g. ISOSpeedRatings)."""
    if val is None or val == '':
        return None
    try:
        # Some cameras write ISO as a tuple like (400,)
        if isinstance(val, (tuple, list)):
            val = val[0]
        return int(float(val)) if isinstance(val, str) and '.' in val else int(val)
    except (TypeError, ValueError, IndexError):
        return None


def apex_shutter_to_seconds(apex_val):
    """
    Convert a ShutterSpeedValue in APEX units to an exposure time in seconds.
    APEX: Tv = -log2(t)  →  t = 2^(-Tv)
    Returns a float or None.
    """
    if apex_val is None:
        return None
    try:
        return float(2 ** (-float(apex_val)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def rational_to_float(val):
    """
    Robustly convert IFDRational, Fraction, (num, denom) tuple, or plain
    number to float.  Returns None on any failure.
    """
    if val is None:
        return None
    try:
        if isinstance(val, tuple) and len(val) == 2:
            num, denom = val
            return float(num) / float(denom) if denom else None
        return float(val)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def parse_gps(exif_map):
    """
    Extract decimal-degree GPS coordinates from a parsed EXIF map.
    Returns (latitude, longitude) floats or (None, None).
    """
    def dms_to_decimal(dms, ref):
        try:
            d = rational_to_float(dms[0])
            m = rational_to_float(dms[1])
            s = rational_to_float(dms[2])
            if None in (d, m, s):
                return None
            decimal = d + m / 60 + s / 3600
            if ref in ('S', 'W'):
                decimal = -decimal
            return decimal
        except (TypeError, IndexError):
            return None

    lat = dms_to_decimal(
        exif_map.get('GPSLatitude'),
        exif_map.get('GPSLatitudeRef', '')
    )
    lon = dms_to_decimal(
        exif_map.get('GPSLongitude'),
        exif_map.get('GPSLongitudeRef', '')
    )
    return lat, lon


# ---------------------------------------------------------------------------
# EXIF parsing
# ---------------------------------------------------------------------------

def parse_exif(image):
    """
    Parse EXIF data from a Pillow Image object.
    Returns a flat dict of tag-name → value, or {} if no EXIF is present.
    """
    raw_exif = image.getexif()
    if not raw_exif:
        print('  [exif] no EXIF data found')
        return {}

    exif_map = {}
    for tag_code, value in raw_exif.items():
        if tag_code in IFD_CODE_LOOKUP:
            ifd_tag_name = IFD_CODE_LOOKUP[tag_code]
            exif_map[ifd_tag_name] = tag_code
            try:
                ifd_data = raw_exif.get_ifd(tag_code).items()
            except Exception as e:
                print(f'  [exif] could not read IFD {ifd_tag_name}: {e}')
                continue
            for nested_key, nested_value in ifd_data:
                nested_tag_name = (
                    ExifTags.GPSTAGS.get(nested_key)
                    or ExifTags.TAGS.get(nested_key)
                    or str(nested_key)
                )
                exif_map[nested_tag_name] = nested_value
        else:
            tag_name = ExifTags.TAGS.get(tag_code, str(tag_code))
            exif_map[tag_name] = value

    return exif_map


def extract_metadata(exif):
    """
    Pull the fields we care about from the parsed EXIF dict, with safe
    coercions and fallback logic.  Returns a dict ready to merge into the
    Appwrite document payload.
    """
    # ExposureTime is the reliable wall-clock shutter speed in seconds.
    # ShutterSpeedValue is in APEX units and requires conversion — we use it
    # only as a fallback when ExposureTime is absent.
    exposure_time = rational_to_float(exif.get('ExposureTime'))
    if exposure_time is None:
        apex_sv = rational_to_float(exif.get('ShutterSpeedValue'))
        exposure_time = apex_shutter_to_seconds(apex_sv)
        if exposure_time is not None:
            print('  [exif] ExposureTime missing — derived from ShutterSpeedValue (APEX)')

    focal_length = rational_to_float(exif.get('FocalLength'))
    f_number = rational_to_float(exif.get('FNumber'))
    # FNumber is occasionally missing; ApertureValue (APEX) can substitute
    if f_number is None:
        aperture_apex = rational_to_float(exif.get('ApertureValue'))
        if aperture_apex is not None:
            f_number = round(math.sqrt(2 ** float(aperture_apex)), 1)
            print('  [exif] FNumber missing — derived from ApertureValue (APEX)')

    iso = filter_int(exif.get('ISOSpeedRatings') or exif.get('PhotographicSensitivity'))

    return {
        # Derived from the already-resolved exposure_time (ExposureTime →
        # ShutterSpeedValue fallback), not ShutterSpeedValue directly —
        # which is absent on many cameras, phones, and scanned film images.
        'shutter_speed': filter_float(exposure_time),
        'focal_length': filter_float(focal_length),
        'exposure_time': filter_float(exposure_time),
        'f_number': filter_float(f_number),
        'iso': iso,
        'lens_make': filter_str(exif.get('LensMake')),
        'lens_model': filter_str(exif.get('LensModel')),
        'camera_make': filter_str(exif.get('Make')),
        'camera_model': filter_str(exif.get('Model')),
        'date': filter_str(exif.get('DateTime') or exif.get('DateTimeOriginal')),
    }


def normalize_exif_payload(raw_exif):
    """Normalize user-edited or extracted EXIF dictionary for Appwrite document creation."""
    raw_exif = raw_exif or {}

    def clean_str(val):
        s = filter_str(val)
        return s if s else None

    exposure_time = filter_float(raw_exif.get('exposure_time'))
    shutter_speed = filter_float(raw_exif.get('shutter_speed'))
    if shutter_speed is None and exposure_time is not None:
        shutter_speed = exposure_time
    if exposure_time is None and shutter_speed is not None:
        exposure_time = shutter_speed

    return {
        'shutter_speed': shutter_speed,
        'focal_length': filter_float(raw_exif.get('focal_length')),
        'exposure_time': exposure_time,
        'f_number': filter_float(raw_exif.get('f_number')),
        'iso': filter_int(raw_exif.get('iso')),
        'lens_make': clean_str(raw_exif.get('lens_make')),
        'lens_model': clean_str(raw_exif.get('lens_model')),
        'camera_make': clean_str(raw_exif.get('camera_make')),
        'camera_model': clean_str(raw_exif.get('camera_model')),
        'date': clean_str(raw_exif.get('date')),
    }


# ---------------------------------------------------------------------------
# Photo inspection & publishing helpers
# ---------------------------------------------------------------------------

def generate_id(title):
    """
    Create a lowercase alphanumeric and underscore ID based on the title.
    Example: "Danny's picture" -> "dannys_picture"
    """
    cleaned = (title or '').replace("'", "").replace("’", "")
    cleaned = re.sub(r'[^a-z0-9]+', '_', cleaned.lower().strip())
    cleaned = cleaned.strip('_')
    return cleaned if cleaned else str(ID.unique())


def is_valid_extension(filename):
    if '.' not in filename:
        return False
    file_ext = filename.rsplit('.', 1)[-1].lower()
    return file_ext in VALID_EXTENSIONS


def is_valid_file(file_path):
    return isfile(file_path) and is_valid_extension(file_path)


def inspect_photo(file_path):
    """
    Open an image file, extract its EXIF metadata and visual dimensions
    (after EXIF orientation transpose).
    """
    with Image.open(file_path) as image:
        exif = parse_exif(image)
        meta = extract_metadata(exif)
        transposed = ImageOps.exif_transpose(image)
        width, height = transposed.size
    return {
        'width': width,
        'height': height,
        'exif': meta,
    }


def publish_photo(client, file_path, metadata):
    """
    Upload full-res photo and generated 1/4 thumbnail to Appwrite storage,
    then create the metadata document in Appwrite database.
    """
    client.check_bucket('photos_full_res')
    full_url = client.upload_file('photos_full_res', file_path)

    photo_is_film = bool(metadata.get('is_film', False))
    folder_dir = os.path.dirname(file_path) or '.'
    base_name = os.path.basename(file_path)
    thumbnail_path = os.path.join(folder_dir, f'thumbnail_{base_name}')

    try:
        with Image.open(file_path) as image:
            raw_exif = {} if photo_is_film else parse_exif(image)
            transposed = ImageOps.exif_transpose(image)
            width, height = transposed.size
            resized = transposed.resize((max(1, width // 4), max(1, height // 4)), Image.LANCZOS)
            resized.save(thumbnail_path, quality=70, optimize=True)

        thumbnail_url = client.upload_file('photos_thumbnail', thumbnail_path)
    finally:
        if os.path.exists(thumbnail_path):
            try:
                remove(thumbnail_path)
            except OSError:
                pass

    title = (metadata.get('title') or '').strip()
    desc = (metadata.get('description') or '').strip()
    custom_doc_id = (metadata.get('doc_id') or '').strip()
    doc_id = custom_doc_id if custom_doc_id else generate_id(title)

    if photo_is_film:
        film_type_input = (metadata.get('film_type') or '').strip()
        film_type = film_type_input if film_type_input else "Will update later"
        date_val = (metadata.get('date') or '').strip() or datetime.now().strftime('%Y:%m:%d %H:%M:%S')

        doc_data = {
            'id': doc_id,
            'title': title,
            'description': desc,
            'date': date_val,
            'width': width,
            'height': height,
            'full_res_url': full_url,
            'thumbnail_url': thumbnail_url,
            'is_film': True,
            'film_type': film_type,
        }
    else:
        if 'exif' in metadata and metadata['exif'] is not None:
            meta = normalize_exif_payload(metadata['exif'])
        else:
            meta = extract_metadata(raw_exif)

        doc_data = {
            'id': doc_id,
            'title': title,
            'description': desc,
            'width': width,
            'height': height,
            'full_res_url': full_url,
            'thumbnail_url': thumbnail_url,
            'is_film': False,
            **meta,
        }

    result = client.create_doc(doc_data)
    print(f'created doc: {result}\n')
    return {
        'doc': result,
        'doc_id': doc_id,
        'full_res_url': full_url,
        'thumbnail_url': thumbnail_url,
        'data': doc_data,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='reatret.net Photo Uploader Web UI')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Host interface for the Web UI (default: 127.0.0.1).')
    parser.add_argument('--port', type=int, default=5000, help='Port for the Web UI (default: 5000).')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging.')
    args = parser.parse_args()

    from web_ui import run_server
    run_server(host=args.host, port=args.port, debug=args.debug)
