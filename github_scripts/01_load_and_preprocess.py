"""
01_load_and_preprocess.py
Load and preprocess PPI (Michaelis et al. 2023) and SGA (Costanzo et al. 2016) data.
Outputs: analysis/processed_data.pkl
"""

import csv
import pickle
import re
from collections import defaultdict
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = str(ROOT.parent / "Resources")
OUT_DIR = str(ROOT / "analysis")

# ── 1. Load PPI nodes (SGD ID → gene name, cluster) ──────────────────────────
print("Loading PPI nodes...")
node_meta = {}  # sgd_id -> {gene_name, cluster_id, description}
with open(f"{DATA_DIR}/Interactome_Data/The_Yeast_Interactome_Nodes.csv") as f:
    reader = csv.DictReader(f, delimiter=";")
    for row in reader:
        sid = row["shared name"].strip()
        gene = row["Gene names  (UniP/SGD-primary or ordered locus)"].strip()
        cluster = row["Cluster ID (Markov)"].strip()
        desc = row["SGD Brief Description"].strip()
        node_meta[sid] = {"gene_name": gene, "cluster_id": cluster, "description": desc}

print(f"  Loaded {len(node_meta)} protein nodes, {len(set(v['cluster_id'] for v in node_meta.values() if v['cluster_id']))} clusters")

# ── 2. Load PPI edges ─────────────────────────────────────────────────────────
print("Loading PPI edges...")
ppi_pairs = {}  # frozenset(sgd_a, sgd_b) -> {score, score_FDR, score_cor, inter_cluster, gene_a, gene_b}
ppi_gene_pairs = {}  # frozenset(gene_a, gene_b) -> same dict (for SGA matching)

with open(f"{DATA_DIR}/Interactome_Data/The_Yeast_Interactome_Edges.csv") as f:
    reader = csv.DictReader(f, delimiter=";")
    for row in reader:
        src = row["source"].strip()
        tgt = row["target"].strip()
        gene_src = row["Source Gene names (SGD/UniProt-primary or ordered locus)"].strip()
        gene_tgt = row["Target Gene names  (SGD/UniProt-primary or ordered locus)"].strip()
        score = float(row["score_FDR+cor"])
        score_fdr = float(row["score_FDR"])
        score_cor = float(row["score_cor"])
        inter_cluster = row["Inter-cluster edge"].strip() == "TRUE"
        n_pubs = row["Count of publications"].strip()
        n_pubs = int(n_pubs) if n_pubs else 0

        key = frozenset([src, tgt])
        val = {
            "score": score, "score_FDR": score_fdr, "score_cor": score_cor,
            "inter_cluster": inter_cluster, "n_pubs": n_pubs,
            "gene_a": gene_src, "gene_b": gene_tgt,
            "sgd_a": src, "sgd_b": tgt
        }
        ppi_pairs[key] = val

        gkey = frozenset([gene_src.upper(), gene_tgt.upper()])
        ppi_gene_pairs[gkey] = val

print(f"  Loaded {len(ppi_pairs)} PPI edges")
print(f"  Score range: {min(v['score'] for v in ppi_pairs.values()):.1f} - {max(v['score'] for v in ppi_pairs.values()):.1f}")

# Build set of PPI SGD IDs for fast filtering
ppi_sgd_ids = set()
for v in ppi_pairs.values():
    ppi_sgd_ids.add(v["sgd_a"])
    ppi_sgd_ids.add(v["sgd_b"])
print(f"  Unique proteins in PPI: {len(ppi_sgd_ids)}")

# Build gene-name -> SGD ID mapping from PPI nodes (for SGA matching)
gene_to_sgd = {}
for sid, meta in node_meta.items():
    gname = meta["gene_name"].upper()
    gene_to_sgd[gname] = sid
    # Also map the systematic name
    gene_to_sgd[sid.upper()] = sid

# ── 3. Stream SGA NxN data ────────────────────────────────────────────────────
print("\nStreaming SGA NxN data (12.7M rows)...")
print("(Keeping only pairs where at least one partner is in PPI dataset)")

sga_file = f"{DATA_DIR}/SGA/SGA_NxN.txt"

# We'll accumulate per (query_orf, array_orf) -> list of (epsilon, pval)
# Using ORF IDs as keys to avoid gene-name aliasing
sga_raw = defaultdict(list)  # frozenset(orf_a, orf_b) -> [(epsilon, pval, qsmf, asmf, dmf)]

