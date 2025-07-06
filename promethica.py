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

import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

# ロギング設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastMCPサーバーを初期化
mcp = FastMCP("promethica")

# API エンドポイント
APIs = {
    "uniprot": "https://rest.uniprot.org",
    "reactome": "https://reactome.org/ContentService",
    "pdb": "https://data.rcsb.org/rest/v1",
    "pdb_search": "https://search.rcsb.org/rcsbsearch/v2/query",
    "go": "http://api.geneontology.org/api"
}

# HTTPクライアント設定
CLIENT_HEADERS = {
    "User-Agent": "Promethica-MCP-Server/1.0.0 (Drug Discovery Research Tool)"
}

async def make_api_request(url: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None) -> Dict[str, Any] | None:
    """汎用APIリクエスト関数"""
    async with httpx.AsyncClient(headers=CLIENT_HEADERS, timeout=30.0) as client:
        try:
            if json_data:
                response = await client.post(url, json=json_data)
            else:
                response = await client.get(url, params=params)
            
            response.raise_for_status()
            
            # レスポンスの内容タイプを確認
            content_type = response.headers.get('content-type', '')
            if 'application/json' in content_type:
                return response.json()
            else:
                return {"text": response.text, "content_type": content_type}
                
        except Exception as e:
            logger.error(f"API request failed for {url}: {e}")
            return None

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
    # バリデーション
    if not query or not query.strip():
        return "エラー: 検索クエリが必要です"
    
    if size > 50:
        size = 50  # 最大50件に制限
    
    # クエリ構築
    search_query = query
    if organism:
        search_query += f' AND organism_name:"{organism}"'
    
    url = f"{APIs['uniprot']}/uniprotkb/search"
    params = {
        "query": search_query,
        "format": format,
        "size": size
    }
    
    result = await make_api_request(url, params)
    if result is None:
        return "エラー: UniProt APIからのデータ取得に失敗しました"
    
    # JSON形式の場合、簡潔にフォーマット
    if format == "json" and "results" in result and result["results"]:
        summary = {
            "query": query,
            "organism": organism,
            "total_found": len(result["results"]),
            "proteins": []
        }
        
        for protein in result["results"]:
            protein_summary = {
                "accession": protein.get("primaryAccession"),
                "name": protein.get("uniProtkbId"),
                "protein_name": protein.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "名前不明"),
                "organism": protein.get("organism", {}).get("scientificName", "不明"),
                "length": protein.get("sequence", {}).get("length", "不明"),
                "reviewed": protein.get("entryType", "").startswith("UniProtKB reviewed")
            }
            summary["proteins"].append(protein_summary)
        
        return json.dumps(summary, indent=2, ensure_ascii=False)
    else:
        return json.dumps(result, indent=2, ensure_ascii=False)

@mcp.tool()
async def get_protein_info(accession: str, format: str = "json") -> str:
    """UniProtアクセッション番号から詳細情報を取得
    
    Args:
        accession: UniProtアクセッション番号
        format: 出力形式（json, tsv, fasta, xml）
    """
    if not accession or not accession.strip():
        return "エラー: アクセッション番号が必要です"
    
    url = f"{APIs['uniprot']}/uniprotkb/{accession}"
    params = {"format": format}
    
    result = await make_api_request(url, params)
    if result is None:
        return f"エラー: アクセッション番号 {accession} の情報取得に失敗しました"
    
    if format == "json":
        return json.dumps(result, indent=2, ensure_ascii=False)
    else:
        return result.get("text", str(result))

@mcp.tool()
async def get_protein_sequence(accession: str, format: str = "fasta") -> str:
    """タンパク質のアミノ酸配列を取得
    
    Args:
        accession: UniProtアクセッション番号
        format: 出力形式（fasta, json）
    """
    if not accession or not accession.strip():
        return "エラー: アクセッション番号が必要です"
    
    url = f"{APIs['uniprot']}/uniprotkb/{accession}"
    params = {"format": format}
    
    result = await make_api_request(url, params)
    if result is None:
        return f"エラー: アクセッション番号 {accession} の配列取得に失敗しました"
    
    if format == "fasta":
        return result.get("text", str(result))
    else:
        return json.dumps(result, indent=2, ensure_ascii=False)

