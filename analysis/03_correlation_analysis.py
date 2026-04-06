"""
03_correlation_analysis.py
For pairs with both PPI and GI: correlation analysis and complex-level breakdown.
Inputs: processed_data.pkl, overlap_results.pkl
Outputs: correlation_results.pkl, per_cluster_correlations.csv
"""

import pickle
import csv
import numpy as np
from scipy.stats import spearmanr, pearsonr
from collections import defaultdict

OUT_DIR = "/Users/han-yingjhuang/Claude_projects/Genetic-interaction/analysis"

print("Loading data...")
with open(f"{OUT_DIR}/processed_data.pkl", "rb") as f:
    d = pickle.load(f)
with open(f"{OUT_DIR}/overlap_results.pkl", "rb") as f:
    ov = pickle.load(f)

node_meta = d["node_meta"]
both_pairs = ov["both_pairs"]

# ── Global correlation: PPI score vs |GI epsilon| ────────────────────────────
ppi_scores = np.array([p["score"] for _, p, _ in both_pairs])
gi_eps = np.array([s["mean_eps"] for _, _, s in both_pairs])
gi_abs_eps = np.abs(gi_eps)

sp_r, sp_p = spearmanr(ppi_scores, gi_abs_eps)
pe_r, pe_p = pearsonr(ppi_scores, gi_abs_eps)

print(f"\n=== GLOBAL CORRELATION (n={len(both_pairs)}) ===")
print(f"PPI score vs |GI epsilon|:")
print(f"  Spearman r={sp_r:.4f}, p={sp_p:.2e}")
print(f"  Pearson  r={pe_r:.4f}, p={pe_p:.2e}")

# Separate by GI direction
pos_gi = [(p, s) for _, p, s in both_pairs if s["mean_eps"] > 0]
neg_gi = [(p, s) for _, p, s in both_pairs if s["mean_eps"] < 0]

if pos_gi:
    ppi_pos = np.array([p["score"] for p, s in pos_gi])
    gi_pos = np.array([abs(s["mean_eps"]) for p, s in pos_gi])
    sp_r_pos, sp_p_pos = spearmanr(ppi_pos, gi_pos)
    print(f"\nPositive GI only (n={len(pos_gi)}): Spearman r={sp_r_pos:.4f}, p={sp_p_pos:.2e}")

if neg_gi:
    ppi_neg = np.array([p["score"] for p, s in neg_gi])
    gi_neg = np.array([abs(s["mean_eps"]) for p, s in neg_gi])
    sp_r_neg, sp_p_neg = spearmanr(ppi_neg, gi_neg)
    print(f"Negative GI only (n={len(neg_gi)}): Spearman r={sp_r_neg:.4f}, p={sp_p_neg:.2e}")

# ── Within- vs between-cluster correlation ────────────────────────────────────
def get_cluster(sgd_id):
    if sgd_id in node_meta:
        return node_meta[sgd_id]["cluster_id"]
    return ""

both_within = ov["both_within"]
both_between = ov["both_between"]

if both_within:
    ppi_w = np.array([p["score"] for _, p, _ in both_within])
    gi_w = np.abs(np.array([s["mean_eps"] for _, _, s in both_within]))
    sp_r_w, sp_p_w = spearmanr(ppi_w, gi_w)
    print(f"\nWithin-cluster pairs (n={len(both_within)}): Spearman r={sp_r_w:.4f}, p={sp_p_w:.2e}")
    print(f"  Mean PPI score: {np.mean(ppi_w):.2f}, Mean |eps|: {np.mean(gi_w):.4f}")

if both_between:
    ppi_b = np.array([p["score"] for _, p, _ in both_between])
    gi_b = np.abs(np.array([s["mean_eps"] for _, _, s in both_between]))
    sp_r_b, sp_p_b = spearmanr(ppi_b, gi_b)
    print(f"\nBetween-cluster pairs (n={len(both_between)}): Spearman r={sp_r_b:.4f}, p={sp_p_b:.2e}")
    print(f"  Mean PPI score: {np.mean(ppi_b):.2f}, Mean |eps|: {np.mean(gi_b):.4f}")

# ── Per-cluster correlation ────────────────────────────────────────────────────
print("\nComputing per-cluster correlations...")

# Group 'Both' pairs by shared cluster (require both partners in same cluster)
cluster_data = defaultdict(list)  # cluster_id -> [(ppi_score, gi_eps)]

