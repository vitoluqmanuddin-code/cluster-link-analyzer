# graph_builder.py

import networkx as nx
from urllib.parse import urlparse


def get_subdomain(url: str) -> str:
    """Ekstrak subdomain dari URL. Contoh: expense.mekari.com"""
    return urlparse(url).netloc


def build_cluster_graph(
    edges: dict,
    input_urls: set,
) -> nx.DiGraph:
    """
    Bangun directed graph HANYA dari URL yang ada di input_urls.

    Aturan:
    - Node hanya dari input_urls
    - Edge hanya antar sesama node di input_urls
    - Link ke luar input_urls diabaikan

    Parameter:
        edges: dict hasil scrape_multiple_with_cache
        input_urls: set URL dari CSV cluster (sudah dinormalisasi)

    Return:
        nx.DiGraph
    """
    G = nx.DiGraph()

    # Tambahkan semua input URLs sebagai node dulu
    for url in input_urls:
        G.add_node(url, title="", node_type="internal")

    # Tambahkan edges hanya kalau source DAN target ada di input_urls
    for edge in edges.get("internal", []):
        if not isinstance(edge, dict):
            continue
        source = edge.get("source", "")
        target = edge.get("target", "")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        if not source or not target:
            continue

        # Hanya tambahkan kalau keduanya ada di input list
        if source in input_urls and target in input_urls:
            G.add_edge(
                source, target,
                anchor=edge.get("anchor", ""),
                link_type="internal"
            )

    return G


def enrich_node_metadata(G: nx.DiGraph, cluster_meta: dict) -> nx.DiGraph:
    """
    Tambahkan metadata dari cluster CSV ke setiap node.

    cluster_meta: dict {url: {module, feature, intent, keyword, language}}
    """
    for url in G.nodes():
        if url in cluster_meta:
            meta = cluster_meta[url]
            G.nodes[url]["title"] = meta.get("keyword", "") or url
            G.nodes[url]["module"] = meta.get("module", "")
            G.nodes[url]["feature"] = meta.get("feature", "")
            G.nodes[url]["intent"] = meta.get("intent", "")
            G.nodes[url]["keyword"] = meta.get("keyword", "")
            G.nodes[url]["language"] = meta.get("language", "")
        else:
            G.nodes[url]["title"] = url.split("/")[-1] or url

    return G


def get_graph_stats(G: nx.DiGraph) -> dict:
    """
    Hitung statistik dasar dari graph.

    Return dict:
    {
        "total_nodes": int,
        "total_edges": int,
        "orphan_nodes": [url, ...],
        "hub_nodes": [
            {"url": url, "inbound_count": int, "title": str,
             "module": str, "feature": str},
            ...
        ]
    }
    """
    total_nodes = G.number_of_nodes()
    total_edges = G.number_of_edges()

    # Orphan = node yang tidak dapat inbound link sama sekali
    orphan_nodes = [
        node for node in G.nodes()
        if G.in_degree(node) == 0
    ]

    # Hub = node diurutkan berdasarkan inbound terbanyak
    # Hub per fitur — top 1 inbound per fitur
    feature_groups = {}
    for node in G.nodes():
        feature = G.nodes[node].get("feature", "")
        if feature not in feature_groups:
            feature_groups[feature] = []
        feature_groups[feature].append(node)

    hub_nodes_data = []
    for feature, nodes in feature_groups.items():
        if not nodes:
            continue
        top_node = max(nodes, key=lambda n: G.in_degree(n))
        if G.in_degree(top_node) > 0:
            hub_nodes_data.append({
                "url": top_node,
                "inbound_count": G.in_degree(top_node),
                "title": G.nodes[top_node].get("keyword", "") or G.nodes[top_node].get("title", "") or top_node,
                "module": G.nodes[top_node].get("module", ""),
                "feature": G.nodes[top_node].get("feature", ""),
                "intent": G.nodes[top_node].get("intent", ""),
            })

    hub_nodes_data = sorted(hub_nodes_data, key=lambda x: x["inbound_count"], reverse=True)

    return {
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "orphan_nodes": orphan_nodes,
        "hub_nodes": hub_nodes_data
    }


def get_module_stats(G: nx.DiGraph, cluster_meta: dict) -> list:
    """
    Hitung metrik keterhubungan per modul.

    Return list of dict diurutkan by skor keterhubungan ascending
    (modul paling lemah di atas):
    [
        {
            "module": str,
            "total_artikel": int,
            "terhubung": int,       # artikel yang punya minimal 1 inbound
            "yatim": int,           # artikel 0 inbound
            "skor": float,          # % artikel yang terhubung
            "total_link": int,      # total edges dalam modul
            "rata_inbound": float,
            "informational": int,
            "commercial": int,
            "transactional": int,
        },
        ...
    ]
    """
    from collections import defaultdict

    modules = defaultdict(list)
    for url, meta in cluster_meta.items():
        if G.has_node(url):
            modules[meta["module"]].append(url)

    stats = []
    for module, urls in modules.items():
        total = len(urls)
        yatim = sum(1 for u in urls if G.in_degree(u) == 0)
        terhubung = total - yatim
        skor = round(terhubung / total * 100, 1) if total else 0

        # Hitung total link DALAM modul (source dan target keduanya di modul ini)
        url_set = set(urls)
        total_link = sum(
            1 for u in urls
            for _, v in G.out_edges(u)
            if v in url_set
        )

        inbound_counts = [G.in_degree(u) for u in urls]
        rata_inbound = round(sum(inbound_counts) / len(inbound_counts), 1) if inbound_counts else 0

        scores = [get_inbound_score(G.in_degree(u))["score"] for u in urls]
        avg_score = sum(scores) / len(scores) if scores else 0
        connectivity = get_connectivity_pct(avg_score)

        intent_counts = defaultdict(int)
        for u in urls:
            intent = cluster_meta[u].get("intent", "Unknown")
            intent_counts[intent] += 1

        stats.append({
            "Modul": module,
            "Total Artikel": total,
            "Yatim": yatim,
            "Rata-rata Inbound": rata_inbound,
            "Skor (%)": connectivity["pct"],
            "Status": connectivity["status"],
            "Total Link Internal": total_link,
            "Informational": intent_counts.get("Informational", 0),
            "Commercial": intent_counts.get("Commercial", 0),
            "Transactional": intent_counts.get("Transactional", 0),
        })

    # Urutkan by skor ascending — modul paling lemah di atas
    return sorted(stats, key=lambda x: x["Skor (%)"])