# ===== PDB関連ツール =====

@mcp.tool()
async def search_pdb_structures(query: str, size: int = 25) -> str:
    """RCSB PDBでタンパク質構造を検索
    
    Args:
        query: 検索クエリ（タンパク質名、PDB IDなど）
        size: 結果数（1-100、デフォルト:25）
    """
    if not query or not query.strip():
        return "エラー: 検索クエリが必要です"
    
    if size < 1 or size > 100:
        return "エラー: サイズは1-100の範囲で指定してください"
    
    # PDB検索クエリ構築
    search_query = {
        "query": {
            "type": "terminal",
            "service": "text",
            "parameters": {
                "value": query
            }
        },
        "return_type": "entry",
        "request_options": {
            "return_all_hits": True,
            "results_verbosity": "minimal",
            "sort": [{"sort_by": "score", "direction": "desc"}]
        }
    }
    
    result = await make_api_request(APIs["pdb_search"], json_data=search_query)
    if result is None:
        return "エラー: PDB検索に失敗しました"
    
    # 結果を制限
    if "result_set" in result:
        result["result_set"] = result["result_set"][:size]
    
    return json.dumps(result, indent=2, ensure_ascii=False)

@mcp.tool()
async def get_pdb_structure_info(pdb_id: str) -> str:
    """PDB IDから構造詳細情報を取得
    
    Args:
        pdb_id: PDB ID（4文字）
    """
    if not pdb_id or len(pdb_id.strip()) != 4:
        return "エラー: PDB IDは4文字である必要があります"
    
    pdb_id = pdb_id.upper()
    url = f"{APIs['pdb']}/core/entry/{pdb_id}"
    
    result = await make_api_request(url)
    if result is None:
        return f"エラー: PDB ID {pdb_id} の構造情報取得に失敗しました"
    
    return json.dumps(result, indent=2, ensure_ascii=False)

# ===== Reactome関連ツール =====

@mcp.tool()
async def search_pathways(query: str, species: str = "Homo sapiens") -> str:
    """Reactomeでバイオロジカルパスウェイを検索
    
    Args:
        query: 検索クエリ（パスウェイ名、遺伝子名など）
        species: 生物種（デフォルト: Homo sapiens）
    """
    if not query or not query.strip():
        return "エラー: 検索クエリが必要です"
    
    url = f"{APIs['reactome']}/data/query/{quote(query)}"
    params = {"species": species}
    
    result = await make_api_request(url, params)
    if result is None:
        return "エラー: Reactome検索に失敗しました"
    
    return json.dumps(result, indent=2, ensure_ascii=False)

@mcp.tool()
async def get_protein_pathways(accession: str) -> str:
    """タンパク質が関与するパスウェイを取得
    
    Args:
        accession: UniProtアクセッション番号
    """
    if not accession or not accession.strip():
        return "エラー: アクセッション番号が必要です"
    
    url = f"{APIs['reactome']}/data/participants/{accession}/pathways"
    
    result = await make_api_request(url)
    if result is None:
        return f"エラー: アクセッション番号 {accession} のパスウェイ情報取得に失敗しました"
    
    return json.dumps(result, indent=2, ensure_ascii=False)

# ===== Gene Ontology関連ツール =====

@mcp.tool()
async def search_go_terms(go_term: str = None, query: str = None) -> str:
    """Gene Ontology用語を検索
    
    Args:
        go_term: GO term ID（例: GO:0005524）
        query: 検索クエリ（go_termまたはqueryのいずれかが必要）
    """
    if not go_term and not query:
        return "エラー: GO term IDまたは検索クエリが必要です"
    
    if go_term:
        # 特定のGO termの詳細を取得
        url = f"{APIs['go']}/bioentity/{go_term}"
    else:
        # クエリで検索
        url = f"{APIs['go']}/search/entity/{quote(query)}"
    
    result = await make_api_request(url)
    if result is None:
        return "エラー: Gene Ontology検索に失敗しました"
    
    return json.dumps(result, indent=2, ensure_ascii=False)

