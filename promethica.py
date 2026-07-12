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
    "opentargets": "https://api.platform.opentargets.org/api/v4/graphql",
    "chembl": "https://www.ebi.ac.uk/chembl/api/data",
}

CLIENT_HEADERS = {
    "User-Agent": "Promethica-MCP-Server/1.0.0 (Drug Discovery Research Tool)",
    "Accept": "application/json",
}

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_RETRIES = 3

# 一過性の可能性が高いステータス（レート制限＋ゲートウェイ/サーバ系5xx）はリトライする。
# 520/522/524 はCloudflare由来の一過性エラー（Reactomeで頻発）。
RETRIABLE_STATUS = {429, 500, 502, 503, 504, 520, 522, 524}

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


def _read_text_file(path: str, max_bytes: int = 20 * 1024 * 1024) -> str:
    """ローカルテキストファイルを読み込む（.gz にも対応、サイズ上限あり）。同期I/Oなのでto_thread経由で呼ぶ。"""
    import gzip
    import os

    if os.path.getsize(path) > max_bytes:
        raise OSError(f"ファイルが大きすぎます（上限 {max_bytes // (1024 * 1024)}MB）")
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        return f.read()


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

            if response.status_code in RETRIABLE_STATUS:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt + random.random()
                logger.warning("retriable status %s %s (attempt=%s, sleep=%.1fs)", response.status_code, url, attempt + 1, delay)
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


async def _graphql_request(
    url: str, query: str, variables: Optional[dict] = None
) -> dict[str, Any]:
    """GraphQL POSTリクエスト。成功時はdataフィールドを、失敗時は {"_error": ...} を返す。

    GraphQLはHTTP 200でもトップレベルに "errors" を返すため、それも失敗として扱う。
    """
    result = await make_api_request(url, json_data={"query": query, "variables": variables or {}})
    if _is_error(result):
        return result
    if isinstance(result, dict) and result.get("errors"):
        return {
            "_error": "GraphQL error",
            "_status": 200,
            "_body": json.dumps(result["errors"], ensure_ascii=False)[:500],
        }
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return {"_error": "unexpected GraphQL response", "_status": None, "_body": str(result)[:500]}


async def _resolve_ensembl_id(gene: str) -> Optional[str]:
    """遺伝子シンボル（またはEnsembl ID）をOpen TargetsのEnsembl target IDに解決する。

    既にENSG始まりならそのまま返す。見つからなければ None。
    """
    g = gene.strip()
    if g.upper().startswith("ENSG"):
        return g

    query = """
    query resolveTarget($q: String!) {
      search(queryString: $q, entityNames: ["target"], page: {index: 0, size: 1}) {
        hits { id entity name }
      }
    }
    """
    data = await _graphql_request(APIs["opentargets"], query, {"q": g})
    if _is_error(data):
        return None
    hits = (data.get("search") or {}).get("hits") or []
    for h in hits:
        if h.get("entity") == "target" and h.get("id"):
            return h["id"]
    return None


# ===== UniProtフィールドフィルタ =====

# UniProt JSONレスポンスの主要トップレベルキー → 論理フィールド名のマッピング
# "references" は文献情報で非常に大きくなるため、デフォルトでは除外する
UNIPROT_FIELD_MAP: dict[str, list[str]] = {
    "basic": ["primaryAccession", "uniProtkbId", "entryType", "annotationScore"],
    "protein_name": ["proteinDescription"],
    "organism": ["organism", "organismHosts"],
    "gene": ["genes"],
    "function": ["comments"],
    "features": ["features"],
    "keywords": ["keywords"],
    "sequence": ["sequence"],
    "subcellular_location": ["comments"],
    "disease": ["comments"],
    "interaction": ["comments"],
    "cross_references": ["uniProtKBCrossReferences"],
    "references": ["references", "citations"],
    "go_annotations": ["uniProtKBCrossReferences"],
    "structure": ["uniProtKBCrossReferences"],
    "secondary_accessions": ["secondaryAccessions"],
    "entry_audit": ["entryAudit"],
}

# デフォルトで返すフィールド（references を除外した軽量版）
DEFAULT_FIELDS = [
    "basic", "protein_name", "organism", "gene", "function",
    "features", "keywords", "sequence", "cross_references",
]

# すべてのフィールド
ALL_FIELD_NAMES = sorted(UNIPROT_FIELD_MAP.keys())


