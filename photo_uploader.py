from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.services.storage import Storage
from appwrite.id import ID
from appwrite.input_file import InputFile
import secret


client = Client()
client.set_endpoint('https://reatret.net/v1')
client.set_project('6643f12100122b48edf9')
client.set_key(secret.api_key)

databases = Databases(client)
storage = Storage(client)

storage = Storage(client)

result = storage.create_file(
    bucket_id = 'photos_thumbnail',
    file_id = ID.unique(),
    file = InputFile.from_path('homura.JPG'),
    permissions = ["read(\"any\")"] # optional
)

upload_id = result['$id']
print(result)


print(f'https://reatret.net/v1/storage/buckets/photos_thumbnail/files/{upload_id}/view?project=6643f12100122b48edf9')

def createDoc():
    result = databases.create_document(
        database_id = 'photos',
        collection_id = 'metadata',
        document_id = ID.unique(),
        data = {
        'id': 'test',
        'title': 'Title',
        'description': 'desc.',
        'photo_full_res_url' : 'h',
        'width': 100,
        'height': 100,
        'full_res_url': 'https://media.istockphoto.com/id/1458782106/photo/scenic-aerial-view-of-the-mountain-landscape-with-a-forest-and-the-crystal-blue-river-in.jpg?s=612x612&w=is&k=20&c=FKTfwrl6zzuQUkwfonWJNXXVsHdlSnkdm1izsbCEf_E=',
        'thumbnail_url': 'https://media.istockphoto.com/id/1458782106/photo/scenic-aerial-view-of-the-mountain-landscape-with-a-forest-and-the-crystal-blue-river-in.jpg?s=612x612&w=is&k=20&c=FKTfwrl6zzuQUkwfonWJNXXVsHdlSnkdm1izsbCEf_E='
        },
        permissions = ["read(\"any\")"] # optional
    )

    print(result)