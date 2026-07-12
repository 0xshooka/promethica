#!/usr/bin/env python3
"""
Promethica MCP Server
創薬支援用タンパク質情報統合MCPサーバー

対応API:
- UniProt REST API (タンパク質基本情報・触媒活性)
- Rhea REST API (生化学反応・ChEBI参加物質) ※CC-BY 4.0
- RCSB PDB API (構造情報)
- Gene Ontology API (機能分類)
- Open Targets / ChEMBL (疾患関連・薬剤・副作用)
"""

import asyncio
import json
import logging
import random
import re
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
    "rhea": "https://www.rhea-db.org/rhea",
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
# 520/522/524 はCloudflare由来の一過性エラー。
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
    enrich=True の場合、抽出したUniProtアクセッションから触媒反応（Rhea）と
    GO注釈（UniProt）を補完取得する。

    Args:
        file_path: ローカルの.pdbファイルパス
        enrich: 抽出したUniProt IDから触媒反応・GO注釈を補完するか（デフォルト: True）
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
        reactions_result, uniprot_result = await asyncio.gather(
            _uniprot_catalytic_reactions(acc),
            make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc_enc}", {"format": "json"}),
            return_exceptions=True,
        )

        enrichment: dict[str, Any] = {"primary_accession": acc, "reactions": None, "go_annotations": None}

        if not isinstance(reactions_result, Exception) and not _is_error(reactions_result):
            enrichment["reactions"] = [
                {
                    "rhea_id": r.get("rhea_id"),
                    "name": r.get("name"),
                    "ec": r.get("ec"),
                    "url": f"https://www.rhea-db.org/rhea/{_rhea_num(r['rhea_id'])}" if r.get("rhea_id") else None,
                }
                for r in reactions_result.get("reactions", [])
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


# ===== Rhea + UniProt 反応ツール =====

# 反応ネットワークで「通貨代謝物（ハブ）」として扱い、既定でエッジ生成から除外するChEBI群。
# H2OやH(+)等は多数の反応に共通して現れ、無意味な連結（見かけ上のブリッジ）を生むため。
_CURRENCY_CHEBI: dict[str, str] = {
    "CHEBI:15377": "H2O", "CHEBI:15378": "H(+)", "CHEBI:30616": "ATP",
    "CHEBI:456216": "ADP", "CHEBI:456215": "AMP", "CHEBI:43474": "phosphate",
    "CHEBI:33019": "diphosphate", "CHEBI:57540": "NAD(+)", "CHEBI:57945": "NADH",
    "CHEBI:58349": "NADP(+)", "CHEBI:57783": "NADPH", "CHEBI:16526": "CO2",
    "CHEBI:15379": "O2", "CHEBI:28938": "NH4(+)", "CHEBI:57287": "CoA",
    "CHEBI:16240": "H2O2", "CHEBI:29985": "L-glutamate", "CHEBI:33813": "pyrophosphate",
}

_RHEA_PART_RE = re.compile(r'<a data-molid="chebi:(\d+)"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _rhea_num(rhea_id: str) -> str:
    """'RHEA:10300' / 'rhea:10300' / '10300' から数値部分を取り出す。"""
    return rhea_id.strip().split(":")[-1]


def _chebi_norm(chebi_id: str) -> str:
    """'chebi:15377' / 'CHEBI:15377' / '15377' を 'CHEBI:15377' に正規化する。"""
    return f"CHEBI:{chebi_id.strip().split(':')[-1]}"


def _parse_rhea_participants(html: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Rheaのhtmlequationから左辺(基質)/右辺(生成物)の (ChEBI, 名前) リストを抽出する。

    反応式は ' = ' で基質と生成物に分かれ、各参加物質は data-molid="chebi:N" を持つ。
    """
    parts = re.split(r"</span>\s*=\s*<span", html, maxsplit=1)
    if len(parts) == 2:
        left_html, right_html = parts[0], parts[1]
    else:
        seg = html.split(" = ", 1)
        left_html, right_html = seg[0], (seg[1] if len(seg) > 1 else "")

    def side(h: str) -> list[tuple[str, str]]:
        return [(_chebi_norm(cid), _TAG_RE.sub("", name_html).strip())
                for cid, name_html in _RHEA_PART_RE.findall(h)]

    return side(left_html), side(right_html)


async def _fetch_rhea_reaction(rhea_id: str) -> dict[str, Any]:
    """Rhea APIから1反応を取得し、基質/生成物を構造化して返す。失敗時 {"_error": ...}。"""
    num = _rhea_num(rhea_id)
    res = await make_api_request(APIs["rhea"], {"query": f"RHEA:{num}", "format": "json", "limit": 1})
    if _is_error(res):
        return res
    results = res.get("results") if isinstance(res, dict) else None
    if not results:
        return {"_error": f"Rhea反応 {rhea_id} が見つかりませんでした", "_status": None}
    r = results[0]
    left, right = _parse_rhea_participants(r.get("htmlequation", ""))
    return {"rhea_id": f"RHEA:{r.get('id')}", "equation": r.get("equation"), "left": left, "right": right}


async def _uniprot_catalytic_reactions(accession: str) -> dict[str, Any]:
    """UniProtエントリの CATALYTIC ACTIVITY 注釈からRhea反応情報を抽出する。失敗時 {"_error": ...}。"""
    acc_enc = quote(accession.strip(), safe="")
    u = await make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc_enc}", {"format": "json"})
    if _is_error(u):
        return u
    name = (u.get("proteinDescription", {}).get("recommendedName", {})
            .get("fullName", {}).get("value"))
    reactions = []
    for c in u.get("comments", []):
        if c.get("commentType") != "CATALYTIC ACTIVITY":
            continue
        rx = c.get("reaction", {}) or {}
        rhea_id = None
        chebis = []
        for xref in rx.get("reactionCrossReferences", []) or []:
            db, xid = xref.get("database"), xref.get("id", "")
            if db == "Rhea" and xid.startswith("RHEA:") and rhea_id is None:
                rhea_id = xid
            elif db == "ChEBI":
                chebis.append(xid)
        reactions.append({"rhea_id": rhea_id, "name": rx.get("name"), "ec": rx.get("ecNumber"),
                          "chebi_ids": chebis})
    return {"accession": accession.strip(), "protein_name": name, "reactions": reactions}


async def _rhea_reactions_by_chebi(chebi_id: str, limit: int) -> dict[str, Any]:
    """指定ChEBIを含むRhea反応を検索する（ブリッジ探索用）。失敗時 {"_error": ...}。"""
    num = _chebi_norm(chebi_id).split(":")[-1]
    res = await make_api_request(APIs["rhea"], {"query": f"CHEBI:{num}", "format": "json", "limit": limit})
    if _is_error(res):
        return res
    out = []
    for r in (res.get("results") or []):
        left, right = _parse_rhea_participants(r.get("htmlequation", ""))
        out.append({"rhea_id": f"RHEA:{r.get('id')}", "equation": r.get("equation"),
                    "left": left, "right": right})
    return {"reactions": out}


async def _uniprot_enzymes_for_rhea(rhea_id: str, organism: str, size: int) -> list[dict[str, Any]]:
    """Rhea反応を触媒する酵素をUniProtから逆引きする（候補酵素の提示用）。"""
    q = f"rhea:{_rhea_num(rhea_id)}"
    if organism:
        q += f' AND organism_name:"{_escape_lucene(organism)}"'
    res = await make_api_request(
        f"{APIs['uniprot']}/uniprotkb/search",
        {"query": q, "format": "json", "size": size,
         "fields": "accession,protein_name,organism_name"},
    )
    if _is_error(res) or not isinstance(res, dict):
        return []
    return [
        {
            "accession": p.get("primaryAccession"),
            "name": (p.get("proteinDescription", {}).get("recommendedName", {})
                     .get("fullName", {}).get("value")),
            "organism": p.get("organism", {}).get("scientificName"),
        }
        for p in res.get("results", [])
    ]


@mcp.tool()
async def search_reactions(query: str, limit: int = 10) -> str:
    """Rheaで生化学反応を検索（キーワード・代謝物名・EC番号・ChEBI IDなど）

    Reactomeの「パスウェイ検索」の後継。Rheaは酵素反応の専門DBで、ライセンスがCC-BY 4.0のため
    商用利用の条件も明快。

    Args:
        query: 検索クエリ（例: "citrate", "CHEBI:15377", "1.2.1.12", "glyceraldehyde"）
        limit: 結果数（1-50、デフォルト:10）
    """
    if not query or not query.strip():
        return _err("検索クエリが必要です")
    limit = max(1, min(limit, 50))
    res = await make_api_request(APIs["rhea"], {"query": query.strip(), "format": "json", "limit": limit})
    if _is_error(res):
        return _err("Rhea反応の検索に失敗しました", **res)
    reactions = []
    for r in (res.get("results") or []):
        left, right = _parse_rhea_participants(r.get("htmlequation", ""))
        reactions.append({
            "rhea_id": f"RHEA:{r.get('id')}",
            "equation": r.get("equation"),
            "substrates": [{"chebi": c, "name": n} for c, n in left],
            "products": [{"chebi": c, "name": n} for c, n in right],
            "url": f"https://www.rhea-db.org/rhea/{r.get('id')}",
        })
    return _ok({
        "query": query.strip(),
        "count": res.get("count", len(reactions)),
        "returned": len(reactions),
        "reactions": reactions,
        "source": "Rhea (CC-BY 4.0)",
    })


@mcp.tool()
async def get_protein_reactions(accession: str) -> str:
    """タンパク質（酵素）が触媒する生化学反応を取得（UniProt触媒活性 → Rhea）

    Reactomeの get_protein_pathways の後継。UniProtの CATALYTIC ACTIVITY 注釈から
    Rhea反応ID・EC番号・反応式を取得する。

    Args:
        accession: UniProtアクセッション番号
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")
    result = await _uniprot_catalytic_reactions(accession)
    if _is_error(result):
        return _err(f"アクセッション {accession} の反応情報取得に失敗しました", **result)
    reactions = [
        {
            "rhea_id": r["rhea_id"],
            "name": r["name"],
            "ec": r["ec"],
            "url": f"https://www.rhea-db.org/rhea/{_rhea_num(r['rhea_id'])}" if r["rhea_id"] else None,
        }
        for r in result["reactions"]
    ]
    return _ok({
        "accession": result["accession"],
        "protein_name": result["protein_name"],
        "total_reactions": len(reactions),
        "reactions": reactions,
        "source": "UniProt catalytic activity + Rhea",
        "note": "反応が空の場合、このタンパク質は酵素でないか触媒活性が注釈されていない可能性があります。",
    })


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


# ===== 反応ネットワーク・gap検出ツール（Phase 2, Rhea+UniProt） =====


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


async def _build_reaction_network_data(
    accessions: list[str], max_reactions: int, exclude_currency: bool
) -> dict[str, Any]:
    """酵素(UniProt)集合のRhea反応から、代謝物⇄反応の二部グラフを構築する。

    戻り値: {"nodes": {...}, "edges": [...], "reactions": [...], "_meta": {...}} または {"_error": ...}。
    ノードキー: 反応=RHEA:ID / 代謝物=CHEBI:ID。
    エッジ: 基質→反応(input) / 反応→生成物(output)。方向はRheaの反応式の左右に従う。
    """
    accs = [a.strip() for a in accessions if a and a.strip()]
    if not accs:
        return {"_error": "UniProtアクセッションが1つ以上必要です", "_status": None}

    # 1. 各酵素の触媒反応を並列取得し、rhea_id -> 触媒酵素 のマップを作る
    cat_results = await asyncio.gather(
        *[_uniprot_catalytic_reactions(a) for a in accs], return_exceptions=True
    )
    rhea_to_enzymes: dict[str, list[str]] = {}
    rhea_meta: dict[str, dict] = {}
    enzymes_without_reactions: list[str] = []
    for acc, res in zip(accs, cat_results):
        if isinstance(res, Exception) or _is_error(res):
            continue
        has = False
        for r in res.get("reactions", []):
            rid = r.get("rhea_id")
            if not rid:
                continue
            has = True
            rhea_to_enzymes.setdefault(rid, [])
            if acc not in rhea_to_enzymes[rid]:
                rhea_to_enzymes[rid].append(acc)
            rhea_meta.setdefault(rid, {"name": r.get("name"), "ec": r.get("ec")})
        if not has:
            enzymes_without_reactions.append(acc)

    if not rhea_to_enzymes:
        return {"_error": "指定した酵素にRhea反応が見つかりませんでした（酵素でない可能性があります）",
                "_status": None, "accessions": accs}

    rhea_ids = list(rhea_to_enzymes.keys())
    total_reactions = len(rhea_ids)
    truncated = total_reactions > max_reactions
    rhea_ids = rhea_ids[:max_reactions]

    # 2. 各Rhea反応の構造化参加物質を並列取得
    details = await asyncio.gather(
        *[_fetch_rhea_reaction(rid) for rid in rhea_ids], return_exceptions=True
    )

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    processed: list[dict[str, Any]] = []

    def _metabolite_node(chebi: str, name: str) -> Optional[str]:
        if exclude_currency and chebi in _CURRENCY_CHEBI:
            return None
        if chebi not in nodes:
            nodes[chebi] = {"id": chebi, "label": name or chebi, "type": "metabolite"}
        return chebi

    for rid, detail in zip(rhea_ids, details):
        if isinstance(detail, Exception) or _is_error(detail):
            continue
        meta = rhea_meta.get(rid, {})
        label = meta.get("name") or detail.get("equation") or rid
        nodes[rid] = {
            "id": rid, "label": label, "type": "reaction",
            "ec": meta.get("ec"), "equation": detail.get("equation"),
            "enzymes": rhea_to_enzymes.get(rid, []),
        }
        subs = [s for s in (_metabolite_node(c, n) for c, n in detail["left"]) if s]
        prods = [p for p in (_metabolite_node(c, n) for c, n in detail["right"]) if p]
        for s in subs:
            edges.append({"id": f"{s}->{rid}", "source": s, "target": rid, "role": "input"})
        for p in prods:
            edges.append({"id": f"{rid}->{p}", "source": rid, "target": p, "role": "output"})
        processed.append({"rhea_id": rid, "name": label, "substrates": subs, "products": prods})

    return {
        "nodes": nodes,
        "edges": edges,
        "reactions": processed,
        "_meta": {
            "accessions": accs,
            "enzymes_without_reactions": enzymes_without_reactions,
            "total_reactions": total_reactions,
            "reactions_processed": len(processed),
            "truncated": truncated,
            "max_reactions": max_reactions,
            "currency_excluded": exclude_currency,
        },
    }


def _compute_network_gaps(graph: dict[str, Any]) -> dict[str, Any]:
    """グラフデータから弱連結成分・境界代謝物を算出する（detect/proposeで共用）。"""
    nodes = graph["nodes"]
    edges = graph["edges"]

    uf = _UnionFind()
    for n in nodes:
        uf.add(n)
    for e in edges:
        uf.union(e["source"], e["target"])
    components = uf.components()
    comp_index = {n: idx for idx, comp in enumerate(components) for n in comp}

    consumed: set[str] = set()  # 反応のinput（消費される代謝物）
    produced: set[str] = set()  # 反応のoutput（生成される代謝物）
    for e in edges:
        if e["role"] == "input":
            consumed.add(e["source"])
        elif e["role"] == "output":
            produced.add(e["target"])

    boundary_inputs = [
        n for n in nodes
        if nodes[n]["type"] == "metabolite" and n in consumed and n not in produced
    ]
    boundary_outputs = [
        n for n in nodes
        if nodes[n]["type"] == "metabolite" and n in produced and n not in consumed
    ]

    return {
        "components": components,
        "comp_index": comp_index,
        "boundary_inputs": boundary_inputs,
        "boundary_outputs": boundary_outputs,
    }


def _validate_accessions(accessions: Optional[list[str]]) -> Optional[list[str]]:
    if not accessions:
        return None
    return [a.strip() for a in accessions if a and a.strip()] or None


@mcp.tool()
async def build_reaction_network(
    accessions: list[str],
    max_reactions: int = 40,
    exclude_currency: bool = True,
    as_cytoscape: bool = False,
) -> str:
    """酵素(UniProt)集合の触媒反応から、代謝物⇄反応の二部グラフを構築（Phase 2, Rhea+UniProt）

    Reactomeの build_pathway_graph の後継。入力は「反応でつなぎたい酵素の集合」（UniProtアクセッション）。
    各酵素のRhea反応の基質→反応→生成物を有向エッジとして表現する。

    Args:
        accessions: UniProtアクセッションのリスト（例: ["P04406", "P00558"]）
        max_reactions: 処理する反応の最大数（デフォルト:40）
        exclude_currency: H2O/ATP/NAD(+)等の通貨代謝物をエッジから除外するか（デフォルト:True）
        as_cytoscape: TrueならCytoscape.js形式（elements）で返す
    """
    accs = _validate_accessions(accessions)
    if not accs:
        return _err("UniProtアクセッションのリストが必要です")
    max_reactions = max(1, min(max_reactions, 200))

    graph = await _build_reaction_network_data(accs, max_reactions, exclude_currency)
    if _is_error(graph):
        return _err("反応ネットワークの構築に失敗しました", **graph)

    meta = graph["_meta"]
    if as_cytoscape:
        elements = {
            "nodes": [{"data": n} for n in graph["nodes"].values()],
            "edges": [{"data": e} for e in graph["edges"]],
        }
        return _ok({"format": "cytoscape", "elements": elements, "_meta": meta})

    return _ok({
        "accessions": meta["accessions"],
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "nodes": list(graph["nodes"].values()),
        "edges": graph["edges"],
        "_meta": meta,
        "hint": "可視化用にはas_cytoscape=True。gap検出はdetect_network_gaps、候補提示はpropose_gap_node_candidates。",
    })


@mcp.tool()
async def detect_network_gaps(
    accessions: list[str],
    max_reactions: int = 40,
    exclude_currency: bool = True,
) -> str:
    """酵素集合の反応ネットワーク上の未接続部分（gap）を検出（Phase 2, Rhea+UniProt）

    グラフを弱連結成分に分解し、複数成分に分かれている場合は「未接続領域（gap候補）」として報告する。
    境界代謝物（どの反応でも生成されない基質＝供給境界／どの反応でも消費されない生成物＝末端）も列挙する。

    Args:
        accessions: UniProtアクセッションのリスト
        max_reactions: 処理する反応の最大数（デフォルト:40）
        exclude_currency: 通貨代謝物を除外するか（デフォルト:True）
    """
    accs = _validate_accessions(accessions)
    if not accs:
        return _err("UniProtアクセッションのリストが必要です")
    max_reactions = max(1, min(max_reactions, 200))

    graph = await _build_reaction_network_data(accs, max_reactions, exclude_currency)
    if _is_error(graph):
        return _err("反応ネットワークのgap検出に失敗しました", **graph)

    nodes = graph["nodes"]
    gaps = _compute_network_gaps(graph)
    components = gaps["components"]

    component_summaries = [
        {
            "size": len(comp),
            "reactions": sum(1 for n in comp if nodes[n]["type"] == "reaction"),
            "metabolites": sum(1 for n in comp if nodes[n]["type"] == "metabolite"),
            "sample_labels": [nodes[n]["label"] for n in comp if nodes[n]["type"] == "metabolite"][:5],
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
            f"{num_components}個の未接続成分に分断されています。これらの成分間には既知反応データ上の"
            f"つながりが無く、gap候補（本来つながるべき反応・中間代謝物が未記載/未取得の可能性）です。"
        )

    return _ok({
        "accessions": graph["_meta"]["accessions"],
        "num_components": num_components,
        "components": component_summaries,
        "boundary_inputs": boundary_inputs[:20],
        "boundary_outputs": boundary_outputs[:20],
        "boundary_inputs_count": len(boundary_inputs),
        "boundary_outputs_count": len(boundary_outputs),
        "interpretation": interpretation,
        "_meta": graph["_meta"],
        "note": "境界代謝物は外部供給される基質/末端生成物。分断が反応数上限や通貨代謝物除外による"
                "見かけ上のものでないか _meta を確認してください。",
    })


@mcp.tool()
async def export_network_cytoscape(
    accessions: list[str],
    max_reactions: int = 40,
    exclude_currency: bool = True,
) -> str:
    """酵素集合の反応ネットワークをCytoscape.js形式（elements）でエクスポート（Phase 2, 可視化用）

    Args:
        accessions: UniProtアクセッションのリスト
        max_reactions: 処理する反応の最大数（デフォルト:40）
        exclude_currency: 通貨代謝物を除外するか（デフォルト:True）
    """
    accs = _validate_accessions(accessions)
    if not accs:
        return _err("UniProtアクセッションのリストが必要です")
    max_reactions = max(1, min(max_reactions, 200))

    graph = await _build_reaction_network_data(accs, max_reactions, exclude_currency)
    if _is_error(graph):
        return _err("Cytoscapeエクスポートに失敗しました", **graph)

    elements = {
        "nodes": [{"data": n} for n in graph["nodes"].values()],
        "edges": [{"data": e} for e in graph["edges"]],
    }
    return _ok({"format": "cytoscape", "elements": elements, "_meta": graph["_meta"]})


# ===== ノード候補提示ツール（Phase 3, 仮説生成） =====


@mcp.tool()
async def propose_gap_node_candidates(
    accessions: list[str],
    max_reactions: int = 40,
    exclude_currency: bool = True,
    max_boundary_metabolites: int = 12,
    max_candidates_per_boundary: int = 8,
    organism: str = "Homo sapiens",
) -> str:
    """反応ネットワークのgap（未接続部分）を埋めうる「ノード候補」を仮説として提示（Phase 3）

    既知反応DB(Rhea)に基づき、gap境界の代謝物を「他の反応で消費/生成している既知反応」を辿って、
    そこに置きうる中間ノード（反応と、それを触媒しうる酵素）の候補を列挙する。特に、ある成分の
    境界出力を消費し別の成分の境界入力を生成する反応は「ブリッジ候補」として提示する。

    重要: 本ツールは**既知反応に基づく候補・仮説の提示のみ**を行い、実在性の断定や新規配列の生成は
    一切行わない。候補には必ず根拠（どの境界代謝物・どの方向から導いたか）を併記する。

    Args:
        accessions: UniProtアクセッションのリスト
        max_reactions: グラフ構築時に処理する反応の最大数（デフォルト:40）
        exclude_currency: 通貨代謝物を除外するか（デフォルト:True。ブリッジ品質のため推奨）
        max_boundary_metabolites: 候補探索する境界代謝物の上限（入力/出力それぞれ、デフォルト:12）
        max_candidates_per_boundary: 境界代謝物1つあたりの候補反応の上限（デフォルト:8）
        organism: 候補酵素の逆引き対象生物種（デフォルト: Homo sapiens。空文字で全生物種）
    """
    accs = _validate_accessions(accessions)
    if not accs:
        return _err("UniProtアクセッションのリストが必要です")
    max_reactions = max(1, min(max_reactions, 200))
    max_boundary_metabolites = max(1, min(max_boundary_metabolites, 40))
    max_candidates_per_boundary = max(1, min(max_candidates_per_boundary, 25))

    graph = await _build_reaction_network_data(accs, max_reactions, exclude_currency)
    if _is_error(graph):
        return _err("ノード候補提示に失敗しました", **graph)

    nodes = graph["nodes"]
    gaps = _compute_network_gaps(graph)
    comp_index = gaps["comp_index"]
    own_reactions = {r["rhea_id"] for r in graph["reactions"]}

    b_outputs = gaps["boundary_outputs"][:max_boundary_metabolites]
    b_inputs = gaps["boundary_inputs"][:max_boundary_metabolites]

    # 境界代謝物を含むRhea反応を並列検索
    query_ids = b_outputs + b_inputs
    search_results = await asyncio.gather(
        *[_rhea_reactions_by_chebi(cid, max_candidates_per_boundary * 3) for cid in query_ids],
        return_exceptions=True,
    )
    result_map = {
        cid: r for cid, r in zip(query_ids, search_results)
        if not isinstance(r, Exception) and not _is_error(r)
    }

    boundary_analysis: list[dict[str, Any]] = []
    # 反応 -> {side: [{chebi, component}]}。Rhea反応は双方向なので左右(side)は保持しつつ
    # ブリッジ判定は方向非依存（対辺に別成分の境界代謝物があれば連結できる）とする。
    reaction_hits: dict[str, dict[str, Any]] = {}

    def _side_of(cid: str, rxn: dict) -> Optional[str]:
        if cid in {c for c, _ in rxn["left"]}:
            return "left"
        if cid in {c for c, _ in rxn["right"]}:
            return "right"
        return None

    boundary_meta = [(cid, "boundary_output") for cid in b_outputs] + \
                    [(cid, "boundary_input") for cid in b_inputs]

    for cid, role in boundary_meta:
        res = result_map.get(cid)
        comp = comp_index.get(cid)
        cands = []
        for rxn in (res or {}).get("reactions", []):
            rid = rxn["rhea_id"]
            if rid in own_reactions:
                continue
            side = _side_of(cid, rxn)
            if side is None:
                continue
            cands.append({"rhea_id": rid, "equation": rxn["equation"]})
            hit = reaction_hits.setdefault(rid, {"equation": rxn["equation"], "left": [], "right": []})
            hit[side].append({"chebi": cid, "component": comp})
            if len(cands) >= max_candidates_per_boundary:
                break
        boundary_analysis.append({
            "metabolite": {"id": cid, "label": nodes[cid]["label"]},
            "role": role,
            "component": comp,
            "direction": ("この代謝物が関与する他の既知反応（下流/上流に置きうる候補ノード）"),
            "candidate_reactions": cands,
        })

    # ブリッジ候補: 対辺(左右)に異なる成分の境界代謝物を持つ反応＝2成分を相互変換で直結しうる反応
    reaction_eq = {rid: h["equation"] for rid, h in reaction_hits.items()}
    bridge_links: dict[str, list[tuple[dict, dict]]] = {}
    for rid, h in reaction_hits.items():
        pairs = [(l, r) for l in h["left"] for r in h["right"] if l["component"] != r["component"]]
        if pairs:
            bridge_links[rid] = pairs
    bridge_rhea_ids = list(bridge_links.keys())

    # ブリッジ候補反応を触媒しうる酵素をUniProtから逆引き（候補ノード＝酵素）
    enzyme_lists = await asyncio.gather(
        *[_uniprot_enzymes_for_rhea(rid, organism, 5) for rid in bridge_rhea_ids],
        return_exceptions=True,
    )
    enzyme_map = {
        rid: (e if not isinstance(e, Exception) else [])
        for rid, e in zip(bridge_rhea_ids, enzyme_lists)
    }

    bridge_candidates: list[dict[str, Any]] = []
    for rid in bridge_rhea_ids:
        seen_pairs: set[tuple[str, str]] = set()
        for left, right in bridge_links[rid]:
            key = (left["chebi"], right["chebi"])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            bridge_candidates.append({
                "candidate_reaction": {"rhea_id": rid, "equation": reaction_eq.get(rid),
                                       "url": f"https://www.rhea-db.org/rhea/{_rhea_num(rid)}"},
                "candidate_enzymes": enzyme_map.get(rid, []),
                "connects_components": [left["component"], right["component"]],
                "rationale": (
                    f"成分{left['component']}の境界代謝物『{nodes[left['chebi']]['label']}』と"
                    f"成分{right['component']}の境界代謝物『{nodes[right['chebi']]['label']}』を相互変換する既知反応。"
                    f"両成分を直接つなぐ中間ノードの候補（Rhea反応は双方向）。"
                ),
            })

    num_components = len(gaps["components"])
    return _ok({
        "accessions": graph["_meta"]["accessions"],
        "gap_present": num_components > 1,
        "num_components": num_components,
        "bridge_candidates": bridge_candidates,
        "boundary_analysis": boundary_analysis,
        "_meta": graph["_meta"],
        "disclaimer": (
            "これらは既知反応データベース(Rhea)に基づく候補・仮説であり、gapの実在性や候補ノードの"
            "妥当性を断定するものではありません。新規タンパク質配列の生成は行っていません。"
            "候補は必ず実験・文献で検証してください。"
        ),
        "hint": "bridge_candidates が2成分を直接つなぐ最有力の候補（candidate_enzymesに触媒酵素も併記）。"
                "boundary_analysis は各境界からの拡張候補です。",
    })


# ===== 統合分析ツール =====


@mcp.tool()
async def comprehensive_protein_analysis(
    accession: str,
    fields: Optional[list[str]] = None,
    sources: Optional[list[str]] = None,
    max_pdb_results: int = 5,
) -> str:
    """タンパク質の包括的分析（UniProt + Rhea反応 + PDB統合）

    Args:
        accession: UniProtアクセッション番号
        fields: UniProt情報のフィールド指定（get_protein_infoと同じ。デフォルト: references除外の軽量版）。
                全データ取得は fields=["all"]。
        sources: 取得するデータソース一覧（デフォルト: ["uniprot", "reactions", "pdb"]）。
                 一部のみ指定して高速化可能（例: sources=["uniprot"]）。
        max_pdb_results: 返すPDB構造の最大数（デフォルト: 5）
    """
    if not accession or not accession.strip():
        return _err("アクセッション番号が必要です")
    acc = accession.strip()
    acc_enc = quote(acc, safe="")

    active_sources = set(sources) if sources else {"uniprot", "reactions", "pdb"}

    # 各ソースのタスクを必要に応じて作成
    uniprot_task = None
    reactions_task = None
    pdb_task = None

    if "uniprot" in active_sources:
        uniprot_task = make_api_request(f"{APIs['uniprot']}/uniprotkb/{acc_enc}", {"format": "json"})

    if "reactions" in active_sources:
        reactions_task = _uniprot_catalytic_reactions(acc)

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
    tasks = [t for t in (uniprot_task, reactions_task, pdb_task) if t is not None]
    results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
    result_iter = iter(results)

    uniprot_result = next(result_iter) if uniprot_task else None
    reactions_result = next(result_iter) if reactions_task else None
    pdb_result = next(result_iter) if pdb_task else None

    analysis: dict[str, Any] = {
        "accession": acc,
        "uniprot_info": None,
        "reactions": None,
        "pdb_structures": None,
        "active_sources": sorted(active_sources),
        "error_messages": [],
    }

    if uniprot_result is not None:
        if isinstance(uniprot_result, Exception) or _is_error(uniprot_result):
            analysis["error_messages"].append({"source": "uniprot", "detail": str(uniprot_result)})
        else:
            analysis["uniprot_info"] = _filter_uniprot_response(uniprot_result, fields)

    if reactions_result is not None:
        if isinstance(reactions_result, Exception) or _is_error(reactions_result):
            analysis["error_messages"].append({"source": "reactions", "detail": str(reactions_result)})
        else:
            analysis["reactions"] = reactions_result.get("reactions")

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
