# cluster_parser.py

import pandas as pd
from urllib.parse import urlparse


def load_cluster_csv(filepath) -> pd.DataFrame:
    """
    Baca CSV cluster (dari Google Sheets export).
    Terima filepath string atau file object dari Streamlit uploader.
    """
    df = pd.read_csv(filepath, dtype=str, keep_default_na=False)
    df.columns = df.columns.str.strip()
    return df


def normalize_cluster_url(url: str) -> str:
    """
    Normalisasi URL cluster — buang trailing slash dan query param.
    Sama dengan normalize_url di scraper.py tapi tanpa import circular.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url.strip())
    clean = parsed._replace(fragment="", query="")
    return clean.geturl().rstrip("/")


def detect_language(url: str) -> str:
    """
    Deteksi bahasa artikel dari URL.
    - expense.mekari.com/en/blog/... → EN
    - expense.mekari.com/blog/...    → ID
    """
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.startswith("/en/") or "/en/" in path:
        return "EN"
    return "ID"


def extract_cluster_metadata(df: pd.DataFrame) -> dict:
    """
    Dari DataFrame CSV cluster, bangun dict metadata per URL.

    Return dict:
    {
        "https://expense.mekari.com/blog/...": {
            "module": "Spend Control",
            "feature": "Custom Policy",
            "intent": "Informational",
            "keyword": "fraud detection system",
            "language": "ID"
        },
        ...
    }
    """
    # Deteksi nama kolom — fleksibel terhadap variasi kapitalisasi
    col_map = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if col_lower == "module":
            col_map["module"] = col
        elif col_lower == "feature":
            col_map["feature"] = col
        elif "url" in col_lower:
            col_map["url"] = col
        elif col_lower == "intent":
            col_map["intent"] = col
        elif "keyword" in col_lower:
            col_map["keyword"] = col

    required = ["module", "feature", "url"]
    missing = [k for k in required if k not in col_map]
    if missing:
        raise ValueError(f"Kolom berikut tidak ditemukan di CSV cluster: {missing}")

    metadata = {}
    for _, row in df.iterrows():
        url = normalize_cluster_url(row[col_map["url"]])
        if not url:
            continue

        metadata[url] = {
            "module": row[col_map["module"]].strip() if "module" in col_map else "",
            "feature": row[col_map["feature"]].strip() if "feature" in col_map else "",
            "intent": row[col_map.get("intent", "")].strip() if "intent" in col_map and col_map["intent"] in row else "",
            "keyword": row[col_map.get("keyword", "")].strip() if "keyword" in col_map and col_map["keyword"] in row else "",
            "language": detect_language(url)
        }

    return metadata


def get_cluster_options(cluster_meta: dict) -> dict:
    """
    Ekstrak semua nilai unik untuk filter dropdown.

    Return dict:
    {
        "modules": ["Accounts Payable", "General", "Procurement", ...],
        "features": {
            "Spend Control": ["Approval Automation", "Budget Allocation", ...],
            "Procurement": ["General", "Purchase Order", ...],
            ...
        },
        "intents": ["Commercial", "Informational", "Transactional"],
        "languages": ["EN", "ID"]
    }
    """
    modules = sorted(set(v["module"] for v in cluster_meta.values() if v["module"]))
    intents = sorted(set(v["intent"] for v in cluster_meta.values() if v["intent"]))
    languages = sorted(set(v["language"] for v in cluster_meta.values()))

    features = {}
    for v in cluster_meta.values():
        mod = v["module"]
        feat = v["feature"]
        if mod and feat:
            if mod not in features:
                features[mod] = set()
            features[mod].add(feat)

    features = {mod: sorted(feats) for mod, feats in features.items()}

    return {
        "modules": modules,
        "features": features,
        "intents": intents,
        "languages": languages
    }


def get_cluster_stats(cluster_meta: dict, graph_stats: dict, G) -> list:
    """
    Hitung metrik per modul:
    - Total artikel
    - Artikel yatim (0 inbound)
    - Rata-rata inbound
    - Breakdown per intent

    Return list of dict, diurutkan by total artikel descending.
    """
    from collections import defaultdict

    orphan_set = set(graph_stats["orphan_nodes"])

    # Kelompokkan URL per modul
    modules = defaultdict(list)
    for url, meta in cluster_meta.items():
        modules[meta["module"]].append(url)

    stats = []
    for module, urls in modules.items():
        total = len(urls)
        orphans = sum(1 for u in urls if u in orphan_set)

        inbound_counts = [G.in_degree(u) for u in urls if G.has_node(u)]
        avg_inbound = round(sum(inbound_counts) / len(inbound_counts), 1) if inbound_counts else 0

        # Breakdown intent
        intent_counts = defaultdict(int)
        for u in urls:
            if u in cluster_meta:
                intent = cluster_meta[u].get("intent", "Unknown")
                intent_counts[intent] += 1

        stats.append({
            "Module": module,
            "Total Artikel": total,
            "Artikel Yatim": orphans,
            "% Yatim": f"{round(orphans/total*100)}%" if total else "0%",
            "Rata-rata Inbound": avg_inbound,
            "Informational": intent_counts.get("Informational", 0),
            "Commercial": intent_counts.get("Commercial", 0),
            "Transactional": intent_counts.get("Transactional", 0),
        })

    return sorted(stats, key=lambda x: x["Total Artikel"], reverse=True)