"""
04_figures.py
Generate all analysis figures.
Inputs: processed_data.pkl, overlap_results.pkl, correlation_results.pkl
Outputs: analysis/figures/*.png
"""

import pickle
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
from scipy.stats import spearmanr
from sklearn.metrics import roc_curve, auc

OUT_DIR = "/Users/han-yingjhuang/Claude_projects/Genetic-interaction/analysis"
FIG_DIR = f"{OUT_DIR}/figures"
os.makedirs(FIG_DIR, exist_ok=True)

# Style
sns.set_style("whitegrid")
plt.rcParams.update({"font.size": 11, "axes.labelsize": 12, "axes.titlesize": 13})
COLORS = {"both": "#2166ac", "gi_only": "#d6604d", "ppi_only": "#4dac26", "nonsig": "#bababa"}

print("Loading data...")
with open(f"{OUT_DIR}/processed_data.pkl", "rb") as f:
    d = pickle.load(f)
with open(f"{OUT_DIR}/overlap_results.pkl", "rb") as f:
    ov = pickle.load(f)
with open(f"{OUT_DIR}/correlation_results.pkl", "rb") as f:
    cr = pickle.load(f)

node_meta = d["node_meta"]
both_pairs = ov["both_pairs"]
gi_only_pairs = ov["gi_only_pairs"]
ppi_only_pairs = ov["ppi_only_pairs"]
ppi_with_nonsig_gi = ov["ppi_with_nonsig_gi"]
cluster_stats = cr["cluster_stats"]

# ── Fig 1: Overlap pie chart ──────────────────────────────────────────────────
print("Fig 1: Overlap pie chart...")
fig, ax = plt.subplots(figsize=(6, 5))
n_both = len(both_pairs)
n_nonsig = len(ppi_with_nonsig_gi)
n_ppi_only = len(ppi_only_pairs)
n_gi_only = len(gi_only_pairs)

total_ppi = n_both + n_nonsig + n_ppi_only
sizes = [n_both, n_nonsig, n_ppi_only]
labels = [f"PPI + significant GI\n(n={n_both:,})",
          f"PPI + measured non-sig GI\n(n={n_nonsig:,})",
          f"PPI, no SGA measurement\n(n={n_ppi_only:,})"]
colors = [COLORS["both"], COLORS["nonsig"], COLORS["ppi_only"]]
explode = (0.05, 0, 0)

wedges, texts, autotexts = ax.pie(sizes, labels=None, colors=colors, explode=explode,
                                   autopct="%1.1f%%", startangle=140,
                                   textprops={"fontsize": 10})
ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.25),
          fontsize=9, frameon=False)
ax.set_title(f"Distribution of 31,004 PPI pairs\nby SGA coverage (n={total_ppi:,} PPI total)", pad=10)
plt.tight_layout()
plt.savefig(f"{FIG_DIR}/fig1_overlap_pie.png", dpi=300, bbox_inches="tight")
plt.close()

# ── Fig 2: GI epsilon distribution by category ────────────────────────────────
print("Fig 2: GI epsilon distribution...")
fig, ax = plt.subplots(figsize=(7, 5))

eps_both = [s["mean_eps"] for _, _, s in both_pairs]
eps_nonsig = [s["mean_eps"] for _, _, s in ppi_with_nonsig_gi]

data_plot = [eps_both, eps_nonsig]
labels_plot = [f"PPI + sig GI\n(n={len(eps_both):,})", f"PPI + non-sig GI\n(n={len(eps_nonsig):,})"]
colors_plot = [COLORS["both"], COLORS["nonsig"]]

parts = ax.violinplot(data_plot, positions=[1, 2], showmedians=True, showextrema=False)
for i, pc in enumerate(parts["bodies"]):
    pc.set_facecolor(colors_plot[i])
    pc.set_alpha(0.7)
parts["cmedians"].set_color("black")
parts["cmedians"].set_linewidth(2)

ax.axhline(0, color="gray", linestyle="--", linewidth=1, alpha=0.5)
ax.set_xticks([1, 2])
ax.set_xticklabels(labels_plot)
ax.set_ylabel("Mean GI epsilon score")
ax.set_title("GI epsilon scores: pairs with vs without significant genetic interaction")
plt.tight_layout()
plt.savefig(f"{FIG_DIR}/fig2_gi_epsilon_violin.png", dpi=300, bbox_inches="tight")
plt.close()

# ── Fig 3: PPI score distribution by category ─────────────────────────────────
print("Fig 3: PPI score distribution...")
fig, ax = plt.subplots(figsize=(7, 5))

ppi_both = [p["score"] for _, p, _ in both_pairs]
ppi_nonsig = [p["score"] for _, p, _ in ppi_with_nonsig_gi]
ppi_only = [p["score"] for _, p in ppi_only_pairs]

