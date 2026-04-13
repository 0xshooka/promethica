#!/usr/bin/env python3
"""
Promethica MCP Server
創薬支援用タンパク質情報統合MCPサーバー

対応API:
- UniProt REST API (タンパク質基本情報)
- Reactome REST API (経路情報)
- RCSB PDB API (構造情報)
- Gene Ontology API (機能分類)
"""

import asyncio
import json
import logging
import random
from typing import Any, Optional
from urllib.parse import quote

import httpx
from cachetools import TTLCache
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("promethica")

mcp = FastMCP("promethica")

APIs = {
    "uniprot": "https://rest.uniprot.org",
    "reactome": "https://reactome.org/ContentService/data",
    "reactome_analysis": "https://reactome.org/AnalysisService/data",
    "pdb": "https://data.rcsb.org/rest/v1",
    "pdb_search": "https://search.rcsb.org/rcsbsearch/v2/query",
    "go": "https://api.geneontology.org/api",
}

CLIENT_HEADERS = {
    "User-Agent": "Promethica-MCP-Server/1.0.0 (Drug Discovery Research Tool)",
    "Accept": "application/json",
}

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_RETRIES = 3

# Module-level client with connection pooling. Lazily created to avoid loop issues.
_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()

# Simple TTL cache for idempotent GET responses (keyed by method+url+params).
_response_cache: TTLCache = TTLCache(maxsize=256, ttl=600)


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    headers=CLIENT_HEADERS,
                    timeout=DEFAULT_TIMEOUT,
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                    follow_redirects=True,
                )
    return _client


def _escape_lucene(value: str) -> str:
    """UniProtのLuceneクエリ用に " と \\ をエスケープ."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _err(message: str, **extra: Any) -> str:
    payload = {"error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


async def make_api_request(
    url: str,
    params: Optional[dict] = None,
    json_data: Optional[dict] = None,
    content: Optional[str] = None,
    extra_headers: Optional[dict] = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """汎用APIリクエスト関数。

    戻り値は常にdict: 成功時はJSON/テキストデコード済みデータ、失敗時は {"_error": ..., "_status": ...}。
    - 429/503 はRetry-Afterを尊重して指数バックオフで最大MAX_RETRIES回リトライ
    - ネットワーク/タイムアウトもリトライ
    - GET結果は短時間TTLキャッシュ
    """
    method = "POST" if (json_data is not None or content is not None) else "GET"
    cache_key = None
    if method == "GET" and use_cache:
        cache_key = (url, tuple(sorted((params or {}).items())))
        if cache_key in _response_cache:
            return _response_cache[cache_key]

    client = await _get_client()
    last_err: dict[str, Any] = {"_error": "unknown", "_status": None}

    for attempt in range(MAX_RETRIES):
        try:
            if method == "POST":
                if json_data is not None:
                    response = await client.post(url, params=params, json=json_data, headers=extra_headers)
                else:
                    response = await client.post(url, params=params, content=content, headers=extra_headers)
            else:
                response = await client.get(url, params=params, headers=extra_headers)

            if response.status_code in (429, 503):
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt + random.random()
                logger.warning("rate-limited %s (status=%s, attempt=%s, sleep=%.1fs)", url, response.status_code, attempt + 1, delay)
                last_err = {"_error": f"HTTP {response.status_code}", "_status": response.status_code}
                await asyncio.sleep(delay)
                continue

            if response.status_code >= 400:
                logger.warning("http error %s %s: %s", response.status_code, url, response.text[:200])
                return {"_error": f"HTTP {response.status_code}", "_status": response.status_code, "_body": response.text[:500]}

            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    return response.json()
                except json.JSONDecodeError as e:
                    return {"_error": f"invalid JSON: {e}", "_status": response.status_code, "_body": response.text[:500]}
            return {"text": response.text, "content_type": content_type}

        except (httpx.TimeoutException, httpx.TransportError) as e:
            delay = 2 ** attempt + random.random()
            logger.warning("network error %s (attempt=%s): %s; sleep=%.1fs", url, attempt + 1, e, delay)
            last_err = {"_error": f"network: {type(e).__name__}", "_status": None}
            await asyncio.sleep(delay)

    return last_err


def _is_error(result: dict) -> bool:
    return isinstance(result, dict) and "_error" in result


# ===== UniProt関連ツール =====


@mcp.tool()
async def search_proteins(query: str, organism: str = "Homo sapiens", size: int = 10, format: str = "json") -> str:
    """UniProtデータベースでタンパク質を検索（簡潔な結果を返す）

    Args:
        query: 検索クエリ（タンパク質名、キーワードなど）
        organism: 生物種名（デフォルト: Homo sapiens）
        size: 結果数（1-50、デフォルト:10）
        format: 出力形式（json, tsv, fasta, xml）
    """
    if not query or not query.strip():
        return _err("検索クエリが必要です")
    if format not in ("json", "tsv", "fasta", "xml"):
        return _err(f"不正なformat: {format}")
    size = max(1, min(size, 50))

    search_query = _escape_lucene(query.strip())
    if organism:
        search_query += f' AND organism_name:"{_escape_lucene(organism)}"'

    url = f"{APIs['uniprot']}/uniprotkb/search"
    params = {"query": search_query, "format": format, "size": size}

    result = await make_api_request(url, params)
    if _is_error(result):
        return _err("UniProt APIからのデータ取得に失敗しました", **result)

    if format == "json" and "results" in result and result["results"]:
        summary = {
            "query": query,
            "organism": organism,
            "total_found": len(result["results"]),
            "proteins": [
                {
                    "accession": p.get("primaryAccession"),
                    "name": p.get("uniProtkbId"),
                    "protein_name": p.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "名前不明"),
                    "organism": p.get("organism", {}).get("scientificName", "不明"),
                    "length": p.get("sequence", {}).get("length", "不明"),
                    "reviewed": p.get("entryType", "").startswith("UniProtKB reviewed"),
                }
                for p in result["results"]
            ],
        }
        return _ok(summary)
    return _ok(result)


@mcp.tool()
async def get_protein_info(accession: str, format: str = "json") -> str:
    """UniProtアクセッション番号から詳細情報を取得

    Args:
        accession: UniProtアクセッション番号
        format: 出力形式（json, tsv, fasta, xml）
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")
    if format not in ("json", "tsv", "fasta", "xml"):
        return _err(f"不正なformat: {format}")

    acc = quote(accession.strip(), safe="")
    url = f"{APIs['uniprot']}/uniprotkb/{acc}"
    result = await make_api_request(url, {"format": format})
    if _is_error(result):
        return _err(f"アクセッション番号 {accession} の情報取得に失敗しました", **result)

    if format == "json":
        return _ok(result)
    return result.get("text", _ok(result))


