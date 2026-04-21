"""Rebuild yeast master pair table with all four architecture classes.

The original build/40 script restricts the universe to
`ppi_pairs | indirect_same_complex_pairs`, which silently drops every
SGA-tested pair that is neither same-complex nor co-purified. Those pairs
are the fourth architecture class (inter non-co-purified). This rebuild
expands the universe to include every SGA-tested pair so the fourth
class keeps its SGA-measured epsilon values.

Inputs: build/processed_data.pkl, build/three_class_results.pkl,
build/disease_ortholog_results.pkl.

Output: build/phase1_yeast_master_pairs_v66.{pkl,csv}. The original
non-v66 files are preserved.
"""
import math
import pickle
from collections import Counter
from pathlib import Path

import networkx as nx
import pandas as pd

ROOT = Path(
    "/Users/han-yingjhuang/Library/Mobile Documents/"
    "com~apple~CloudDocs/Business - Projects/Genetic-interaction"
)
BUILD = ROOT / "build"
PROCESSED_PATH = BUILD / "processed_data.pkl"
THREE_CLASS_PATH = BUILD / "three_class_results.pkl"
DISEASE_PATH = BUILD / "disease_ortholog_results.pkl"

OUT_PKL = BUILD / "phase1_yeast_master_pairs_v66.pkl"
OUT_CSV = BUILD / "phase1_yeast_master_pairs_v66.csv"


def pair_tuple(pair_key):
    return tuple(sorted(pair_key))


def safe_strip(value):
    if value is None:
        return ""
    return str(value).strip()


def safe_mean(values):
    clean = [v for v in values if v is not None
             and not (isinstance(v, float) and math.isnan(v))]
    if not clean:
        return math.nan
    return sum(clean) / len(clean)


def load_pickle(path):
    with path.open("rb") as handle:
        return pickle.load(handle)


def build_ppi_graph(ppi_pairs, gi_nodes):
    graph = nx.Graph()
    for pair_key in ppi_pairs:
        gene_a, gene_b = pair_tuple(pair_key)
        graph.add_edge(gene_a, gene_b)
    degree_map = dict(graph.degree())
    sub_nodes = sorted(set(graph.nodes()) & gi_nodes)
    subgraph = graph.subgraph(sub_nodes).copy()
    print(
        f"[ppi-graph] betweenness on GI subgraph: "
        f"{subgraph.number_of_nodes():,} nodes, "
        f"{subgraph.number_of_edges():,} edges"
    )
    betweenness = nx.betweenness_centrality(subgraph, normalized=True)
    return degree_map, betweenness


def compute_cluster_size_map(node_meta):
    counts = Counter()
    for meta in node_meta.values():
        cluster_id = safe_strip(meta.get("cluster_id", ""))
        if cluster_id:
            counts[cluster_id] += 1
    return dict(counts)


def compute_indirect_same_complex_pairs(sga_pairs, ppi_pairs, node_meta):
    indirect = set()
    for pair_key in sga_pairs:
        if pair_key in ppi_pairs:
            continue
        gene_a, gene_b = pair_tuple(pair_key)
        cluster_a = safe_strip(node_meta.get(gene_a, {}).get("cluster_id", ""))
        cluster_b = safe_strip(node_meta.get(gene_b, {}).get("cluster_id", ""))
        if cluster_a and cluster_a == cluster_b:
            indirect.add(pair_key)
    return indirect


