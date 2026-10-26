# Data sources, attribution, and citation

Promethica queries upstream services at runtime and does not redistribute a bundled database snapshot. Users remain responsible for the current terms, attribution, and citation requirements of each provider.

| Source | Use in Promethica | Terms and citation starting point |
| --- | --- | --- |
| UniProt | Protein records and catalytic annotations | [UniProt license and API documentation](https://www.uniprot.org/api-documentation) — copyrightable database content is CC BY 4.0 |
| Rhea | Curated biochemical reactions and ChEBI participants | [Rhea license](https://www.rhea-db.org/help/license-disclaimer) — CC BY 4.0; cite Rhea for published use |
| Gene Ontology / GO-CAM | GO annotations and causal activity models | [GO citation policy](https://geneontology.org/docs/go-citation-policy/) — CC BY 4.0; published or redistributed results should identify the relevant release/date where available |
| ChEBI identifiers | Chemical entity identifiers referenced by Rhea | Review the current [ChEBI licensing page](https://www.ebi.ac.uk/chebi/aboutChebiForward.do) for the exact use case |
| RCSB PDB | Structure search and metadata | Review the current [RCSB PDB policies](https://www.rcsb.org/pages/policies) and cite the PDB entry and primary structure publication |
| Open Targets | Target-disease, drug, and FAERS-derived signals | Review the current [Open Targets terms](https://platform.opentargets.org/licence) and cite the Platform release used |
| ChEMBL | Compound, mechanism, and indication records | Review the current [ChEMBL licensing information](https://www.ebi.ac.uk/chembl/downloads) and cite the release used |

API responses can change independently of Promethica. For reproducible work, record Promethica version, query inputs, retrieval date, upstream database release when exposed, stable IDs, limits, and completeness metadata. GO specifically asks tools using its data to expose release/date and DOI where applicable; the live API used here does not currently include release metadata in every response, so callers should record the retrieval date and consult the current GO release for publication-grade provenance.

The repository's software license does not replace or relicense upstream data. Before redistributing cached or transformed records, verify the provider's current terms rather than relying only on this summary.
