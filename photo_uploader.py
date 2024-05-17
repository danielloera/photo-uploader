from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.storage import Storage
from appwrite.id import ID
from appwrite.input_file import InputFile
from os import listdir, remove
from os.path import isfile, join
from PIL import Image, ImageOps, ExifTags
import secret

VALID_EXTENSIONS = {'jpg', 'png'}

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
            bucket_id = bucket,
            file_id = ID.unique(),
            file = InputFile.from_path(file_path),
            permissions = ["read(\"any\")"] # optional
        )
        print(f'uploaded: {file_path}')
        upload_id = result['$id']
        return f'https://reatret.net/v1/storage/buckets/{bucket}/files/{upload_id}/view?project={self.project_id}'

    def create_doc(self, data):
        return self.databases.create_document(
            database_id = 'photos',
            collection_id = 'metadata',
            document_id = ID.unique(),
            data = data,
            permissions = ["read(\"any\")"] # optional
        )

def nullable_float(val):
    return val if val is None else float(val)

def nullable_str(val):
    return val if val is None else str(val)

def nullable_int(val):
    return val if val is None else int(val)

def is_valid_file(file_path):
    file_ext = file_path.split('.')[-1].lower()
    return isfile(file_path) and (file_ext in VALID_EXTENSIONS)

def parse_exif(img_exif):
    IFD_CODE_LOOKUP = {i.value: i.name for i in ExifTags.IFD}
    exif_map = {}

    for tag_code, value in img_exif.items():
        # if the tag is an IFD block, nest into it
        if tag_code in IFD_CODE_LOOKUP:
            ifd_tag_name = IFD_CODE_LOOKUP[tag_code]
            exif_map[ifd_tag_name] = tag_code
            ifd_data = img_exif.get_ifd(tag_code).items()
            for nested_key, nested_value in ifd_data:
                nested_tag_name = ExifTags.GPSTAGS.get(nested_key, None) or ExifTags.TAGS.get(nested_key, None) or nested_key
                exif_map[nested_tag_name] = nested_value
        else:
            exif_map[ExifTags.TAGS.get(tag_code)] = value
    return exif_map

def main():
    photo_folder_path = input("Image folder to upload: ")
    client = AppWriteHelper('6643f12100122b48edf9')
    photos_in_dir = [f for f in listdir(photo_folder_path) if is_valid_file(join(photo_folder_path, f))]

    for photo in photos_in_dir:
        full_path = f'{photo_folder_path}/{photo}'
        full_url = client.upload_file('photos_full_res', full_path)

        thumbnail_path = f'{photo_folder_path}/thumbnail_{photo}'
        image = Image.open(full_path)
        exif = parse_exif(image.getexif())
        image = ImageOps.exif_transpose(image)
        width, height = image.size
        # downsize the image with an LANCZOS filter (gives the highest quality)
        resized = image.resize((width // 4, height // 4), Image.LANCZOS)
        resized.save(thumbnail_path, quality=70, optimized=True)

        thumbnail_url = client.upload_file('photos_thumbnail', thumbnail_path)

        doc_id = input("ID: ")
        title = input("Title: ")
        desc = input("Description: ")

        result = client.create_doc(
            {
            'id': doc_id,
            'title': title,
            'description': desc,
            'width': width,
            'height': height,
            'full_res_url': full_url,
            'thumbnail_url': thumbnail_url,
            'shutter_speed': nullable_float(exif.get('ShutterSpeedValue', None)),
            'focal_length': nullable_float(exif.get('FocalLength', None)),
            'exposure_time': nullable_float(exif.get('ExposureTime', None)),
            'f_number': nullable_float(exif.get('FNumber', None)),
            'iso': nullable_int(exif.get('ISOSpeedRatings', None)),
            'lens_make': nullable_str(exif.get('LensMake', None)),
            'lens_model': nullable_str(exif.get('LensModel', None)),
            'camera_make': nullable_str(exif.get('Make', None)),
            'camera_model': nullable_str(exif.get('Model', None)),
            'date': nullable_str(exif.get('DateTime', None)),
            }
        )
        remove(thumbnail_path)
        print(f'created doc: {result}\n\n')
    print('done.')

if __name__ == '__main__':
    main()