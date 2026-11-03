import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing GitHub secret: {name}")
    return value

def upload_video(video_path, title, description, tags):
    creds = Credentials(
        token=None,
        refresh_token=required("YT_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=required("YT_CLIENT_ID"),
        client_secret=required("YT_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    creds.refresh(Request())

    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:20],
            "categoryId": "27",
            "defaultLanguage": "hi",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=-1,
        resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"YouTube upload progress: {int(status.progress() * 100)}%")
    return response["id"]
