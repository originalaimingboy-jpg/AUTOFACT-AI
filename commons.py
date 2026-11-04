
import html
import re
import time
import random
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image


API = "https://commons.wikimedia.org/w/api.php"

ALLOWED = {
    "CC0",
    "Public domain",
    "Public domain mark",
    "CC BY 4.0",
    "CC BY 3.0",
    "CC BY 2.0",
    "CC BY-SA 4.0",
    "CC BY-SA 3.0",
    "CC BY-SA 2.0"
}

HEADERS = {
    "User-Agent": (
        "AUTOFACT-AI/2.0 "
        "(educational YouTube video generator)"
    )
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def plain(value):
    return re.sub(
        r"<[^>]+>",
        "",
        html.unescape(value or "")
    ).strip()


def get_license(metadata):
    return plain(
        metadata.get(
            "LicenseShortName",
            {}
        ).get("value", "")
    )


def is_allowed_license(license_name):
    return any(
        license_name.startswith(item)
        for item in ALLOWED
    )


def get_credit(page, info, metadata, license_name):
    artist = plain(
        metadata.get(
            "Artist",
            {}
        ).get("value", "Unknown author")
    )

    title = page.get(
        "title",
        "Wikimedia Commons file"
    )

    source_url = info.get(
        "descriptionurl",
        ""
    )

    license_url = plain(
        metadata.get(
            "LicenseUrl",
            {}
        ).get("value", "")
    )

    description = plain(
        metadata.get(
            "ImageDescription",
            {}
        ).get("value", "")
    )

    return (
        f"{title} — {artist} — {license_name}. "
        f"Source: {source_url} "
        f"License: {license_url}"
    ).strip()


def search_commons(query, media_type="bitmap", limit=15):
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:{media_type} {query}",
        "gsrnamespace": 6,
        "gsrlimit": limit,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime|size",
        "iiurlwidth": 1600,
        "format": "json"
    }

    response = SESSION.get(
        API,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    pages = data.get(
        "query",
        {}
    ).get(
        "pages",
        {}
    )

    return list(pages.values())


def download_file(url, destination):
    response = SESSION.get(
        url,
        timeout=60,
        stream=True
    )

    response.raise_for_status()

    destination = Path(destination)
    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with destination.open("wb") as file:
        for chunk in response.iter_content(
            chunk_size=1024 * 128
        ):
            if chunk:
                file.write(chunk)

    if not destination.exists() or destination.stat().st_size < 1000:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file is empty or invalid.")

    return destination


def validate_image(path):
    try:
        with Image.open(path) as image:
            image.verify()

        with Image.open(path) as image:
            if image.width < 200 or image.height < 200:
                return False

        return True

    except Exception:
        return False


def find_commons_image(
    query,
    output_path,
    exclude_titles=None
):
    exclude_titles = set(
        exclude_titles or []
    )

    queries = [
        query,
        query.replace(" ", " AND "),
    ]

    checked = set()

    for search_query in queries:
        try:
            pages = search_commons(
                search_query,
                media_type="bitmap",
                limit=20
            )

            random.shuffle(pages)

            for page in pages:
                title = page.get("title", "")

                if not title or title in exclude_titles:
                    continue

                if title in checked:
                    continue

                checked.add(title)

                info_list = page.get(
                    "imageinfo",
                    []
                )

                if not info_list:
                    continue

                info = info_list[0]
                metadata = info.get(
                    "extmetadata",
                    {}
                )

                license_name = get_license(metadata)

                if not is_allowed_license(license_name):
                    continue

                mime = info.get("mime", "")

                if not mime.startswith("image/"):
                    continue

                url = (
                    info.get("thumburl")
                    or info.get("url")
                )

                if not url:
                    continue

                destination = Path(output_path)

                try:
                    download_file(
                        url,
                        destination
                    )

                    if not validate_image(destination):
                        destination.unlink(missing_ok=True)
                        continue

                except Exception as exc:
                    print(
                        f"Image download failed: {exc}",
                        flush=True
                    )
                    continue

                credit = get_credit(
                    page,
                    info,
                    metadata,
                    license_name
                )

                print(
                    f"Commons image selected: {title}",
                    flush=True
                )

                return str(destination), credit

        except Exception as exc:
            print(
                f"Commons search failed for "
                f"'{search_query}': {exc}",
                flush=True
            )

        time.sleep(0.5)

    print(
        f"No suitable Commons image found for: {query}",
        flush=True
    )

    return None, None


def find_commons_video(
    query,
    output_path,
    exclude_titles=None
):
    exclude_titles = set(
        exclude_titles or []
    )

    try:
        pages = search_commons(
            query,
            media_type="video",
            limit=15
        )

        random.shuffle(pages)

        for page in pages:
            title = page.get("title", "")

            if not title or title in exclude_titles:
                continue

            info_list = page.get(
                "imageinfo",
                []
            )

            if not info_list:
                continue

            info = info_list[0]
            metadata = info.get(
                "extmetadata",
                {}
            )

            license_name = get_license(metadata)

            if not is_allowed_license(license_name):
                continue

            mime = info.get("mime", "")

            if not mime.startswith("video/"):
                continue

            url = info.get("url", "")

            if not url:
                continue

            extension = Path(
                urlparse(url).path
            ).suffix.lower()

            if extension not in {
                ".webm",
                ".mp4",
                ".ogv",
                ".ogg"
            }:
                continue

            destination = Path(output_path)

            try:
                download_file(
                    url,
                    destination
                )

            except Exception as exc:
                print(
                    f"Video download failed: {exc}",
                    flush=True
                )
                continue

            credit = get_credit(
                page,
                info,
                metadata,
                license_name
            )

            print(
                f"Commons video selected: {title}",
                flush=True
            )

            return str(destination), credit

    except Exception as exc:
        print(
            f"Commons video search failed for "
            f"'{query}': {exc}",
            flush=True
        )

    return None, None