# ===== 統合分析ツール =====

@mcp.tool()
async def comprehensive_protein_analysis(accession: str) -> str:
    """タンパク質の包括的分析（UniProt + Reactome + PDB統合）
    
    Args:
        accession: UniProtアクセッション番号
    """
    if not accession or not accession.strip():
        return "エラー: アクセッション番号が必要です"
    
    analysis_result = {
        "accession": accession,
        "uniprot_info": None,
        "pathways": None,
        "pdb_structures": None,
        "error_messages": []
    }
    
    # UniProt情報取得
    try:
        uniprot_url = f"{APIs['uniprot']}/uniprotkb/{accession}"
        uniprot_result = await make_api_request(uniprot_url, {"format": "json"})
        if uniprot_result:
            analysis_result["uniprot_info"] = uniprot_result
        else:
            analysis_result["error_messages"].append("UniProt情報の取得に失敗")
    except Exception as e:
        analysis_result["error_messages"].append(f"UniProt取得エラー: {str(e)}")
    
    # Reactomeパスウェイ情報取得
    try:
        pathway_url = f"{APIs['reactome']}/data/participants/{accession}/pathways"
        pathway_result = await make_api_request(pathway_url)
        if pathway_result:
            analysis_result["pathways"] = pathway_result
        else:
            analysis_result["error_messages"].append("パスウェイ情報の取得に失敗")
    except Exception as e:
        analysis_result["error_messages"].append(f"パスウェイ取得エラー: {str(e)}")
    
    # PDB構造検索（UniProt IDで）
    try:
        pdb_search_query = {
            "query": {
                "type": "terminal",
                "service": "text",
                "parameters": {"value": accession}
            },
            "return_type": "entry",
            "request_options": {
                "return_all_hits": True,
                "results_verbosity": "minimal",
                "sort": [{"sort_by": "score", "direction": "desc"}]
            }
        }
        pdb_result = await make_api_request(APIs["pdb_search"], json_data=pdb_search_query)
        if pdb_result and "result_set" in pdb_result:
            # 結果を5件に制限
            analysis_result["pdb_structures"] = {
                "result_set": pdb_result["result_set"][:5],
                "total_count": pdb_result.get("total_count", 0)
            }
        else:
            analysis_result["error_messages"].append("PDB構造情報の取得に失敗")
    except Exception as e:
        analysis_result["error_messages"].append(f"PDB検索エラー: {str(e)}")
    
    return json.dumps(analysis_result, indent=2, ensure_ascii=False)

# ===== 追加の便利ツール =====

@mcp.tool()
async def get_primary_protein_for_gene(gene: str, organism: str = "Homo sapiens") -> str:
    """遺伝子に対応する主要なタンパク質情報を取得（最も関連性の高い1つのみ）
    
    Args:
        gene: 遺伝子名またはシンボル（例: APP, BRCA1, INS）
        organism: 生物種名（デフォルト: Homo sapiens）
    """
    if not gene or not gene.strip():
        return "エラー: 遺伝子名が必要です"
    
    # レビュー済みのエントリを優先して検索
    search_query = f'gene:"{gene}" AND organism_name:"{organism}" AND reviewed:true'
    
    url = f"{APIs['uniprot']}/uniprotkb/search"
    params = {
        "query": search_query,
        "format": "json",
        "size": 1  # 最も関連性の高い1つのみ
    }
    
    result = await make_api_request(url, params)
    if result is None:
        return f"エラー: 遺伝子 {gene} の検索に失敗しました"
    
    if "results" in result and result["results"]:
        protein = result["results"][0]
        
        primary_info = {
            "gene": gene,
            "organism": organism,
            "primary_protein": {
                "accession": protein.get("primaryAccession"),
                "name": protein.get("uniProtkbId"),
                "protein_name": protein.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "名前不明"),
                "organism": protein.get("organism", {}).get("scientificName", "不明"),
                "length": protein.get("sequence", {}).get("length", "不明"),
                "reviewed": protein.get("entryType", "").startswith("UniProtKB reviewed"),
                "function": None
            }
        }
        
        # 機能情報を追加
        comments = protein.get("comments", [])
        for comment in comments:
            if comment.get("commentType") == "FUNCTION":
                primary_info["primary_protein"]["function"] = comment.get("texts", [{}])[0].get("value", "")
                break
        
        return json.dumps(primary_info, indent=2, ensure_ascii=False)
    else:
        return f"遺伝子 {gene} ({organism}) に対応するレビュー済みタンパク質が見つかりませんでした"

