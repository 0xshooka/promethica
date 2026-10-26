import asyncio
import gzip
import json
import time

import httpx
import pytest
from cachetools import TTLCache
from unittest.mock import AsyncMock

import promethica as p


pytestmark = pytest.mark.asyncio


GOCAM = {
    "id": "gomodel:abc123",
    "title": "test model",
    "taxon": "NCBITaxon:9606",
    "status": "production",
    "activities": [
        {
            "id": "gomodel:abc123/a1",
            "enabled_by": {
                "type": "EnabledByProteinComplexAssociation",
                "term": "GO:0000001",
                "members": ["UniProtKB:P1", "UniProtKB:P2"],
                "evidence": [{"term": "ECO:0000314", "reference": "PMID:1"}],
            },
            "molecular_function": {"term": "GO:0000002"},
            "causal_associations": [{
                "predicate": "RO:0002409",
                "downstream_activity": "gomodel:abc123/a2",
                "evidence": [{"term": "ECO:0000315", "reference": "PMID:2"}],
            }],
        },
        {"id": "gomodel:abc123/a2", "enabled_by": None,
         "molecular_function": None, "causal_associations": None},
    ],
    "objects": [
        {"id": "GO:0000001", "label": "complex"},
        {"id": "GO:0000002", "label": "activity"},
        {"id": "UniProtKB:P1", "label": "one"},
        {"id": "UniProtKB:P2", "label": "two"},
    ],
}


async def test_go_term_id_endpoint(monkeypatch):
    request = AsyncMock(return_value={"associations": []})
    monkeypatch.setattr(p, "make_api_request", request)
    result = json.loads(await p.search_go_terms("go:0005524"))
    assert result["go_term"] == "GO:0005524"
    assert "/bioentity/function/GO:0005524" in request.await_args.args[0]
    assert "error" in json.loads(await p.search_go_terms("kinase"))
    alias = json.loads(await p.get_go_term_annotations("GO:0005524"))
    assert alias["go_term"] == "GO:0005524"


async def test_gocam_model_complex_evidence_and_missing_fields(monkeypatch):
    monkeypatch.setattr(p, "make_api_request", AsyncMock(return_value=GOCAM))
    result = json.loads(await p.get_gocam_model("gomodel:abc123", True, True))
    assert result["activities"][0]["enabled_by"]["members"][1]["id"] == "UniProtKB:P2"
    assert result["causal_associations"][0]["has_evidence"] is True
    assert result["causal_associations"][0]["evidence"][0]["eco_id"] == "ECO:0000315"
    assert result["activities"][1]["enabled_by"] is None


async def test_gocam_taxon_list_client_limit(monkeypatch):
    async def fake(url, *args, **kwargs):
        if "/taxon/" in url:
            return [{"gocam": "http://model.geneontology.org/a"},
                    {"gocam": "http://model.geneontology.org/b"}]
        return {**GOCAM, "id": f"gomodel:{url.rsplit('/', 1)[-1]}"}
    monkeypatch.setattr(p, "make_api_request", fake)
    result = json.loads(await p.list_gocam_models_by_taxon("9606", 1))
    assert result["taxon"] == "NCBITaxon:9606"
    assert result["total_returned_by_api"] == 2 and result["returned_to_client"] == 1
    assert result["truncated"] is True


async def test_gocam_publication_empty_is_success(monkeypatch):
    monkeypatch.setattr(p, "make_api_request", AsyncMock(return_value=[]))
    result = json.loads(await p.list_gocam_models_by_publication("PMID:123"))
    assert result["models"] == [] and result["total_returned_by_api"] == 0


async def test_rhea_parser_chebi_normalization_and_multiple_participants():
    equation = ('<span><a data-molid="chebi:1">A<sup>+</sup></a></span> + '
                '<span><a data-molid="CHEBI:2"><i>B</i></a></span> + '
                '<span><a data-molid="rhea-comp:9">carrier</a></span> = '
                '<span><a data-molid="chebi:3">C</a></span>')
    left, right = p._parse_rhea_participants(equation)
    assert left == [("CHEBI:1", "A+"), ("CHEBI:2", "B"), ("RHEA-COMP:9", "carrier")]
    assert right == [("CHEBI:3", "C")]
    assert p._chebi_norm("15377") == "CHEBI:15377"
    with pytest.raises(ValueError):
        p._chebi_norm("CHEBI:nope")


