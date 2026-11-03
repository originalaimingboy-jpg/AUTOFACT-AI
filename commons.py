import html
import re
import time
from pathlib import Path

import requests
from PIL import Image

API = "https://commons.wikimedia.org/w/api.php"
ALLOWED = {
    "CC0", "Public domain", "Public domain mark",
    "CC BY 4.0", "CC BY 3.0", "CC BY-SA 4.0", "CC BY-SA 3.0",
    "CC BY 2.0", "CC BY-SA 2.0"
}
HEADERS = {"User-Agent": "AUTOFACT-AI/1.0 (weekly educational video generator)"}

def _plain(value):
    return re.sub(r"<[^>]+>", "", html.unescape(value or "")).strip()

def find_commons_image(query, output_path):
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": 6,
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": 1200,
        "format": "json",
    }
    try:
        response = requests.get(API, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        for page in pages.values():
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            license_name = _plain(meta.get("LicenseShortName", {}).get("value", ""))
            if not any(license_name.startswith(x) for x in ALLOWED):
                continue
            url = info.get("thumburl") or info.get("url")
            if not url:
                continue
            img = requests.get(url, headers=HEADERS, timeout=45)
            img.raise_for_status()
            temp = Path(output_path)
            temp.write_bytes(img.content)
            with Image.open(temp) as im:
                im.verify()
            artist = _plain(meta.get("Artist", {}).get("value", "Unknown author"))
            title = page.get("title", "Wikimedia Commons file")
            description = _plain(meta.get("ImageDescription", {}).get("value", ""))
            source_url = info.get("descriptionurl", "")
            license_url = _plain(meta.get("LicenseUrl", {}).get("value", ""))
            credit = f"{title} — {artist} — {license_name}. {source_url} {license_url}".strip()
            return str(temp), credit
    except Exception as exc:
        print(f"Commons search failed for '{query}': {exc}")
    return None, None
