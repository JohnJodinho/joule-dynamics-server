import uuid
import tempfile
import os
from appwrite.client import Client
from appwrite.services.storage import Storage
from appwrite.input_file import InputFile
from config import (
    APP_WRITE_PROJECT_ID,
    APP_WRITE_API_ENDPOINT,
    APP_WRITE_API_KEY,
    APP_WRITE_BUCKET_ID
)

client = Client()
client.set_endpoint(APP_WRITE_API_ENDPOINT)
client.set_project(APP_WRITE_PROJECT_ID)
client.set_key(APP_WRITE_API_KEY)

storage = Storage(client)

async def upload_document_to_appwrite(content: str, format: str) -> str:
    """
    Uploads a generated document to Appwrite storage and returns a view/download URL.
    """
    if format not in ["csv", "md"]:
        format = "md"
        
    file_id = str(uuid.uuid4())
    filename = f"report_{file_id}.{format}"
    
    temp_path = os.path.join(tempfile.gettempdir(), filename)
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(content)
        
    try:
        result = storage.create_file(
            bucket_id=APP_WRITE_BUCKET_ID,
            file_id=file_id,
            file=InputFile.from_path(temp_path)
        )
        
        url = f"{APP_WRITE_API_ENDPOINT}/storage/buckets/{APP_WRITE_BUCKET_ID}/files/{file_id}/view?project={APP_WRITE_PROJECT_ID}"
        return url
    except Exception as e:
        print(f"Appwrite Upload Error: {e}")
        return f"Error uploading file: {e}"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
