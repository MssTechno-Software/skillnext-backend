# """Google Drive helpers (same approach as the documents upload code).

# Folder layout:
#   DRIVE_ROOT_FOLDER_ID/
#     └── Courses/
#           └── <course short name>_<course id>/
#                 └── <module name>_<module id>/   <- videos go here
# """
# import os
# from typing import BinaryIO

# from fastapi import HTTPException
# from google.auth.transport.requests import Request
# from google.oauth2.credentials import Credentials
# from googleapiclient.discovery import build
# from googleapiclient.http import MediaIoBaseUpload

# from app.config import settings

# SCOPES = ["https://www.googleapis.com/auth/drive"]
# FOLDER_MIME = "application/vnd.google-apps.folder"
# CHUNK_SIZE = 8 * 1024 * 1024  # stream to Drive in 8 MB chunks, never hold the whole video in RAM
# TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.json")  # app/token.json


# def get_drive_service():
#     if not settings.DRIVE_ROOT_FOLDER_ID:
#         raise HTTPException(500, "DRIVE_ROOT_FOLDER_ID is not configured.")
#     if not os.path.exists(settings.DRIVE_TOKEN_FILE):
#         raise HTTPException(500, f"{settings.DRIVE_TOKEN_FILE} missing on server.")

#     creds = Credentials.from_authorized_user_file(settings.DRIVE_TOKEN_FILE, SCOPES)
#     if creds.expired and creds.refresh_token:
#         creds.refresh(Request())
#         with open(settings.DRIVE_TOKEN_FILE, "w") as f:
#             f.write(creds.to_json())
#     return build("drive", "v3", credentials=creds, cache_discovery=False)


# def _safe(name: str) -> str:
#     # Drive allows most characters, but keep names tidy and safe inside the search query.
#     cleaned = name.replace("/", "-").replace("\\", "-").replace("'", "").strip()
#     return cleaned[:100] or "untitled"


# def get_or_create_folder(service, folder_name: str, parent_id: str) -> str:
#     safe_name = _safe(folder_name)
#     query = (
#         f"name='{safe_name}' and '{parent_id}' in parents and "
#         f"mimeType='{FOLDER_MIME}' and trashed=false"
#     )
#     files = service.files().list(q=query, fields="files(id, name)").execute().get("files", [])
#     if files:
#         return files[0]["id"]

#     metadata = {"name": safe_name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
#     return service.files().create(body=metadata, fields="id").execute()["id"]


# def get_module_folder(course_label: str, module_label: str) -> str:
#     """Create (if needed) and return the Drive folder id for one module."""
#     service = get_drive_service()
#     root = settings.DRIVE_ROOT_FOLDER_ID
#     courses = get_or_create_folder(service, "Courses", root)
#     course = get_or_create_folder(service, course_label, courses)
#     return get_or_create_folder(service, module_label, course)


# def upload_file(fileobj: BinaryIO, filename: str, mimetype: str, folder_id: str) -> str:
#     service = get_drive_service()
#     fileobj.seek(0)
#     media = MediaIoBaseUpload(
#         fileobj, mimetype=mimetype or "application/octet-stream",
#         chunksize=CHUNK_SIZE, resumable=True,
#     )
#     uploaded = service.files().create(
#         body={"name": filename, "parents": [folder_id]},
#         media_body=media,
#         fields="id",
#     ).execute(num_retries=3)
#     return uploaded["id"]


# def make_public(file_id: str) -> None:
#     service = get_drive_service()
#     service.permissions().create(
#         fileId=file_id, body={"type": "anyone", "role": "reader"}
#     ).execute()


# def delete_file(file_id: str) -> None:
#     service = get_drive_service()
#     service.files().delete(fileId=file_id).execute()


# def view_url(file_id: str) -> str:
#     return f"https://drive.google.com/file/d/{file_id}/view"


# def preview_url(file_id: str) -> str:  # embeddable in an <iframe> (video player / PDF viewer)
#     return f"https://drive.google.com/file/d/{file_id}/preview"


# def download_url(file_id: str) -> str:
#     return f"https://drive.google.com/uc?export=download&id={file_id}"

"""Google Drive helpers (same approach as the documents upload code).

Folder layout:
  DRIVE_ROOT_FOLDER_ID/
    └── Courses/
          └── <course short name>_<course id>/
                └── <module name>_<module id>/   <- videos go here
"""
import os
from typing import BinaryIO

from fastapi import HTTPException
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config import settings

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK_SIZE = 8 * 1024 * 1024  # stream to Drive in 8 MB chunks, never hold the whole video in RAM


def get_drive_service():
    if not settings.DRIVE_ROOT_FOLDER_ID:
        raise HTTPException(500, "DRIVE_ROOT_FOLDER_ID is not configured.")
    if not os.path.exists(settings.DRIVE_TOKEN_FILE):
        raise HTTPException(500, f"{settings.DRIVE_TOKEN_FILE} missing on server.")

    creds = Credentials.from_authorized_user_file(settings.DRIVE_TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(settings.DRIVE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _safe(name: str) -> str:
    # Drive allows most characters, but keep names tidy and safe inside the search query.
    cleaned = name.replace("/", "-").replace("\\", "-").replace("'", "").strip()
    return cleaned[:100] or "untitled"


def get_or_create_folder(service, folder_name: str, parent_id: str) -> str:
    safe_name = _safe(folder_name)
    query = (
        f"name='{safe_name}' and '{parent_id}' in parents and "
        f"mimeType='{FOLDER_MIME}' and trashed=false"
    )
    files = service.files().list(q=query, fields="files(id, name)").execute().get("files", [])
    if files:
        return files[0]["id"]

    metadata = {"name": safe_name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    return service.files().create(body=metadata, fields="id").execute()["id"]


def get_module_folder(course_label: str, module_label: str) -> str:
    """Create (if needed) and return the Drive folder id for one module."""
    service = get_drive_service()
    root = settings.DRIVE_ROOT_FOLDER_ID
    courses = get_or_create_folder(service, "Courses", root)
    course = get_or_create_folder(service, course_label, courses)
    return get_or_create_folder(service, module_label, course)


def upload_file(fileobj: BinaryIO, filename: str, mimetype: str, folder_id: str) -> str:
    service = get_drive_service()
    fileobj.seek(0)
    media = MediaIoBaseUpload(
        fileobj, mimetype=mimetype or "application/octet-stream",
        chunksize=CHUNK_SIZE, resumable=True,
    )
    uploaded = service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id",
    ).execute(num_retries=3)
    return uploaded["id"]


def make_public(file_id: str) -> None:
    service = get_drive_service()
    service.permissions().create(
        fileId=file_id, body={"type": "anyone", "role": "reader"}
    ).execute()


def delete_file(file_id: str) -> None:
    service = get_drive_service()
    service.files().delete(fileId=file_id).execute()


def view_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def preview_url(file_id: str) -> str:  # embeddable in an <iframe> (video player / PDF viewer)
    return f"https://drive.google.com/file/d/{file_id}/preview"


def download_url(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={file_id}"