@mcp.tool()
async def get_protein_sequence(accession: str, format: str = "fasta") -> str:
    """タンパク質のアミノ酸配列を取得

    Args:
        accession: UniProtアクセッション番号
        format: 出力形式（fasta, json）
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")
    if format not in ("fasta", "json"):
        return _err(f"不正なformat: {format}")

    acc = quote(accession.strip(), safe="")
    url = f"{APIs['uniprot']}/uniprotkb/{acc}"
    result = await make_api_request(url, {"format": format})
    if _is_error(result):
        return _err(f"アクセッション番号 {accession} の配列取得に失敗しました", **result)

    if format == "fasta":
        return result.get("text", _ok(result))
    return _ok(result)


# ===== PDB関連ツール =====


@mcp.tool()
async def search_pdb_structures(query: str, size: int = 25) -> str:
    """RCSB PDBでタンパク質構造を検索

    Args:
        query: 検索クエリ（タンパク質名、PDB IDなど）
        size: 結果数（1-100、デフォルト:25）
    """
    if not query or not query.strip():
        return _err("検索クエリが必要です")
    if size < 1 or size > 100:
        return _err("サイズは1-100の範囲で指定してください")

    search_query = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": query},
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": size},
            "results_verbosity": "minimal",
            "sort": [{"sort_by": "score", "direction": "desc"}],
        },
    }

    result = await make_api_request(APIs["pdb_search"], json_data=search_query)

    # 204 No Content = 検索結果0件（正常）
    if isinstance(result, dict) and result.get("_status") == 204:
        return _ok({
            "query_id": None,
            "result_type": "entry",
            "total_count": 0,
            "result_set": [],
            "message": f"'{query}' に一致する構造は見つかりませんでした"
        })

    if _is_error(result):
        return _err("PDB検索に失敗しました", **result)
    return _ok(result)


@mcp.tool()
async def get_pdb_structure_info(pdb_id: str) -> str:
    """PDB IDから構造詳細情報を取得

    Args:
        pdb_id: PDB ID（4文字）
    """
    if not pdb_id or len(pdb_id.strip()) != 4 or not pdb_id.strip().isalnum():
        return _err("PDB IDは4文字の英数字である必要があります")

    pid = quote(pdb_id.strip().upper(), safe="")
    url = f"{APIs['pdb']}/core/entry/{pid}"
    result = await make_api_request(url)
    if _is_error(result):
        return _err(f"PDB ID {pdb_id} の構造情報取得に失敗しました", **result)
    return _ok(result)


# ===== Reactome関連ツール =====


@mcp.tool()
async def search_pathways(query: str, species: str = "Homo sapiens") -> str:
    """Reactomeでバイオロジカルパスウェイを検索

    Args:
        query: 検索クエリ（パスウェイ名、遺伝子名など）
        species: 生物種（デフォルト: Homo sapiens）
    """
    if not query or not query.strip():
        return _err("検索クエリが必要です")

    q = query.strip()

    # 1. Reactome Search API（SolR検索）
    #    エンドポイント: /ContentService/search/query （/data/ 配下ではない）
    search_url = APIs["reactome"].replace("/data", "") + "/search/query"
    result = await make_api_request(
        search_url,
        {"query": q, "species": species, "cluster": "true"},
    )
    if not _is_error(result):
        return _ok(result)

    # 2. フォールバック: Reactome stable ID or dbId として直接クエリ
    q_enc = quote(q, safe="")
    result2 = await make_api_request(
        f"{APIs['reactome']}/query/{q_enc}",
        {"species": species},
    )
    if not _is_error(result2):
        return _ok(result2)

    return _err(
        "Reactomeパスウェイの取得に失敗しました",
        query=q,
        species=species,
        suggestion=f"https://reactome.org/content/query?q={q_enc}",
    )


@mcp.tool()
async def get_protein_pathways(accession: str) -> str:
    """タンパク質が関与するパスウェイを取得（Reactome AnalysisService使用）

    Args:
        accession: UniProtアクセッション番号
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")
    acc = accession.strip()
    acc_enc = quote(acc, safe="")

    # ===== 1. Mapping API（軽量・高速、新規追加） =====
    mapping_url = f"{APIs['reactome']}/mapping/UniProt/{acc_enc}/pathways"
    mapping = await make_api_request(mapping_url)
    if not _is_error(mapping) and isinstance(mapping, list) and len(mapping) > 0:
        return _ok({
            "accession": acc,
            "pathways": [
                {
                    "stId": p.get("stId"),
                    "name": p.get("displayName"),
                    "species": p.get("speciesName"),
                    "url": f"https://reactome.org/PathwayBrowser/#/{p.get('stId')}",
                }
                for p in mapping
            ],
            "total_count": len(mapping),
            "method": "Reactome Mapping API",
        })

    # ===== 2. AnalysisService フォールバック（既存のまま） =====
    analysis = await make_api_request(
        f"{APIs['reactome_analysis']}/identifiers",
        params={
            "interactors": "false",
            "pageSize": "20",
            "page": "1",
            "sortBy": "ENTITIES_PVALUE",
            "order": "ASC",
            "resource": "TOTAL",
        },
        content=acc,
        extra_headers={"Content-Type": "text/plain"},
        use_cache=False,
    )

    if not _is_error(analysis):
        out = {
            "accession": acc,
            "analysis_summary": analysis.get("summary", {}),
            "pathways": [
                {
                    "stId": p.get("stId"),
                    "name": p.get("name"),
                    "species": p.get("species", {}).get("name"),
                    "entities_found": p.get("entities", {}).get("found", 0),
                    "entities_total": p.get("entities", {}).get("total", 0),
                    "p_value": p.get("entities", {}).get("pValue"),
                    "fdr": p.get("entities", {}).get("fdr"),
                    "url": f"https://reactome.org/PathwayBrowser/#/{p.get('stId')}",
                    "details_url": f"https://reactome.org/detail/{p.get('stId')}",
                }
                for p in analysis.get("pathways", [])
            ],
            "method": "Reactome AnalysisService",
            "timestamp": analysis.get("summary", {}).get("createdOn"),
        }
        token = analysis.get("summary", {}).get("token")
        if token:
            out["analysis_token"] = token
            out["analysis_url"] = f"https://reactome.org/PathwayBrowser/#/DTAB=AN&ANALYSIS={token}"
        return _ok(out)

    logger.info("Reactome AnalysisService failed, falling back to UniProt for %s", acc)

    # ===== 3. UniProtフォールバック（既存のまま） =====
    uni = await make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc_enc}", {"format": "json"})
    if _is_error(uni):
        return _err(
            "パスウェイ情報の取得に失敗しました",
            accession=acc,
            reactome_error=analysis,
            uniprot_error=uni,
            suggestion=f"https://reactome.org/content/query?q={acc_enc}",
        )

    info: dict[str, Any] = {
        "accession": acc,
        "source": "UniProt (Reactome fallback)",
        "pathways": [],
        "cross_references": [],
        "protein_name": uni.get("proteinDescription", {})
            .get("recommendedName", {})
            .get("fullName", {})
            .get("value", "Unknown"),
    }
    for ref in uni.get("uniProtKBCrossReferences", []):
        if ref.get("database") == "Reactome":
            entry = {
                "id": ref.get("id"),
                "url": f"https://reactome.org/detail/{ref.get('id')}",
                "source": "UniProt cross-reference",
            }
            for prop in ref.get("properties", []):
                if prop.get("key") == "PathwayName":
                    entry["pathway_name"] = prop.get("value")
            info["cross_references"].append(entry)
    for comment in uni.get("comments", []):
        if comment.get("commentType") in ("PATHWAY", "FUNCTION"):
            info["pathways"].append(
                {
                    "type": comment.get("commentType"),
                    "text": (comment.get("texts") or [{}])[0].get("value", ""),
                    "source": "UniProt annotation",
                }
            )
    return _ok(info)