def _filter_uniprot_response(data: dict, fields: Optional[list[str]] = None) -> dict:
    """UniProt JSONレスポンスを指定フィールドでフィルタする。

    fields が None の場合はデフォルト（references除外）を適用。
    fields に "all" が含まれる場合はフィルタなしで全データを返す。
    """
    if not isinstance(data, dict):
        return data

    if fields is not None and "all" in fields:
        return data

    selected = fields if fields is not None else DEFAULT_FIELDS

    # 許可するトップレベルキーを収集
    allowed_keys: set[str] = set()
    for field_name in selected:
        if field_name in UNIPROT_FIELD_MAP:
            allowed_keys.update(UNIPROT_FIELD_MAP[field_name])

    # basic は常に含める
    allowed_keys.update(UNIPROT_FIELD_MAP["basic"])

    filtered = {k: v for k, v in data.items() if k in allowed_keys}

    # メタ情報として、除外されたフィールドがあることを示す
    excluded = [f for f in ALL_FIELD_NAMES if f not in selected and f != "basic"]
    if excluded:
        filtered["_excluded_fields"] = excluded
        filtered["_hint"] = "除外フィールドが必要な場合は fields パラメータで指定してください（例: fields=['references']）。全データは fields=['all'] で取得できます。"

    return filtered


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
async def get_protein_info(
    accession: str,
    format: str = "json",
    fields: Optional[list[str]] = None,
) -> str:
    """UniProtアクセッション番号から詳細情報を取得

    Args:
        accession: UniProtアクセッション番号
        format: 出力形式（json, tsv, fasta, xml）
        fields: 取得するフィールド一覧（デフォルト: references除外の軽量版）。
                指定可能: basic, protein_name, organism, gene, function, features,
                keywords, sequence, cross_references, references, disease,
                interaction, go_annotations, structure, secondary_accessions, entry_audit。
                全データ取得は fields=["all"]。
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
        return _ok(_filter_uniprot_response(result, fields))
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


@mcp.tool()
async def search_similar_structures(
    pdb_id: str,
    assembly_id: str = "1",
    operator: str = "strict_shape_match",
    size: int = 10,
) -> str:
    """PDB IDを起点に、構造的（3D形状）に類似する既知構造を検索（RCSB structure similarity）

    Args:
        pdb_id: 起点となるPDB ID（4文字）
        assembly_id: アセンブリID（デフォルト: "1"）
        operator: マッチ強度（"strict_shape_match" または "relaxed_shape_match"）
        size: 結果数（1-100、デフォルト:10）。先頭は入力構造自身がヒットする場合がある。
    """
    if not pdb_id or len(pdb_id.strip()) != 4 or not pdb_id.strip().isalnum():
        return _err("PDB IDは4文字の英数字である必要があります")
    if operator not in ("strict_shape_match", "relaxed_shape_match"):
        return _err(f"不正なoperator: {operator}（strict_shape_match / relaxed_shape_match）")
    size = max(1, min(size, 100))

    pid = pdb_id.strip().upper()
    search_query = {
        "query": {
            "type": "terminal",
            "service": "structure",
            "parameters": {
                "value": {"entry_id": pid, "assembly_id": assembly_id},
                "operator": operator,
            },
        },
        "return_type": "polymer_entity",
        "request_options": {
            "paginate": {"start": 0, "rows": size},
            "results_verbosity": "minimal",
            "sort": [{"sort_by": "score", "direction": "desc"}],
        },
    }

    result = await make_api_request(APIs["pdb_search"], json_data=search_query)

    if isinstance(result, dict) and result.get("_status") == 204:
        return _ok({
            "query_pdb_id": pid,
            "operator": operator,
            "total_count": 0,
            "result_set": [],
            "message": f"'{pid}' に構造的に類似する結果は見つかりませんでした",
        })
    if _is_error(result):
        return _err(f"PDB ID {pdb_id} の類似構造検索に失敗しました", **result)

    return _ok({
        "query_pdb_id": pid,
        "operator": operator,
        "total_count": result.get("total_count", 0),
        "result_set": result.get("result_set", [])[:size],
        "note": "identifierは <PDB_ID>_<entity_id> 形式（polymer entity単位）。先頭は入力構造自身の可能性あり。",
    })


def _parse_pdb_header(text: str) -> dict[str, Any]:
    """PDBフォーマットのヘッダ行から主要メタデータを抽出する（座標は解析しない軽量パーサ）。"""
    info: dict[str, Any] = {
        "pdb_id": None,
        "classification": None,
        "deposition_date": None,
        "title": None,
        "experimental_method": None,
        "resolution": None,
        "keywords": None,
        "compounds": [],
        "source_organisms": [],
        "uniprot_accessions": [],
    }
    title_parts: list[str] = []
    keyword_parts: list[str] = []
    compnd_parts: list[str] = []
    source_parts: list[str] = []

    for raw in text.splitlines():
        record = raw[:6].strip()
        if record == "HEADER":
            info["classification"] = raw[10:50].strip() or None
            info["deposition_date"] = raw[50:59].strip() or None
            info["pdb_id"] = raw[62:66].strip() or None
        elif record == "TITLE":
            title_parts.append(raw[10:].strip())
        elif record == "KEYWDS":
            keyword_parts.append(raw[10:].strip())
        elif record == "EXPDTA":
            info["experimental_method"] = (info.get("experimental_method") or "") + raw[10:].strip()
        elif record == "COMPND":
            compnd_parts.append(raw[10:].strip())
        elif record == "SOURCE":
            source_parts.append(raw[10:].strip())
        elif record == "REMARK" and raw[7:10].strip() == "2" and "RESOLUTION" in raw:
            # REMARK   2 RESOLUTION.    1.74 ANGSTROMS.  → "RESOLUTION." より後ろの最初の数値を採用
            after = raw.split("RESOLUTION.", 1)[1] if "RESOLUTION." in raw else ""
            for tok in after.split():
                try:
                    info["resolution"] = float(tok)
                    break
                except ValueError:
                    continue
        elif record == "DBREF" and "UNP" in raw:
            # DBREF ... UNP  P69905 ...  (UNP = UniProt)
            toks = raw.split()
            try:
                idx = toks.index("UNP")
                acc = toks[idx + 1]
                if acc not in info["uniprot_accessions"]:
                    info["uniprot_accessions"].append(acc)
            except (ValueError, IndexError):
                pass

    info["title"] = " ".join(p for p in title_parts if p) or None
    info["keywords"] = " ".join(p for p in keyword_parts if p) or None

    for part in compnd_parts:
        if part.startswith("MOLECULE:"):
            info["compounds"].append(part[len("MOLECULE:"):].strip().rstrip(";"))
    for part in source_parts:
        if part.startswith("ORGANISM_SCIENTIFIC:"):
            info["source_organisms"].append(part[len("ORGANISM_SCIENTIFIC:"):].strip().rstrip(";"))

    return info


@mcp.tool()
async def parse_local_pdb(file_path: str, enrich: bool = True) -> str:
    """手元のローカル.pdbファイルからメタ情報を抽出し、関連情報を補完する（研究者D）

    座標は解析せず、HEADER/TITLE/COMPND/SOURCE/EXPDTA/REMARK/DBREF などのヘッダ行から
    分類・構造概要・実験手法・解像度・UniProt相互参照を抽出する。
    enrich=True の場合、抽出したUniProtアクセッションから関連パスウェイ（Reactome）と
    GO注釈（UniProt）を補完取得する。

    Args:
        file_path: ローカルの.pdbファイルパス
        enrich: 抽出したUniProt IDから関連パスウェイ・GO注釈を補完するか（デフォルト: True）
    """
    if not file_path or not file_path.strip():
        return _err("ファイルパスが必要です")

    path = file_path.strip()
    try:
        text = await asyncio.to_thread(_read_text_file, path)
    except FileNotFoundError:
        return _err(f"ファイルが見つかりません: {path}")
    except OSError as e:
        return _err(f"ファイルの読み込みに失敗しました: {e}")

    if "HEADER" not in text and "ATOM" not in text and "HETATM" not in text:
        return _err("PDBフォーマットとして認識できませんでした（HEADER/ATOM行が見つかりません）", file_path=path)

    header = _parse_pdb_header(text)
    out: dict[str, Any] = {"file_path": path, "header": header}

    if enrich and header["uniprot_accessions"]:
        acc = header["uniprot_accessions"][0]
        acc_enc = quote(acc, safe="")
        pathways_result, uniprot_result = await asyncio.gather(
            make_api_request(f"{APIs['reactome']}/mapping/UniProt/{acc_enc}/pathways"),
            make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc_enc}", {"format": "json"}),
            return_exceptions=True,
        )

        enrichment: dict[str, Any] = {"primary_accession": acc, "pathways": None, "go_annotations": None}

        if not isinstance(pathways_result, Exception) and not _is_error(pathways_result) and isinstance(pathways_result, list):
            enrichment["pathways"] = [
                {
                    "stId": p.get("stId"),
                    "name": p.get("displayName"),
                    "species": p.get("speciesName"),
                    "url": f"https://reactome.org/PathwayBrowser/#/{p.get('stId')}",
                }
                for p in pathways_result
            ]

        if not isinstance(uniprot_result, Exception) and not _is_error(uniprot_result):
            go_terms = []
            for ref in uniprot_result.get("uniProtKBCrossReferences", []):
                if ref.get("database") == "GO":
                    term = {"id": ref.get("id")}
                    for prop in ref.get("properties", []):
                        if prop.get("key") == "GoTerm":
                            term["term"] = prop.get("value")
                        elif prop.get("key") == "GoEvidenceType":
                            term["evidence"] = prop.get("value")
                    go_terms.append(term)
            enrichment["go_annotations"] = go_terms

        out["enrichment"] = enrichment
    elif enrich:
        out["enrichment"] = {
            "note": "UniProt相互参照(DBREF)が見つからなかったため補完をスキップしました。"
                    "HEADERのpdb_idがあれば get_pdb_structure_info で詳細取得できます。",
        }

    return _ok(out)


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


# ===== Open Targets関連ツール（疾患関連・既知薬・副作用） =====


@mcp.tool()
async def get_disease_associations(gene: str, size: int = 25) -> str:
    """遺伝子（ターゲット）に関連する疾患を取得（Open Targets, 研究者C）

    Args:
        gene: 遺伝子シンボル（例: BRCA1）またはEnsembl target ID（例: ENSG00000012048）
        size: 結果数（1-100、デフォルト:25）。関連スコア降順。
    """
    if not gene or not gene.strip():
        return _err("遺伝子名が必要です")
    size = max(1, min(size, 100))

    ensembl_id = await _resolve_ensembl_id(gene)
    if not ensembl_id:
        return _err(f"遺伝子 {gene} をOpen TargetsのEnsembl target IDに解決できませんでした")

    query = """
    query targetDiseases($id: String!, $size: Int!) {
      target(ensemblId: $id) {
        id
        approvedSymbol
        approvedName
        associatedDiseases(page: {index: 0, size: $size}) {
          count
          rows { score disease { id name therapeuticAreas { id name } } }
        }
      }
    }
    """
    data = await _graphql_request(APIs["opentargets"], query, {"id": ensembl_id, "size": size})
    if _is_error(data):
        return _err(f"遺伝子 {gene} の疾患関連情報の取得に失敗しました", **data)

    target = data.get("target")
    if not target:
        return _err(f"Ensembl ID {ensembl_id} に対応するターゲットが見つかりませんでした")

    assoc = target.get("associatedDiseases") or {}
    return _ok({
        "gene": gene,
        "ensembl_id": target.get("id"),
        "approved_symbol": target.get("approvedSymbol"),
        "approved_name": target.get("approvedName"),
        "total_count": assoc.get("count", 0),
        "diseases": [
            {
                "efo_id": (r.get("disease") or {}).get("id"),
                "name": (r.get("disease") or {}).get("name"),
                "association_score": r.get("score"),
                "therapeutic_areas": [ta.get("name") for ta in (r.get("disease") or {}).get("therapeuticAreas", [])],
            }
            for r in assoc.get("rows", [])
        ],
        "source": "Open Targets Platform",
    })


@mcp.tool()
async def get_drugs_for_target(gene: str, size: int = 25) -> str:
    """遺伝子（ターゲット）に対する既知薬・治療候補を取得（Open Targets, 研究者C）

    Args:
        gene: 遺伝子シンボル（例: EGFR）またはEnsembl target ID
        size: 結果数（1-100、デフォルト:25）
    """
    if not gene or not gene.strip():
        return _err("遺伝子名が必要です")
    size = max(1, min(size, 100))

    ensembl_id = await _resolve_ensembl_id(gene)
    if not ensembl_id:
        return _err(f"遺伝子 {gene} をOpen TargetsのEnsembl target IDに解決できませんでした")

    query = """
    query targetDrugs($id: String!) {
      target(ensemblId: $id) {
        id
        approvedSymbol
        drugAndClinicalCandidates {
          count
          rows {
            id
            maxClinicalStage
            drug {
              id
              name
              drugType
              mechanismsOfAction { uniqueActionTypes rows { mechanismOfAction actionType } }
            }
            diseases { disease { id name } }
          }
        }
      }
    }
    """
    data = await _graphql_request(APIs["opentargets"], query, {"id": ensembl_id})
    if _is_error(data):
        return _err(f"遺伝子 {gene} の薬剤情報の取得に失敗しました", **data)

    target = data.get("target")
    if not target:
        return _err(f"Ensembl ID {ensembl_id} に対応するターゲットが見つかりませんでした")

    cand = target.get("drugAndClinicalCandidates") or {}
    drugs = []
    for r in cand.get("rows", [])[:size]:
        drug = r.get("drug") or {}
        moa = drug.get("mechanismsOfAction") or {}
        drugs.append({
            "chembl_id": drug.get("id"),
            "name": drug.get("name"),
            "drug_type": drug.get("drugType"),
            "max_clinical_stage": r.get("maxClinicalStage"),
            "action_types": moa.get("uniqueActionTypes") or [],
            "mechanisms_of_action": [m.get("mechanismOfAction") for m in moa.get("rows", [])],
            "indications": [
                (d.get("disease") or {}).get("name")
                for d in r.get("diseases", [])
                if (d.get("disease") or {}).get("name")
            ],
        })

    return _ok({
        "gene": gene,
        "ensembl_id": target.get("id"),
        "approved_symbol": target.get("approvedSymbol"),
        "total_count": cand.get("count", 0),
        "returned": len(drugs),
        "drugs": drugs,
        "source": "Open Targets Platform",
    })


@mcp.tool()
async def get_known_adverse_effects(drug: str, size: int = 25) -> str:
    """既知薬の報告済み副作用（ファーマコビジランス）を検索・列挙（Open Targets/FAERS, 研究者E）

    de novoな副作用「予測」ではなく、FAERS等に登録済みの既知の有害事象を検索・列挙する。

    Args:
        drug: ChEMBL ID（例: CHEMBL1201580）または薬剤名（例: aspirin）
        size: 結果数（1-100、デフォルト:25）。有意性（logLR）降順。
    """
    if not drug or not drug.strip():
        return _err("薬剤IDまたは薬剤名が必要です")
    size = max(1, min(size, 100))

    d = drug.strip()
    chembl_id = d if d.upper().startswith("CHEMBL") else None

    # 薬剤名の場合はOpen Targets searchでChEMBL IDに解決
    if chembl_id is None:
        resolve_q = """
        query resolveDrug($q: String!) {
          search(queryString: $q, entityNames: ["drug"], page: {index: 0, size: 1}) {
            hits { id entity name }
          }
        }
        """
        rdata = await _graphql_request(APIs["opentargets"], resolve_q, {"q": d})
        if _is_error(rdata):
            return _err(f"薬剤 {drug} の解決に失敗しました", **rdata)
        hits = (rdata.get("search") or {}).get("hits") or []
        for h in hits:
            if h.get("entity") == "drug" and h.get("id"):
                chembl_id = h["id"]
                break
        if chembl_id is None:
            return _err(f"薬剤 {drug} をChEMBL IDに解決できませんでした")

    query = """
    query drugAEs($id: String!, $size: Int!) {
      drug(chemblId: $id) {
        id
        name
        adverseEvents(page: {index: 0, size: $size}) {
          count
          criticalValue
          rows { name count logLR meddraCode }
        }
      }
    }
    """
    data = await _graphql_request(APIs["opentargets"], query, {"id": chembl_id, "size": size})
    if _is_error(data):
        return _err(f"薬剤 {drug} の副作用情報の取得に失敗しました", **data)

    drug_obj = data.get("drug")
    if not drug_obj:
        return _err(f"ChEMBL ID {chembl_id} に対応する薬剤が見つかりませんでした")

    aes = drug_obj.get("adverseEvents") or {}
    return _ok({
        "query": drug,
        "chembl_id": drug_obj.get("id"),
        "drug_name": drug_obj.get("name"),
        "total_count": aes.get("count", 0),
        "critical_value": aes.get("criticalValue"),
        "adverse_events": [
            {
                "name": r.get("name"),
                "report_count": r.get("count"),
                "log_likelihood_ratio": r.get("logLR"),
                "meddra_code": r.get("meddraCode"),
            }
            for r in aes.get("rows", [])
        ],
        "note": "FAERSベースの既知の有害事象報告。logLRが criticalValue を上回るものが統計的に有意。",
        "source": "Open Targets Platform / FDA FAERS",
    })


# ===== ChEMBL関連ツール（化合物の化学的詳細） =====


@mcp.tool()
async def get_drug_details(chembl_id: str) -> str:
    """ChEMBL IDから薬剤・化合物の化学的詳細（構造・作用機序・適応）を取得（ChEMBL）

    Args:
        chembl_id: ChEMBL molecule ID（例: CHEMBL25）
    """
    if not chembl_id or not chembl_id.strip():
        return _err("ChEMBL IDが必要です")
    cid = chembl_id.strip().upper()
    if not cid.startswith("CHEMBL"):
        return _err("ChEMBL IDは 'CHEMBL' で始まる必要があります")

    cid_enc = quote(cid, safe="")
    molecule, mechanisms, indications = await asyncio.gather(
        make_api_request(f"{APIs['chembl']}/molecule/{cid_enc}.json"),
        make_api_request(f"{APIs['chembl']}/mechanism.json", {"molecule_chembl_id": cid}),
        make_api_request(f"{APIs['chembl']}/drug_indication.json", {"molecule_chembl_id": cid}),
        return_exceptions=True,
    )

    if isinstance(molecule, Exception) or _is_error(molecule):
        return _err(f"ChEMBL ID {chembl_id} の情報取得に失敗しました",
                    **(molecule if isinstance(molecule, dict) else {"_error": str(molecule)}))

    props = molecule.get("molecule_properties") or {}
    structs = molecule.get("molecule_structures") or {}
    out: dict[str, Any] = {
        "chembl_id": molecule.get("molecule_chembl_id"),
        "name": molecule.get("pref_name"),
        "molecule_type": molecule.get("molecule_type"),
        "max_phase": molecule.get("max_phase"),
        "first_approval": molecule.get("first_approval"),
        "oral": molecule.get("oral"),
        "parenteral": molecule.get("parenteral"),
        "properties": {
            "full_mwt": props.get("full_mwt"),
            "alogp": props.get("alogp"),
            "hba": props.get("hba"),
            "hbd": props.get("hbd"),
            "psa": props.get("psa"),
            "ro5_violations": props.get("num_ro5_violations"),
        },
        "canonical_smiles": structs.get("canonical_smiles"),
        "mechanisms": [],
        "indications": [],
    }

    if not isinstance(mechanisms, Exception) and not _is_error(mechanisms):
        out["mechanisms"] = [
            {
                "mechanism_of_action": m.get("mechanism_of_action"),
                "action_type": m.get("action_type"),
                "target_chembl_id": m.get("target_chembl_id"),
            }
            for m in mechanisms.get("mechanisms", [])
        ]

    if not isinstance(indications, Exception) and not _is_error(indications):
        seen: set[str] = set()
        for ind in indications.get("drug_indications", []):
            name = ind.get("efo_term") or ind.get("mesh_heading")
            key = f"{name}|{ind.get('max_phase_for_ind')}"
            if name and key not in seen:
                seen.add(key)
                out["indications"].append({
                    "indication": name,
                    "max_phase": ind.get("max_phase_for_ind"),
                    "efo_id": ind.get("efo_id"),
                })

    out["source"] = "ChEMBL"
    return _ok(out)


# ===== パスウェイグラフ・gap検出ツール（Phase 2） =====

# ReactionLikeEvent と見なすschemaClass以外（＝Pathway階層）はグラフ構築時にスキップする
_PATHWAY_SCHEMA_CLASSES = {"Pathway", "TopLevelPathway"}


class _UnionFind:
    """連結成分検出用の軽量Union-Find（新規依存を避けるため自前実装）。"""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self._parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # 経路圧縮
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def components(self) -> list[list[str]]:
        groups: dict[str, list[str]] = {}
        for node in self._parent:
            groups.setdefault(self.find(node), []).append(node)
        return sorted(groups.values(), key=len, reverse=True)


async def _build_pathway_graph_data(pathway_id: str, max_reactions: int) -> dict[str, Any]:
    """パスウェイの反応から二部グラフ（エンティティ⇄反応）を構築する。

    戻り値: {"nodes": {...}, "edges": [...], "reactions": [...], "_meta": {...}}
    もしくは失敗時 {"_error": ...}。
    ノードは stId をキーとする dict。エッジは input(entity→reaction) / output(reaction→entity)。
    """
    pid = pathway_id.strip()
    events = await make_api_request(f"{APIs['reactome']}/pathway/{quote(pid, safe='')}/containedEvents")
    if _is_error(events):
        return events
    if not isinstance(events, list):
        return {"_error": "containedEventsが配列ではありません", "_status": None, "_body": str(events)[:300]}

    # containedEvents は dict と bare int(dbId参照) が混在するため dict のみ対象にする
    reactions = [
        e for e in events
        if isinstance(e, dict) and e.get("stId") and e.get("schemaClass") not in _PATHWAY_SCHEMA_CLASSES
    ]
    total_reactions = len(reactions)
    truncated = total_reactions > max_reactions
    reactions = reactions[:max_reactions]

    if not reactions:
        return {
            "_error": "このパスウェイに反応（ReactionLikeEvent）が見つかりませんでした",
            "_status": None,
            "pathway_id": pid,
        }

    details = await asyncio.gather(
        *[make_api_request(f"{APIs['reactome']}/query/{quote(r['stId'], safe='')}") for r in reactions],
        return_exceptions=True,
    )

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    processed: list[dict[str, Any]] = []

    def _add_node(entity: Any, node_type: str) -> Optional[str]:
        if not isinstance(entity, dict):
            return None
        sid = entity.get("stId")
        if not sid:
            return None
        if sid not in nodes:
            nodes[sid] = {
                "id": sid,
                "label": entity.get("displayName") or entity.get("name") or sid,
                "type": node_type,
                "schema_class": entity.get("schemaClass"),
            }
        return sid

    for rxn, detail in zip(reactions, details):
        if isinstance(detail, Exception) or _is_error(detail):
            continue
        rid = _add_node(detail, "reaction")
        if rid is None:
            continue
        inputs = [i for i in (_add_node(e, "entity") for e in detail.get("input", []) or []) if i]
        outputs = [o for o in (_add_node(e, "entity") for e in detail.get("output", []) or []) if o]
        for i in inputs:
            edges.append({"id": f"{i}->{rid}", "source": i, "target": rid, "role": "input"})
        for o in outputs:
            edges.append({"id": f"{rid}->{o}", "source": rid, "target": o, "role": "output"})
        processed.append({"stId": rid, "name": nodes[rid]["label"], "inputs": inputs, "outputs": outputs})

    return {
        "nodes": nodes,
        "edges": edges,
        "reactions": processed,
        "_meta": {
            "pathway_id": pid,
            "total_reactions_in_pathway": total_reactions,
            "reactions_processed": len(processed),
            "truncated": truncated,
            "max_reactions": max_reactions,
        },
    }


def _compute_pathway_gaps(graph: dict[str, Any]) -> dict[str, Any]:
    """グラフデータから弱連結成分・境界エンティティを算出する（detect/proposeで共用）。"""
    nodes = graph["nodes"]
    edges = graph["edges"]

    uf = _UnionFind()
    for n in nodes:
        uf.add(n)
    for e in edges:
        uf.union(e["source"], e["target"])
    components = uf.components()
    comp_index = {n: idx for idx, comp in enumerate(components) for n in comp}

    consumed: set[str] = set()  # 反応のinputになっている（消費される）
    produced: set[str] = set()  # 反応のoutputになっている（生成される）
    for e in edges:
        if e["role"] == "input":
            consumed.add(e["source"])
        elif e["role"] == "output":
            produced.add(e["target"])

    boundary_inputs = [
        n for n in nodes
        if nodes[n]["type"] == "entity" and n in consumed and n not in produced
    ]
    boundary_outputs = [
        n for n in nodes
        if nodes[n]["type"] == "entity" and n in produced and n not in consumed
    ]

    return {
        "components": components,
        "comp_index": comp_index,
        "consumed": consumed,
        "produced": produced,
        "boundary_inputs": boundary_inputs,
        "boundary_outputs": boundary_outputs,
    }


@mcp.tool()
async def build_pathway_graph(
    pathway_id: str,
    max_reactions: int = 40,
    as_cytoscape: bool = False,
) -> str:
    """Reactomeパスウェイを反応ネットワーク（エンティティ⇄反応の二部グラフ）として構築（Phase 2）

    各反応の input（基質）→反応→output（生成物）を有向エッジとして表現する。

    Args:
        pathway_id: Reactome stable ID（例: R-HSA-70171 = Glycolysis）
        max_reactions: 処理する反応の最大数（デフォルト:40、APIコール数の上限）
        as_cytoscape: TrueならCytoscape.js形式（elements）で返す（可視化用）
    """
    if not pathway_id or not pathway_id.strip():
        return _err("パスウェイIDが必要です")
    max_reactions = max(1, min(max_reactions, 200))

    graph = await _build_pathway_graph_data(pathway_id, max_reactions)
    if _is_error(graph):
        return _err("パスウェイグラフの構築に失敗しました", **graph)

    meta = graph["_meta"]
    if as_cytoscape:
        elements = {
            "nodes": [{"data": n} for n in graph["nodes"].values()],
            "edges": [{"data": e} for e in graph["edges"]],
        }
        return _ok({"format": "cytoscape", "elements": elements, "_meta": meta})

    return _ok({
        "pathway_id": meta["pathway_id"],
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "nodes": list(graph["nodes"].values()),
        "edges": graph["edges"],
        "_meta": meta,
        "hint": "可視化用にはas_cytoscape=Trueを指定。gap検出はdetect_pathway_gapsを使用。",
    })


@mcp.tool()
async def detect_pathway_gaps(pathway_id: str, max_reactions: int = 40) -> str:
    """パスウェイ反応ネットワーク上の未接続部分（gap）を検出（Phase 2）

    グラフを弱連結成分に分解し、複数成分に分かれている場合はそれらを「未接続領域（gap候補）」
    として報告する。あわせて、境界エンティティ（どの反応でも生成されない入力＝供給境界／
    どの反応でも消費されない出力＝末端）を列挙する。将来的にはネットワーク図として図示する。

    Args:
        pathway_id: Reactome stable ID（例: R-HSA-70171）
        max_reactions: 処理する反応の最大数（デフォルト:40）
    """
    if not pathway_id or not pathway_id.strip():
        return _err("パスウェイIDが必要です")
    max_reactions = max(1, min(max_reactions, 200))

    graph = await _build_pathway_graph_data(pathway_id, max_reactions)
    if _is_error(graph):
        return _err("パスウェイのgap検出に失敗しました", **graph)

    nodes = graph["nodes"]
    gaps = _compute_pathway_gaps(graph)
    components = gaps["components"]

    component_summaries = [
        {
            "size": len(comp),
            "reactions": sum(1 for n in comp if nodes[n]["type"] == "reaction"),
            "entities": sum(1 for n in comp if nodes[n]["type"] == "entity"),
            "sample_labels": [nodes[n]["label"] for n in comp if nodes[n]["type"] == "entity"][:5],
        }
        for comp in components
    ]

    boundary_inputs = [{"id": n, "label": nodes[n]["label"]} for n in gaps["boundary_inputs"]]
    boundary_outputs = [{"id": n, "label": nodes[n]["label"]} for n in gaps["boundary_outputs"]]

    num_components = len(components)
    if num_components <= 1:
        interpretation = "単一の連結成分。反応ネットワーク上に明示的な分断（gap）は検出されませんでした。"
    else:
        interpretation = (
            f"{num_components}個の未接続成分に分断されています。これらの成分間には既知データ上の"
            f"つながりが無く、gap候補（本来つながるべき反応・中間体が未記載の可能性）です。"
        )

    return _ok({
        "pathway_id": graph["_meta"]["pathway_id"],
        "num_components": num_components,
        "components": component_summaries,
        "boundary_inputs": boundary_inputs[:20],
        "boundary_outputs": boundary_outputs[:20],
        "boundary_inputs_count": len(boundary_inputs),
        "boundary_outputs_count": len(boundary_outputs),
        "interpretation": interpretation,
        "_meta": graph["_meta"],
        "note": "boundary_inputs は外部供給される基質、boundary_outputs は末端生成物。"
                "分断が反応数上限による見かけ上のものでないか _meta.truncated を確認してください。",
    })


@mcp.tool()
async def export_pathway_cytoscape(pathway_id: str, max_reactions: int = 40) -> str:
    """パスウェイ反応ネットワークをCytoscape.js形式（elements）でエクスポート（Phase 2, 可視化用）

    Args:
        pathway_id: Reactome stable ID（例: R-HSA-70171）
        max_reactions: 処理する反応の最大数（デフォルト:40）
    """
    if not pathway_id or not pathway_id.strip():
        return _err("パスウェイIDが必要です")
    max_reactions = max(1, min(max_reactions, 200))

    graph = await _build_pathway_graph_data(pathway_id, max_reactions)
    if _is_error(graph):
        return _err("Cytoscapeエクスポートに失敗しました", **graph)

    elements = {
        "nodes": [{"data": n} for n in graph["nodes"].values()],
        "edges": [{"data": e} for e in graph["edges"]],
    }
    return _ok({"format": "cytoscape", "elements": elements, "_meta": graph["_meta"]})


# ===== ノード候補提示ツール（Phase 3, 仮説生成） =====


def _event_summary(ev: Any) -> Optional[dict[str, Any]]:
    if not isinstance(ev, dict) or not ev.get("stId"):
        return None
    return {"stId": ev.get("stId"), "name": ev.get("displayName"), "schema_class": ev.get("schemaClass")}


def _reference_summary(entity_detail: dict) -> Optional[dict[str, Any]]:
    """エンティティのreferenceEntity（ChEBI/UniProt等）から識別子コンテキストを抽出。"""
    ref = entity_detail.get("referenceEntity")
    if not isinstance(ref, dict):
        return None
    name = ref.get("name")
    if isinstance(name, list):
        name = name[0] if name else None
    return {
        "identifier": ref.get("identifier"),
        "database": ref.get("databaseName"),
        "name": name,
    }


@mcp.tool()
async def propose_gap_node_candidates(
    pathway_id: str,
    max_reactions: int = 40,
    max_boundary_entities: int = 12,
    max_candidates_per_boundary: int = 8,
) -> str:
    """パスウェイのgap（未接続部分）を埋めうる「ノード候補」を仮説として提示（Phase 3）

    既知の反応データベース(Reactome)に基づき、gap境界の代謝物を「他の反応で消費/生成している
    反応」を辿って、そこに存在しうる中間ノードの候補を列挙する。特に、ある成分の境界出力を消費し
    別の成分の境界入力を生成する反応は、2成分を直接つなぐ「ブリッジ候補」として提示する。

    重要: 本ツールは**既知反応に基づく候補・仮説の提示のみ**を行い、実在性の断定や新規配列の生成は
    一切行わない。候補には必ず根拠（どの境界エンティティ・どの方向から導いたか）を併記する。

    Args:
        pathway_id: Reactome stable ID（例: R-HSA-70171）
        max_reactions: グラフ構築時に処理する反応の最大数（デフォルト:40）
        max_boundary_entities: 候補探索する境界エンティティの上限（入力/出力それぞれ、デフォルト:12）
        max_candidates_per_boundary: 境界エンティティ1つあたりの候補反応の上限（デフォルト:8）
    """
    if not pathway_id or not pathway_id.strip():
        return _err("パスウェイIDが必要です")
    max_reactions = max(1, min(max_reactions, 200))
    max_boundary_entities = max(1, min(max_boundary_entities, 40))
    max_candidates_per_boundary = max(1, min(max_candidates_per_boundary, 25))

    graph = await _build_pathway_graph_data(pathway_id, max_reactions)
    if _is_error(graph):
        return _err("ノード候補提示に失敗しました", **graph)

    nodes = graph["nodes"]
    gaps = _compute_pathway_gaps(graph)
    comp_index = gaps["comp_index"]
    own_reactions = {r["stId"] for r in graph["reactions"]}  # パスウェイ自身の反応（候補から除外）

    b_outputs = gaps["boundary_outputs"][:max_boundary_entities]
    b_inputs = gaps["boundary_inputs"][:max_boundary_entities]

    # 境界エンティティの詳細を並列取得（consumedByEvent / producedByEvent / referenceEntity）
    target_ids = b_outputs + b_inputs
    details = await asyncio.gather(
        *[make_api_request(f"{APIs['reactome']}/query/{quote(eid, safe='')}") for eid in target_ids],
        return_exceptions=True,
    )
    detail_map = {
        eid: d for eid, d in zip(target_ids, details)
        if not isinstance(d, Exception) and not _is_error(d) and isinstance(d, dict)
    }

    boundary_analysis: list[dict[str, Any]] = []
    consumer_map: dict[str, list[dict]] = {}  # 反応stId -> [{entity, component}] (境界出力を消費する反応)
    producer_map: dict[str, list[dict]] = {}  # 反応stId -> [{entity, component}] (境界入力を生成する反応)
    event_names: dict[str, str] = {}  # 候補反応stId -> displayName（グラフ外の反応名を保持）

    def _candidate_events(detail: dict, key: str) -> list[dict]:
        out = []
        for ev in detail.get(key, []) or []:
            s = _event_summary(ev)
            if s and s["stId"] not in own_reactions:
                out.append(s)
        return out[:max_candidates_per_boundary]

    for eid in b_outputs:
        detail = detail_map.get(eid)
        if not detail:
            continue
        cands = _candidate_events(detail, "consumedByEvent")
        boundary_analysis.append({
            "entity": {"id": eid, "label": nodes[eid]["label"]},
            "role": "boundary_output",
            "component": comp_index.get(eid),
            "reference": _reference_summary(detail),
            "direction": "この代謝物を消費する既知反応（＝下流に置きうる候補ノード）",
            "candidate_reactions": cands,
        })
        for c in cands:
            consumer_map.setdefault(c["stId"], []).append({"entity": eid, "component": comp_index.get(eid)})
            event_names[c["stId"]] = c["name"]

    for eid in b_inputs:
        detail = detail_map.get(eid)
        if not detail:
            continue
        cands = _candidate_events(detail, "producedByEvent")
        boundary_analysis.append({
            "entity": {"id": eid, "label": nodes[eid]["label"]},
            "role": "boundary_input",
            "component": comp_index.get(eid),
            "reference": _reference_summary(detail),
            "direction": "この代謝物を生成する既知反応（＝上流に置きうる候補ノード）",
            "candidate_reactions": cands,
        })
        for c in cands:
            producer_map.setdefault(c["stId"], []).append({"entity": eid, "component": comp_index.get(eid)})
            event_names[c["stId"]] = c["name"]

    # ブリッジ候補: ある成分の境界出力を消費し、別成分の境界入力を生成する同一反応
    bridge_candidates: list[dict[str, Any]] = []
    for rid in set(consumer_map) & set(producer_map):
        for src in consumer_map[rid]:
            for dst in producer_map[rid]:
                if src["component"] != dst["component"]:
                    label = nodes[rid]["label"] if rid in nodes else event_names.get(rid, rid)
                    bridge_candidates.append({
                        "candidate_reaction": {"stId": rid, "name": label},
                        "connects_components": [src["component"], dst["component"]],
                        "rationale": (
                            f"成分{src['component']}の境界出力『{nodes[src['entity']]['label']}』を消費し、"
                            f"成分{dst['component']}の境界入力『{nodes[dst['entity']]['label']}』を生成する既知反応。"
                            f"両成分を直接つなぐ中間ノードの候補。"
                        ),
                    })

    num_components = len(gaps["components"])
    return _ok({
        "pathway_id": graph["_meta"]["pathway_id"],
        "gap_present": num_components > 1,
        "num_components": num_components,
        "bridge_candidates": bridge_candidates,
        "boundary_analysis": boundary_analysis,
        "_meta": graph["_meta"],
        "disclaimer": (
            "これらは既知反応データベース(Reactome)に基づく候補・仮説であり、gapの実在性や"
            "候補ノードの妥当性を断定するものではありません。新規タンパク質配列の生成は行っていません。"
            "候補は必ず実験・文献で検証してください。"
        ),
        "hint": "bridge_candidates が2成分を直接つなぐ最有力の候補。boundary_analysis は各境界からの"
                "拡張候補で、gapが無い場合でもパスウェイ拡張のヒントになります。",
    })


# ===== 統合分析ツール =====


@mcp.tool()
async def comprehensive_protein_analysis(
    accession: str,
    fields: Optional[list[str]] = None,
    sources: Optional[list[str]] = None,
    max_pdb_results: int = 5,
) -> str:
    """タンパク質の包括的分析（UniProt + Reactome + PDB統合）

    Args:
        accession: UniProtアクセッション番号
        fields: UniProt情報のフィールド指定（get_protein_infoと同じ。デフォルト: references除外の軽量版）。
                全データ取得は fields=["all"]。
        sources: 取得するデータソース一覧（デフォルト: ["uniprot", "reactome", "pdb"]）。
                 一部のみ指定して高速化可能（例: sources=["uniprot"]）。
        max_pdb_results: 返すPDB構造の最大数（デフォルト: 5）
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")
    acc = accession.strip()
    acc_enc = quote(acc, safe="")

    active_sources = set(sources) if sources else {"uniprot", "reactome", "pdb"}

    # 各ソースのタスクを必要に応じて作成
    uniprot_task = None
    pathways_task = None
    pdb_task = None

    if "uniprot" in active_sources:
        uniprot_task = make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc_enc}", {"format": "json"})

    if "reactome" in active_sources:
        pathways_task = make_api_request(f"{APIs['reactome']}/mapping/UniProt/{acc_enc}/pathways")

    if "pdb" in active_sources:
        pdb_query = {
            "query": {
                "type": "terminal",
                "service": "text",
                "parameters": {
                    "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
                    "operator": "exact_match",
                    "value": acc,
                },
            },
            "return_type": "entry",
            "request_options": {
                "paginate": {"start": 0, "rows": max(max_pdb_results, 25)},
                "results_verbosity": "minimal",
                "sort": [{"sort_by": "score", "direction": "desc"}],
            },
        }
        pdb_task = make_api_request(APIs["pdb_search"], json_data=pdb_query)

    # 有効なタスクだけ並列実行
    tasks = [t for t in (uniprot_task, pathways_task, pdb_task) if t is not None]
    results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
    result_iter = iter(results)

    uniprot_result = next(result_iter) if uniprot_task else None
    pathway_result = next(result_iter) if pathways_task else None
    pdb_result = next(result_iter) if pdb_task else None

    analysis: dict[str, Any] = {
        "accession": acc,
        "uniprot_info": None,
        "pathways": None,
        "pdb_structures": None,
        "active_sources": sorted(active_sources),
        "error_messages": [],
    }

    if uniprot_result is not None:
        if isinstance(uniprot_result, Exception) or _is_error(uniprot_result):
            analysis["error_messages"].append({"source": "uniprot", "detail": str(uniprot_result)})
        else:
            analysis["uniprot_info"] = _filter_uniprot_response(uniprot_result, fields)

    if pathway_result is not None:
        if isinstance(pathway_result, Exception) or _is_error(pathway_result):
            analysis["error_messages"].append({"source": "reactome", "detail": str(pathway_result)})
        else:
            analysis["pathways"] = pathway_result

    if pdb_result is not None:
        if isinstance(pdb_result, Exception) or _is_error(pdb_result):
            analysis["error_messages"].append({"source": "pdb", "detail": str(pdb_result)})
        elif "result_set" in pdb_result:
            analysis["pdb_structures"] = {
                "result_set": pdb_result["result_set"][:max_pdb_results],
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
async def get_protein_features(
    accession: str,
    feature_types: Optional[list[str]] = None,
    include_comments: bool = True,
    include_keywords: bool = True,
    max_features: int = 100,
) -> str:
    """タンパク質の機能的特徴とドメイン情報を取得

    Args:
        accession: UniProtアクセッション番号
        feature_types: 取得するfeatureタイプの一覧（例: ["Domain", "Active site", "Binding site"]）。
                       Noneの場合は全タイプを返す。
        include_comments: commentsセクションを含めるか（デフォルト: True）
        include_keywords: keywordsセクションを含めるか（デフォルト: True）
        max_features: 返すfeaturesの最大数（デフォルト: 100）
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")

    acc = quote(accession.strip(), safe="")
    result = await make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc}", {"format": "json"})
    if _is_error(result):
        return _err(f"アクセッション番号 {accession} の特徴情報取得に失敗しました", **result)

    features = result.get("features", [])

    # feature_types が指定されていればフィルタ
    if feature_types:
        type_set = set(feature_types)
        features = [f for f in features if f.get("type") in type_set]

    total_features = len(features)
    features = features[:max_features]

    out: dict[str, Any] = {
        "accession": result.get("primaryAccession"),
        "name": result.get("uniProtkbId"),
        "features": features,
        "total_features": total_features,
    }

    if total_features > max_features:
        out["_truncated"] = True
        out["_hint"] = f"全{total_features}件中{max_features}件を返しています。max_features を増やすか feature_types で絞り込んでください。"

    if include_comments:
        out["comments"] = result.get("comments", [])
    if include_keywords:
        out["keywords"] = result.get("keywords", [])

    # 主要タイプの集計は常に含める
    out["domains"] = [f for f in features if f.get("type") == "Domain"]
    out["active_sites"] = [f for f in features if f.get("type") == "Active site"]
    out["binding_sites"] = [f for f in features if f.get("type") == "Binding site"]

    return _ok(out)


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