def get_inbound_score(inbound_count: int) -> dict:
    """
    Hitung skor dan status keterhubungan per artikel berdasarkan inbound count.
    """
    if inbound_count >= 5:
        return {"score": 3, "status": "Excellent"}
    elif inbound_count >= 3:
        return {"score": 2, "status": "Standard"}
    elif inbound_count >= 1:
        return {"score": 1, "status": "Low"}
    else:
        return {"score": 0, "status": "Orphaned"}


def get_connectivity_pct(score_avg: float) -> dict:
    """
    Konversi rata-rata skor (0-3) ke persentase dan status modul/fitur.
    """
    pct = round(score_avg / 3 * 100, 1)
    if pct >= 80:
        status = "Excellent"
    elif pct >= 60:
        status = "Standard"
    elif pct >= 40:
        status = "Needs Improvement"
    else:
        status = "Poor"
    return {"pct": pct, "status": status}


def get_article_detail(G: nx.DiGraph, url: str, cluster_meta: dict) -> dict:
    """
    Ambil detail inbound dan outbound untuk satu artikel.

    Return dict:
    {
        "url": str,
        "meta": {module, feature, intent, keyword, language},
        "inbound": [
            {"url": str, "anchor": str, "module": str, "feature": str},
            ...
        ],
        "outbound": [
            {"url": str, "anchor": str, "module": str, "feature": str},
            ...
        ],
        "missing_links": [
            {"url": str, "keyword": str, "module": str, "feature": str},
            ...
        ]  # artikel sesama modul yang belum terhubung sama sekali
    }
    """
    if not G.has_node(url):
        return {}

    meta = cluster_meta.get(url, {})
    current_module = meta.get("module", "")
    current_feature = meta.get("feature", "")

    def categorize(other_module: str, other_feature: str) -> str:
        if other_module == current_module and other_feature == current_feature:
            return "Sesama Fitur"
        elif other_module == current_module:
            return "Sesama Modul"
        else:
            return "Beda Modul"

    # Inbound — siapa yang link ke artikel ini
    inbound = []
    for source, _, data in G.in_edges(url, data=True):
        source_meta = cluster_meta.get(source, {})
        inbound.append({
            "url": source,
            "anchor": data.get("anchor", ""),
            "module": source_meta.get("module", ""),
            "feature": source_meta.get("feature", ""),
            "keyword": source_meta.get("keyword", ""),
            "category": categorize(source_meta.get("module", ""), source_meta.get("feature", "")),
        })

    # Outbound — artikel ini link ke mana
    outbound = []
    for _, target, data in G.out_edges(url, data=True):
        target_meta = cluster_meta.get(target, {})
        outbound.append({
            "url": target,
            "anchor": data.get("anchor", ""),
            "module": target_meta.get("module", ""),
            "feature": target_meta.get("feature", ""),
            "keyword": target_meta.get("keyword", ""),
            "category": categorize(target_meta.get("module", ""), target_meta.get("feature", "")),
        })

    # Missing links — sesama modul yang belum terhubung sama sekali
    inbound_urls = {e["url"] for e in inbound}
    outbound_urls = {e["url"] for e in outbound}
    connected_urls = inbound_urls | outbound_urls | {url}
    current_feature = meta.get("feature", "")

    missing_same_feature = []
    missing_same_module = []

    for other_url, other_meta in cluster_meta.items():
        if other_url == url:
            continue
        if other_meta.get("module") != current_module:
            continue
        if not G.has_node(other_url):
            continue
        if other_url not in connected_urls:
            item = {
                "url": other_url,
                "keyword": other_meta.get("keyword", ""),
                "module": other_meta.get("module", ""),
                "feature": other_meta.get("feature", ""),
                "intent": other_meta.get("intent", ""),
            }
            if other_meta.get("feature") == current_feature:
                missing_same_feature.append(item)
            else:
                missing_same_module.append(item)

    category_order = {"Sesama Fitur": 0, "Sesama Modul": 1, "Beda Modul": 2}

    return {
        "url": url,
        "meta": meta,
        "inbound": sorted(inbound, key=lambda x: category_order.get(x["category"], 3)),
        "outbound": sorted(outbound, key=lambda x: category_order.get(x["category"], 3)),
        "missing_same_feature": sorted(missing_same_feature, key=lambda x: x["keyword"]),
        "missing_same_module": sorted(missing_same_module, key=lambda x: x["feature"]),
    }