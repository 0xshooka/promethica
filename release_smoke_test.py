#!/usr/bin/env python3
"""Opt-in live API smoke tests for release validation.

This script makes bounded, read-only requests to every external service used by
Promethica. It is intentionally excluded from the offline pytest suite.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

import promethica as p


Check = tuple[str, Callable[[], Awaitable[str]]]


def _json_success(payload: str) -> tuple[bool, str]:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return bool(payload.strip()), "non-JSON payload"
    if isinstance(value, dict) and value.get("error"):
        return False, str(value["error"])
    return True, "ok"


async def main() -> int:
    checks: list[Check] = [
        ("UniProt search", lambda: p.search_proteins("GAPDH", "Homo sapiens", 1)),
        ("UniProt record", lambda: p.get_protein_info("P04406", fields=["basic", "function"])),
        ("UniProt sequence", lambda: p.get_protein_sequence("P04406")),
        ("UniProt gene", lambda: p.search_by_gene("GAPDH", "Homo sapiens", 1)),
        ("UniProt primary", lambda: p.get_primary_protein_for_gene("GAPDH")),
        ("UniProt features", lambda: p.get_protein_features("P04406", max_features=2)),
        ("Rhea search", lambda: p.search_reactions("RHEA:10300", 1)),
        ("UniProt/Rhea reactions", lambda: p.get_protein_reactions("P04406")),
        ("RCSB PDB search", lambda: p.search_pdb_structures("crambin", 1)),
        ("RCSB PDB record", lambda: p.get_pdb_structure_info("1CRN")),
        ("RCSB structure similarity", lambda: p.search_similar_structures("1CRN", size=1)),
        ("GO term annotations", lambda: p.get_go_term_annotations("GO:0005524", 1)),
        ("GO-CAM model", lambda: p.get_gocam_model("5323da1800000002", include_evidence=True)),
        ("GO-CAM taxon", lambda: p.list_gocam_models_by_taxon("NCBITaxon:9606", 1)),
        ("GO-CAM publication", lambda: p.list_gocam_models_by_publication("PMID:17996703", 1)),
        ("Open Targets diseases", lambda: p.get_disease_associations("BRCA1", 1)),
        ("Open Targets drugs", lambda: p.get_drugs_for_target("EGFR", 1)),
        ("Open Targets/FAERS", lambda: p.get_known_adverse_effects("CHEMBL25", 1)),
        ("ChEMBL details", lambda: p.get_drug_details("CHEMBL25")),
        ("Integrated analysis", lambda: p.comprehensive_protein_analysis(
            "P04406", sources=["uniprot", "reactions", "pdb"], max_pdb_results=1
        )),
        ("Reaction network", lambda: p.build_reaction_network(["P04406"], max_reactions=2)),
        ("Network gaps", lambda: p.detect_network_gaps(["P04406"], max_reactions=2)),
        ("Cytoscape export", lambda: p.export_network_cytoscape(["P04406"], max_reactions=2)),
        ("Gap candidates", lambda: p.propose_gap_node_candidates(
            ["P04406"], max_reactions=2, max_boundary_metabolites=1,
            max_candidates_per_boundary=1
        )),
    ]

    failures: list[str] = []
    for name, call in checks:
        started = time.monotonic()
        try:
            payload = await call()
            ok, detail = _json_success(payload)
        except Exception as exc:  # smoke runner must report and continue
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        print(f"{'PASS' if ok else 'FAIL'}\t{elapsed:5.2f}s\t{name}\t{detail}")
        if not ok:
            failures.append(f"{name}: {detail}")

    if p._client is not None:
        await p._client.aclose()
    print(f"SUMMARY\t{len(checks) - len(failures)}/{len(checks)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
