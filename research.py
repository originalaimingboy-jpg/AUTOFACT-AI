import requests
from urllib.parse import quote

API = "https://en.wikipedia.org/w/api.php"

def get_wikipedia_context(query):
    headers = {"User-Agent": "AUTOFACT-AI/1.0 (educational Shorts project)"}
    try:
        search = requests.get(
            API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 1,
                "format": "json",
            },
            headers=headers,
            timeout=25,
        )
        search.raise_for_status()
        items = search.json().get("query", {}).get("search", [])
        if not items:
            return None

        title = items[0]["title"]
        page = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}",
            headers=headers,
            timeout=25,
        )
        page.raise_for_status()
        data = page.json()
        extract = data.get("extract", "")
        page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
        if not extract:
            return None
        return f"Article: {title}\nURL: {page_url}\nSummary: {extract}"
    except Exception as exc:
        print(f"Wikipedia lookup failed: {exc}")
        return None
