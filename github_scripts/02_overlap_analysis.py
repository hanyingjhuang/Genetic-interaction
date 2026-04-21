"""
02_overlap_analysis.py
Compute overlap between PPI and SGA datasets. Categorize pairs and compute statistics.
Inputs: analysis/processed_data.pkl
Outputs: analysis/overlap_results.pkl, analysis/overlap_stats.csv
"""

import pickle
import csv
from scipy.stats import mannwhitneyu, fisher_exact
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OUT_DIR = str(ROOT / "analysis")

print("Loading processed data...")
with open(f"{OUT_DIR}/processed_data.pkl", "rb") as f:
    d = pickle.load(f)

ppi_pairs = d["ppi_pairs"]
sga_pairs = d["sga_pairs"]
node_meta = d["node_meta"]

# ── Categorize all pairs ──────────────────────────────────────────────────────
# Universe: all pairs that appear in at least one dataset
# Category A: Both PPI and significant GI (p<0.05)
# Category B: GI only (SGA p<0.05, not in PPI)
# Category C: PPI only (in PPI, SGA not significant or not measured)
# Category D: PPI + non-significant GI (in PPI and SGA but p>=0.05)

both_pairs = []         # in PPI and SGA sig
gi_only_pairs = []      # SGA sig, not PPI
ppi_only_pairs = []     # PPI, no SGA sig
ppi_with_nonsig_gi = [] # PPI + measured but non-sig GI
ppi_unmeasured = []     # PPI, no SGA measurement at all

print("Categorizing pairs...")

for key, ppi_data in ppi_pairs.items():
    if key in sga_pairs:
        sga_data = sga_pairs[key]
        if sga_data["sig_p05"]:
            both_pairs.append((key, ppi_data, sga_data))
        else:
            ppi_with_nonsig_gi.append((key, ppi_data, sga_data))
    else:
        ppi_only_pairs.append((key, ppi_data))

for key, sga_data in sga_pairs.items():
    if key not in ppi_pairs and sga_data["sig_p05"]:
        gi_only_pairs.append((key, sga_data))

print(f"\n=== OVERLAP SUMMARY ===")
print(f"Total PPI pairs: {len(ppi_pairs):,}")
print(f"Total SGA pairs measured (with PPI partner): {len(sga_pairs):,}")
print(f"  Both PPI and significant GI (p<0.05): {len(both_pairs):,}")
print(f"  PPI + measured but non-significant GI: {len(ppi_with_nonsig_gi):,}")
print(f"  PPI only (no SGA measurement): {len(ppi_only_pairs):,}")
print(f"  Significant GI only (no PPI): {len(gi_only_pairs):,}")

# ── Signal analysis for 'disagreements' ───────────────────────────────────────
# Q: Do PPI-only pairs have lower GI epsilon scores than Both-pairs?
both_eps = [s["mean_eps"] for _, _, s in both_pairs]
nonsig_eps = [s["mean_eps"] for _, _, s in ppi_with_nonsig_gi]

if both_eps and nonsig_eps:
    stat, pval = mannwhitneyu(np.abs(both_eps), np.abs(nonsig_eps), alternative="greater")
    print(f"\nMann-Whitney U: |eps| for Both-pairs > PPI+nonsig-GI pairs")
    print(f"  U={stat:.0f}, p={pval:.2e}")
    print(f"  Mean |eps|: Both={np.mean(np.abs(both_eps)):.4f}, PPI+nonsig={np.mean(np.abs(nonsig_eps)):.4f}")

# ── PPI score for GI-only vs Both pairs ──────────────────────────────────────
both_ppi_scores = [p["score"] for _, p, _ in both_pairs]
ppi_only_scores = [p["score"] for _, p in ppi_only_pairs]
ppi_nonsig_scores = [p["score"] for _, p, _ in ppi_with_nonsig_gi]

print(f"\nPPI score distributions:")
print(f"  Both (PPI+GI): mean={np.mean(both_ppi_scores):.2f}, median={np.median(both_ppi_scores):.2f}")
print(f"  PPI+nonsig-GI: mean={np.mean(ppi_nonsig_scores):.2f}, median={np.median(ppi_nonsig_scores):.2f}")
if ppi_only_scores:
    print(f"  PPI-only (unmeasured): mean={np.mean(ppi_only_scores):.2f}, median={np.median(ppi_only_scores):.2f}")

# Test: do 'Both' pairs have higher PPI scores?
if both_ppi_scores and ppi_nonsig_scores:
    stat2, pval2 = mannwhitneyu(both_ppi_scores, ppi_nonsig_scores, alternative="greater")
    print(f"\nMann-Whitney U: PPI score for Both > PPI+nonsig")
    print(f"  U={stat2:.0f}, p={pval2:.2e}")

# ── Within-cluster vs between-cluster enrichment ──────────────────────────────
def get_cluster(sgd_id, meta):
    if sgd_id in meta:
        return meta[sgd_id]["cluster_id"]
    return ""