def main():
    print("[load] processed_data, disease")
    processed = load_pickle(PROCESSED_PATH)
    disease = load_pickle(DISEASE_PATH)

    ppi_pairs = processed["ppi_pairs"]
    sga_pairs = processed["sga_pairs"]
    node_meta = processed["node_meta"]
    disease_per_gene = disease["per_gene"]

    print(
        f"[input] ppi_pairs={len(ppi_pairs):,} "
        f"sga_pairs={len(sga_pairs):,} "
        f"node_meta={len(node_meta):,}"
    )

    indirect_same_complex_pairs = compute_indirect_same_complex_pairs(
        sga_pairs, ppi_pairs, node_meta
    )
    try:
        three_class = load_pickle(THREE_CLASS_PATH)
        cached_indirect = set(
            three_class["class_summary"]["indirect_same_complex"]["pair_keys"]
        )
        if indirect_same_complex_pairs != cached_indirect:
            print(
                f"[warn] computed indirect differs from cached "
                f"({len(indirect_same_complex_pairs):,} vs "
                f"{len(cached_indirect):,})"
            )
    except (NotImplementedError, ModuleNotFoundError, AttributeError) as exc:
        print(f"[warn] skipping three_class sanity check: {exc!r}")

    gi_nodes = set()
    for pair_key in sga_pairs:
        gi_nodes.update(pair_key)
    degree_map, betweenness_map = build_ppi_graph(ppi_pairs, gi_nodes)
    cluster_size_map = compute_cluster_size_map(node_meta)

    # Expanded universe: every SGA-tested pair + every PPI-detected pair.
    # The added set(sga_pairs) is what restores the fourth arch class.
    universe_keys = set(ppi_pairs) | indirect_same_complex_pairs | set(sga_pairs)
    print(
        f"[universe] ppi={len(ppi_pairs):,} "
        f"indirect={len(indirect_same_complex_pairs):,} "
        f"sga={len(sga_pairs):,} "
        f"union={len(universe_keys):,}"
    )

    rows = []
    for pair_key in sorted(universe_keys, key=pair_tuple):
        gene_a, gene_b = pair_tuple(pair_key)
        meta_a = node_meta.get(gene_a, {})
        meta_b = node_meta.get(gene_b, {})
        ppi_info = ppi_pairs.get(pair_key)
        sga_info = sga_pairs.get(pair_key)

        name_a = (
            safe_strip(meta_a.get("gene_name", ""))
            or safe_strip(ppi_info.get("gene_a", "") if ppi_info else "")
            or gene_a
        )
        name_b = (
            safe_strip(meta_b.get("gene_name", ""))
            or safe_strip(ppi_info.get("gene_b", "") if ppi_info else "")
            or gene_b
        )

        cluster_id_a = safe_strip(meta_a.get("cluster_id", ""))
        cluster_id_b = safe_strip(meta_b.get("cluster_id", ""))
        same_complex = bool(cluster_id_a) and cluster_id_a == cluster_id_b

        gi_tested = sga_info is not None
        gi_sig_p05 = bool(sga_info["sig_p05"]) if gi_tested else False
        gi_sig_p01 = bool(sga_info["sig_p01"]) if gi_tested else False
        gi_pvalue = float(sga_info["min_pval"]) if gi_tested else math.nan
        gi_epsilon = float(sga_info["mean_eps"]) if gi_tested else math.nan
        abs_gi_epsilon = abs(gi_epsilon) if gi_tested else math.nan
        if not gi_tested:
            gi_sign = math.nan
        elif gi_sig_p05 and gi_epsilon > 0:
            gi_sign = 1
        elif gi_sig_p05 and gi_epsilon < 0:
            gi_sign = -1
        else:
            gi_sign = 0

        ppi_detected = ppi_info is not None
        ppi_score = float(ppi_info["score"]) if ppi_detected else math.nan
        ppi_inter_cluster = bool(ppi_info["inter_cluster"]) if ppi_detected else False

        direct_within_complex_ppi = bool(ppi_detected and same_complex)
        indirect_same_complex_noncontact = bool((not ppi_detected) and same_complex)
        between_complex_ppi = bool(ppi_detected and not same_complex)
        inter_non_copurified = bool(
            gi_tested and (not ppi_detected) and (not same_complex)
        )

        # Cleaner four-class logic: route SGA-tested pairs by PPI+complex
        # first, then non-tested PPI goes into ppi_only_no_gi.
        if gi_tested and direct_within_complex_ppi:
            arch_class = "direct_within_complex"
        elif gi_tested and between_complex_ppi:
            arch_class = "between_complex_PPI"
        elif gi_tested and indirect_same_complex_noncontact:
            arch_class = "indirect_same_complex_noncontact"
        elif gi_tested and inter_non_copurified:
            arch_class = "inter_non_copurified"
        elif (not gi_tested) and ppi_detected:
            arch_class = "ppi_only_no_gi"
        else:
            arch_class = "unclassified"

        degree_a = float(degree_map.get(gene_a, math.nan))
        degree_b = float(degree_map.get(gene_b, math.nan))
        bet_a = float(betweenness_map.get(gene_a, math.nan))
        bet_b = float(betweenness_map.get(gene_b, math.nan))
        complex_size_a = (
            float(cluster_size_map.get(cluster_id_a, math.nan))
            if cluster_id_a else math.nan
        )
        complex_size_b = (
            float(cluster_size_map.get(cluster_id_b, math.nan))
            if cluster_id_b else math.nan
        )

        disease_a = disease_per_gene.get(gene_a, {})
        disease_b = disease_per_gene.get(gene_b, {})
        has_human_ortholog_a = bool(disease_a.get("has_human_ortholog", False))
        has_human_ortholog_b = bool(disease_b.get("has_human_ortholog", False))
        has_disease_ortholog_a = bool(disease_a.get("has_disease_ortholog", False))
        has_disease_ortholog_b = bool(disease_b.get("has_disease_ortholog", False))

        rows.append({
            "gene_A": gene_a,
            "gene_B": gene_b,
            "name_A": name_a,
            "name_B": name_b,
            "GI_tested": gi_tested,
            "GI_significant_p05": gi_sig_p05,
            "GI_significant_p01": gi_sig_p01,
            "GI_pvalue": gi_pvalue,
            "GI_epsilon": gi_epsilon,
            "abs_GI_epsilon": abs_gi_epsilon,
            "GI_sign": gi_sign,
            "PPI_detected": ppi_detected,
            "PPI_score": ppi_score,
            "PPI_inter_cluster": ppi_inter_cluster,
            "same_complex": same_complex,
            "arch_class": arch_class,
            "direct_within_complex_PPI": direct_within_complex_ppi,
            "indirect_same_complex_noncontact": indirect_same_complex_noncontact,
            "between_complex_PPI": between_complex_ppi,
            "inter_non_copurified": inter_non_copurified,
            "degree_A": degree_a,
            "degree_B": degree_b,
            "mean_degree": safe_mean([degree_a, degree_b]),
            "betweenness_A": bet_a,
            "betweenness_B": bet_b,
            "mean_betweenness": safe_mean([bet_a, bet_b]),
            "complex_size_A": complex_size_a,
            "complex_size_B": complex_size_b,
            "mean_complex_size": safe_mean([complex_size_a, complex_size_b]),
            "cluster_id_A": cluster_id_a,
            "cluster_id_B": cluster_id_b,
            "has_human_ortholog_A": has_human_ortholog_a,
            "has_human_ortholog_B": has_human_ortholog_b,
            "has_disease_ortholog_A": has_disease_ortholog_a,
            "has_disease_ortholog_B": has_disease_ortholog_b,
            "disease_pair": bool(has_disease_ortholog_a and has_disease_ortholog_b),
            "ortholog_support": bool(has_human_ortholog_a and has_human_ortholog_b),
        })

    df = pd.DataFrame(rows).sort_values(
        ["arch_class", "gene_A", "gene_B"]
    ).reset_index(drop=True)
    df.to_pickle(OUT_PKL)
    df.to_csv(OUT_CSV, index=False)
    print(f"\n[saved] {OUT_PKL.name}  shape={df.shape}")
    print(f"[saved] {OUT_CSV.name}")

    # Per-class summary
    print("\n[summary] rows by arch_class:")
    summary = df.groupby("arch_class").agg(
        n=("GI_tested", "size"),
        n_gi_tested=("GI_tested", "sum"),
        n_gi_sig_p05=("GI_significant_p05", "sum"),
        n_pos=("GI_sign", lambda s: int((s == 1).sum())),
        n_neg=("GI_sign", lambda s: int((s == -1).sum())),
    )
    summary["gi_tested_rate"] = summary["n_gi_tested"] / summary["n"]
    summary["gi_sig_rate"] = (
        summary["n_gi_sig_p05"] / summary["n_gi_tested"].replace(0, math.nan)
    )
    print(summary.to_string())

    # Sanity check on the newly-recovered class.
    inco = df[df["arch_class"] == "inter_non_copurified"]
    n_eps = inco["GI_epsilon"].notna().sum()
    print(
        f"\n[check] inter_non_copurified: {len(inco):,} rows, "
        f"{n_eps:,} with non-NaN GI_epsilon"
    )
    assert n_eps == len(inco), (
        "inter_non_copurified rows must all carry an SGA epsilon"
    )
    print("[check] PASS")


if __name__ == "__main__":
    main()
