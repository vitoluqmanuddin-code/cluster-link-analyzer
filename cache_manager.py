# cache_manager.py

import json
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


def get_cache_path(cache_dir=None) -> str:
    if cache_dir:
        return cache_dir
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "cache")


def ensure_cache_dir(cache_dir=None) -> str:
    path = get_cache_path(cache_dir)
    os.makedirs(path, exist_ok=True)
    return path


def url_to_filename(url: str) -> str:
    url = url.replace("https://", "").replace("http://", "")
    url = url.replace("/", "_").replace(":", "_").replace("?", "_").replace("&", "_")
    url = url.strip("_")
    if len(url) > 200:
        url = url[:200]
    return url + ".json"


def save_to_cache(url: str, data: dict, cache_dir=None):
    folder = ensure_cache_dir(cache_dir)
    filename = url_to_filename(url)
    filepath = os.path.join(folder, filename)
    data["cached_at"] = datetime.now().isoformat()
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_from_cache(url: str, cache_dir=None):
    folder = get_cache_path(cache_dir)
    filename = url_to_filename(url)
    filepath = os.path.join(folder, filename)
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def is_cached(url: str, cache_dir=None) -> bool:
    folder = get_cache_path(cache_dir)
    filename = url_to_filename(url)
    filepath = os.path.join(folder, filename)
    return os.path.exists(filepath)


def delete_cache(url: str, cache_dir=None) -> bool:
    folder = get_cache_path(cache_dir)
    filename = url_to_filename(url)
    filepath = os.path.join(folder, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


def clear_all_cache(cache_dir=None) -> int:
    folder = get_cache_path(cache_dir)
    if not os.path.exists(folder):
        return 0
    count = 0
    for filename in os.listdir(folder):
        if filename.endswith(".json"):
            os.remove(os.path.join(folder, filename))
            count += 1
    return count


def get_cache_summary(cache_dir=None) -> dict:
    folder = get_cache_path(cache_dir)
    if not os.path.exists(folder):
        return {"total_cached": 0, "urls": []}
    urls = []
    for filename in os.listdir(folder):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(folder, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            urls.append({
                "url": data.get("url", ""),
                "cached_at": data.get("cached_at", ""),
                "status": data.get("status", ""),
                "internal_count": len(data.get("internal_outbound", [])),
                "external_count": len(data.get("external_outbound", []))
            })
        except (json.JSONDecodeError, IOError):
            continue
    urls.sort(key=lambda x: x["cached_at"], reverse=True)
    return {"total_cached": len(urls), "urls": urls}


def scrape_with_cache(url: str, scrape_fn, force_refresh: bool = False, cache_dir=None, **scrape_kwargs) -> dict:
    if not force_refresh:
        cached = load_from_cache(url, cache_dir)
        if cached:
            cached["from_cache"] = True
            return cached
    result = scrape_fn(url, **scrape_kwargs)
    if result["status"] == "ok":
        save_to_cache(url, result, cache_dir)
    result["from_cache"] = False
    return result


def scrape_multiple_with_cache(
    url_list: list,
    scrape_fn,
    force_refresh: bool = False,
    cache_dir=None,
    progress_callback=None,
    max_workers: int = 5,
    **scrape_kwargs
) -> dict:
    """
    Scrape banyak URL dengan caching — parallel menggunakan ThreadPoolExecutor.
    """
    from scraper import normalize_url
    normalized_list = [normalize_url(u) for u in url_list if u.strip()]

    edges = {
        "internal": [],
        "external": [],
        "errors": [],
        "cache_hits": 0,
        "cache_misses": 0
    }

    total = len(normalized_list)

    # Pisahkan URL yang sudah cache dan yang belum
    cached_urls = []
    fresh_urls = []
    for url in normalized_list:
        if not force_refresh and is_cached(url, cache_dir):
            cached_urls.append(url)
        else:
            fresh_urls.append(url)

    results = {}

    # Load dari cache
    for url in cached_urls:
        cached = load_from_cache(url, cache_dir)
        if cached:
            cached["from_cache"] = True
            results[url] = cached

    # Scrape fresh URLs secara parallel
    def scrape_one(url):
        result = scrape_fn(url, **scrape_kwargs)
        if result["status"] == "ok":
            save_to_cache(url, result, cache_dir)
        result["from_cache"] = False
        return url, result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scrape_one, url): url for url in fresh_urls}
        for future in as_completed(futures):
            url, result = future.result()
            results[url] = result
            if progress_callback:
                progress_callback(len(results), total, url, result.get("from_cache", False))

    # Susun edges
    for url in normalized_list:
        result = results.get(url)
        if not result:
            continue

        if result.get("from_cache"):
            edges["cache_hits"] += 1
        else:
            edges["cache_misses"] += 1

        if result["status"] == "error":
            edges["errors"].append({
                "url": url,
                "error_msg": result["error_msg"]
            })
            continue

        # internal_outbound dari scraper adalah list of string URL
        for target in result.get("internal_outbound", []):
            if isinstance(target, str):
                edges["internal"].append({
                    "source": url,
                    "target": target,
                    "anchor": "",
                    "source_title": ""
                })
            elif isinstance(target, dict):
                # Format lama dari cache — handle juga
                edges["internal"].append({
                    "source": url,
                    "target": target.get("url", ""),
                    "anchor": target.get("anchor", ""),
                    "source_title": ""
                })

        for target in result.get("external_outbound", []):
            if isinstance(target, str):
                edges["external"].append({
                    "source": url,
                    "target": target,
                    "anchor": "",
                    "source_title": ""
                })
            elif isinstance(target, dict):
                edges["external"].append({
                    "source": url,
                    "target": target.get("url", ""),
                    "anchor": target.get("anchor", ""),
                    "source_title": ""
                })

    return edges