@mcp.tool()
async def get_protein_features(accession: str) -> str:
    """タンパク質の機能的特徴とドメイン情報を取得
    
    Args:
        accession: UniProtアクセッション番号
    """
    if not accession or not accession.strip():
        return "エラー: アクセッション番号が必要です"
    
    url = f"{APIs['uniprot']}/uniprotkb/{accession}"
    params = {"format": "json"}
    
    result = await make_api_request(url, params)
    if result is None:
        return f"エラー: アクセッション番号 {accession} の特徴情報取得に失敗しました"
    
    # 特徴情報を抽出
    features_info = {
        "accession": result.get("primaryAccession"),
        "name": result.get("uniProtkbId"),
        "features": result.get("features", []),
        "comments": result.get("comments", []),
        "keywords": result.get("keywords", []),
        "domains": [f for f in result.get("features", []) if f.get("type") == "Domain"],
        "active_sites": [f for f in result.get("features", []) if f.get("type") == "Active site"],
        "binding_sites": [f for f in result.get("features", []) if f.get("type") == "Binding site"]
    }
    
    return json.dumps(features_info, indent=2, ensure_ascii=False)

@mcp.tool()
async def search_by_gene(gene: str, organism: str = "Homo sapiens", size: int = 10) -> str:
    """遺伝子名でタンパク質を検索（簡潔な結果を返す）
    
    Args:
        gene: 遺伝子名またはシンボル（例: BRCA1, INS, APP）
        organism: 生物種名（デフォルト: Homo sapiens）
        size: 結果数（1-50、デフォルト:10）
    """
    if not gene or not gene.strip():
        return "エラー: 遺伝子名が必要です"
    
    if size > 50:
        size = 50  # 最大50件に制限
    
    # クエリ構築
    search_query = f'gene:"{gene}"'
    if organism:
        search_query += f' AND organism_name:"{organism}"'
    
    url = f"{APIs['uniprot']}/uniprotkb/search"
    params = {
        "query": search_query,
        "format": "json",
        "size": size
    }
    
    result = await make_api_request(url, params)
    if result is None:
        return f"エラー: 遺伝子 {gene} の検索に失敗しました"
    
    # 結果を簡潔にフォーマット
    if "results" in result and result["results"]:
        summary = {
            "gene": gene,
            "organism": organism,
            "total_found": len(result["results"]),
            "proteins": []
        }
        
        for protein in result["results"]:
            protein_summary = {
                "accession": protein.get("primaryAccession"),
                "name": protein.get("uniProtkbId"),
                "protein_name": protein.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "名前不明"),
                "length": protein.get("sequence", {}).get("length", "不明"),
                "reviewed": protein.get("entryType", "").startswith("UniProtKB reviewed")
            }
            summary["proteins"].append(protein_summary)
        
        return json.dumps(summary, indent=2, ensure_ascii=False)
    else:
        return f"遺伝子 {gene} に関連するタンパク質が見つかりませんでした"

if __name__ == "__main__":
    # サーバーを起動
    mcp.run(transport='stdio')