# For 'Both' pairs: within-cluster vs between-cluster
both_within_cluster = 0
both_between_cluster = 0
for key, ppi_data, sga_data in both_pairs:
    ids = list(key)
    c1 = get_cluster(ids[0], node_meta)
    c2 = get_cluster(ids[1], node_meta)
    if c1 and c2 and c1 == c2:
        both_within_cluster += 1
    else:
        both_between_cluster += 1

# For 'PPI+nonsig-GI' pairs
nonsig_within = 0
nonsig_between = 0
for key, ppi_data, sga_data in ppi_with_nonsig_gi:
    ids = list(key)
    c1 = get_cluster(ids[0], node_meta)
    c2 = get_cluster(ids[1], node_meta)
    if c1 and c2 and c1 == c2:
        nonsig_within += 1
    else:
        nonsig_between += 1

print(f"\nWithin-cluster vs between-cluster:")
print(f"  Both: within={both_within_cluster}, between={both_between_cluster}")
print(f"  PPI+nonsig: within={nonsig_within}, between={nonsig_between}")

# Fisher exact: are 'Both' enriched for between-cluster (inter-complex) pairs?
# (genetic interactions between complexes are more informative about between-pathway relationships)
contingency = [[both_between_cluster, both_within_cluster],
               [nonsig_between, nonsig_within]]
odds, pval_fe = fisher_exact(contingency)
print(f"  Fisher exact (between-cluster enrichment in Both vs nonsig): OR={odds:.2f}, p={pval_fe:.2e}")

# ── GI direction for 'Both' pairs ────────────────────────────────────────────
pos_gi = sum(1 for _, _, s in both_pairs if s["mean_eps"] > 0)
neg_gi = sum(1 for _, _, s in both_pairs if s["mean_eps"] < 0)
print(f"\nGI direction for Both pairs:")
print(f"  Positive GI (epsilon>0): {pos_gi} ({100*pos_gi/len(both_pairs):.1f}%)")
print(f"  Negative GI (epsilon<0): {neg_gi} ({100*neg_gi/len(both_pairs):.1f}%)")

# Within-cluster: more positive GI?
both_within = [(k, p, s) for k, p, s in both_pairs
               if get_cluster(list(k)[0], node_meta) == get_cluster(list(k)[1], node_meta)
               and get_cluster(list(k)[0], node_meta)]
both_between = [(k, p, s) for k, p, s in both_pairs
                if get_cluster(list(k)[0], node_meta) != get_cluster(list(k)[1], node_meta)
                or not get_cluster(list(k)[0], node_meta)]

if both_within:
    pos_within = sum(1 for _, _, s in both_within if s["mean_eps"] > 0)
    print(f"\nWithin-cluster Both pairs: {len(both_within)}, positive GI={pos_within} ({100*pos_within/len(both_within):.1f}%)")
if both_between:
    pos_between = sum(1 for _, _, s in both_between if s["mean_eps"] > 0)
    print(f"Between-cluster Both pairs: {len(both_between)}, positive GI={pos_between} ({100*pos_between/len(both_between):.1f}%)")

# ── Save results ──────────────────────────────────────────────────────────────
overlap_results = {
    "both_pairs": both_pairs,
    "gi_only_pairs": gi_only_pairs,
    "ppi_only_pairs": ppi_only_pairs,
    "ppi_with_nonsig_gi": ppi_with_nonsig_gi,
    "both_within_cluster": both_within_cluster,
    "both_between_cluster": both_between_cluster,
    "nonsig_within": nonsig_within,
    "nonsig_between": nonsig_between,
    "pos_gi": pos_gi,
    "neg_gi": neg_gi,
    "both_within": both_within,
    "both_between": both_between,
}

with open(f"{OUT_DIR}/overlap_results.pkl", "wb") as f:
    pickle.dump(overlap_results, f)

# Save summary CSV
rows = [
    ["Category", "Count", "Fraction of PPI"],
    ["PPI total", len(ppi_pairs), "1.000"],
    ["Both (PPI + significant GI)", len(both_pairs), f"{len(both_pairs)/len(ppi_pairs):.3f}"],
    ["PPI + measured non-significant GI", len(ppi_with_nonsig_gi), f"{len(ppi_with_nonsig_gi)/len(ppi_pairs):.3f}"],
    ["PPI only (not in SGA)", len(ppi_only_pairs), f"{len(ppi_only_pairs)/len(ppi_pairs):.3f}"],
    ["Significant GI only (no PPI)", len(gi_only_pairs), "N/A"],
    ["Both: within-cluster", both_within_cluster, f"{both_within_cluster/max(len(both_pairs),1):.3f}"],
    ["Both: between-cluster", both_between_cluster, f"{both_between_cluster/max(len(both_pairs),1):.3f}"],
    ["Both: positive GI", pos_gi, f"{pos_gi/max(len(both_pairs),1):.3f}"],
    ["Both: negative GI", neg_gi, f"{neg_gi/max(len(both_pairs),1):.3f}"],
]

with open(f"{OUT_DIR}/overlap_stats.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerows(rows)

print(f"\nSaved overlap_results.pkl and overlap_stats.csv")
print("Done.")
