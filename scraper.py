# scraper.py

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed


def normalize_url(url: str) -> str:
    """
    Buang trailing slash, fragment (#section), dan query param dari URL.
    Contoh: "https://example.com/artikel/?utm_source=x#bagian"
            → "https://example.com/artikel"
    """
    parsed = urlparse(url.strip())
    clean = parsed._replace(fragment="", query="")
    return clean.geturl().rstrip("/")


def scrape_links(
    source_url: str,
    timeout: int = 10,
    content_selectors: list = None
) -> dict:
    """
    Fetch source_url, kategorikan semua link yang ditemukan beserta anchor text.

    content_selectors: list of CSS selector string, dicoba satu per satu
    sampai ada yang ketemu.

    Return dict:
    {
        "url": source_url,
        "internal_outbound": [
            {"url": "https://...", "anchor": "teks anchor"},
            ...
        ],
        "external_outbound": [
            {"url": "https://...", "anchor": "teks anchor"},
            ...
        ],
        "status": "ok" | "error",
        "error_msg": ""
    }
    """
    source_url = normalize_url(source_url)
    source_domain = urlparse(source_url).netloc

    result = {
        "url": source_url,
        "internal_outbound": [],
        "external_outbound": [],
        "status": "ok",
        "error_msg": ""
    }

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; InternalLinkBot/1.0)"}
        response = requests.get(source_url, headers=headers, timeout=timeout)
        response.raise_for_status()

    except requests.exceptions.RequestException as e:
        result["status"] = "error"
        result["error_msg"] = str(e)
        return result

    soup = BeautifulSoup(response.text, "html.parser")

    # Tentukan area pencarian link
    if content_selectors:
        search_area = None
        for selector in content_selectors:
            search_area = soup.select_one(selector)
            if search_area:
                break
        if not search_area:
            search_area = soup
    else:
        search_area = soup

    # Pakai dict supaya bisa simpan anchor text per URL
    # Key: absolute URL, Value: anchor text terpanjang yang ditemukan
    internal_found = {}
    external_found = {}

    for tag in search_area.find_all("a", href=True):
        href = tag["href"]

        # Skip link yang bukan http atau relative path
        if not href.startswith(("http", "/")):
            continue

        absolute = normalize_url(urljoin(source_url, href))
        link_domain = urlparse(absolute).netloc

        # Jangan simpan link ke diri sendiri
        if absolute == source_url:
            continue

        # Skip URL yang tidak relevan untuk analisis konten
        BLACKLIST_PATTERNS = [
            "/blog/author",
            "/en/blog/author",
            "/reviewer",
            "/en/reviewer",
            "/harga",
            "/pricing",
        ]
        if any(pattern in absolute for pattern in BLACKLIST_PATTERNS):
            continue

        # Ambil anchor text — bersihkan whitespace berlebih
        anchor = " ".join(tag.get_text().split()).strip()

        if link_domain == source_domain:
            # Kalau URL sudah ada, prioritaskan anchor yang lebih panjang
            if absolute not in internal_found or len(anchor) > len(internal_found[absolute]):
                internal_found[absolute] = anchor
        else:
            if absolute not in external_found or len(anchor) > len(external_found[absolute]):
                external_found[absolute] = anchor

    result["internal_outbound"] = [
        {"url": url, "anchor": anchor}
        for url, anchor in sorted(internal_found.items())
    ]
    result["external_outbound"] = [
        {"url": url, "anchor": anchor}
        for url, anchor in sorted(external_found.items())
    ]

    return result


def scrape_multiple_urls(
    url_list: list,
    timeout: int = 10,
    progress_callback=None,
    content_selectors: list = None
) -> dict:
    """
    Scrape banyak URL sekaligus, bangun struktur edges.

    Return dict:
    {
        "internal": [
            {"source": url, "target": url, "anchor": str, "source_title": ""},
            ...
        ],
        "external": [
            {"source": url, "target": url, "anchor": str, "source_title": ""},
            ...
        ],
        "errors": [
            {"url": url, "error_msg": str},
            ...
        ]
    }
    """
    normalized_list = [normalize_url(u) for u in url_list if u.strip()]

    edges = {
        "internal": [],
        "external": [],
        "errors": []
    }

    total = len(normalized_list)

    for i, url in enumerate(normalized_list):
        result = scrape_links(url, timeout=timeout, content_selectors=content_selectors)

        if progress_callback:
            progress_callback(i + 1, total, url)

        if result["status"] == "error":
            edges["errors"].append({
                "url": url,
                "error_msg": result["error_msg"]
            })
            continue

        for item in result["internal_outbound"]:
            edges["internal"].append({
                "source": url,
                "target": item["url"],
                "anchor": item["anchor"],
                "source_title": ""
            })

        for item in result["external_outbound"]:
            edges["external"].append({
                "source": url,
                "target": item["url"],
                "anchor": item["anchor"],
                "source_title": ""
            })

    return edges