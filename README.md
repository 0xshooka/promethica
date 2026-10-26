# Promethica

Promethica is a lightweight, read-only MCP server that lets ChatGPT, Claude, and other MCP clients query life-science databases and run deterministic reaction-network analyses. Version 0.1.0 focuses on traceability, bounded responses, and making failures visible instead of turning missing API data into biological claims.

Promethica integrates UniProt, Rhea, ChEBI identifiers, RCSB PDB, Gene Ontology and GO-CAM, Open Targets, and ChEMBL. It does not call a paid LLM API and does not use an LLM for database retrieval or graph calculations.

## Installation

Python 3.11 or later is required.

```bash
git clone https://github.com/0xshooka/promethica.git
cd promethica
uv sync --extra dev
uv run python promethica.py
```

With pip:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python promethica.py
```

## MCP client configuration

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "promethica": {
      "command": "/absolute/path/to/promethica/.venv/bin/python",
      "args": ["/absolute/path/to/promethica/promethica.py"],
      "env": {
        "PROMETHICA_ALLOWED_PDB_DIRS": "/absolute/path/to/approved/pdb-files"
      }
    }
  }
}
```

The server uses stdio. Do not write diagnostic output to stdout from integrations around it.

## Tools

### UniProt

- `search_proteins(query, organism, size, format)`
- `get_protein_info(accession, format, fields)` — lightweight by default; use `fields=["all"]` explicitly for the full record
- `get_protein_sequence(accession, format)`
- `get_protein_features(accession, feature_types, include_comments, include_keywords, max_features)`
- `search_by_gene(gene, organism, size)`
- `get_primary_protein_for_gene(gene, organism)`

### RCSB PDB

- `search_pdb_structures(query, size)`
- `get_pdb_structure_info(pdb_id)`
- `search_similar_structures(pdb_id, assembly_id, operator, size)`
- `parse_local_pdb(file_path, enrich)`

Local PDB access is restricted to `PROMETHICA_ALLOWED_PDB_DIRS`, a platform path-separator-delimited list. If unset, only the server's working directory is allowed. Paths are resolved before authorization, symlink escapes are rejected, only `.pdb`, `.ent`, and their gzip forms are accepted, and both compressed and expanded input are size-limited. File contents are never returned or sent to an external service; only parsed identifiers can be used for optional enrichment calls.

### Rhea and reaction networks

- `search_reactions(query, limit)`
- `get_protein_reactions(accession)`
- `build_reaction_network(accessions, max_reactions, exclude_currency, as_cytoscape)`
- `detect_network_gaps(accessions, max_reactions, exclude_currency)`
- `export_network_cytoscape(accessions, max_reactions, exclude_currency)`
- `propose_gap_node_candidates(accessions, ...)`

Rhea's left and right sides are not automatically substrates and products. Promethica preserves Rhea direction (`left_to_right`, `right_to_left`, `bidirectional`, or `undefined`) and keeps non-ChEBI `RHEA-COMP` participants in graph connectivity without mislabeling them as boundary metabolites. Undefined-direction reactions contribute to weak connectivity but not input/output boundary claims. Degree-one metabolites on those reactions are reported separately as `structural_boundaries` and can seed direction-neutral candidate searches. Candidate bridges are structural hypotheses, not proof of a metabolically feasible pathway. Malformed or incomplete Rhea equations are reported as `parse_error` failures instead of being accepted as disconnected reactions.

Network responses include completeness metadata: failed UniProt/Rhea fetches, accessions with no reaction annotation, reaction IDs skipped by `max_reactions`, and whether analysis is complete. Gap presence is reported as indeterminate when required data is incomplete.

### Gene Ontology and GO-CAM

- `get_go_term_annotations(go_term, limit)` — retrieves bounded annotations for an exact `GO:nnnnnnn` ID; it is not keyword search
- `search_go_terms(go_term, limit)` — backward-compatible alias retained for existing MCP clients
- `get_gocam_model(model_id, include_details, include_evidence, max_activities)`
- `list_gocam_models_by_taxon(taxon, limit)`
- `list_gocam_models_by_publication(pmid, limit)`

Examples:

```text
get_gocam_model(model_id="gomodel:5323da1800000002", include_evidence=true)
list_gocam_models_by_taxon(taxon="NCBITaxon:9606", limit=10)
list_gocam_models_by_publication(pmid="PMID:17996703")
```

The list routes currently expose no API-side pagination parameters. Promethica obtains the API list, applies a bounded client-side return limit, and reports both counts. A GO-CAM activity may be enabled by one gene product, a protein complex with multiple members, or no populated optional field; the parser does not assume a single protein. Causal edges report whether evidence is present, separately from the existence of the edge.

### Disease, drugs, and integrated retrieval

- `get_disease_associations(gene, size)`
- `get_drugs_for_target(gene, size)`
- `get_known_adverse_effects(drug, size)`
- `get_drug_details(chembl_id)`
- `comprehensive_protein_analysis(accession, fields, sources, max_pdb_results)`

Open Targets association scores are ranking signals, not probabilities of causality or drug success. FAERS data are spontaneous-reporting signals and do not establish causality, incidence, or individual risk.

## Response and HTTP behavior

Tools return JSON strings. API failure is represented separately from a valid zero-result response. Successful GETs are cached for 10 minutes; POSTs are never cached. HTTP 204 is a successful empty response. HTTP 429 and transient 5xx/network failures are retried up to three attempts, respecting numeric or HTTP-date `Retry-After` values with a bounded delay. The shared HTTP client keeps connection pooling and global concurrency limits.

## Tests

```bash
uv run pytest
```

The default suite mocks external APIs and runs offline. Live endpoint checks are intentionally separate from the unit suite so lack of network access does not fail normal development.

Before a release, run the opt-in bounded live smoke suite:

```bash
uv run python release_smoke_test.py
```

## Scientific limitations

- Database records are observations and curated assertions that can be incomplete or revised.
- A zero-result response differs from an API failure and from the absence of an annotation.
- GO-CAM causal relations are curated model assertions with evidence; model presence alone does not prove a conclusion.
- Currency-metabolite exclusion can remove real connections or suppress hub-driven false connections.
- Weakly disconnected reaction components are gap candidates only. Compartment, thermodynamics, kinetics, expression, and organism context are not evaluated.
- No output is medical advice or a substitute for primary literature and experimental validation.

See [DATA_SOURCES.md](DATA_SOURCES.md) for source, citation, and data-license notes.

## Contributing and changes

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## License

Promethica software is licensed under Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Upstream database data retain their own licenses and attribution requirements.