CHUNKSIZE = 200_000
total_rows = 0
kept_rows = 0
chunks_processed = 0

# Regex to extract ORF from strain ID like "YAL002W_sn273"
orf_re = re.compile(r'^(Y[A-Z]{2}\d+[CW](?:-[A-Z])?)')

for chunk in pd.read_csv(sga_file, sep="\t", chunksize=CHUNKSIZE,
                          dtype={"Genetic interaction score (ε)": float,
                                 "P-value": float},
                          low_memory=False):
    total_rows += len(chunk)
    chunks_processed += 1
    if chunks_processed % 5 == 0:
        print(f"  Processed {total_rows:,} rows, kept {kept_rows:,}...")

    # Extract ORF from strain IDs
    q_strain = chunk["Query Strain ID"].str.extract(r'^(Y[A-Z]{2}\d+[CW](?:-[A-Z])?)', expand=False)
    a_strain = chunk["Array Strain ID"].str.extract(r'^(Y[A-Z]{2}\d+[CW](?:-[A-Z])?)', expand=False)

    # Filter: keep if either partner is in PPI
    mask = q_strain.isin(ppi_sgd_ids) | a_strain.isin(ppi_sgd_ids)
    sub = chunk[mask].copy()
    sub["q_orf"] = q_strain[mask]
    sub["a_orf"] = a_strain[mask]

    kept_rows += len(sub)

    for _, row in sub.iterrows():
        qorf = row["q_orf"]
        aorf = row["a_orf"]
        if pd.isna(qorf) or pd.isna(aorf):
            continue
        eps = row["Genetic interaction score (ε)"]
        pval = row["P-value"]
        qsmf = row["Query single mutant fitness (SMF)"]
        asmf = row["Array SMF"]
        dmf = row["Double mutant fitness"]
        if pd.isna(eps) or pd.isna(pval):
            continue
        key = frozenset([qorf, aorf])
        sga_raw[key].append((eps, pval, qsmf, asmf, dmf))

print(f"\nTotal SGA rows: {total_rows:,}")
print(f"Rows with PPI partner: {kept_rows:,}")
print(f"Unique SGA gene pairs (with PPI partner): {len(sga_raw):,}")

# ── 4. Aggregate SGA measurements per pair ────────────────────────────────────
print("\nAggregating SGA measurements per pair...")
sga_pairs = {}  # frozenset(orf_a, orf_b) -> {mean_eps, min_pval, n_meas, sig}

for key, measurements in sga_raw.items():
    eps_vals = [m[0] for m in measurements]
    pval_vals = [m[1] for m in measurements]
    qsmf_vals = [m[2] for m in measurements if not pd.isna(m[2])]
    asmf_vals = [m[3] for m in measurements if not pd.isna(m[3])]
    dmf_vals = [m[4] for m in measurements if not pd.isna(m[4])]

    mean_eps = sum(eps_vals) / len(eps_vals)
    min_pval = min(pval_vals)
    n_meas = len(measurements)

    sga_pairs[key] = {
        "mean_eps": mean_eps,
        "min_pval": min_pval,
        "n_measurements": n_meas,
        "sig_p05": min_pval < 0.05,
        "sig_p01": min_pval < 0.01,
        "mean_qsmf": sum(qsmf_vals)/len(qsmf_vals) if qsmf_vals else None,
        "mean_asmf": sum(asmf_vals)/len(asmf_vals) if asmf_vals else None,
        "mean_dmf": sum(dmf_vals)/len(dmf_vals) if dmf_vals else None,
    }

print(f"Aggregated {len(sga_pairs):,} unique SGA gene pairs")
sig05 = sum(1 for v in sga_pairs.values() if v["sig_p05"])
sig01 = sum(1 for v in sga_pairs.values() if v["sig_p01"])
print(f"  Significant at p<0.05: {sig05:,}")
print(f"  Significant at p<0.01: {sig01:,}")

# ── 5. Save ──────────────────────────────────────────────────────────────────
print("\nSaving processed data...")
processed = {
    "ppi_pairs": ppi_pairs,
    "ppi_gene_pairs": ppi_gene_pairs,
    "sga_pairs": sga_pairs,
    "node_meta": node_meta,
    "ppi_sgd_ids": ppi_sgd_ids,
    "gene_to_sgd": gene_to_sgd,
}
with open(f"{OUT_DIR}/processed_data.pkl", "wb") as f:
    pickle.dump(processed, f)

print(f"Saved to {OUT_DIR}/processed_data.pkl")
print("\nDone.")