data_plot = [ppi_both, ppi_nonsig, ppi_only]
labels_plot = [f"Both\n(n={len(ppi_both):,})", f"PPI+non-sig\n(n={len(ppi_nonsig):,})", f"PPI-only\n(n={len(ppi_only):,})"]
colors_plot = [COLORS["both"], COLORS["nonsig"], COLORS["ppi_only"]]

parts = ax.violinplot(data_plot, positions=[1, 2, 3], showmedians=True, showextrema=False)
for i, pc in enumerate(parts["bodies"]):
    pc.set_facecolor(colors_plot[i])
    pc.set_alpha(0.7)
parts["cmedians"].set_color("black")
parts["cmedians"].set_linewidth(2)

ax.set_xticks([1, 2, 3])
ax.set_xticklabels(labels_plot)
ax.set_ylabel("PPI interaction score")
ax.set_title("PPI scores across interaction categories")
plt.tight_layout()
plt.savefig(f"{FIG_DIR}/fig3_ppi_score_violin.png", dpi=300, bbox_inches="tight")
plt.close()

# ── Fig 4: Scatter PPI score vs |GI epsilon| ──────────────────────────────────
print("Fig 4: Scatter plot...")
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# Color by GI sign
ppi_s = np.array([p["score"] for _, p, _ in both_pairs])
gi_e = np.array([s["mean_eps"] for _, _, s in both_pairs])
colors_dot = [COLORS["both"] if e > 0 else COLORS["gi_only"] for e in gi_e]

ax = axes[0]
ax.scatter(ppi_s + np.random.normal(0, 0.05, len(ppi_s)), np.abs(gi_e),
           c=colors_dot, alpha=0.3, s=8, rasterized=True)
ax.set_xlabel("PPI interaction score")
ax.set_ylabel("|GI epsilon score|")
r, p = spearmanr(ppi_s, np.abs(gi_e))
ax.set_title(f"PPI score vs |GI epsilon|\n(Spearman r={r:.3f}, p={p:.1e}, n={len(ppi_s):,})")
patch_pos = mpatches.Patch(color=COLORS["both"], label="Positive GI")
patch_neg = mpatches.Patch(color=COLORS["gi_only"], label="Negative GI")
ax.legend(handles=[patch_pos, patch_neg], fontsize=9, frameon=False)

# Binned scatter (for clarity)
ax2 = axes[1]
bins = np.arange(2, 11, 0.5)
bin_centers = (bins[:-1] + bins[1:]) / 2
bin_means = []
bin_sems = []
for i in range(len(bins) - 1):
    mask = (ppi_s >= bins[i]) & (ppi_s < bins[i+1])
    vals = np.abs(gi_e[mask])
    if len(vals) > 0:
        bin_means.append(np.mean(vals))
        bin_sems.append(np.std(vals) / np.sqrt(len(vals)))
    else:
        bin_means.append(np.nan)
        bin_sems.append(np.nan)

ax2.errorbar(bin_centers, bin_means, yerr=bin_sems, fmt="o-",
             color=COLORS["both"], capsize=3, linewidth=1.5, markersize=5)
ax2.set_xlabel("PPI interaction score (binned)")
ax2.set_ylabel("Mean |GI epsilon score|")
ax2.set_title("Mean |GI epsilon| per PPI score bin")

plt.tight_layout()
plt.savefig(f"{FIG_DIR}/fig4_scatter_ppi_vs_gi.png", dpi=300, bbox_inches="tight")
plt.close()

# ── Fig 5: ROC curve (PPI score predicting significant GI) ────────────────────
print("Fig 5: ROC curve...")

# For pairs that have SGA measurements: label = 1 if significant GI, 0 if not
# Score = PPI score
measured_pairs = both_pairs + ppi_with_nonsig_gi
y_true = np.array([1]*len(both_pairs) + [0]*len(ppi_with_nonsig_gi))
y_score = np.array([p["score"] for _, p, _ in measured_pairs])

if len(y_true) > 0 and y_true.sum() > 0:
    fpr, tpr, _ = roc_curve(y_true, y_score)
    roc_auc = auc(fpr, tpr)

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot(fpr, tpr, color=COLORS["both"], lw=2, label=f"ROC (AUC = {roc_auc:.3f})")
    ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.5)
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate")
    ax.set_title("ROC: PPI score predicting significant GI")
    ax.legend(fontsize=10, frameon=False)
    plt.tight_layout()
    plt.savefig(f"{FIG_DIR}/fig5_roc_curve.png", dpi=300, bbox_inches="tight")
    plt.close()
    print(f"  ROC AUC = {roc_auc:.3f}")

# ── Fig 6: Per-cluster correlation bar chart ──────────────────────────────────
print("Fig 6: Per-cluster correlations...")

