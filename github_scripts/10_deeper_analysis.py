#!/usr/bin/env python3
"""
10_deeper_analysis.py

Expanded analyses for the yeast GI-vs-PPI study:
1. GO enrichment for concordant PPI-GI gene sets
2. Human ortholog and disease-gene analysis
3. Network topology analysis
4. Markov-cluster deep-dive analysis
5. GI sign predictive modeling
6. Subcellular compartment co-localization analysis

Outputs:
    analysis/go_enrichment_results.pkl
    analysis/go_enrichment_summary.csv
    analysis/disease_ortholog_results.pkl
    analysis/network_topology_results.pkl
    analysis/complex_deepdive_results.pkl
    analysis/predictive_model_results.pkl
    analysis/subcellular_compartment_results.pkl
"""

from __future__ import annotations

import csv
import gzip
import math
import os
import pickle
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(__file__).resolve().parent / ".mplconfig"),
)

import networkx as nx
import numpy as np
import pandas as pd
import requests
from goatools.goea.go_enrichment_ns import GOEnrichmentStudyNS
from goatools.obo_parser import GODag
from scipy.stats import fisher_exact, mannwhitneyu
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "analysis"
DATA_DIR = ROOT.parent / "Resources"
DOWNLOAD_DIR = ROOT.parent / "Resources" / "external_data_cache"
DOWNLOAD_DIR.mkdir(exist_ok=True)

PROCESSED_PATH = ANALYSIS_DIR / "processed_data.pkl"
OVERLAP_PATH = ANALYSIS_DIR / "overlap_results.pkl"
CORRELATION_PATH = ANALYSIS_DIR / "correlation_results.pkl"
NODES_CSV = DATA_DIR / "Interactome_Data" / "The_Yeast_Interactome_Nodes.csv"

GO_RESULTS_PATH = ANALYSIS_DIR / "go_enrichment_results.pkl"
GO_SUMMARY_CSV_PATH = ANALYSIS_DIR / "go_enrichment_summary.csv"
DISEASE_RESULTS_PATH = ANALYSIS_DIR / "disease_ortholog_results.pkl"
NETWORK_RESULTS_PATH = ANALYSIS_DIR / "network_topology_results.pkl"
COMPLEX_RESULTS_PATH = ANALYSIS_DIR / "complex_deepdive_results.pkl"
PREDICTIVE_RESULTS_PATH = ANALYSIS_DIR / "predictive_model_results.pkl"
COMPARTMENT_RESULTS_PATH = ANALYSIS_DIR / "subcellular_compartment_results.pkl"
ORTHOLOG_CACHE_PATH = ANALYSIS_DIR / "alliance_ortholog_cache.pkl"

NS_LABELS = {"BP": "biological_process", "MF": "molecular_function", "CC": "cellular_component"}
ASPECT_TO_NS = {"P": "BP", "F": "MF", "C": "CC"}
COMPARTMENT_PATTERNS = {
    "nucleus": [r"\bnucleus\b", r"\bnucleolus\b", r"\bchromosome\b", r"\bnuclear\b"],
    "cytoplasm": [r"\bcytoplasm\b", r"\bcytosol\b"],
    "mitochondrion": [r"mitochondr"],
    "endoplasmic reticulum": [r"endoplasmic reticulum", r"\ber\b"],
    "golgi": [r"\bgolgi\b"],
    "vacuole": [r"\bvacuol"],
    "plasma membrane": [r"cell membrane", r"plasma membrane"],
    "endosome": [r"\bendosom"],
    "peroxisome": [r"\bperoxisom"],
    "vesicle": [r"\bvesicle\b"],
    "ribosome": [r"\bribosom"],
    "cytoskeleton": [r"\bcytoskeleton\b", r"\bactin\b", r"\bmicrotubule\b", r"\bspindle\b"],
    "bud neck": [r"\bbud neck\b"],
    "cell wall": [r"\bcell wall\b"],
}


def log(message: str) -> None:
    print(message, flush=True)


def save_pickle(obj, path: Path) -> None:
    with path.open("wb") as handle:
        pickle.dump(obj, handle)


def load_pickle(path: Path):
    with path.open("rb") as handle:
        return pickle.load(handle)


def normalize_sgd_id(raw: str) -> str:
    if raw is None:
        return ""
    clean = raw.strip().strip('"').strip().rstrip(";")
    if clean.startswith("SGD:"):
        clean = clean.split(":", 1)[1]
    return clean


def parse_semicolon_list(raw: str) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(";") if item.strip()]


def load_full_node_metadata() -> Dict[str, dict]:
    full_meta = {}
    with NODES_CSV.open() as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            orf = row["shared name"].strip()
            gene_name = row["Gene names  (UniP/SGD-primary or ordered locus)"].strip()
            full_meta[orf] = {
                "orf": orf,
                "gene_name": gene_name,
                "cluster_id": row["Cluster ID (Markov)"].strip(),
                "description": row["SGD Brief Description"].strip(),
                "sgd_name": row["SGD Name"].strip(),
                "sgd_id": normalize_sgd_id(row["SGD ID"]),
                "uniprot_id": row["UniProt ID"].strip(),
                "qualifier": row["SGD Qualifier"].strip(),
                "location_raw": row["Subcellular location [CC]"].strip(),
                "complex_names": parse_semicolon_list(row["CPX - Recommended name"].strip()),
                "complex_ids": parse_semicolon_list(row["Cross-reference (ComplexPortal)"].strip()),
                "all_names": parse_semicolon_list(
                    row["Gene names  (UniP/SGD-primary+synonym or ordered locus)"].strip()
                ),
            }
    return full_meta


def build_orf_mappings(full_meta: Dict[str, dict]) -> Tuple[Dict[str, str], Dict[str, str]]:
    sgdid_to_orf = {}
    gene_to_orf = {}
    for orf, meta in full_meta.items():
        if meta["sgd_id"]:
            sgdid_to_orf[meta["sgd_id"]] = orf
            sgdid_to_orf[f"SGD:{meta['sgd_id']}"] = orf
        gene_to_orf[orf.upper()] = orf
        if meta["gene_name"]:
            gene_to_orf[meta["gene_name"].upper()] = orf
        for alias in meta["all_names"]:
            gene_to_orf[alias.upper()] = orf
    return sgdid_to_orf, gene_to_orf