@pytest.mark.parametrize("equation", [
    '<span><a data-molid="chebi:1">A</a></span>',
    '<span><a data-molid="chebi:1">A</a></span> = <span>missing id</span>',
    '<span><a data-molid="chebi:1">A</span> = <span><a data-molid="chebi:2">B</a></span>',
])
async def test_rhea_parser_rejects_unsplittable_empty_or_partial_results(equation):
    with pytest.raises(ValueError):
        p._parse_rhea_participants(equation)


async def test_rhea_parse_failure_is_explicit_and_marks_network_partial(monkeypatch):
    malformed = {
        "results": [{
            "id": 123,
            "equation": "A = B",
            "htmlequation": '<span><a data-molid="chebi:1">A</a></span>',
        }]
    }
    monkeypatch.setattr(p, "make_api_request", AsyncMock(return_value=malformed))
    parsed = await p._fetch_rhea_reaction("RHEA:123")
    assert parsed["_error_type"] == "parse_error"
    assert parsed["rhea_id"] == "RHEA:123"

    monkeypatch.setattr(p, "_uniprot_catalytic_reactions", AsyncMock(return_value={
        "reactions": [{"rhea_id": "RHEA:123", "name": "bad", "ec": None}]
    }))
    graph = await p._build_reaction_network_data(["P1"], 10, True)
    assert graph["analysis_possible"] is False
    assert graph["reaction_fetch_failures"][0]["error_type"] == "parse_error"
    assert graph["_meta"]["completeness"] == "partial"
    assert graph["_meta"]["reaction_fetch_failures"][0]["error_type"] == "parse_error"


async def test_rhea_parse_failure_propagates_to_partial_network_metadata(monkeypatch):
    async def catalytic(_acc):
        return {"reactions": [
            {"rhea_id": "RHEA:1", "name": "good", "ec": None},
            {"rhea_id": "RHEA:2", "name": "bad", "ec": None},
        ]}

    async def reaction(rid):
        if rid == "RHEA:2":
            return {"_error": "parse failed", "_error_type": "parse_error", "_status": None,
                    "detail": "could not split"}
        return {"equation": "a = b", "direction": "undefined",
                "left": [("CHEBI:10", "a")], "right": [("CHEBI:20", "b")]}

    monkeypatch.setattr(p, "_uniprot_catalytic_reactions", catalytic)
    monkeypatch.setattr(p, "_fetch_rhea_reaction", reaction)
    graph = await p._build_reaction_network_data(["P1"], 10, True)
    assert graph["_meta"]["completeness"] == "partial"
    assert graph["_meta"]["incomplete_reasons"] == ["rhea_fetch_failures"]
    assert graph["_meta"]["reaction_fetch_failures"][0]["error_type"] == "parse_error"


async def test_rhea_search_reports_partial_parse_failures(monkeypatch):
    response = {"count": 2, "results": [
        {"id": 1, "equation": "A = B", "htmlequation":
         '<span><a data-molid="chebi:1">A</a></span> = '
         '<span><a data-molid="chebi:2">B</a></span>'},
        {"id": 2, "equation": "C = D", "htmlequation": "not parseable"},
    ]}
    monkeypatch.setattr(p, "make_api_request", AsyncMock(return_value=response))
    result = json.loads(await p.search_reactions("test"))
    assert result["returned"] == 1
    assert result["_meta"]["completeness"] == "partial"
    assert result["_meta"]["parse_failures"][0]["_error_type"] == "parse_error"


