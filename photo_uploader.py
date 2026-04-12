from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.storage import Storage
from appwrite.id import ID
from appwrite.input_file import InputFile
from os import listdir, remove
from os.path import isfile, join
from PIL import Image, ImageOps, ExifTags
from fractions import Fraction
import argparse
import math
import secret

VALID_EXTENSIONS = {'jpg', 'png'}
IFD_CODE_LOOKUP = {i.value: i.name for i in ExifTags.IFD}


class AppWriteHelper:
    def __init__(self, project_id):
        client = Client()
        client.set_endpoint('https://reatret.net/v1')
        client.set_project(project_id)
        client.set_key(secret.api_key)
        self.project_id = project_id
        self.databases = Databases(client)
        self.storage = Storage(client)

    def upload_file(self, bucket, file_path):
        result = self.storage.create_file(
            bucket_id=bucket,
            file_id=ID.unique(),
            file=InputFile.from_path(file_path),
            permissions=["read(\"any\")"]
        )
        print(f'uploaded: {file_path}')
        upload_id = result['$id']
        return f'https://reatret.net/v1/storage/buckets/{bucket}/files/{upload_id}/view?project={self.project_id}'

    def create_doc(self, data):
        return self.databases.create_document(
            database_id='photos',
            collection_id='metadata',
            document_id=ID.unique(),
            data=data,
            permissions=["read(\"any\")"]
        )


# ---------------------------------------------------------------------------
# EXIF value coercion helpers
# ---------------------------------------------------------------------------

def filter_float(val):
    """Convert EXIF value to float, handling IFDRational, tuples, and APEX."""
    if val is None:
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
    if val is None:
        return None
    try:
        # Some cameras write ISO as a tuple like (400,)
        if isinstance(val, (tuple, list)):
            val = val[0]
        return int(val)
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

    gps_lat, gps_lon = parse_gps(exif)

    return {
        'shutter_speed': filter_float(rational_to_float(exif.get('ShutterSpeedValue'))),
        'focal_length': filter_float(focal_length),
        'exposure_time': filter_float(exposure_time),
        'f_number': filter_float(f_number),
        'iso': iso,
        'lens_make': filter_str(exif.get('LensMake')),
        'lens_model': filter_str(exif.get('LensModel')),
        'camera_make': filter_str(exif.get('Make')),
        'camera_model': filter_str(exif.get('Model')),
        'date': filter_str(exif.get('DateTime') or exif.get('DateTimeOriginal')),
        'gps_latitude': filter_float(gps_lat),
        'gps_longitude': filter_float(gps_lon),
    }


# ---------------------------------------------------------------------------

def is_valid_file(file_path):
    file_ext = file_path.split('.')[-1].lower()
    return isfile(file_path) and (file_ext in VALID_EXTENSIONS)


def main(photo_folder_path):
    client = AppWriteHelper('6643f12100122b48edf9')
    photos_in_dir = [f for f in listdir(photo_folder_path) if is_valid_file(join(photo_folder_path, f))]

    for photo in photos_in_dir:
        full_path = f'{photo_folder_path}/{photo}'
        print(f'\nProcessing: {photo}')
        full_url = client.upload_file('photos_full_res', full_path)

        thumbnail_path = f'{photo_folder_path}/thumbnail_{photo}'
        image = Image.open(full_path)
        exif = parse_exif(image)
        image = ImageOps.exif_transpose(image)
        width, height = image.size
        resized = image.resize((width // 4, height // 4), Image.LANCZOS)
        resized.save(thumbnail_path, quality=70, optimize=True)
        thumbnail_url = client.upload_file('photos_thumbnail', thumbnail_path)
        remove(thumbnail_path)

        meta = extract_metadata(exif)

        # Show a quick summary of what was found
        found = {k: v for k, v in meta.items() if v is not None}
        missing = [k for k, v in meta.items() if v is None]
        if missing:
            print(f'  [exif] missing fields: {", ".join(missing)}')
        print(f'  [exif] extracted: {found}')

        doc_id = input("ID: ")
        title = input("Title: ")
        desc = input("Description: ")

        result = client.create_doc({
            'id': doc_id,
            'title': title,
            'description': desc,
            'width': width,
            'height': height,
            'full_res_url': full_url,
            'thumbnail_url': thumbnail_url,
            **meta,
        })
        print(f'created doc: {result}\n')

    print('done.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Upload photos to Appwrite.')
    parser.add_argument('--photo_folder', type=str, help='Photo folder to upload.')
    args = parser.parse_args()
    main(args.photo_folder)