def get_pair_genes(pair_records: Sequence[Tuple[frozenset, dict, dict]] | Sequence[Tuple[frozenset, dict]]) -> Set[str]:
    genes = set()
    for rec in pair_records:
        pair = rec[0]
        genes.update(pair)
    return genes


def filter_valid_pairs(pair_records: Sequence[Tuple], valid_genes: Set[str]) -> List[Tuple]:
    return [record for record in pair_records if set(record[0]).issubset(valid_genes)]


def download_first_available(
    urls: Sequence[str],
    out_path: Path,
    description: str,
    timeout: int = 120,
) -> Tuple[Path | None, str | None]:
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path, "cached"

    last_error = None
    for url in urls:
        try:
            log(f"Downloading {description} from {url}")
            response = requests.get(url, timeout=timeout, stream=True)
            content_type = response.headers.get("content-type", "")
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                continue
            if "text/html" in content_type.lower() and not url.endswith(".obo"):
                last_error = f"Unexpected HTML response from {url}"
                continue
            with out_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 15):
                    if chunk:
                        handle.write(chunk)
            return out_path, url
        except Exception as exc:  # noqa: BLE001
            last_error = repr(exc)

    log(f"WARNING: failed to download {description}. Last error: {last_error}")
    return None, last_error


def load_text_lines(path: Path) -> Iterable[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                yield line
    else:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                yield line


def parse_go_associations(
    gaf_path: Path,
    background_genes: Set[str],
    sgdid_to_orf: Dict[str, str],
    gene_to_orf: Dict[str, str],
) -> Tuple[Dict[str, Dict[str, Set[str]]], Dict[str, Set[str]]]:
    ns2assoc = {ns: defaultdict(set) for ns in NS_LABELS}
    gene2gos = defaultdict(set)
    kept_lines = 0

    for line in load_text_lines(gaf_path):
        if not line or line.startswith("!"):
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 9:
            continue
        qualifier = parts[3]
        if "NOT" in qualifier.split("|"):
            continue
        go_id = parts[4].strip()
        aspect = parts[8].strip()
        ns = ASPECT_TO_NS.get(aspect)
        if ns is None:
            continue

        orf = None
        db_object_id = normalize_sgd_id(parts[1])
        symbol = parts[2].strip().upper()
        if db_object_id:
            orf = sgdid_to_orf.get(db_object_id) or sgdid_to_orf.get(f"SGD:{db_object_id}")
        if orf is None and symbol:
            orf = gene_to_orf.get(symbol)
        if orf is None or orf not in background_genes:
            continue

        ns2assoc[ns][orf].add(go_id)
        gene2gos[orf].add(go_id)
        kept_lines += 1

    log(
        "Parsed GO annotations for "
        f"{len(gene2gos):,} PPI-network genes from {kept_lines:,} annotation rows."
    )
    return {ns: dict(mapping) for ns, mapping in ns2assoc.items()}, dict(gene2gos)


def serialize_go_result(result) -> dict:
    return {
        "GO": result.GO,
        "name": result.name,
        "namespace": result.NS,
        "enrichment": result.enrichment,
        "study_count": int(result.study_count),
        "study_n": int(result.study_n),
        "pop_count": int(result.pop_count),
        "pop_n": int(result.pop_n),
        "ratio_in_study": tuple(result.ratio_in_study),
        "ratio_in_pop": tuple(result.ratio_in_pop),
        "p_uncorrected": float(result.p_uncorrected),
        "p_fdr_bh": float(getattr(result, "p_fdr_bh", np.nan)),
        "depth": int(result.depth),
    }


def run_go_enrichment(
    study_genes: Set[str],
    population_genes: Set[str],
    ns2assoc: Dict[str, Dict[str, Set[str]]],
    godag: GODag,
    label: str,
) -> dict:
    goea = GOEnrichmentStudyNS(
        sorted(population_genes),
        ns2assoc,
        godag,
        propagate_counts=True,
        alpha=0.05,
        methods=["fdr_bh"],
    )
    results = goea.run_study(sorted(study_genes))
    enriched = [
        serialize_go_result(result)
        for result in results
        if result.enrichment == "e" and getattr(result, "p_fdr_bh", 1.0) <= 0.05
    ]
    enriched.sort(key=lambda row: (row["p_fdr_bh"], row["p_uncorrected"], row["GO"]))

    top_by_namespace = {}
    for ns in NS_LABELS:
        ns_rows = [row for row in enriched if row["namespace"] == ns]
        top_by_namespace[ns] = ns_rows[:20]

    summary = {
        "label": label,
        "study_size": len(study_genes),
        "annotated_study_size": sum(
            1 for gene in study_genes if any(gene in assoc for assoc in ns2assoc.values())
        ),
        "population_size": len(population_genes),
        "n_significant": len(enriched),
        "significant_terms": enriched,
        "top_by_namespace": top_by_namespace,
    }
    return summary


def fetch_alliance_human_orthologs(sgd_id: str) -> dict:
    url = f"https://www.alliancegenome.org/api/gene/SGD:{sgd_id}/orthologs"
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            payload = response.json()
            orthologs = {}
            for result in payload.get("results", []):
                relation = result.get("geneToGeneOrthologyGenerated", {})
                object_gene = relation.get("objectGene", {})
                if object_gene.get("taxon", {}).get("curie") != "NCBITaxon:9606":
                    continue
                symbol = object_gene.get("geneSymbol", {}).get("displayText")
                curie = object_gene.get("primaryExternalId")
                if not symbol and not curie:
                    continue
                key = curie or symbol
                orthologs[key] = {
                    "symbol": symbol,
                    "curie": curie,
                    "strict_filter": bool(relation.get("strictFilter")),
                    "moderate_filter": bool(relation.get("moderateFilter")),
                    "confidence": relation.get("confidence", {}).get("name"),
                    "matched_methods": [item.get("name") for item in relation.get("predictionMethodsMatched", [])],
                }
            return {
                "success": True,
                "url": url,
                "n_results": payload.get("returnedRecords", payload.get("total", len(payload.get("results", [])))),
                "human_orthologs": sorted(
                    orthologs.values(),
                    key=lambda item: (not item["strict_filter"], item["symbol"] or item["curie"] or ""),
                ),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = repr(exc)
            time.sleep(1.5 * (attempt + 1))

    return {
        "success": False,
        "url": url,
        "n_results": 0,
        "human_orthologs": [],
        "error": last_error,
    }


def build_ortholog_cache(orfs: Sequence[str], full_meta: Dict[str, dict]) -> Dict[str, dict]:
    cache = {}
    if ORTHOLOG_CACHE_PATH.exists():
        cache = load_pickle(ORTHOLOG_CACHE_PATH)

    missing = [orf for orf in sorted(set(orfs)) if orf not in cache]
    if not missing:
        return cache

    log(f"Fetching Alliance orthologs for {len(missing):,} genes...")
    completed = 0
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_orf = {
            executor.submit(fetch_alliance_human_orthologs, full_meta[orf]["sgd_id"]): orf
            for orf in missing
            if full_meta[orf].get("sgd_id")
        }
        for future in as_completed(future_to_orf):
            orf = future_to_orf[future]
            result = future.result()
            result["orf"] = orf
            result["gene_name"] = full_meta[orf]["gene_name"]
            result["sgd_id"] = full_meta[orf]["sgd_id"]
            cache[orf] = result
            completed += 1
            if completed % 250 == 0 or completed == len(future_to_orf):
                log(f"  Ortholog fetch progress: {completed:,}/{len(future_to_orf):,}")

    for orf in missing:
        if orf not in cache:
            cache[orf] = {
                "success": False,
                "url": None,
                "n_results": 0,
                "human_orthologs": [],
                "error": "No SGD ID available",
                "orf": orf,
                "gene_name": full_meta[orf]["gene_name"],
                "sgd_id": full_meta[orf]["sgd_id"],
            }

    save_pickle(cache, ORTHOLOG_CACHE_PATH)
    return cache


def load_mim2gene(mim_path: Path) -> Set[int]:
    disease_geneids = set()
    for line in load_text_lines(mim_path):
        if not line or line.startswith("#"):
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 5:
            continue
        gene_id = parts[1].strip()
        mim_type = parts[2].strip().lower()
        if gene_id.isdigit() and "gene" in mim_type:
            disease_geneids.add(int(gene_id))
    return disease_geneids


def load_human_gene_info(gene_info_path: Path) -> dict:
    symbol_to_geneid = {}
    alias_to_geneids = defaultdict(set)
    hgnc_to_geneid = {}

    for line in load_text_lines(gene_info_path):
        if not line or line.startswith("#"):
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 6:
            continue
        tax_id, gene_id, symbol, _, synonyms, dbxrefs = parts[:6]
        if tax_id != "9606" or not gene_id.isdigit():
            continue
        gene_id_int = int(gene_id)
        if symbol and symbol != "-":
            symbol_to_geneid.setdefault(symbol, gene_id_int)
            alias_to_geneids[symbol].add(gene_id_int)
        if synonyms and synonyms != "-":
            for syn in synonyms.split("|"):
                syn = syn.strip()
                if syn and syn != "-":
                    alias_to_geneids[syn].add(gene_id_int)
        if dbxrefs and dbxrefs != "-":
            for entry in dbxrefs.split("|"):
                if entry.startswith("HGNC:"):
                    hgnc_to_geneid[entry] = gene_id_int

    unique_alias_to_geneid = {alias: next(iter(ids)) for alias, ids in alias_to_geneids.items() if len(ids) == 1}
    return {
        "symbol_to_geneid": symbol_to_geneid,
        "unique_alias_to_geneid": unique_alias_to_geneid,
        "hgnc_to_geneid": hgnc_to_geneid,
    }


def annotate_ortholog_disease(ortholog_cache: Dict[str, dict], human_gene_maps: dict, disease_geneids: Set[int]) -> Dict[str, dict]:
    annotated = {}
    for orf, payload in ortholog_cache.items():
        orthologs = []
        for ortholog in payload.get("human_orthologs", []):
            curie = ortholog.get("curie")
            symbol = ortholog.get("symbol")
            gene_id = None
            if curie in human_gene_maps["hgnc_to_geneid"]:
                gene_id = human_gene_maps["hgnc_to_geneid"][curie]
            elif symbol in human_gene_maps["symbol_to_geneid"]:
                gene_id = human_gene_maps["symbol_to_geneid"][symbol]
            elif symbol in human_gene_maps["unique_alias_to_geneid"]:
                gene_id = human_gene_maps["unique_alias_to_geneid"][symbol]

            orthologs.append(
                {
                    **ortholog,
                    "ncbi_gene_id": gene_id,
                    "is_disease_gene": gene_id in disease_geneids if gene_id is not None else False,
                }
            )

        annotated[orf] = {
            **payload,
            "human_orthologs": orthologs,
            "has_human_ortholog": any(item.get("strict_filter") for item in orthologs) or bool(orthologs),
            "has_disease_ortholog": any(item["is_disease_gene"] for item in orthologs),
        }
    return annotated


def compute_enrichment_vs_background(
    study_genes: Set[str],
    background_genes: Set[str],
    disease_flags: Dict[str, bool],
) -> dict:
    study_genes = set(study_genes) & set(background_genes)
    rest_genes = set(background_genes) - set(study_genes)
    a = sum(disease_flags.get(gene, False) for gene in study_genes)
    b = len(study_genes) - a
    c = sum(disease_flags.get(gene, False) for gene in rest_genes)
    d = len(rest_genes) - c

    odds_ratio, p_value = fisher_exact([[a, b], [c, d]])

    a_c, b_c, c_c, d_c = [value + 0.5 for value in (a, b, c, d)]
    corrected_or = (a_c * d_c) / (b_c * c_c)
    se = math.sqrt((1 / a_c) + (1 / b_c) + (1 / c_c) + (1 / d_c))
    ci_low = math.exp(math.log(corrected_or) - 1.96 * se)
    ci_high = math.exp(math.log(corrected_or) + 1.96 * se)

    return {
        "study_size": len(study_genes),
        "study_disease": a,
        "study_non_disease": b,
        "background_rest_size": len(rest_genes),
        "background_rest_disease": c,
        "background_rest_non_disease": d,
        "odds_ratio": odds_ratio,
        "odds_ratio_corrected": corrected_or,
        "ci95_low": ci_low,
        "ci95_high": ci_high,
        "p_value": p_value,
    }


def mann_whitney_summary(values_a: Sequence[float], values_b: Sequence[float], label_a: str, label_b: str) -> dict:
    clean_a = np.array([value for value in values_a if value is not None and not np.isnan(value)], dtype=float)
    clean_b = np.array([value for value in values_b if value is not None and not np.isnan(value)], dtype=float)
    if len(clean_a) == 0 or len(clean_b) == 0:
        return {
            "label_a": label_a,
            "label_b": label_b,
            "n_a": len(clean_a),
            "n_b": len(clean_b),
            "u_statistic": np.nan,
            "p_value": np.nan,
            "median_a": np.nan,
            "median_b": np.nan,
            "mean_a": np.nan,
            "mean_b": np.nan,
        }
    u_statistic, p_value = mannwhitneyu(clean_a, clean_b, alternative="two-sided")
    return {
        "label_a": label_a,
        "label_b": label_b,
        "n_a": len(clean_a),
        "n_b": len(clean_b),
        "u_statistic": float(u_statistic),
        "p_value": float(p_value),
        "median_a": float(np.median(clean_a)),
        "median_b": float(np.median(clean_b)),
        "mean_a": float(np.mean(clean_a)),
        "mean_b": float(np.mean(clean_b)),
    }


def compute_go_jaccard(gene_a: str, gene_b: str, gene2gos: Dict[str, Set[str]]) -> float:
    gos_a = gene2gos.get(gene_a, set())
    gos_b = gene2gos.get(gene_b, set())
    if not gos_a and not gos_b:
        return 0.0
    union = gos_a | gos_b
    if not union:
        return 0.0
    return len(gos_a & gos_b) / len(union)


def parse_compartments(raw_location: str) -> Set[str]:
    if not raw_location:
        return set()
    cleaned = re.sub(r"\{[^}]*\}", "", raw_location)
    cleaned = re.sub(r"Note=.*", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("SUBCELLULAR LOCATION:", " ").lower()
    found = set()
    for label, patterns in COMPARTMENT_PATTERNS.items():
        if any(re.search(pattern, cleaned) for pattern in patterns):
            found.add(label)
    return found


def compute_pair_compartment_stats(
    pair_records: Sequence[Tuple],
    compartment_map: Dict[str, Set[str]],
    label: str,
) -> dict:
    shared = 0
    one_or_both_annotated = 0
    pair_rows = []
    for record in pair_records:
        pair = record[0]
        gene_a, gene_b = sorted(pair)
        comp_a = compartment_map.get(gene_a, set())
        comp_b = compartment_map.get(gene_b, set())
        overlap = comp_a & comp_b
        if comp_a or comp_b:
            one_or_both_annotated += 1
        if overlap:
            shared += 1
        pair_rows.append(
            {
                "pair": tuple(sorted(pair)),
                "gene_a": gene_a,
                "gene_b": gene_b,
                "compartments_a": sorted(comp_a),
                "compartments_b": sorted(comp_b),
                "shared_compartments": sorted(overlap),
                "share_any_compartment": bool(overlap),
            }
        )
    total = len(pair_rows)
    return {
        "label": label,
        "n_pairs": total,
        "n_with_any_annotation": one_or_both_annotated,
        "n_shared_compartment": shared,
        "shared_rate_all_pairs": shared / total if total else np.nan,
        "shared_rate_annotated_pairs": shared / one_or_both_annotated if one_or_both_annotated else np.nan,
        "pair_rows": pair_rows,
    }


def try_load_essential_gene_set(full_meta: Dict[str, dict], gene_to_orf: Dict[str, str]) -> dict:
    urls = [
        "https://downloads.yeastgenome.org/curation/calculated_datasets/essential_orf.gz",
        "http://downloads.yeastgenome.org/curation/calculated_datasets/essential_orf.gz",
    ]
    essential_path, source = download_first_available(urls, DOWNLOAD_DIR / "essential_orf.gz", "SGD essential ORF list")

    essential_orfs = set()
    if essential_path is not None:
        try:
            for line in load_text_lines(essential_path):
                if not line.strip() or line.startswith("#"):
                    continue
                token = line.strip().split("\t")[0].strip()
                token_upper = token.upper()
                if token_upper in gene_to_orf:
                    essential_orfs.add(gene_to_orf[token_upper])
            if essential_orfs:
                return {
                    "source": source,
                    "available": True,
                    "essential_orfs": essential_orfs,
                }
        except Exception as exc:  # noqa: BLE001
            log(f"WARNING: failed to parse essential ORF file: {exc!r}")

    qualifier_hits = {
        orf
        for orf, meta in full_meta.items()
        if "essential" in meta.get("qualifier", "").lower()
    }
    if qualifier_hits:
        return {
            "source": "node_metadata_qualifier",
            "available": True,
            "essential_orfs": qualifier_hits,
        }

    return {
        "source": "unavailable",
        "available": False,
        "essential_orfs": set(),
    }


def main() -> None:
    log("Loading core processed data...")
    processed = load_pickle(PROCESSED_PATH)
    overlap = load_pickle(OVERLAP_PATH)
    correlation = load_pickle(CORRELATION_PATH)
    full_meta = load_full_node_metadata()
    sgdid_to_orf, gene_to_orf = build_orf_mappings(full_meta)

    ppi_pairs = processed["ppi_pairs"]
    raw_ppi_genes = set()
    for pair in ppi_pairs:
        raw_ppi_genes.update(pair)
    ppi_genes = {gene for gene in raw_ppi_genes if gene in full_meta}
    invalid_ppi_genes = sorted(raw_ppi_genes - ppi_genes)

    both_pairs = filter_valid_pairs(overlap["both_pairs"], ppi_genes)
    both_within = filter_valid_pairs(overlap["both_within"], ppi_genes)
    both_between = filter_valid_pairs(overlap["both_between"], ppi_genes)
    ppi_with_nonsig_gi = filter_valid_pairs(overlap["ppi_with_nonsig_gi"], ppi_genes)
    ppi_only_pairs = filter_valid_pairs(overlap["ppi_only_pairs"], ppi_genes)
    discordant_ppi_pairs = list(ppi_with_nonsig_gi) + list(ppi_only_pairs)

    concordant_genes = get_pair_genes(both_pairs)
    within_genes = get_pair_genes(both_within)
    between_genes = get_pair_genes(both_between)
    discordant_genes = get_pair_genes(discordant_ppi_pairs)

    log(
        "Loaded network context: "
        f"{len(ppi_pairs):,} raw PPI edges, {len(ppi_genes):,} valid PPI genes, "
        f"{len(both_pairs):,} concordant pairs."
    )
    if invalid_ppi_genes:
        log(
            f"Excluded {len(invalid_ppi_genes):,} malformed or unresolved PPI node IDs "
            "from metadata-dependent analyses."
        )

    # 1. GO enrichment
    log("\n[1/6] GO enrichment analysis")
    gaf_path, gaf_source = download_first_available(
        [
            "https://downloads.yeastgenome.org/curation/literature/gene_association.sgd.gz",
            "https://current.geneontology.org/annotations/sgd.gaf.gz",
        ],
        DOWNLOAD_DIR / "sgd.gaf.gz",
        "SGD GO annotations",
    )
    obo_path, obo_source = download_first_available(
        [
            "http://purl.obolibrary.org/obo/go/go-basic.obo",
            "https://current.geneontology.org/ontology/go-basic.obo",
        ],
        DOWNLOAD_DIR / "go-basic.obo",
        "GO basic ontology",
    )
    if gaf_path is None or obo_path is None:
        raise RuntimeError("GO enrichment requires both a GAF file and the GO OBO file.")

    log("Loading GO ontology...")
    godag = GODag(str(obo_path), optional_attrs={"relationship"})
    ns2assoc, gene2gos = parse_go_associations(gaf_path, ppi_genes, sgdid_to_orf, gene_to_orf)

    go_results = {
        "metadata": {
            "gaf_source": gaf_source,
            "obo_source": obo_source,
            "population_size": len(ppi_genes),
        },
        "within_complex": run_go_enrichment(within_genes, ppi_genes, ns2assoc, godag, "within_complex"),
        "between_complex": run_go_enrichment(between_genes, ppi_genes, ns2assoc, godag, "between_complex"),
        "overall_concordant": run_go_enrichment(concordant_genes, ppi_genes, ns2assoc, godag, "overall_concordant"),
    }

    go_summary_rows = []
    for label, result in go_results.items():
        if label == "metadata":
            continue
        for row in result["significant_terms"]:
            go_summary_rows.append(
                {
                    "analysis": label,
                    "study_size": result["study_size"],
                    **row,
                    "namespace_label": NS_LABELS[row["namespace"]],
                    "-log10_fdr": -math.log10(max(row["p_fdr_bh"], 1e-300)),
                }
            )
    go_summary_df = pd.DataFrame(go_summary_rows)
    if not go_summary_df.empty:
        go_summary_df.sort_values(["analysis", "p_fdr_bh", "GO"], inplace=True)
    go_summary_df.to_csv(GO_SUMMARY_CSV_PATH, index=False)
    save_pickle(go_results, GO_RESULTS_PATH)

    for label in ("within_complex", "between_complex", "overall_concordant"):
        result = go_results[label]
        log(
            f"  {label}: study genes={result['study_size']}, "
            f"significant GO terms={result['n_significant']}"
        )

    # 2. Human ortholog and disease analysis
    log("\n[2/6] Human ortholog and disease-gene analysis")
    mim_path, mim_source = download_first_available(
        ["https://ftp.ncbi.nih.gov/gene/DATA/mim2gene_medgen"],
        DOWNLOAD_DIR / "mim2gene_medgen.txt",
        "NCBI mim2gene_medgen",
    )
    human_gene_info_path, human_gene_info_source = download_first_available(
        ["https://ftp.ncbi.nih.gov/gene/DATA/GENE_INFO/Mammalia/Homo_sapiens.gene_info.gz"],
        DOWNLOAD_DIR / "Homo_sapiens.gene_info.gz",
        "NCBI Homo sapiens gene_info",
    )
    if mim_path is None or human_gene_info_path is None:
        raise RuntimeError("Disease-gene analysis requires mim2gene and Homo sapiens gene_info.")

    disease_geneids = load_mim2gene(mim_path)
    human_gene_maps = load_human_gene_info(human_gene_info_path)
    ortholog_cache = build_ortholog_cache(sorted(ppi_genes), full_meta)
    annotated_orthologs = annotate_ortholog_disease(ortholog_cache, human_gene_maps, disease_geneids)

    disease_flags = {orf: payload["has_disease_ortholog"] for orf, payload in annotated_orthologs.items()}
    human_ortholog_flags = {orf: payload["has_human_ortholog"] for orf, payload in annotated_orthologs.items()}

    disease_results = {
        "metadata": {
            "mim_source": mim_source,
            "human_gene_info_source": human_gene_info_source,
            "ortholog_cache_path": str(ORTHOLOG_CACHE_PATH),
            "n_disease_geneids": len(disease_geneids),
            "n_ppi_genes": len(ppi_genes),
            "n_ppi_genes_with_human_ortholog": int(sum(human_ortholog_flags.get(gene, False) for gene in ppi_genes)),
            "n_ppi_genes_with_disease_ortholog": int(sum(disease_flags.get(gene, False) for gene in ppi_genes)),
        },
        "per_gene": annotated_orthologs,
        "enrichment": {
            "within_complex": compute_enrichment_vs_background(within_genes, ppi_genes, disease_flags),
            "between_complex": compute_enrichment_vs_background(between_genes, ppi_genes, disease_flags),
            "overall_concordant": compute_enrichment_vs_background(concordant_genes, ppi_genes, disease_flags),
        },
        "study_gene_summaries": {
            "within_complex": {
                "n_study_genes": len(within_genes),
                "n_with_human_ortholog": int(sum(human_ortholog_flags.get(gene, False) for gene in within_genes)),
                "n_with_disease_ortholog": int(sum(disease_flags.get(gene, False) for gene in within_genes)),
            },
            "between_complex": {
                "n_study_genes": len(between_genes),
                "n_with_human_ortholog": int(sum(human_ortholog_flags.get(gene, False) for gene in between_genes)),
                "n_with_disease_ortholog": int(sum(disease_flags.get(gene, False) for gene in between_genes)),
            },
            "overall_concordant": {
                "n_study_genes": len(concordant_genes),
                "n_with_human_ortholog": int(sum(human_ortholog_flags.get(gene, False) for gene in concordant_genes)),
                "n_with_disease_ortholog": int(sum(disease_flags.get(gene, False) for gene in concordant_genes)),
            },
        },
    }
    save_pickle(disease_results, DISEASE_RESULTS_PATH)

    # 3. Network topology
    log("\n[3/6] Network topology analysis")
    graph = nx.Graph()
    for pair in ppi_pairs:
        if not set(pair).issubset(ppi_genes):
            continue
        gene_a, gene_b = tuple(pair)
        graph.add_edge(gene_a, gene_b)

    degree_map = dict(graph.degree())
    log(f"  Graph built: {graph.number_of_nodes():,} nodes, {graph.number_of_edges():,} edges")
    log("  Computing betweenness centrality on the full network...")
    betweenness_map = nx.betweenness_centrality(graph, normalized=True)
    clustering_map = nx.clustering(graph)

    concordant_edge_count_by_gene = Counter()
    for pair, _, _ in both_pairs:
        for gene in pair:
            concordant_edge_count_by_gene[gene] += 1

    detectability_rate = {
        gene: concordant_edge_count_by_gene.get(gene, 0) / degree_map[gene] if degree_map[gene] else np.nan
        for gene in degree_map
    }

    node_rows = []
    for gene in sorted(graph.nodes()):
        node_rows.append(
            {
                "orf": gene,
                "gene_name": full_meta.get(gene, {}).get("gene_name", gene),
                "cluster_id": full_meta.get(gene, {}).get("cluster_id", ""),
                "degree": degree_map.get(gene, 0),
                "betweenness": betweenness_map.get(gene, np.nan),
                "clustering": clustering_map.get(gene, np.nan),
                "gi_detectability_rate": detectability_rate.get(gene, np.nan),
                "in_concordant": gene in concordant_genes,
                "in_discordant": gene in discordant_genes,
                "in_within_concordant": gene in within_genes,
                "in_between_concordant": gene in between_genes,
            }
        )
    node_metrics_df = pd.DataFrame(node_rows)

    degree_threshold = float(np.quantile(node_metrics_df["degree"], 0.9))
    hub_genes = set(node_metrics_df.loc[node_metrics_df["degree"] >= degree_threshold, "orf"])
    node_metrics_df["is_hub"] = node_metrics_df["orf"].isin(hub_genes)

    network_results = {
        "graph_summary": {
            "n_nodes": int(graph.number_of_nodes()),
            "n_edges": int(graph.number_of_edges()),
            "degree_90th_percentile": degree_threshold,
            "n_hubs": len(hub_genes),
            "concordant_gene_overlap_with_discordant": len(concordant_genes & discordant_genes),
            "within_gene_overlap_with_between": len(within_genes & between_genes),
        },
        "node_metrics": node_metrics_df,
        "tests": {
            "concordant_vs_discordant_degree": mann_whitney_summary(
                node_metrics_df.loc[node_metrics_df["in_concordant"], "degree"],
                node_metrics_df.loc[node_metrics_df["in_discordant"], "degree"],
                "concordant",
                "discordant",
            ),
            "concordant_vs_discordant_betweenness": mann_whitney_summary(
                node_metrics_df.loc[node_metrics_df["in_concordant"], "betweenness"],
                node_metrics_df.loc[node_metrics_df["in_discordant"], "betweenness"],
                "concordant",
                "discordant",
            ),
            "concordant_vs_discordant_clustering": mann_whitney_summary(
                node_metrics_df.loc[node_metrics_df["in_concordant"], "clustering"],
                node_metrics_df.loc[node_metrics_df["in_discordant"], "clustering"],
                "concordant",
                "discordant",
            ),
            "within_vs_between_degree": mann_whitney_summary(
                node_metrics_df.loc[node_metrics_df["in_within_concordant"], "degree"],
                node_metrics_df.loc[node_metrics_df["in_between_concordant"], "degree"],
                "within",
                "between",
            ),
            "within_vs_between_betweenness": mann_whitney_summary(
                node_metrics_df.loc[node_metrics_df["in_within_concordant"], "betweenness"],
                node_metrics_df.loc[node_metrics_df["in_between_concordant"], "betweenness"],
                "within",
                "between",
            ),
            "hub_vs_nonhub_detectability": mann_whitney_summary(
                node_metrics_df.loc[node_metrics_df["is_hub"], "gi_detectability_rate"],
                node_metrics_df.loc[~node_metrics_df["is_hub"], "gi_detectability_rate"],
                "hub",
                "non_hub",
            ),
        },
    }
    save_pickle(network_results, NETWORK_RESULTS_PATH)

    # 4. Complex deep-dive
    log("\n[4/6] Complex deep-dive analysis")
    essential_info = try_load_essential_gene_set(full_meta, gene_to_orf)
    essential_orfs = essential_info["essential_orfs"]

    cluster_to_pairs = defaultdict(list)
    for record in both_within:
        pair = tuple(record[0])
        cluster_id = full_meta[pair[0]]["cluster_id"]
        if cluster_id and cluster_id == full_meta[pair[1]]["cluster_id"]:
            cluster_to_pairs[cluster_id].append(record)

    top_clusters = sorted(cluster_to_pairs.items(), key=lambda item: len(item[1]), reverse=True)[:10]
    bp_assoc = {"BP": ns2assoc["BP"]}
    cluster_rows = []
    for cluster_id, cluster_pairs in top_clusters:
        cluster_members = {
            gene for gene, meta in full_meta.items() if meta.get("cluster_id") == cluster_id
        }
        unique_pair_genes = get_pair_genes(cluster_pairs)
        pair_eps = np.array([entry[2]["mean_eps"] for entry in cluster_pairs], dtype=float)
        pair_ppi = np.array([entry[1]["score"] for entry in cluster_pairs], dtype=float)
        pos_fraction = float(np.mean(pair_eps > 0)) if len(pair_eps) else np.nan
        mean_eps = float(np.mean(pair_eps)) if len(pair_eps) else np.nan
        mean_abs_eps = float(np.mean(np.abs(pair_eps))) if len(pair_eps) else np.nan
        mean_ppi = float(np.mean(pair_ppi)) if len(pair_ppi) else np.nan

        if essential_info["available"]:
            gene_essential_fraction = (
                sum(gene in essential_orfs for gene in cluster_members) / len(cluster_members)
                if cluster_members
                else np.nan
            )
            pair_essential_fraction = (
                sum(bool(set(pair) & essential_orfs) for pair, _, _ in cluster_pairs) / len(cluster_pairs)
                if cluster_pairs
                else np.nan
            )
        else:
            gene_essential_fraction = np.nan
            pair_essential_fraction = np.nan

        cluster_go = run_go_enrichment(cluster_members, ppi_genes, bp_assoc, godag, f"cluster_{cluster_id}")
        top_bp = cluster_go["top_by_namespace"]["BP"][:3]

        cluster_member_meta = [full_meta[gene] for gene in cluster_members if gene in full_meta]
        complex_name_counts = Counter(
            complex_name
            for meta in cluster_member_meta
            for complex_name in meta.get("complex_names", [])
        )
        ortholog_symbols = Counter(
            ortholog["symbol"]
            for gene in cluster_members
            for ortholog in annotated_orthologs.get(gene, {}).get("human_orthologs", [])
            if ortholog.get("symbol")
        )
        cluster_correlation = next(
            (item for item in correlation["cluster_stats"] if item["cluster_id"] == cluster_id),
            None,
        )

        cluster_rows.append(
            {
                "cluster_id": cluster_id,
                "cluster_name": complex_name_counts.most_common(1)[0][0]
                if complex_name_counts
                else f"Cluster {cluster_id}",
                "n_both_pairs": len(cluster_pairs),
                "n_cluster_members": len(cluster_members),
                "n_pair_genes": len(unique_pair_genes),
                "pos_gi_fraction": pos_fraction,
                "mean_eps": mean_eps,
                "mean_abs_eps": mean_abs_eps,
                "mean_ppi_score": mean_ppi,
                "gene_essential_fraction": gene_essential_fraction,
                "pair_has_essential_fraction": pair_essential_fraction,
                "top_go_bp_terms": top_bp,
                "candidate_human_complex_orthologs": ortholog_symbols.most_common(5),
                "corr_r": cluster_correlation["spearman_r"] if cluster_correlation else np.nan,
                "corr_p": cluster_correlation["spearman_p"] if cluster_correlation else np.nan,
            }
        )

    cluster_rows.sort(key=lambda row: row["n_both_pairs"], reverse=True)
    best_correlated_clusters = sorted(
        [row for row in cluster_rows if not pd.isna(row["corr_r"])],
        key=lambda row: row["corr_r"],
        reverse=True,
    )
    complex_results = {
        "essential_gene_source": essential_info["source"],
        "essential_gene_available": essential_info["available"],
        "clusters": cluster_rows,
        "highest_within_complex_correlations": best_correlated_clusters[:10],
    }
    save_pickle(complex_results, COMPLEX_RESULTS_PATH)

    # 5. GI sign predictive model
    log("\n[5/6] GI sign predictive model")
    model_rows = []
    for pair, ppi_data, gi_data in both_pairs:
        gene_a, gene_b = sorted(pair)
        eps = gi_data["mean_eps"]
        if eps == 0:
            continue
        model_rows.append(
            {
                "gene_a": gene_a,
                "gene_b": gene_b,
                "ppi_score": ppi_data["score"],
                "within_complex": int(
                    bool(
                        full_meta[gene_a]["cluster_id"]
                        and full_meta[gene_a]["cluster_id"] == full_meta[gene_b]["cluster_id"]
                    )
                ),
                "degree_a": degree_map.get(gene_a, 0),
                "degree_b": degree_map.get(gene_b, 0),
                "go_jaccard": compute_go_jaccard(gene_a, gene_b, gene2gos),
                "gi_sign_positive": int(eps > 0),
            }
        )
    model_df = pd.DataFrame(model_rows)
    feature_cols = ["ppi_score", "within_complex", "degree_a", "degree_b", "go_jaccard"]
    baseline_cols = ["ppi_score"]
    X = model_df[feature_cols]
    y = model_df["gi_sign_positive"].astype(int)
    X_baseline = model_df[baseline_cols]

    preprocessor = ColumnTransformer(
        transformers=[
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                feature_cols,
            )
        ]
    )
    baseline_preprocessor = ColumnTransformer(
        transformers=[
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                baseline_cols,
            )
        ]
    )
    model_pipeline = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("classifier", LogisticRegression(max_iter=5000, solver="lbfgs")),
        ]
    )
    baseline_pipeline = Pipeline(
        steps=[
            ("preprocessor", baseline_preprocessor),
            ("classifier", LogisticRegression(max_iter=5000, solver="lbfgs")),
        ]
    )

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    full_prob = cross_val_predict(model_pipeline, X, y, cv=cv, method="predict_proba", n_jobs=1)[:, 1]
    baseline_prob = cross_val_predict(baseline_pipeline, X_baseline, y, cv=cv, method="predict_proba", n_jobs=1)[:, 1]
    full_auc = roc_auc_score(y, full_prob)
    baseline_auc = roc_auc_score(y, baseline_prob)
    full_fpr, full_tpr, _ = roc_curve(y, full_prob)
    baseline_fpr, baseline_tpr, _ = roc_curve(y, baseline_prob)

    model_pipeline.fit(X, y)
    coefficients = model_pipeline.named_steps["classifier"].coef_[0]
    feature_importances = [
        {"feature": feature, "coefficient": float(coef), "abs_coefficient": float(abs(coef))}
        for feature, coef in zip(feature_cols, coefficients)
    ]
    feature_importances.sort(key=lambda item: item["abs_coefficient"], reverse=True)

    predictive_results = {
        "n_pairs": len(model_df),
        "positive_fraction": float(y.mean()),
        "feature_columns": feature_cols,
        "baseline_columns": baseline_cols,
        "auc_full_model": float(full_auc),
        "auc_baseline": float(baseline_auc),
        "delta_auc": float(full_auc - baseline_auc),
        "roc_full_model": {"fpr": full_fpr.tolist(), "tpr": full_tpr.tolist()},
        "roc_baseline": {"fpr": baseline_fpr.tolist(), "tpr": baseline_tpr.tolist()},
        "feature_importances": feature_importances,
        "model_table": model_df,
    }
    save_pickle(predictive_results, PREDICTIVE_RESULTS_PATH)

    # 6. Subcellular compartment analysis
    log("\n[6/6] Subcellular compartment analysis")
    compartment_map = {gene: parse_compartments(meta.get("location_raw", "")) for gene, meta in full_meta.items()}
    within_compartments = compute_pair_compartment_stats(both_within, compartment_map, "within_concordant")
    between_compartments = compute_pair_compartment_stats(both_between, compartment_map, "between_concordant")
    discordant_compartments = compute_pair_compartment_stats(discordant_ppi_pairs, compartment_map, "discordant_ppi")

    def pair_group_fisher(group_a: dict, group_b: dict) -> dict:
        table = [
            [
                group_a["n_shared_compartment"],
                group_a["n_pairs"] - group_a["n_shared_compartment"],
            ],
            [
                group_b["n_shared_compartment"],
                group_b["n_pairs"] - group_b["n_shared_compartment"],
            ],
        ]
        odds_ratio, p_value = fisher_exact(table)
        return {
            "table": table,
            "odds_ratio": odds_ratio,
            "p_value": p_value,
        }

    compartment_results = {
        "within_concordant": within_compartments,
        "between_concordant": between_compartments,
        "discordant_ppi": discordant_compartments,
        "tests": {
            "within_vs_between": pair_group_fisher(within_compartments, between_compartments),
            "within_vs_discordant": pair_group_fisher(within_compartments, discordant_compartments),
            "between_vs_discordant": pair_group_fisher(between_compartments, discordant_compartments),
        },
    }
    save_pickle(compartment_results, COMPARTMENT_RESULTS_PATH)

    # Final summary
    log("\n=== DEEPER ANALYSIS SUMMARY ===")
    log(
        "GO enrichment:"
        f" within={go_results['within_complex']['n_significant']},"
        f" between={go_results['between_complex']['n_significant']},"
        f" overall={go_results['overall_concordant']['n_significant']} significant terms"
    )

    for label in ("within_complex", "between_complex", "overall_concordant"):
        summary = disease_results["study_gene_summaries"][label]
        enrich = disease_results["enrichment"][label]
        log(
            f"Disease orthologs {label}: "
            f"{summary['n_with_disease_ortholog']}/{summary['n_study_genes']} genes, "
            f"OR={enrich['odds_ratio_corrected']:.3f}, 95% CI={enrich['ci95_low']:.3f}-{enrich['ci95_high']:.3f}, "
            f"p={enrich['p_value']:.3e}"
        )

    topology_test = network_results["tests"]["concordant_vs_discordant_degree"]
    log(
        "Network topology concordant vs discordant degree: "
        f"median={topology_test['median_a']:.2f} vs {topology_test['median_b']:.2f}, "
        f"p={topology_test['p_value']:.3e}"
    )
    hub_test = network_results["tests"]["hub_vs_nonhub_detectability"]
    log(
        "Hub vs non-hub GI detectability: "
        f"mean={hub_test['mean_a']:.4f} vs {hub_test['mean_b']:.4f}, "
        f"p={hub_test['p_value']:.3e}"
    )

    if cluster_rows:
        top_cluster = cluster_rows[0]
        log(
            "Top complex by concordant-pair count: "
            f"{top_cluster['cluster_name']} (cluster {top_cluster['cluster_id']}), "
            f"n={top_cluster['n_both_pairs']}, "
            f"positive_GI_fraction={top_cluster['pos_gi_fraction']:.3f}, "
            f"mean_eps={top_cluster['mean_eps']:.4f}, mean_ppi={top_cluster['mean_ppi_score']:.3f}"
        )
    if best_correlated_clusters:
        best_cluster = best_correlated_clusters[0]
        log(
            "Highest within-complex PPI-to-GI correlation among top complexes: "
            f"{best_cluster['cluster_name']} (cluster {best_cluster['cluster_id']}), "
            f"r={best_cluster['corr_r']:.3f}, p={best_cluster['corr_p']:.3e}"
        )

    log(
        "Predictive model: "
        f"AUC full={predictive_results['auc_full_model']:.3f}, "
        f"baseline={predictive_results['auc_baseline']:.3f}, "
        f"delta={predictive_results['delta_auc']:.3f}"
    )
    log(
        "Top predictive features: "
        + ", ".join(
            f"{item['feature']} ({item['coefficient']:.3f})"
            for item in predictive_results["feature_importances"][:5]
        )
    )

    for label, result in (
        ("within", within_compartments),
        ("between", between_compartments),
        ("discordant", discordant_compartments),
    ):
        log(
            f"Compartment sharing {label}: "
            f"{result['n_shared_compartment']}/{result['n_pairs']} "
            f"({100 * result['shared_rate_all_pairs']:.1f}%)"
        )

    log("\nSaved analysis outputs:")
    for path in [
        GO_RESULTS_PATH,
        GO_SUMMARY_CSV_PATH,
        DISEASE_RESULTS_PATH,
        NETWORK_RESULTS_PATH,
        COMPLEX_RESULTS_PATH,
        PREDICTIVE_RESULTS_PATH,
        COMPARTMENT_RESULTS_PATH,
    ]:
        log(f"  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