async def test_currency_exclusion_and_weak_components(monkeypatch):
    async def catalytic(acc):
        return {"reactions": [{"rhea_id": f"RHEA:{1 if acc == 'A' else 2}", "name": acc, "ec": None}]}
    async def reaction(rid):
        if rid.endswith("1"):
            return {"equation": "x = y", "direction": "undefined",
                    "left": [("CHEBI:15377", "water"), ("CHEBI:10", "x")],
                    "right": [("CHEBI:20", "y")]}
        return {"equation": "z => q", "direction": "left_to_right",
                "left": [("CHEBI:30", "z")], "right": [("CHEBI:40", "q")]}
    monkeypatch.setattr(p, "_uniprot_catalytic_reactions", catalytic)
    monkeypatch.setattr(p, "_fetch_rhea_reaction", reaction)
    graph = await p._build_reaction_network_data(["A", "B"], 10, True)
    assert "CHEBI:15377" not in graph["nodes"]
    gaps = p._compute_network_gaps(graph)
    assert len(gaps["components"]) == 2
    assert "CHEBI:30" in gaps["boundary_inputs"] and "CHEBI:40" in gaps["boundary_outputs"]
    assert "CHEBI:10" not in gaps["boundary_inputs"]


async def test_undefined_reactions_use_structural_boundaries_for_bridge_candidates(monkeypatch):
    async def catalytic(acc):
        rid = "RHEA:1" if acc == "A" else "RHEA:2"
        return {"reactions": [{"rhea_id": rid, "name": acc, "ec": None}]}

    async def reaction(rid):
        sides = {
            "RHEA:1": ([('CHEBI:10', 'a')], [('CHEBI:11', 'b')]),
            "RHEA:2": ([('CHEBI:20', 'c')], [('CHEBI:21', 'd')]),
        }
        left, right = sides[rid]
        return {"equation": "x = y", "direction": "undefined", "left": left, "right": right}

    async def search(_cid, _limit):
        return {"reactions": [{
            "rhea_id": "RHEA:99", "equation": "b = c", "direction": "undefined",
            "left": [("CHEBI:11", "b")], "right": [("CHEBI:20", "c")],
        }], "_meta": {"complete": True, "parse_failures": []}}

    monkeypatch.setattr(p, "_uniprot_catalytic_reactions", catalytic)
    monkeypatch.setattr(p, "_fetch_rhea_reaction", reaction)
    monkeypatch.setattr(p, "_rhea_reactions_by_chebi", search)
    monkeypatch.setattr(p, "_uniprot_enzymes_for_rhea", AsyncMock(return_value=[{"accession": "P99"}]))

    graph = await p._build_reaction_network_data(["A", "B"], 10, True)
    gaps = p._compute_network_gaps(graph)
    assert gaps["boundary_inputs"] == [] and gaps["boundary_outputs"] == []
    assert set(gaps["structural_boundaries"]) == {"CHEBI:10", "CHEBI:11", "CHEBI:20", "CHEBI:21"}

    result = json.loads(await p.propose_gap_node_candidates(
        ["A", "B"], max_boundary_metabolites=10, max_candidates_per_boundary=3
    ))
    assert {item["role"] for item in result["boundary_analysis"]} == {"structural_boundary"}
    assert result["bridge_candidates"][0]["candidate_reaction"]["rhea_id"] == "RHEA:99"
    assert result["bridge_candidates"][0]["candidate_enzymes"][0]["accession"] == "P99"


async def test_partial_api_failure_marks_gap_indeterminate(monkeypatch):
    async def catalytic(acc):
        if acc == "BAD":
            return {"_error": "HTTP 503", "_status": 503}
        return {"reactions": [{"rhea_id": "RHEA:1", "name": "r", "ec": None}]}
    async def reaction(_):
        return {"equation": "a => b", "direction": "left_to_right",
                "left": [("CHEBI:10", "a")], "right": [("CHEBI:20", "b")]}
    monkeypatch.setattr(p, "_uniprot_catalytic_reactions", catalytic)
    monkeypatch.setattr(p, "_fetch_rhea_reaction", reaction)
    result = json.loads(await p.detect_network_gaps(["GOOD", "BAD"]))
    assert result["_meta"]["completeness"] == "partial"
    assert result["gap_detection_status"] == "indeterminate_incomplete_data"
    assert result["gap_present"] is None


