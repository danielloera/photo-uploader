from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
import secret

client = Client()
client.set_endpoint('https://reatret.net/v1')
client.set_project('6643f12100122b48edf9')
client.set_key(secret.api_key)
