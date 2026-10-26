# Changelog

All notable changes are documented here. This project follows semantic versioning once 0.1.0 is tagged.

## 0.1.0 - Unreleased

### Added

- Read-only GO-CAM tools for model, taxon, and PMID retrieval using current GO API routes.
- GO-CAM activity, complex/member, causal association, evidence, and provenance summaries.
- Network completeness metadata and indeterminate gap results for incomplete inputs.
- Offline regression tests for GO-CAM, Rhea parsing, graph gaps, HTTP behavior, caching, and local PDB safety.
- An opt-in release smoke runner covering every external service and public network workflow.
- Data-source, contribution, and security documentation.

### Changed

- Corrected `search_go_terms` to accept exact GO term IDs only and use `/bioentity/function/{id}`.
- Cached successful GET responses, handled HTTP 204, bounded `Retry-After`, and avoided sleeping after the final retry.
- Replaced regular-expression extraction of Rhea participants with an HTML parser and preserved Rhea direction semantics.
- Restricted local PDB reads to configured roots and bounded gzip expansion.
- Reduced default UniProt comment payloads and clarified Open Targets/FAERS interpretation.

### Security

- Added path canonicalization, extension validation, regular-file checks, and symlink-escape prevention for local PDB input.

### License

- Changed the project software license from MIT to Apache License 2.0 by explicit copyright-holder instruction and added `NOTICE`.