top20 = [cs for cs in cluster_stats[:20] if not np.isnan(cs["spearman_r"])]
if top20:
    names = [cs["cluster_name"][:25] for cs in top20]
    rs = [cs["spearman_r"] for cs in top20]
    ns = [cs["n_pairs"] for cs in top20]
    sig = [cs["spearman_p"] < 0.05 for cs in top20]

    colors_bar = [COLORS["both"] if s else COLORS["nonsig"] for s in sig]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.barh(range(len(names)), rs, color=colors_bar, edgecolor="white", height=0.7)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels([f"{n} (n={ns[i]})" for i, n in enumerate(names)], fontsize=9)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("Spearman r (PPI score vs |GI epsilon|)")
    ax.set_title("Per-cluster correlation: PPI score vs |GI epsilon|\n(top 20 clusters by pair count)")

    patch_sig = mpatches.Patch(color=COLORS["both"], label="p < 0.05")
    patch_ns = mpatches.Patch(color=COLORS["nonsig"], label="p >= 0.05")
    ax.legend(handles=[patch_sig, patch_ns], fontsize=9, frameon=False)
    plt.tight_layout()
    plt.savefig(f"{FIG_DIR}/fig6_cluster_correlations.png", dpi=300, bbox_inches="tight")
    plt.close()

# ── Fig 7: GI direction within vs between cluster ─────────────────────────────
print("Fig 7: GI direction within vs between cluster...")

both_within = ov["both_within"]
both_between = ov["both_between"]

if both_within and both_between:
    w_eps = np.array([s["mean_eps"] for _, _, s in both_within])
    b_eps = np.array([s["mean_eps"] for _, _, s in both_between])

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=False)

    for ax, eps, label, color in zip(axes,
                                      [w_eps, b_eps],
                                      ["Within-cluster", "Between-cluster"],
                                      [COLORS["both"], COLORS["gi_only"]]):
        ax.hist(eps, bins=40, color=color, alpha=0.7, edgecolor="white")
        ax.axvline(0, color="black", linestyle="--", linewidth=1)
        ax.axvline(np.mean(eps), color="red", linestyle="-", linewidth=1.5, label=f"Mean={np.mean(eps):.3f}")
        frac_pos = (eps > 0).mean()
        ax.set_title(f"{label} (n={len(eps):,})\n{100*frac_pos:.1f}% positive GI")
        ax.set_xlabel("GI epsilon score")
        ax.set_ylabel("Count")
        ax.legend(fontsize=9, frameon=False)

    plt.suptitle("GI epsilon distribution: within-complex vs between-complex protein pairs", y=1.02)
    plt.tight_layout()
    plt.savefig(f"{FIG_DIR}/fig7_gi_direction_within_between.png", dpi=300, bbox_inches="tight")
    plt.close()

# ── Fig 8: Heatmap of top clusters (mean PPI score vs mean |GI epsilon|) ──────
print("Fig 8: Cluster heatmap...")

top_clusters = [cs for cs in cluster_stats if cs["n_pairs"] >= 3][:25]
if len(top_clusters) >= 3:
    import matplotlib.cm as cm

    fig, ax = plt.subplots(figsize=(7, 6))
    xs = [cs["mean_ppi_score"] for cs in top_clusters]
    ys = [cs["mean_abs_eps"] for cs in top_clusters]
    ns = [cs["n_pairs"] for cs in top_clusters]
    rs = [cs["spearman_r"] for cs in top_clusters]

    scatter = ax.scatter(xs, ys, s=[n*10 for n in ns], c=rs, cmap="RdBu_r",
                         vmin=-0.5, vmax=0.5, alpha=0.8, edgecolors="gray", linewidths=0.5)

    # Label top 10 by n_pairs
    for cs in sorted(top_clusters, key=lambda x: x["n_pairs"], reverse=True)[:10]:
        ax.annotate(cs["cluster_name"][:15],
                    (cs["mean_ppi_score"], cs["mean_abs_eps"]),
                    fontsize=7, ha="left", va="bottom",
                    xytext=(3, 3), textcoords="offset points")

    cbar = plt.colorbar(scatter, ax=ax)
    cbar.set_label("Spearman r", fontsize=10)
    ax.set_xlabel("Mean PPI score (within cluster)")
    ax.set_ylabel("Mean |GI epsilon| (within cluster)")
    ax.set_title("Per-cluster relationship: PPI strength vs GI magnitude\n(bubble size = number of pairs)")
    plt.tight_layout()
    plt.savefig(f"{FIG_DIR}/fig8_cluster_heatmap.png", dpi=300, bbox_inches="tight")
    plt.close()

print(f"\nAll figures saved to {FIG_DIR}/")
print("Done.")
