import os
import re
import time
import hashlib
import requests
from PIL import Image

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PIXABAY_API = "https://pixabay.com/api/"
PIXABAY_VIDEO_API = "https://pixabay.com/api/videos/"

HEADERS = {
    "User-Agent": "AUTOFACT-AI/4.0 (educational Shorts project)"
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".ogg"}
ALLOWED_LICENSES = ("cc0", "public domain", "cc by", "cc-by")


def clean_filename(name):
    name = re.sub(r"[^\w.-]", "_", str(name))
    return name[:100]


def _safe_download(url, output_path, min_bytes=5000):
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with SESSION.get(url, timeout=(20, 120), stream=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                return False
            downloaded = 0
            with open(output_path, "wb") as target:
                for chunk in response.iter_content(chunk_size=128 * 1024):
                    if chunk:
                        target.write(chunk)
                        downloaded += len(chunk)
        if downloaded < min_bytes:
            os.remove(output_path)
            return False
        return True
    except Exception as exc:
        print(f"Download failed: {exc}")
        if os.path.exists(output_path):
            os.remove(output_path)
        return False


def validate_image(path):
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception as exc:
        print(f"Invalid image rejected: {exc}")
        if os.path.exists(path):
            os.remove(path)
        return False


def _pixabay_key():
    return os.environ.get("PIXABAY_API_KEY", "").strip()


def _pixabay_search(query, media_type):
    key = _pixabay_key()
    if not key:
        print("PIXABAY_API_KEY missing; skipping Pixabay.")
        return []

    endpoint = PIXABAY_VIDEO_API if media_type == "video" else PIXABAY_API
    params = {
        "key": key,
        "q": str(query)[:100],
        "safesearch": "true",
        "per_page": 30,
        "order": "popular",
    }
    try:
        response = SESSION.get(endpoint, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("hits", [])
    except Exception as exc:
        print(f"Pixabay {media_type} search failed: {exc}")
        return []


def _commons_search(query, media_type, limit=30):
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
    }
    try:
        response = SESSION.get(COMMONS_API, params=params, timeout=30)
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        results = list(pages.values())
        if media_type == "image":
            return [
                p for p in results
                if os.path.splitext(p.get("title", "").lower())[1] in IMAGE_EXTENSIONS
            ]
        return [
            p for p in results
            if os.path.splitext(p.get("title", "").lower())[1] in VIDEO_EXTENSIONS
        ]
    except Exception as exc:
        print(f"Commons search failed: {exc}")
        return []


def _commons_license(page):
    info = page.get("imageinfo", [{}])[0]
    meta = info.get("extmetadata", {})
    license_name = meta.get("LicenseShortName", {}).get("value", "")
    return license_name, meta


def _commons_allowed(page):
    license_name, _ = _commons_license(page)
    return any(token in license_name.lower() for token in ALLOWED_LICENSES)


def _commons_credit(page):
    info = page.get("imageinfo", [{}])[0]
    meta = info.get("extmetadata", {})
    artist = meta.get("Artist", {}).get("value", "Unknown")
    artist = re.sub(r"<[^>]+>", "", artist)
    artist = re.sub(r"\s+", " ", artist).strip()
    license_name = meta.get("LicenseShortName", {}).get("value", "License not specified")
    license_url = meta.get("LicenseUrl", {}).get("value", "")
    page_url = info.get("descriptionurl", "")
    return (
        f"Source: Wikimedia Commons | Creator: {artist} | "
        f"License: {license_name} | License URL: {license_url} | File page: {page_url}"
    )


def _query_variants(query):
    q = re.sub(r"\s+", " ", str(query)).strip() or "science technology"
    variants = [q, f"{q} photograph", f"{q} documentary"]
    # Keep searches focused; do not spray many broad requests.
    return list(dict.fromkeys(variants))


def _asset_id(asset):
    return str(asset.get("id") or asset.get("pageid") or asset.get("url") or asset.get("title") or "")


def find_one_image(query, output_path, used=None):
    used = used if used is not None else set()

    # Pixabay first.
    for hit in _pixabay_search(query, "image"):
        uid = "pixabay-image-" + _asset_id(hit)
        if uid in used:
            continue
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if not url:
            continue
        if _safe_download(url, output_path) and validate_image(output_path):
            used.add(uid)
            user = hit.get("user", "Pixabay contributor")
            page = hit.get("pageURL", "https://pixabay.com/")
            return output_path, (
                f"Source: Pixabay | Creator: {user} | "
                f"Asset page: {page} | Pixabay Content License"
            )

    # Commons fallback.
    for variant in _query_variants(query):
        for page in _commons_search(variant, "image"):
            uid = "commons-" + str(page.get("pageid") or page.get("title"))
            if uid in used or not _commons_allowed(page):
                continue
            info = page.get("imageinfo", [{}])[0]
            url = info.get("url", "")
            if not url:
                continue
            if _safe_download(url, output_path) and validate_image(output_path):
                used.add(uid)
                return output_path, _commons_credit(page)

    return None, None


def find_one_video(query, output_path, used=None):
    used = used if used is not None else set()

    # Pixabay first.
    for hit in _pixabay_search(query, "video"):
        uid = "pixabay-video-" + _asset_id(hit)
        if uid in used:
            continue
        videos = hit.get("videos", {})
        # Prefer medium/large, then smaller as a fallback.
        choice = videos.get("medium") or videos.get("large") or videos.get("small") or videos.get("tiny")
        url = choice.get("url") if choice else None
        if not url:
            continue
        if _safe_download(url, output_path, min_bytes=12000):
            used.add(uid)
            user = hit.get("user", "Pixabay contributor")
            page = hit.get("pageURL", "https://pixabay.com/")
            return output_path, (
                f"Source: Pixabay | Creator: {user} | "
                f"Asset page: {page} | Pixabay Content License"
            )

    # Commons fallback.
    for variant in _query_variants(query):
        for page in _commons_search(variant, "video"):
            uid = "commons-" + str(page.get("pageid") or page.get("title"))
            if uid in used or not _commons_allowed(page):
                continue
            info = page.get("imageinfo", [{}])[0]
            url = info.get("url", "")
            if url and _safe_download(url, output_path, min_bytes=12000):
                used.add(uid)
                return output_path, _commons_credit(page)

    return None, None


def collect_media_assets(scenes, output_dir, required_images=7, required_videos=4):
    """
    Collects distinct, scene-related assets. Pixabay is tried first; Commons is fallback.
    Returns (images, videos, credits). Each asset is {"path": ..., "credit": ..., "query": ...}.
    """
    os.makedirs(output_dir, exist_ok=True)
    images, videos, credits = [], [], []
    used_images, used_videos = set(), set()

    for index, scene in enumerate(scenes):
        query = (
            scene.get("visual_query")
            or scene.get("search_query")
            or scene.get("title")
            or scene.get("caption")
            or "science technology"
        )
        query = str(query).strip()
        print(f"\nCollecting media for scene {index + 1}: {query}")

        if len(images) < required_images:
            path = os.path.join(output_dir, f"asset_image_{len(images)+1}.jpg")
            found, credit = find_one_image(query, path, used_images)
            if found:
                images.append({"path": found, "credit": credit, "query": query})
                credits.append(credit)
                print(f"Image {len(images)}/{required_images} ready")

        if len(videos) < required_videos:
            path = os.path.join(output_dir, f"asset_video_{len(videos)+1}.mp4")
            found, credit = find_one_video(query, path, used_videos)
            if found:
                videos.append({"path": found, "credit": credit, "query": query})
                credits.append(credit)
                print(f"Video {len(videos)}/{required_videos} ready")

        if len(images) >= required_images and len(videos) >= required_videos:
            break

        time.sleep(0.25)

    # A second focused pass using the scene queries in case some first-pass searches missed.
    for scene in scenes:
        if len(images) >= required_images and len(videos) >= required_videos:
            break
        query = (
            scene.get("visual_query")
            or scene.get("search_query")
            or scene.get("title")
            or "science technology"
        )
        if len(images) < required_images:
            path = os.path.join(output_dir, f"asset_image_{len(images)+1}.jpg")
            found, credit = find_one_image(query, path, used_images)
            if found:
                images.append({"path": found, "credit": credit, "query": str(query)})
                credits.append(credit)
        if len(videos) < required_videos:
            path = os.path.join(output_dir, f"asset_video_{len(videos)+1}.mp4")
            found, credit = find_one_video(query, path, used_videos)
            if found:
                videos.append({"path": found, "credit": credit, "query": str(query)})
                credits.append(credit)
        time.sleep(0.25)

    return images, videos, credits


# Backward-compatible wrappers for any other project file still using these names.
def find_commons_image(query, output_path, exclude_titles=None):
    used = set(exclude_titles or [])
    return find_one_image(query, output_path, used)


def find_commons_video(query, output_path, exclude_titles=None):
    used = set(exclude_titles or [])
    return find_one_video(query, output_path, used)