async def test_all_api_failures_are_not_annotation_absence(monkeypatch):
    monkeypatch.setattr(p, "_uniprot_catalytic_reactions",
                        AsyncMock(return_value={"_error": "timeout", "_status": None}))
    result = await p._build_reaction_network_data(["A"], 10, True)
    assert result["analysis_possible"] is False
    assert "fetch_failures" in result


async def _run_http(monkeypatch, handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(p, "_get_client", AsyncMock(return_value=client))
    try:
        return await p.make_api_request("https://example.test/x", use_cache=False, **kwargs)
    finally:
        await client.aclose()


async def test_http_204_and_404(monkeypatch):
    empty = await _run_http(monkeypatch, lambda req: httpx.Response(204, request=req))
    assert empty == {"_empty": True, "_status": 204}
    missing = await _run_http(monkeypatch, lambda req: httpx.Response(404, text="missing", request=req))
    assert missing["_status"] == 404 and p._is_error(missing)


@pytest.mark.parametrize("status", [429, 500])
async def test_http_retries_without_final_sleep(monkeypatch, status):
    calls = 0
    async def no_sleep(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    def handler(req):
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers={"Retry-After": "0"}, request=req)
    result = await _run_http(monkeypatch, handler)
    assert calls == p.MAX_RETRIES and result["_status"] == status


async def test_http_timeout_retries_and_preserves_detail(monkeypatch):
    calls = 0
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    def handler(req):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=req)
    result = await _run_http(monkeypatch, handler)
    assert calls == p.MAX_RETRIES and result["_error"] == "network: ReadTimeout"
    assert result["_detail"] == "slow"


async def test_get_cache_store_reuse_and_expiry(monkeypatch):
    calls = 0
    def handler(req):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"calls": calls}, request=req)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(p, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(p, "_response_cache", TTLCache(maxsize=4, ttl=0.01))
    try:
        first = await p.make_api_request("https://cache.test", {"b": 2, "a": [1]})
        second = await p.make_api_request("https://cache.test", {"a": [1], "b": 2})
        assert first == second and calls == 1
        await asyncio.sleep(0.02)
        third = await p.make_api_request("https://cache.test", {"a": [1], "b": 2})
        assert third["calls"] == 2
    finally:
        await client.aclose()


async def test_local_pdb_path_and_gzip_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHICA_ALLOWED_PDB_DIRS", str(tmp_path))
    good = tmp_path / "good.pdb"
    good.write_text("HEADER    TEST                                    01-JAN-00   1ABC\nTITLE     SAMPLE\n")
    assert "HEADER" in p._read_text_file(str(good), 1024)
    outside = tmp_path.parent / "outside.pdb"
    outside.write_text("HEADER")
    with pytest.raises(PermissionError):
        p._read_text_file(str(outside), 1024)
    bomb = tmp_path / "large.pdb.gz"
    with gzip.open(bomb, "wt") as stream:
        stream.write("HEADER\n" + "A" * 4096)
    with pytest.raises(OSError, match="展開後"):
        p._read_text_file(str(bomb), 1024)


async def test_uniprot_comments_follow_requested_fields():
    data = {"primaryAccession": "P1", "comments": [
        {"commentType": "FUNCTION"}, {"commentType": "DISEASE"},
        {"commentType": "SUBCELLULAR LOCATION"},
    ]}
    result = p._filter_uniprot_response(data, ["function"])
    assert [c["commentType"] for c in result["comments"]] == ["FUNCTION"]


async def test_ensembl_resolution_rejects_ambiguous_exact_hits(monkeypatch):
    monkeypatch.setattr(p, "_graphql_request", AsyncMock(return_value={"search": {"hits": [
        {"id": "ENSG1", "entity": "target", "name": "ABC"},
        {"id": "ENSG2", "entity": "target", "name": "ABC"},
    ]}}))
    assert await p._resolve_ensembl_id("ABC") is None