for key, ppi_data, sga_data in both_pairs:
    ids = list(key)
    c1 = get_cluster(ids[0])
    c2 = get_cluster(ids[1])
    if c1 and c2 and c1 == c2:
        cluster_data[c1].append((ppi_data["score"], sga_data["mean_eps"]))

print(f"  Clusters with both PPI and GI pairs: {len(cluster_data)}")

# Per-cluster stats (require >= 3 pairs for correlation)
cluster_stats = []
for cid, pairs in cluster_data.items():
    n = len(pairs)
    ppi_s = np.array([p[0] for p in pairs])
    gi_s = np.array([p[1] for p in pairs])
    gi_abs = np.abs(gi_s)

    # Get cluster gene names
    cluster_genes = [node_meta[sid]["gene_name"] for sid in node_meta
                     if node_meta[sid]["cluster_id"] == cid][:5]
    cluster_name = "/".join(cluster_genes[:3]) if cluster_genes else f"Cluster_{cid}"

    mean_ppi = float(np.mean(ppi_s))
    mean_eps = float(np.mean(gi_abs))
    frac_pos = float(np.mean(gi_s > 0))
    frac_neg = float(np.mean(gi_s < 0))

    if n >= 3:
        sp_r_c, sp_p_c = spearmanr(ppi_s, gi_abs)
    else:
        sp_r_c, sp_p_c = float("nan"), float("nan")

    cluster_stats.append({
        "cluster_id": cid,
        "cluster_name": cluster_name,
        "n_pairs": n,
        "mean_ppi_score": mean_ppi,
        "mean_abs_eps": mean_eps,
        "frac_pos_gi": frac_pos,
        "frac_neg_gi": frac_neg,
        "spearman_r": sp_r_c,
        "spearman_p": sp_p_c,
    })

# Sort by number of pairs
cluster_stats.sort(key=lambda x: x["n_pairs"], reverse=True)

print(f"\nTop 20 clusters by number of Both pairs:")
print(f"{'Cluster':<12} {'Name':<30} {'n':>5} {'PPI':>6} {'|eps|':>7} {'%pos':>6} {'r':>7} {'p':>8}")
for cs in cluster_stats[:20]:
    r = f"{cs['spearman_r']:.3f}" if not np.isnan(cs['spearman_r']) else " N/A "
    p = f"{cs['spearman_p']:.2e}" if not np.isnan(cs['spearman_p']) else "  N/A  "
    print(f"{cs['cluster_id']:<12} {cs['cluster_name']:<30} {cs['n_pairs']:>5} "
          f"{cs['mean_ppi_score']:>6.2f} {cs['mean_abs_eps']:>7.4f} "
          f"{100*cs['frac_pos_gi']:>5.1f}% {r:>7} {p:>8}")

# ── FDR-based PPI score cutoff analysis ──────────────────────────────────────
print("\nCorrelation by PPI score threshold:")
for threshold in [2, 3, 4, 5, 6, 8]:
    sub = [(p, s) for _, p, s in both_pairs if p["score"] >= threshold]
    if len(sub) >= 5:
        ppi_t = np.array([p["score"] for p, s in sub])
        gi_t = np.abs(np.array([s["mean_eps"] for p, s in sub]))
        sp_r_t, sp_p_t = spearmanr(ppi_t, gi_t)
        print(f"  PPI score >= {threshold}: n={len(sub)}, r={sp_r_t:.4f}, p={sp_p_t:.2e}")

# ── Save ──────────────────────────────────────────────────────────────────────
corr_results = {
    "global_spearman_r": sp_r,
    "global_spearman_p": sp_p,
    "global_pearson_r": pe_r,
    "global_pearson_p": pe_p,
    "pos_gi_spearman_r": sp_r_pos if pos_gi else None,
    "neg_gi_spearman_r": sp_r_neg if neg_gi else None,
    "within_cluster_spearman_r": sp_r_w if both_within else None,
    "between_cluster_spearman_r": sp_r_b if both_between else None,
    "cluster_stats": cluster_stats,
    "ppi_scores": ppi_scores.tolist(),
    "gi_eps": gi_eps.tolist(),
}

with open(f"{OUT_DIR}/correlation_results.pkl", "wb") as f:
    pickle.dump(corr_results, f)

# Save cluster stats CSV
fieldnames = ["cluster_id", "cluster_name", "n_pairs", "mean_ppi_score",
              "mean_abs_eps", "frac_pos_gi", "frac_neg_gi", "spearman_r", "spearman_p"]
with open(f"{OUT_DIR}/per_cluster_correlations.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(cluster_stats)

print(f"\nSaved correlation_results.pkl and per_cluster_correlations.csv")
print("Done.")
