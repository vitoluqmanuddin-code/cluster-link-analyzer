# app.py

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Cluster Link Analyzer",
    page_icon="🔗",
    layout="wide"
)

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title("🔗 Cluster Link Analyzer")

        def try_login():
            if st.session_state.password_input == st.secrets["passwords"]["password"]:
                st.session_state.authenticated = True
            else:
                st.session_state.login_error = True

        st.text_input("Password", type="password", key="password_input", on_change=try_login)

        if st.session_state.get("login_error"):
            st.error("Password salah.")

        st.stop()

check_password()
import pandas as pd
import os
import tempfile
import json
from datetime import datetime
from pyvis.network import Network

from scraper import scrape_links, normalize_url
from cache_manager import scrape_multiple_with_cache, get_cache_summary, clear_all_cache
from cluster_parser import load_cluster_csv, extract_cluster_metadata, get_cluster_options
from graph_builder import (
    build_cluster_graph, enrich_node_metadata,
    get_graph_stats, get_module_stats, get_article_detail,
    get_inbound_score, get_connectivity_pct
)

# ─── Konfigurasi ──────────────────────────────────────────────────────────

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
SESSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

CONTENT_SELECTORS = [
    "section.blog-post__article",
    "div.inner-container",
    "article",
    "div.entry-content",
    "div.post-content",
    "div.article-content",
    "div.content-area",
    "div.main-content",
    "main",
]

INTENT_COLORS = {
    "Informational": "#4B61DD",
    "Commercial":    "#00C853",
    "Transactional": "#FF9100",
}
DEFAULT_COLOR = "#8B95A5"

