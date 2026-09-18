import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from config import GDRIVE_FOLDER_ID

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def _init_drive_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", _SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Guardar el token renovado
            with open("token.json", "w") as token_file:
                token_file.write(creds.to_json())
        else:
            print("❌ Archivo token.json no encontrado o inválido.")
            return None
            
    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        print("✅ Cliente de Google Drive inicializado (OAuth 2.0).")
        return service
    except Exception as e:
        print(f"❌ Error al iniciar Google Drive: {e}")
        return None

_drive = _init_drive_service()

def test_upload():
    if _drive is None:
        return None
    try:
        with open("test_dummy.txt", "w") as f:
            f.write("test")
        file_metadata = {
            "name": "test_dummy.txt",
            "parents": [GDRIVE_FOLDER_ID],
        }
        media = MediaFileUpload("test_dummy.txt", mimetype="text/plain", resumable=True)
        uploaded = (
            _drive.files()
            .create(
                body=file_metadata, 
                media_body=media, 
                fields="id, webViewLink, name",
                supportsAllDrives=True
            )
            .execute()
        )
        file_id = uploaded.get("id", "")
        link = uploaded.get("webViewLink", "")
        
        # Otorga permisos públicos de lectura al link generado
        _drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True
        ).execute()

        return link
    except Exception as e:
        print(f"Error subiendo a Drive: {e}")
        return None

if __name__ == "__main__":
    link = test_upload()
    print("Link:", link)