# ===== Gene Ontology関連ツール =====


@mcp.tool()
async def search_go_terms(go_term: Optional[str] = None, query: Optional[str] = None) -> str:
    """Gene Ontology用語を検索

    Args:
        go_term: GO term ID（例: GO:0005524）
        query: 検索クエリ（go_termまたはqueryのいずれかが必要）
    """
    if not go_term and not query:
        return _err("GO term IDまたは検索クエリが必要です")

    if go_term:
        url = f"{APIs['go']}/bioentity/{quote(go_term.strip(), safe=':')}"
    else:
        url = f"{APIs['go']}/search/entity/{quote(query.strip(), safe='')}"

    result = await make_api_request(url)
    if _is_error(result):
        return _err("Gene Ontology検索に失敗しました", **result)
    return _ok(result)


# ===== 統合分析ツール =====


@mcp.tool()
async def comprehensive_protein_analysis(accession: str) -> str:
    """タンパク質の包括的分析（UniProt + Reactome + PDB統合）

    Args:
        accession: UniProtアクセッション番号
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")
    acc = accession.strip()
    acc_enc = quote(acc, safe="")

    pdb_query = {
        "query": {"type": "terminal", "service": "text", "parameters": {"value": acc}},
        "return_type": "entry",
        "request_options": {
            "return_all_hits": True,
            "results_verbosity": "minimal",
            "sort": [{"sort_by": "score", "direction": "desc"}],
        },
    }

    uniprot_task = make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc_enc}", {"format": "json"})
    pathways_task = make_api_request(f"{APIs['reactome']}/data/participants/{acc_enc}/pathways")
    pdb_task = make_api_request(APIs["pdb_search"], json_data=pdb_query)

    uniprot_result, pathway_result, pdb_result = await asyncio.gather(
        uniprot_task, pathways_task, pdb_task, return_exceptions=True
    )

    analysis: dict[str, Any] = {
        "accession": acc,
        "uniprot_info": None,
        "pathways": None,
        "pdb_structures": None,
        "error_messages": [],
    }

    if isinstance(uniprot_result, Exception) or _is_error(uniprot_result):
        analysis["error_messages"].append({"source": "uniprot", "detail": str(uniprot_result)})
    else:
        analysis["uniprot_info"] = uniprot_result

    if isinstance(pathway_result, Exception) or _is_error(pathway_result):
        analysis["error_messages"].append({"source": "reactome", "detail": str(pathway_result)})
    else:
        analysis["pathways"] = pathway_result

    if isinstance(pdb_result, Exception) or _is_error(pdb_result):
        analysis["error_messages"].append({"source": "pdb", "detail": str(pdb_result)})
    elif "result_set" in pdb_result:
        analysis["pdb_structures"] = {
            "result_set": pdb_result["result_set"][:5],
            "total_count": pdb_result.get("total_count", 0),
        }
    else:
        analysis["pdb_structures"] = pdb_result

    return _ok(analysis)


# ===== 追加の便利ツール =====


@mcp.tool()
async def get_primary_protein_for_gene(gene: str, organism: str = "Homo sapiens") -> str:
    """遺伝子に対応する主要なタンパク質情報を取得（最も関連性の高い1つのみ）

    Args:
        gene: 遺伝子名またはシンボル（例: APP, BRCA1, INS）
        organism: 生物種名（デフォルト: Homo sapiens）
    """
    if not gene or not gene.strip():
        return _err("遺伝子名が必要です")

    g = _escape_lucene(gene.strip())
    o = _escape_lucene(organism)
    search_query = f'gene:"{g}" AND organism_name:"{o}" AND reviewed:true'

    result = await make_api_request(
        f"{APIs['uniprot']}/uniprotkb/search",
        {"query": search_query, "format": "json", "size": 1},
    )
    if _is_error(result):
        return _err(f"遺伝子 {gene} の検索に失敗しました", **result)

    if not result.get("results"):
        return _err(f"遺伝子 {gene} ({organism}) に対応するレビュー済みタンパク質が見つかりませんでした")

    p = result["results"][0]
    primary = {
        "gene": gene,
        "organism": organism,
        "primary_protein": {
            "accession": p.get("primaryAccession"),
            "name": p.get("uniProtkbId"),
            "protein_name": p.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "名前不明"),
            "organism": p.get("organism", {}).get("scientificName", "不明"),
            "length": p.get("sequence", {}).get("length", "不明"),
            "reviewed": p.get("entryType", "").startswith("UniProtKB reviewed"),
            "function": None,
        },
    }
    for comment in p.get("comments", []):
        if comment.get("commentType") == "FUNCTION":
            primary["primary_protein"]["function"] = (comment.get("texts") or [{}])[0].get("value", "")
            break
    return _ok(primary)


@mcp.tool()
async def get_protein_features(accession: str) -> str:
    """タンパク質の機能的特徴とドメイン情報を取得

    Args:
        accession: UniProtアクセッション番号
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")

    acc = quote(accession.strip(), safe="")
    result = await make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc}", {"format": "json"})
    if _is_error(result):
        return _err(f"アクセッション番号 {accession} の特徴情報取得に失敗しました", **result)

    features = result.get("features", [])
    return _ok({
        "accession": result.get("primaryAccession"),
        "name": result.get("uniProtkbId"),
        "features": features,
        "comments": result.get("comments", []),
        "keywords": result.get("keywords", []),
        "domains": [f for f in features if f.get("type") == "Domain"],
        "active_sites": [f for f in features if f.get("type") == "Active site"],
        "binding_sites": [f for f in features if f.get("type") == "Binding site"],
    })