def save_session(cluster_meta: dict, edges: dict, name: str | None = None):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = name or ts
    filepath = os.path.join(SESSION_DIR, f"{name}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({"cluster_meta": cluster_meta, "edges": edges}, f, ensure_ascii=False)
    return name

def load_session(filename: str) -> dict:
    filepath = os.path.join(SESSION_DIR, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def list_sessions() -> list:
    if not os.path.exists(SESSION_DIR):
        return []
    return sorted(
        [f for f in os.listdir(SESSION_DIR) if f.endswith(".json")],
        reverse=True
    )

# ─── Session state ────────────────────────────────────────────────────────

for key, default in {
    "cluster_meta": None,
    "input_urls": None,
    "G": None,
    "scraping_done": False,
    "edges": None,
    "scrape_msg": None,
    "scrape_errors": [],
    "sel_lang": "Semua",
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ─── Sidebar ──────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🔗 Cluster Link Analyzer")
    st.divider()

    sessions = list_sessions()
    if sessions:
        st.subheader("📂 Load Session")
        selected_session = st.radio(
            "Pilih session",
            sessions,
            format_func=lambda x: x.replace(".json", ""),
            key="sel_session"
        )
        if selected_session:
            if st.button("▶️ Load Session", use_container_width=True):
                data = load_session(selected_session)
                cluster_meta = data["cluster_meta"]
                edges = data["edges"]
                input_urls = set(cluster_meta.keys())

                st.session_state.cluster_meta = cluster_meta
                st.session_state.input_urls = input_urls
                st.session_state.edges = edges

                G = build_cluster_graph(edges, input_urls)
                G = enrich_node_metadata(G, cluster_meta)
                st.session_state.G = G
                st.session_state.scraping_done = True
                st.session_state.scrape_msg = f"Session '{selected_session.replace('.json', '')}' berhasil diload."
                st.session_state.scrape_errors = []
                st.rerun()
            if st.button("🗑️ Hapus session ini", use_container_width=True):
                os.remove(os.path.join(SESSION_DIR, selected_session))
                st.success(f"Session '{selected_session.replace('.json', '')}' dihapus.")
                st.rerun()
                data = load_session(selected_session)
                cluster_meta = data["cluster_meta"]
                edges = data["edges"]
                input_urls = set(cluster_meta.keys())

                st.session_state.cluster_meta = cluster_meta
                st.session_state.input_urls = input_urls
                st.session_state.edges = edges

                G = build_cluster_graph(edges, input_urls)
                G = enrich_node_metadata(G, cluster_meta)
                st.session_state.G = G
                st.session_state.scraping_done = True
                st.session_state.scrape_msg = f"Session '{selected_session.replace('.json', '')}' berhasil diload."
                st.session_state.scrape_errors = []
                st.rerun()
        st.divider()

    st.subheader("📂 Upload Data Cluster")
    cluster_file = st.file_uploader(
        "Upload CSV cluster",
        type=["csv"],
        help="CSV dengan kolom: Module, Feature, Published URL, Intent, Main Keyword"
    )

    if cluster_file:
        try:
            cluster_df = load_cluster_csv(cluster_file)
            cluster_meta = extract_cluster_metadata(cluster_df)
            input_urls = set(cluster_meta.keys())

            # Hanya reset kalau CSV berbeda dari sebelumnya
            if st.session_state.input_urls != input_urls:
                st.session_state.cluster_meta = cluster_meta
                st.session_state.input_urls = input_urls
                st.session_state.G = None
                st.session_state.scraping_done = False
                st.session_state.edges = None
                st.session_state.scrape_msg = None
                st.session_state.scrape_errors = []
            else:
                st.session_state.cluster_meta = cluster_meta
                st.session_state.input_urls = input_urls

            st.success(f"{len(input_urls)} URL ter-mapping.")
        except Exception as e:
            st.error(f"Gagal membaca CSV: {e}")

    st.divider()

    if st.session_state.cluster_meta:
        summary = get_cache_summary(CACHE_DIR)
        st.caption(f"💾 Cache: {summary['total_cached']} URL tersimpan")

        force_refresh = st.checkbox("🔄 Force refresh cache", value=False)

        max_workers = st.slider(
            "⚡ Threads scraping",
            min_value=1, max_value=20, value=5, step=1,
        )

        if st.button("🚀 Mulai Scraping", type="primary", use_container_width=True):
            url_list = list(st.session_state.input_urls)
            progress_bar = st.progress(0)
            status_text = st.empty()

            def on_progress(current, total, url, from_cache):
                progress_bar.progress(current / total)
                label = "cache" if from_cache else "scraping"
                status_text.caption(f"[{current}/{total}] {label}: {url[-50:]}")

            with st.spinner("Memproses..."):
                try:
                    edges = scrape_multiple_with_cache(
                        url_list=url_list,
                        scrape_fn=scrape_links,
                        force_refresh=force_refresh,
                        cache_dir=CACHE_DIR,
                        progress_callback=on_progress,
                        max_workers=max_workers,
                        content_selectors=CONTENT_SELECTORS
                    )
                    progress_bar.progress(1.0)
                    status_text.empty()

                    G = build_cluster_graph(edges, st.session_state.input_urls)
                    G = enrich_node_metadata(G, st.session_state.cluster_meta)

                    st.session_state.edges = edges
                    st.session_state.G = G
                    st.session_state.scraping_done = True

                    hits = edges.get("cache_hits", 0)
                    misses = edges.get("cache_misses", 0)
                    errors = len(edges.get("errors", []))
                    csv_name = cluster_file.name.replace(".csv", "") if cluster_file else "session"
                    save_session(st.session_state.cluster_meta, edges, name=f"{csv_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
                    st.session_state.scrape_msg = f"Selesai — {hits} cache, {misses} fresh, {errors} error."
                    st.session_state.scrape_errors = edges.get("errors", [])

                except Exception as e:
                    st.session_state.scrape_msg = f"Error: {e}"
                    st.session_state.scraping_done = False

        if st.session_state.scrape_msg:
            st.success(st.session_state.scrape_msg)
            if st.session_state.scrape_errors:
                with st.expander(f"⚠️ {len(st.session_state.scrape_errors)} URL gagal"):
                    for err in st.session_state.scrape_errors:
                        st.caption(f"- {err['url']}: {err['error_msg']}")

        if summary["total_cached"] > 0:
            if st.button("🗑️ Hapus cache", use_container_width=True):
                deleted = clear_all_cache(CACHE_DIR)
                st.success(f"{deleted} cache dihapus.")
                st.rerun()

    st.divider()
    st.subheader("🌐 Filter Bahasa")
    selected_lang = st.radio(
        "Bahasa",
        ["Semua", "ID", "EN"],
        horizontal=True,
        key="sel_lang"
    )
    st.divider()
    st.caption("Cluster Link Analyzer v1.0")


# ─── Main content ─────────────────────────────────────────────────────────

st.title("Cluster Link Analyzer")
st.caption("By: Muhammad Vito Luqmanuddin")

if not st.session_state.cluster_meta:
    st.info("👈 Upload CSV cluster di sidebar untuk memulai.")
    st.stop()

if not st.session_state.scraping_done or st.session_state.G is None:
    st.info("👈 Klik 'Mulai Scraping' di sidebar setelah upload CSV.")
    st.stop()

G = st.session_state.G
cluster_meta = st.session_state.cluster_meta
input_urls = st.session_state.input_urls

# Filter bahasa global
selected_lang = st.session_state.get("sel_lang", "Semua")
if selected_lang != "Semua":
    cluster_meta = {
        url: meta for url, meta in cluster_meta.items()
        if meta.get("language") == selected_lang
    }
    filtered_input_urls = set(cluster_meta.keys())
    subedges = {
        "internal": [
            e for e in (st.session_state.edges or {}).get("internal", [])
            if e["source"] in filtered_input_urls and e["target"] in filtered_input_urls
        ],
        "external": [
            e for e in (st.session_state.edges or {}).get("external", [])
            if e["source"] in filtered_input_urls
        ],
        "errors": []
    }
    G = build_cluster_graph(subedges, filtered_input_urls)
    G = enrich_node_metadata(G, cluster_meta)
    input_urls = filtered_input_urls

stats = get_graph_stats(G)
options = get_cluster_options(cluster_meta)

tab1, tab2, tab3 = st.tabs([
    "📊 Overview Cluster",
    "🔍 Detail Modul & Fitur",
    "📄 Detail Artikel"
])


# ══════════════════════════════════════════════
# TAB 1 — Overview Cluster
# ══════════════════════════════════════════════

with tab1:
    st.subheader("📊 Overview Cluster")

    col1, col2, col3, col4 = st.columns(4)
    all_scores = [get_inbound_score(G.in_degree(u))["score"] for u in input_urls]
    avg_score_global = sum(all_scores) / len(all_scores) if all_scores else 0
    skor_global = get_connectivity_pct(avg_score_global)

    col1.metric("Total Artikel", stats["total_nodes"])
    col2.metric("Total Internal Link", stats["total_edges"])
    col3.metric("Orphaned Content (Global)", len(stats["orphan_nodes"]))
    col4.metric("Skor Keterhubungan", f"{skor_global['pct']}% ({skor_global['status']})")

    st.divider()

    st.subheader("📦 Keterhubungan per Modul")
    st.caption("Inbound dihitung dari seluruh artikel dalam list, lintas modul. Daftar diurutkan dari modul yang paling lemah koneksinya.")

    module_stats = get_module_stats(G, cluster_meta)
    module_df = pd.DataFrame(module_stats)

    def highlight_status(val):
        if val == "Poor":
            return "background-color: #3d1a1a"
        elif val == "Needs Improvement":
            return "background-color: #3d3010"
        elif val == "Standard":
            return "background-color: #1a1a3d"
        elif val == "Excellent":
            return "background-color: #1a2a1a"
        return ""

    st.dataframe(
        module_df.style.map(highlight_status, subset=["Status"]).format({
            "Rata-rata Inbound": "{:.1f}",
            "Skor (%)": "{:.1f}"
        }),
        use_container_width=True,
        hide_index=True
    )

    st.divider()

    st.subheader("🏆 Hub Artikel (Inbound Terbanyak)")
    st.caption("Artikel dengan inbound link terbanyak dalam fiturnya, satu per fitur.")
    if stats["hub_nodes"]:
        hub_df = pd.DataFrame(stats["hub_nodes"])
        hub_df = hub_df.rename(columns={
            "url": "URL",
            "inbound_count": "Inbound",
            "title": "Keyword",
            "module": "Modul",
            "feature": "Fitur",
            "intent": "Intent"
        })
        st.dataframe(hub_df, use_container_width=True, hide_index=True)

    st.divider()

    st.subheader("📋 Skor Keterhubungan per Artikel")
    st.caption("Skor per artikel berdasarkan jumlah inbound dari seluruh list.")

    article_score_data = []
    for url in input_urls:
        meta = cluster_meta.get(url, {})
        inbound = G.in_degree(url)
        outbound = G.out_degree(url)
        scoring = get_inbound_score(inbound)
        article_score_data.append({
            "Keyword": meta.get("keyword", ""),
            "Modul": meta.get("module", ""),
            "Fitur": meta.get("feature", ""),
            "Intent": meta.get("intent", ""),
            "Bahasa": meta.get("language", ""),
            "Inbound": inbound,
            "Outbound": outbound,
            "Skor": scoring["score"],
            "Status": scoring["status"],
        })
    article_score_df = pd.DataFrame(article_score_data).sort_values(
        ["Skor", "Inbound"], ascending=[True, True]
    )
    st.markdown(
        """
        <div style="font-size: 14px; color: var(--text-color-light, #888); margin-bottom: 8px;">
        <span style="background-color: #3d1a1a; color: #ff8080; padding: 2px 8px; border-radius: 4px;">Orphaned</span>
        0 inbound, skor 0 &nbsp;&middot;&nbsp;
        <span style="background-color: #3d3010; color: #ffd966; padding: 2px 8px; border-radius: 4px;">Low</span>
        1-2 inbound, skor 1 &nbsp;&middot;&nbsp;
        <span style="background-color: #1a1a3d; color: #8080ff; padding: 2px 8px; border-radius: 4px;">Standard</span>
        3-4 inbound, skor 2 &nbsp;&middot;&nbsp;
        <span style="background-color: #1a2a1a; color: #80ff80; padding: 2px 8px; border-radius: 4px;">Excellent</span>
        5+ inbound, skor 3
        </div>
        """,
        unsafe_allow_html=True
    )

    def highlight_article_status(val):
        if val == "Orphaned":
            return "background-color: #3d1a1a"
        elif val == "Low":
            return "background-color: #3d3010"
        elif val == "Standard":
            return "background-color: #1a1a3d"
        elif val == "Excellent":
            return "background-color: #1a2a1a"
        return ""

    st.dataframe(
        article_score_df.drop(columns=["Skor"]).style.map(
            highlight_article_status, subset=["Status"]
        ),
        use_container_width=True,
        hide_index=True
    )

    total_score = article_score_df["Skor"].sum()
    max_score = len(article_score_df) * 3
    pct = round(total_score / max_score * 100, 1) if max_score else 0
    connectivity = get_connectivity_pct(total_score / len(article_score_df) if article_score_df.shape[0] else 0)
    st.caption(f"Total skor: {total_score} / {max_score} — Skor keterhubungan: {pct}% ({connectivity['status']})")

    st.divider()

    st.subheader("🔴 Orphaned Content (Global)")
    st.caption("Artikel yang belum mendapat satu pun internal link dari seluruh artikel dalam list.")
    if stats["orphan_nodes"]:
        orphan_data = []
        for url in stats["orphan_nodes"]:
            meta = cluster_meta.get(url, {})
            orphan_data.append({
                "Keyword": meta.get("keyword", ""),
                "URL": url,
                "Modul": meta.get("module", ""),
                "Fitur": meta.get("feature", ""),
                "Intent": meta.get("intent", ""),
                "Outbound": G.out_degree(url),
            })
        orphan_df = pd.DataFrame(orphan_data).sort_values(["Modul", "Fitur"])
        st.dataframe(orphan_df, use_container_width=True, hide_index=True)
    else:
        st.success("Semua artikel sudah mendapat minimal 1 inbound link!")

    st.divider()
    st.subheader("🔗 Semua Internal Link")
    st.caption("Seluruh internal link antar artikel dalam list.")

    if st.session_state.edges:
        link_data = []
        for edge in st.session_state.edges.get("internal", []):
            source = edge.get("source", "")
            target = edge.get("target", "")
            if source not in cluster_meta or target not in cluster_meta:
                continue
            source_meta = cluster_meta.get(source, {})
            target_meta = cluster_meta.get(target, {})
            link_data.append({
                "Page URL": source,
                "Modul": source_meta.get("module", ""),
                "Fitur": source_meta.get("feature", ""),
                "Destination URL": target,
                "Modul Tujuan": target_meta.get("module", ""),
                "Fitur Tujuan": target_meta.get("feature", ""),
                "Anchor Text": edge.get("anchor", ""),
            })
        if link_data:
            link_df = pd.DataFrame(link_data).sort_values(["Modul", "Fitur"])
            st.dataframe(link_df, use_container_width=True, hide_index=True)
        else:
            st.info("Tidak ada internal link ditemukan.")


# ══════════════════════════════════════════════
# TAB 2 — Detail Modul & Fitur
# ══════════════════════════════════════════════

with tab2:
    st.subheader("🔍 Detail Modul & Fitur")

    col1, col2 = st.columns(2)
    with col1:
        module_opts_t2 = ["Semua Modul"] + options["modules"]
        selected_module = st.selectbox(
            "Pilih Modul",
            module_opts_t2,
            index=module_opts_t2.index(st.session_state.get("sel_module_tab3", "Semua Modul"))
            if st.session_state.get("sel_module_tab3", "Semua Modul") in module_opts_t2 else 0,
            key="sel_module"
        )
    with col2:
        if selected_module == "Semua Modul":
            all_features = sorted(set(
                f for feats in options["features"].values() for f in feats
            ))
            feature_opts = ["Semua Fitur"] + all_features
        else:
            feature_opts = ["Semua Fitur"] + options["features"].get(selected_module, [])
        selected_feature = st.selectbox(
            "Pilih Fitur",
            feature_opts,
            index=feature_opts.index(st.session_state.get("sel_feature_tab3", "Semua Fitur"))
            if st.session_state.get("sel_feature_tab3", "Semua Fitur") in feature_opts else 0,
            key="sel_feature"
        )

    # Filter URL
    filtered_urls = set()
    for url, meta in cluster_meta.items():
        if selected_module != "Semua Modul" and meta.get("module") != selected_module:
            continue
        if selected_feature != "Semua Fitur" and meta.get("feature") != selected_feature:
            continue
        if G.has_node(url):
            filtered_urls.add(url)

    if not filtered_urls:
        st.warning("Tidak ada artikel ditemukan untuk filter ini.")
    else:
        subG = G.subgraph(filtered_urls)
        sub_stats = get_graph_stats(subG)

        col1, col2, col3, col4 = st.columns(4)
        sub_terhubung = sub_stats["total_nodes"] - len(sub_stats["orphan_nodes"])
        sub_skor = round(sub_terhubung / sub_stats["total_nodes"] * 100, 1) if sub_stats["total_nodes"] else 0

        sub_all_scores = [get_inbound_score(subG.in_degree(u))["score"] for u in filtered_urls]
        sub_avg_score = sum(sub_all_scores) / len(sub_all_scores) if sub_all_scores else 0
        sub_connectivity_top = get_connectivity_pct(sub_avg_score)

        col1.metric("Artikel dalam cluster", sub_stats["total_nodes"])
        col2.metric("Link internal", sub_stats["total_edges"])
        col3.metric("Orphaned Content", len(sub_stats["orphan_nodes"]))
        col4.metric("Skor keterhubungan", f"{sub_connectivity_top['pct']}% ({sub_connectivity_top['status']})")
        st.caption("Link dihitung hanya dari sesama artikel dalam modul/fitur yang dipilih.")

        st.divider()

        show_graph = st.checkbox("🕸️ Tampilkan graf", value=True, key="show_graph_2")

        if show_graph:
            net = Network(
                height="700px", width="100%",
                directed=True, bgcolor="#0e1117", font_color="white"
            )
            net.force_atlas_2based(
                gravity=-50,
                central_gravity=0.01,
                spring_length=200,
                spring_strength=0.08,
                damping=0.4,
                overlap=1
            )

            orphan_set = set(sub_stats["orphan_nodes"])
            
            # Hub per fitur — top 1 inbound per fitur = pillar
            hub_set = set()
            feature_groups = {}
            for url in filtered_urls:
                feature = cluster_meta.get(url, {}).get("feature", "")
                if feature not in feature_groups:
                    feature_groups[feature] = []
                feature_groups[feature].append(url)
            
            for feature, urls in feature_groups.items():
                if urls:
                    pillar = max(urls, key=lambda u: subG.in_degree(u))
                    if subG.in_degree(pillar) > 0:
                        hub_set.add(pillar)

            for node in subG.nodes():
                meta = cluster_meta.get(node, {})
                keyword = meta.get("keyword", "") or node.split("/")[-1]
                intent = meta.get("intent", "")
                in_deg = subG.in_degree(node)
                out_deg = subG.out_degree(node)

                label = (keyword[:25] + "...") if len(keyword) > 25 else keyword
                feature = cluster_meta.get(node, {}).get("feature", "")
                module = cluster_meta.get(node, {}).get("module", "")
                tooltip = (
                    f"{keyword}\n"
                    f"Modul: {module} › {feature}\n"
                    f"Intent: {intent}\n"
                    f"Inbound: {in_deg} | Outbound: {out_deg}\n"
                    f"URL: {node}"
                )

                base_color = INTENT_COLORS.get(intent, DEFAULT_COLOR)
                if node in hub_set:
                    label = f"★ {keyword[:22]}..." if len(keyword) > 22 else f"★ {keyword}"
                    color = {
                        "background": base_color,
                        "border": "#B44FFF",
                        "highlight": {"background": base_color, "border": "#B44FFF"}
                    }
                    size = 30
                    net.add_node(node, label=label, title=tooltip, color=color, size=size, borderWidth=4)
                elif node in orphan_set:
                    label = f"✕ {keyword[:22]}..." if len(keyword) > 22 else f"✕ {keyword}"
                    color = {
                        "background": base_color,
                        "border": "#FF4B4B",
                        "highlight": {"background": base_color, "border": "#FF4B4B"}
                    }
                    size = 15
                    net.add_node(node, label=label, title=tooltip, color=color, size=size, borderWidth=4)
                else:
                    color = base_color
                    size = 15 + in_deg * 3
                    net.add_node(node, label=label, title=tooltip, color=color, size=size)

            for source, target, data in subG.edges(data=True):
                source_keyword = cluster_meta.get(source, {}).get("keyword", source.split("/")[-1])
                target_keyword = cluster_meta.get(target, {}).get("keyword", target.split("/")[-1])
                anchor = data.get("anchor", "")
                edge_tooltip = f"{source_keyword}\n→ {target_keyword}"
                if anchor:
                    edge_tooltip += f"\nAnchor: {anchor}"
                net.add_edge(source, target, color="#4B61DD", arrows="to", width=2.5, title=edge_tooltip)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp:
                net.save_graph(tmp.name)
                with open(tmp.name, "r", encoding="utf-8") as f:
                    html = f.read()
            os.unlink(tmp.name)

            components.html(html, height=520, scrolling=False)
            st.caption(
                "★ Pillar (border ungu)  |  "
                "✕ Yatim dalam modul (border merah)  |  "
                "🔵 Informational  |  "
                "🟢 Commercial  |  "
                "🟠 Transactional"
            )

        st.divider()

        st.subheader("📋 Daftar Artikel")
        cluster_data = []
        for url in filtered_urls:
            meta = cluster_meta.get(url, {})
            inbound = subG.in_degree(url)
            scoring = get_inbound_score(inbound)
            cluster_data.append({
                "Keyword": meta.get("keyword", ""),
                "URL": url,
                "Modul": meta.get("module", ""),
                "Fitur": meta.get("feature", ""),
                "Intent": meta.get("intent", ""),
                "Bahasa": meta.get("language", ""),
                "Inbound": inbound,
                "Outbound": subG.out_degree(url),
                "Skor": scoring["score"],
                "Status": scoring["status"],
            })
        cluster_df = pd.DataFrame(cluster_data).sort_values("Inbound", ascending=False)
        st.markdown(
            """
            <div style="font-size: 14px; color: var(--text-color-light, #888); margin-bottom: 8px;">
            <span style="background-color: #3d1a1a; color: #ff8080; padding: 2px 8px; border-radius: 4px;">Orphaned</span>
            0 inbound, skor 0 &nbsp;&middot;&nbsp;
            <span style="background-color: #3d3010; color: #ffd966; padding: 2px 8px; border-radius: 4px;">Low</span>
            1-2 inbound, skor 1 &nbsp;&middot;&nbsp;
            <span style="background-color: #1a1a3d; color: #8080ff; padding: 2px 8px; border-radius: 4px;">Standard</span>
            3-4 inbound, skor 2 &nbsp;&middot;&nbsp;
            <span style="background-color: #1a2a1a; color: #80ff80; padding: 2px 8px; border-radius: 4px;">Excellent</span>
            5+ inbound, skor 3
            </div>
            """,
            unsafe_allow_html=True
        )

        def highlight_cluster_status(val):
            if val == "Orphaned":
                return "background-color: #3d1a1a"
            elif val == "Low":
                return "background-color: #3d3010"
            elif val == "Standard":
                return "background-color: #1a1a3d"
            elif val == "Excellent":
                return "background-color: #1a2a1a"
            return ""

        st.dataframe(
            cluster_df.drop(columns=["Skor"]).style.map(
                highlight_cluster_status, subset=["Status"]
            ),
            use_container_width=True,
            hide_index=True
        )

        sub_scores = [get_inbound_score(subG.in_degree(u))["score"] for u in filtered_urls]
        sub_total = sum(sub_scores)
        sub_max = len(sub_scores) * 3
        sub_pct = round(sub_total / sub_max * 100, 1) if sub_max else 0
        sub_connectivity = get_connectivity_pct(sum(sub_scores) / len(sub_scores) if sub_scores else 0)
        st.caption(f"Total skor: {sub_total} / {sub_max} — Skor keterhubungan: {sub_pct}% ({sub_connectivity['status']})")

        st.divider()
        st.subheader("🔗 Semua Internal Link dalam Cluster Ini")

        if st.session_state.edges:
            sub_link_data = []
            for edge in st.session_state.edges.get("internal", []):
                source = edge.get("source", "")
                target = edge.get("target", "")
                if source not in filtered_urls or target not in filtered_urls:
                    continue
                source_meta = cluster_meta.get(source, {})
                target_meta = cluster_meta.get(target, {})
                sub_link_data.append({
                    "Page URL": source,
                    "Fitur": source_meta.get("feature", ""),
                    "Destination URL": target,
                    "Fitur Tujuan": target_meta.get("feature", ""),
                    "Anchor Text": edge.get("anchor", ""),
                })
            if sub_link_data:
                sub_link_df = pd.DataFrame(sub_link_data).sort_values("Fitur")
                st.dataframe(sub_link_df, use_container_width=True, hide_index=True)
            else:
                st.info("Tidak ada internal link dalam cluster ini.")

        if sub_stats["orphan_nodes"]:
            st.divider()
            st.subheader("🔴 Orphaned Content dalam Modul Ini")
            st.caption("Artikel yang 0 inbound dari sesama artikel dalam modul/fitur yang dipilih.")
            orphan_data = []
            for url in sub_stats["orphan_nodes"]:
                meta = cluster_meta.get(url, {})
                orphan_data.append({
                    "Keyword": meta.get("keyword", ""),
                    "URL": url,
                    "Fitur": meta.get("feature", ""),
                    "Intent": meta.get("intent", ""),
                    "Outbound": subG.out_degree(url),
                })
            st.dataframe(pd.DataFrame(orphan_data), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════
# TAB 3 — Detail Artikel
# ══════════════════════════════════════════════

with tab3:
    st.subheader("📄 Detail Artikel")

    col1, col2 = st.columns(2)
    with col1:
        module_opts = ["Semua Modul"] + options["modules"]
        tab3_module = st.selectbox(
            "Filter by Modul",
            module_opts,
            index=module_opts.index(st.session_state.get("sel_module", "Semua Modul"))
            if st.session_state.get("sel_module", "Semua Modul") in module_opts else 0,
            key="sel_module_tab3"
        )

    with col2:
        if tab3_module == "Semua Modul":
            all_feats = sorted(set(
                f for feats in options["features"].values() for f in feats
            ))
            feat_opts = ["Semua Fitur"] + all_feats
        else:
            feat_opts = ["Semua Fitur"] + options["features"].get(tab3_module, [])

        tab3_feature = st.selectbox(
            "Filter by Fitur",
            feat_opts,
            index=feat_opts.index(st.session_state.get("sel_feature", "Semua Fitur"))
            if st.session_state.get("sel_feature", "Semua Fitur") in feat_opts else 0,
            key="sel_feature_tab3"
        )

    article_options = {}
    for url, meta in cluster_meta.items():
        if tab3_module != "Semua Modul" and meta.get("module") != tab3_module:
            continue
        if tab3_feature != "Semua Fitur" and meta.get("feature") != tab3_feature:
            continue
        keyword = meta.get("keyword", "")
        label = f"{keyword} ({meta.get('module', '')} › {meta.get('feature', '')})"
        article_options[label] = url

    filtered_options = article_options

    if not filtered_options:
        st.warning("Artikel tidak ditemukan.")
    else:
        selected_label = st.selectbox(
            "Pilih artikel",
            [""] + list(filtered_options.keys()),
            format_func=lambda x: "Pilih artikel..." if x == "" else x,
            key="sel_article"
        )

        if not selected_label:
            st.info("Pilih artikel di atas untuk melihat detail inbound, outbound, dan gap.")
            st.stop()

        selected_url = filtered_options[selected_label]
        detail = get_article_detail(G, selected_url, cluster_meta)

        if not detail:
            st.error("Artikel tidak ditemukan di graf.")
        else:
            meta = detail["meta"]

            st.divider()
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Modul", meta.get("module", "-"))
            col2.metric("Fitur", meta.get("feature", "-"))
            col3.metric("Intent", meta.get("intent", "-"))
            col4.metric("Bahasa", meta.get("language", "-"))

            col1, col2, col3 = st.columns(3)
            col1.metric("Inbound", len(detail["inbound"]))
            col2.metric("Outbound", len(detail["outbound"]))
            col3.metric("Gap", len(detail["missing_same_feature"]) + len(detail["missing_same_module"]))

            st.caption(f"🔗 {selected_url}")

            st.divider()

            col_in, col_out = st.columns(2)

            CATEGORY_COLORS = {
                "Sesama Fitur": "#1a2a1a",
                "Sesama Modul": "#1a1a3d",
                "Beda Modul": "#2a2a2a",
            }
            CATEGORY_TEXT_COLORS = {
                "Sesama Fitur": "#80ff80",
                "Sesama Modul": "#8080ff",
                "Beda Modul": "#aaaaaa",
            }

            with col_in:
                st.subheader(f"⬅️ Inbound ({len(detail['inbound'])})")
                st.caption("Artikel yang memberikan link ke artikel ini.")
                st.markdown(
                    """
                    <div style="font-size: 13px; margin-bottom: 8px;">
                    <span style="background-color: #1a2a1a; color: #80ff80; padding: 2px 8px; border-radius: 4px;">Sesama Fitur</span>
                    &nbsp;&middot;&nbsp;
                    <span style="background-color: #1a1a3d; color: #8080ff; padding: 2px 8px; border-radius: 4px;">Sesama Modul</span>
                    &nbsp;&middot;&nbsp;
                    <span style="background-color: #2a2a2a; color: #aaaaaa; padding: 2px 8px; border-radius: 4px;">Beda Modul</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                if detail["inbound"]:
                    for item in detail["inbound"]:
                        item_meta = cluster_meta.get(item["url"], {})
                        keyword = item_meta.get("keyword", item["url"].split("/")[-1])
                        cat = item["category"]
                        bg = CATEGORY_COLORS.get(cat, "#2a2a2a")
                        fg = CATEGORY_TEXT_COLORS.get(cat, "#aaaaaa")
                        st.markdown(
                            f'<div style="border-left: 4px solid {fg}; padding-left: 8px; margin-bottom: 4px;">'
                            f'<span style="font-size: 11px; color: {fg};">{cat}</span></div>',
                            unsafe_allow_html=True
                        )
                        with st.expander(f"**{keyword}**"):
                            st.caption(f"Anchor: *{item['anchor']}*" if item["anchor"] else "Anchor: -")
                            st.caption(f"Modul: {item['module']} › {item['feature']}")
                            st.caption(f"URL: {item['url']}")
                else:
                    st.warning("Belum ada artikel yang link ke sini.")

            with col_out:
                st.subheader(f"➡️ Outbound ({len(detail['outbound'])})")
                st.caption("Artikel yang mendapat link dari artikel ini.")
                st.markdown(
                    """
                    <div style="font-size: 13px; margin-bottom: 8px;">
                    <span style="background-color: #1a2a1a; color: #80ff80; padding: 2px 8px; border-radius: 4px;">Sesama Fitur</span>
                    &nbsp;&middot;&nbsp;
                    <span style="background-color: #1a1a3d; color: #8080ff; padding: 2px 8px; border-radius: 4px;">Sesama Modul</span>
                    &nbsp;&middot;&nbsp;
                    <span style="background-color: #2a2a2a; color: #aaaaaa; padding: 2px 8px; border-radius: 4px;">Beda Modul</span>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                if detail["outbound"]:
                    for item in detail["outbound"]:
                        item_meta = cluster_meta.get(item["url"], {})
                        keyword = item_meta.get("keyword", item["url"].split("/")[-1])
                        cat = item["category"]
                        bg = CATEGORY_COLORS.get(cat, "#2a2a2a")
                        fg = CATEGORY_TEXT_COLORS.get(cat, "#aaaaaa")
                        st.markdown(
                            f'<div style="border-left: 4px solid {fg}; padding-left: 8px; margin-bottom: 4px;">'
                            f'<span style="font-size: 11px; color: {fg};">{cat}</span></div>',
                            unsafe_allow_html=True
                        )
                        with st.expander(f"**{keyword}**"):
                            st.caption(f"Anchor: *{item['anchor']}*" if item["anchor"] else "Anchor: -")
                            st.caption(f"Modul: {item['module']} › {item['feature']}")
                            st.caption(f"URL: {item['url']}")
                else:
                    st.warning("Artikel ini belum link ke artikel lain dalam list.")

            st.divider()

            total_gap = len(detail["missing_same_feature"]) + len(detail["missing_same_module"])
            st.subheader(f"❌ Gap ({total_gap})")

            st.caption(f"Sesama Fitur: {meta.get('feature', '')} ({len(detail['missing_same_feature'])})")
            if detail["missing_same_feature"]:
                cols = st.columns(3)
                for i, item in enumerate(detail["missing_same_feature"]):
                    with cols[i % 3]:
                        with st.expander(f"**{item['keyword'] or item['url'].split('/')[-1]}**"):
                            st.caption(f"Intent: {item['intent']}")
                            st.caption(f"URL: {item['url']}")
            else:
                st.success("Semua artikel sesama fitur sudah terhubung!")

            st.divider()

            st.caption(f"Sesama Modul: {meta.get('module', '')}, Beda Fitur ({len(detail['missing_same_module'])})")
            if detail["missing_same_module"]:
                cols = st.columns(3)
                for i, item in enumerate(detail["missing_same_module"]):
                    with cols[i % 3]:
                        with st.expander(f"**{item['keyword'] or item['url'].split('/')[-1]}**"):
                            st.caption(f"Fitur: {item['feature']}")
                            st.caption(f"Intent: {item['intent']}")
                            st.caption(f"URL: {item['url']}")
            else:
                st.success("Semua artikel sesama modul sudah terhubung atau artikel kurang!")