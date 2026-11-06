
import os
import re
import time
import requests
from PIL import Image

API_URL = "https://commons.wikimedia.org/w/api.php"

HEADERS = {
    "User-Agent": "AUTOFACT-AI/3.0 (educational video project)"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".ogg"}

ALLOWED_LICENSES = (
    "cc0",
    "public domain",
    "cc by",
    "cc-by",
)


def clean_filename(name):
    name = re.sub(r"[^\w.-]", "_", name)
    return name[:100]


def search_commons(query, limit=30):
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
            timeout=30
        )
        response.raise_for_status()

        data = response.json()
        pages = data.get("query", {}).get("pages", {})

        results = list(pages.values())

        print(
            f"Commons returned {len(results)} results "
            f"for: {query}"
        )

        return results

    except Exception as error:
        print(f"Commons search error: {error}")
        return []


def get_license_info(page):
    info = page.get("imageinfo", [{}])[0]
    metadata = info.get("extmetadata", {})

    artist = metadata.get(
        "Artist", {}
    ).get("value", "Unknown")

    license_name = metadata.get(
        "LicenseShortName", {}
    ).get("value", "License not specified")

    license_url = metadata.get(
        "LicenseUrl", {}
    ).get("value", "")

    source_url = info.get("descriptionurl", "")

    artist = re.sub(r"<[^>]+>", "", artist)
    artist = re.sub(r"\s+", " ", artist).strip()

    return (
        f"Source: Wikimedia Commons | "
        f"Artist: {artist} | "
        f"License: {license_name} | "
        f"License URL: {license_url} | "
        f"File page: {source_url}"
    )


def is_acceptable_license(page):
    info = page.get("imageinfo", [{}])[0]
    metadata = info.get("extmetadata", {})

    license_name = metadata.get(
        "LicenseShortName", {}
    ).get("value", "").lower()

    return any(
        item in license_name
        for item in ALLOWED_LICENSES
    )


def download_file(url, output_path):
    try:
        os.makedirs(
            os.path.dirname(output_path) or ".",
            exist_ok=True
        )

        with SESSION.get(
            url,
            timeout=(20, 90),
            stream=True
        ) as response:

            response.raise_for_status()

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if "text/html" in content_type:
                print("Rejected HTML response instead of media.")
                return False

            downloaded = 0

            with open(output_path, "wb") as file:
                for chunk in response.iter_content(
                    chunk_size=1024 * 128
                ):
                    if chunk:
                        file.write(chunk)
                        downloaded += len(chunk)

        if downloaded < 5000:
            print("Rejected: file too small.")
            os.remove(output_path)
            return False

        print(
            f"Downloaded: {downloaded / (1024 * 1024):.2f} MB"
        )

        return True

    except Exception as error:
        print(f"Download error: {error}")

        if os.path.exists(output_path):
            os.remove(output_path)

        return False


def validate_image(path):
    try:
        with Image.open(path) as image:
            image.verify()

        return True

    except Exception as error:
        print(f"Invalid image: {error}")

        if os.path.exists(path):
            os.remove(path)

        return False


def build_search_queries(query, media_type):
    query = re.sub(r"\s+", " ", str(query)).strip()

    if not query:
        query = "science technology"

    if media_type == "image":
        candidates = [
            query,
            f"{query} photograph",
            f"{query} photo",
            f"{query} NASA",
            f"{query} Wikimedia",
        ]
    else:
        candidates = [
            f"{query} filetype:video",
            f"{query} video",
            f"{query} animation filetype:video",
        ]

    unique = []

    for item in candidates:
        if item not in unique:
            unique.append(item)

    return unique


def find_commons_image(
    query,
    output_path,
    exclude_titles=None
):
    exclude_titles = set(exclude_titles or [])

    for search_query in build_search_queries(
        query, "image"
    ):
        print(f"\nSearching Commons image: {search_query}")

        pages = search_commons(search_query, limit=30)

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

            extension = os.path.splitext(
                url.split("?")[0]
            )[1].lower()

            if extension not in IMAGE_EXTENSIONS:
                continue

            print(f"Trying image: {title}")

            if download_file(url, output_path):
                if validate_image(output_path):
                    credit = get_license_info(page)

                    print(f"IMAGE SELECTED: {title}")

                    return output_path, credit

        time.sleep(0.5)

    print(f"NO COMMONS IMAGE FOUND: {query}")
    return None, None


def find_commons_video(
    query,
    output_path,
    exclude_titles=None
):
    exclude_titles = set(exclude_titles or [])

    for search_query in build_search_queries(
        query, "video"
    ):
        print(f"\nSearching Commons video: {search_query}")

        pages = search_commons(search_query, limit=30)

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

            extension = os.path.splitext(
                url.split("?")[0]
            )[1].lower()

            if extension not in VIDEO_EXTENSIONS:
                continue

            print(f"Trying video: {title}")

            if download_file(url, output_path):
                credit = get_license_info(page)

                print(f"VIDEO SELECTED: {title}")

                return output_path, credit

        time.sleep(0.5)

    print(f"NO COMMONS VIDEO FOUND: {query}")
    return None, None