@mcp.tool()
async def search_by_gene(gene: str, organism: str = "Homo sapiens", size: int = 10) -> str:
    """遺伝子名でタンパク質を検索（簡潔な結果を返す）

    Args:
        gene: 遺伝子名またはシンボル（例: BRCA1, INS, APP）
        organism: 生物種名（デフォルト: Homo sapiens）
        size: 結果数（1-50、デフォルト:10）
    """
    if not gene or not gene.strip():
        return _err("遺伝子名が必要です")
    size = max(1, min(size, 50))

    g = _escape_lucene(gene.strip())
    search_query = f'gene:"{g}"'
    if organism:
        search_query += f' AND organism_name:"{_escape_lucene(organism)}"'

    result = await make_api_request(
        f"{APIs['uniprot']}/uniprotkb/search",
        {"query": search_query, "format": "json", "size": size},
    )
    if _is_error(result):
        return _err(f"遺伝子 {gene} の検索に失敗しました", **result)

    if not result.get("results"):
        return _err(f"遺伝子 {gene} に関連するタンパク質が見つかりませんでした")

    summary = {
        "gene": gene,
        "organism": organism,
        "total_found": len(result["results"]),
        "proteins": [
            {
                "accession": p.get("primaryAccession"),
                "name": p.get("uniProtkbId"),
                "protein_name": p.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "名前不明"),
                "length": p.get("sequence", {}).get("length", "不明"),
                "reviewed": p.get("entryType", "").startswith("UniProtKB reviewed"),
            }
            for p in result["results"]
        ],
    }
    return _ok(summary)


if __name__ == "__main__":
    mcp.run(transport="stdio")
