import os
import re
import time
import requests
from urllib.parse import quote

API_URL = "https://commons.wikimedia.org/w/api.php"

HEADERS = {
    "User-Agent": "AUTOFACT-AI/2.0 (educational video project)"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def clean_filename(name):
    name = re.sub(r"[^\w.-]", "_", name)
    return name[:100]


def search_commons(query, limit=12):
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
        response = SESSION.get(
            API_URL,
            params=params,
            timeout=25
        )
        response.raise_for_status()
        data = response.json()

        pages = data.get("query", {}).get("pages", {})
        return list(pages.values())

    except Exception as error:
        print(f"Commons search error: {error}")
        return []


def get_license_info(page):
    info = page.get("imageinfo", [{}])[0]
    metadata = info.get("extmetadata", {})

    artist = metadata.get("Artist", {}).get("value", "Unknown")
    license_name = metadata.get("LicenseShortName", {}).get(
        "value", "License not specified"
    )
    license_url = metadata.get("LicenseUrl", {}).get("value", "")
    source_url = info.get("descriptionurl", "")

    # Remove HTML tags from metadata
    artist = re.sub(r"<[^>]+>", "", artist)

    credit = (
        f"Source: Wikimedia Commons | "
        f"Artist: {artist} | "
        f"License: {license_name} | "
        f"License URL: {license_url} | "
        f"File page: {source_url}"
    )

    return credit


def is_acceptable_license(page):
    info = page.get("imageinfo", [{}])[0]
    metadata = info.get("extmetadata", {})

    license_name = metadata.get(
        "LicenseShortName", {}
    ).get("value", "").lower()

    allowed = [
        "cc0",
        "public domain",
        "cc by",
        "cc-by",
        "cc by-sa",
        "cc-by-sa",
    ]

    return any(item in license_name for item in allowed)


def download_file(url, output_path):
    try:
        response = SESSION.get(
            url,
            timeout=60,
            stream=True
        )
        response.raise_for_status()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, "wb") as file:
            for chunk in response.iter_content(1024 * 128):
                if chunk:
                    file.write(chunk)

        if os.path.getsize(output_path) < 5000:
            os.remove(output_path)
            return False

        return True

    except Exception as error:
        print(f"Download error: {error}")

        if os.path.exists(output_path):
            os.remove(output_path)

        return False


def find_commons_image(query, output_path, exclude_titles=None):
    exclude_titles = set(exclude_titles or [])

    search_queries = [
        query,
        query + " photograph",
        query + " NASA",
    ]

    for search_query in search_queries:
        print(f"Searching Commons image: {search_query}")

        pages = search_commons(search_query)

        for page in pages:
            title = page.get("title", "")

            if title in exclude_titles:
                continue

            if not is_acceptable_license(page):
                continue

            info = page.get("imageinfo", [{}])[0]
            url = info.get("url", "")

            if not url:
                continue

            extension = os.path.splitext(url.split("?")[0])[1].lower()

            if extension not in [".jpg", ".jpeg", ".png", ".webp"]:
                continue

            if download_file(url, output_path):
                credit = get_license_info(page)
                print(f"Image selected: {title}")
                return output_path, credit

        time.sleep(0.5)

    return None, None


def find_commons_video(query, output_path, exclude_titles=None):
    exclude_titles = set(exclude_titles or [])

    search_queries = [
        query + " filetype:video",
        query + " video",
    ]

    for search_query in search_queries:
        print(f"Searching Commons video: {search_query}")

        pages = search_commons(search_query)

        for page in pages:
            title = page.get("title", "")

            if title in exclude_titles:
                continue

            if not is_acceptable_license(page):
                continue

            info = page.get("imageinfo", [{}])[0]
            url = info.get("url", "")

            if not url:
                continue

            extension = os.path.splitext(url.split("?")[0])[1].lower()

            if extension not in [".webm", ".mp4", ".ogv", ".ogg"]:
                continue

            if download_file(url, output_path):
                credit = get_license_info(page)
                print(f"Video selected: {title}")
                return output_path, credit

        time.sleep(0.5)

    return None, None