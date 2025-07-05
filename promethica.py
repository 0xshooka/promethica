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
import sys
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote

import aiohttp
from mcp import Server, types
from mcp.server.stdio import stdio_server

# ロギング設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PromeithicaServer:
    """Promethica MCP Server メインクラス"""
    
    def __init__(self):
        self.server = Server("promethica-server")
        self.session: Optional[aiohttp.ClientSession] = None
        
        # API エンドポイント
        self.apis = {
            "uniprot": "https://rest.uniprot.org",
            "reactome": "https://reactome.org/ContentService",
            "pdb": "https://data.rcsb.org/rest/v1",
            "pdb_search": "https://search.rcsb.org/rcsbsearch/v2/query",
            "go": "http://api.geneontology.org/api"
        }
        
        # セッション用ヘッダー
        self.headers = {
            "User-Agent": "Promethica-MCP-Server/1.0.0 (Drug Discovery Research Tool)"
        }

    async def __aenter__(self):
        """非同期コンテキストマネージャー開始"""
        self.session = aiohttp.ClientSession(
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=30)
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """非同期コンテキストマネージャー終了"""
        if self.session:
            await self.session.close()

    # ===== バリデーション関数 =====
    
    def validate_protein_search_args(self, args: Dict[str, Any]) -> bool:
        """タンパク質検索引数のバリデーション"""
        if not isinstance(args.get("query"), str) or not args["query"].strip():
            return False
        
        organism = args.get("organism")
        if organism is not None and not isinstance(organism, str):
            return False
            
        size = args.get("size", 25)
        if not isinstance(size, int) or size < 1 or size > 500:
            return False
            
        return True

    def validate_accession_args(self, args: Dict[str, Any]) -> bool:
        """アクセッション番号引数のバリデーション"""
        accession = args.get("accession")
        if not isinstance(accession, str) or not accession.strip():
            return False
        return True

    def validate_pdb_id_args(self, args: Dict[str, Any]) -> bool:
        """PDB ID引数のバリデーション"""
        pdb_id = args.get("pdb_id")
        if not isinstance(pdb_id, str) or not pdb_id.strip():
            return False
        # PDB IDは通常4文字
        if len(pdb_id.strip()) != 4:
            return False
        return True

    def validate_go_term_args(self, args: Dict[str, Any]) -> bool:
        """GO term引数のバリデーション"""
        go_term = args.get("go_term")
        if go_term and isinstance(go_term, str):
            return True
        query = args.get("query")
        if query and isinstance(query, str):
            return True
        return False

    def validate_pathway_search_args(self, args: Dict[str, Any]) -> bool:
        """パスウェイ検索引数のバリデーション"""
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return False
        return True

    # ===== API リクエスト関数 =====

    async def make_request(self, url: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """汎用APIリクエスト関数"""
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    content_type = response.headers.get('content-type', '')
                    if 'application/json' in content_type:
                        return await response.json()
                    else:
                        text = await response.text()
                        return {"text": text, "content_type": content_type}
                else:
                    raise Exception(f"API request failed: {response.status} - {await response.text()}")
        except aiohttp.ClientError as e:
            raise Exception(f"Network error: {str(e)}")

    # ===== UniProt関連ツール =====

    async def search_proteins(self, args: Dict[str, Any]) -> types.CallToolResult:
        """UniProtでタンパク質を検索"""
        if not self.validate_protein_search_args(args):
            raise Exception("Invalid protein search arguments")

        query = args["query"]
        organism = args.get("organism")
        size = args.get("size", 25)
        format_type = args.get("format", "json")

        # クエリ構築
        search_query = query
        if organism:
            search_query += f' AND organism_name:"{organism}"'

        url = f"{self.apis['uniprot']}/uniprotkb/search"
        params = {
            "query": search_query,
            "format": format_type,
            "size": size
        }

        try:
            result = await self.make_request(url, params)
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False)
                )]
            )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text", 
                    text=f"タンパク質検索エラー: {str(e)}"
                )],
                isError=True
            )

    async def get_protein_info(self, args: Dict[str, Any]) -> types.CallToolResult:
        """UniProtアクセッション番号から詳細情報を取得"""
        if not self.validate_accession_args(args):
            raise Exception("Invalid accession arguments")

        accession = args["accession"]
        format_type = args.get("format", "json")

        url = f"{self.apis['uniprot']}/uniprotkb/{accession}"
        params = {"format": format_type}

        try:
            result = await self.make_request(url, params)
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False)
                )]
            )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=f"タンパク質情報取得エラー: {str(e)}"
                )],
                isError=True
            )

    async def get_protein_sequence(self, args: Dict[str, Any]) -> types.CallToolResult:
        """タンパク質配列を取得"""
        if not self.validate_accession_args(args):
            raise Exception("Invalid accession arguments")

        accession = args["accession"]
        format_type = args.get("format", "fasta")

        url = f"{self.apis['uniprot']}/uniprotkb/{accession}"
        params = {"format": format_type}

        try:
            result = await self.make_request(url, params)
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=result.get("text", json.dumps(result, indent=2, ensure_ascii=False))
                )]
            )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=f"配列取得エラー: {str(e)}"
                )],
                isError=True
            )

    # ===== PDB関連ツール =====

    async def search_pdb_structures(self, args: Dict[str, Any]) -> types.CallToolResult:
        """PDBで構造を検索"""
        if not self.validate_protein_search_args(args):
            raise Exception("Invalid PDB search arguments")

        query = args["query"]
        size = args.get("size", 25)

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

        try:
            async with self.session.post(
                self.apis["pdb_search"],
                json=search_query
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    # 結果を制限
                    if "result_set" in result:
                        result["result_set"] = result["result_set"][:size]
                    
                    return types.CallToolResult(
                        content=[types.TextContent(
                            type="text",
                            text=json.dumps(result, indent=2, ensure_ascii=False)
                        )]
                    )
                else:
                    raise Exception(f"PDB search failed: {response.status}")
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=f"PDB検索エラー: {str(e)}"
                )],
                isError=True
            )

    async def get_pdb_structure_info(self, args: Dict[str, Any]) -> types.CallToolResult:
        """PDB構造詳細情報を取得"""
        if not self.validate_pdb_id_args(args):
            raise Exception("Invalid PDB ID arguments")

        pdb_id = args["pdb_id"].upper()

        url = f"{self.apis['pdb']}/core/entry/{pdb_id}"

        try:
            result = await self.make_request(url)
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False)
                )]
            )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=f"PDB構造情報取得エラー: {str(e)}"
                )],
                isError=True
            )

    # ===== Reactome関連ツール =====

    async def search_pathways(self, args: Dict[str, Any]) -> types.CallToolResult:
        """Reactomeでパスウェイを検索"""
        if not self.validate_pathway_search_args(args):
            raise Exception("Invalid pathway search arguments")

        query = args["query"]
        species = args.get("species", "Homo sapiens")

        url = f"{self.apis['reactome']}/data/query/{quote(query)}"
        params = {"species": species}

        try:
            result = await self.make_request(url, params)
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False)
                )]
            )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=f"パスウェイ検索エラー: {str(e)}"
                )],
                isError=True
            )

    async def get_protein_pathways(self, args: Dict[str, Any]) -> types.CallToolResult:
        """タンパク質が関与するパスウェイを取得"""
        if not self.validate_accession_args(args):
            raise Exception("Invalid accession arguments")

        accession = args["accession"]

        url = f"{self.apis['reactome']}/data/participants/{accession}/pathways"

        try:
            result = await self.make_request(url)
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False)
                )]
            )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=f"タンパク質パスウェイ取得エラー: {str(e)}"
                )],
                isError=True
            )

    # ===== Gene Ontology関連ツール =====

    async def search_go_terms(self, args: Dict[str, Any]) -> types.CallToolResult:
        """Gene Ontology用語を検索"""
        if not self.validate_go_term_args(args):
            raise Exception("Invalid GO term arguments")

        go_term = args.get("go_term")
        query = args.get("query")

        if go_term:
            # 特定のGO termの詳細を取得
            url = f"{self.apis['go']}/bioentity/{go_term}"
        else:
            # クエリで検索
            url = f"{self.apis['go']}/search/entity/{quote(query)}"

        try:
            result = await self.make_request(url)
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, ensure_ascii=False)
                )]
            )
        except Exception as e:
            return types.CallToolResult(
                content=[types.TextContent(
                    type="text",
                    text=f"GO検索エラー: {str(e)}"
                )],
                isError=True
            )

    # ===== 統合分析ツール =====

    async def comprehensive_protein_analysis(self, args: Dict[str, Any]) -> types.CallToolResult:
        """タンパク質の包括的分析（複数APIを統合）"""
        if not self.validate_accession_args(args):
            raise Exception("Invalid accession arguments")

        accession = args["accession"]
        analysis_result = {
            "accession": accession,
            "uniprot_info": None,
            "pathways": None,
            "pdb_structures": None,
            "error_messages": []
        }

        # UniProt情報取得
        try:
            uniprot_result = await self.get_protein_info({"accession": accession})
            if not uniprot_result.isError:
                analysis_result["uniprot_info"] = json.loads(uniprot_result.content[0].text)
        except Exception as e:
            analysis_result["error_messages"].append(f"UniProt取得エラー: {str(e)}")

        # Reactomeパスウェイ情報取得
        try:
            pathway_result = await self.get_protein_pathways({"accession": accession})
            if not pathway_result.isError:
                analysis_result["pathways"] = json.loads(pathway_result.content[0].text)
        except Exception as e:
            analysis_result["error_messages"].append(f"パスウェイ取得エラー: {str(e)}")

        # PDB構造検索（UniProt IDで）
        try:
            pdb_result = await self.search_pdb_structures({"query": accession, "size": 5})
            if not pdb_result.isError:
                analysis_result["pdb_structures"] = json.loads(pdb_result.content[0].text)
        except Exception as e:
            analysis_result["error_messages"].append(f"PDB検索エラー: {str(e)}")

        return types.CallToolResult(
            content=[types.TextContent(
                type="text",
                text=json.dumps(analysis_result, indent=2, ensure_ascii=False)
            )]
        )

    # ===== MCPサーバー設定 =====

    def setup_handlers(self):
        """MCPハンドラーの設定"""
        
        @self.server.list_tools()
        async def handle_list_tools() -> types.ListToolsResult:
            """利用可能なツール一覧を返す"""
            return types.ListToolsResult(
                tools=[
                    # UniProt関連ツール
                    types.Tool(
                        name="search_proteins",
                        description="UniProtデータベースでタンパク質を検索",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "検索クエリ（タンパク質名、キーワードなど）"},
                                "organism": {"type": "string", "description": "生物種名（オプション）"},
                                "size": {"type": "integer", "description": "結果数（1-500、デフォルト:25）", "minimum": 1, "maximum": 500},
                                "format": {"type": "string", "enum": ["json", "tsv", "fasta", "xml"], "description": "出力形式"}
                            },
                            "required": ["query"]
                        }
                    ),
                    types.Tool(
                        name="get_protein_info",
                        description="UniProtアクセッション番号から詳細情報を取得",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "accession": {"type": "string", "description": "UniProtアクセッション番号"},
                                "format": {"type": "string", "enum": ["json", "tsv", "fasta", "xml"], "description": "出力形式"}
                            },
                            "required": ["accession"]
                        }
                    ),
                    types.Tool(
                        name="get_protein_sequence",
                        description="タンパク質のアミノ酸配列を取得",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "accession": {"type": "string", "description": "UniProtアクセッション番号"},
                                "format": {"type": "string", "enum": ["fasta", "json"], "description": "出力形式"}
                            },
                            "required": ["accession"]
                        }
                    ),
                    # PDB関連ツール
                    types.Tool(
                        name="search_pdb_structures",
                        description="RCSB PDBでタンパク質構造を検索",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "検索クエリ（タンパク質名、PDB IDなど）"},
                                "size": {"type": "integer", "description": "結果数（デフォルト:25）", "minimum": 1, "maximum": 100}
                            },
                            "required": ["query"]
                        }
                    ),
                    types.Tool(
                        name="get_pdb_structure_info",
                        description="PDB IDから構造詳細情報を取得",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "pdb_id": {"type": "string", "description": "PDB ID（4文字）"}
                            },
                            "required": ["pdb_id"]
                        }
                    ),
                    # Reactome関連ツール
                    types.Tool(
                        name="search_pathways",
                        description="Reactomeでバイオロジカルパスウェイを検索",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "検索クエリ（パスウェイ名、遺伝子名など）"},
                                "species": {"type": "string", "description": "生物種（デフォルト: Homo sapiens）"}
                            },
                            "required": ["query"]
                        }
                    ),
                    types.Tool(
                        name="get_protein_pathways",
                        description="タンパク質が関与するパスウェイを取得",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "accession": {"type": "string", "description": "UniProtアクセッション番号"}
                            },
                            "required": ["accession"]
                        }
                    ),
                    # Gene Ontology関連ツール
                    types.Tool(
                        name="search_go_terms",
                        description="Gene Ontology用語を検索",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "go_term": {"type": "string", "description": "GO term ID（例: GO:0005524）"},
                                "query": {"type": "string", "description": "検索クエリ（GO termまたはqueryのいずれかが必要）"}
                            }
                        }
                    ),
                    # 統合分析ツール
                    types.Tool(
                        name="comprehensive_protein_analysis",
                        description="タンパク質の包括的分析（UniProt + Reactome + PDB統合）",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "accession": {"type": "string", "description": "UniProtアクセッション番号"}
                            },
                            "required": ["accession"]
                        }
                    )
                ]
            )

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
            """ツール呼び出しハンドラー"""
            try:
                if name == "search_proteins":
                    return await self.search_proteins(arguments)
                elif name == "get_protein_info":
                    return await self.get_protein_info(arguments)
                elif name == "get_protein_sequence":
                    return await self.get_protein_sequence(arguments)
                elif name == "search_pdb_structures":
                    return await self.search_pdb_structures(arguments)
                elif name == "get_pdb_structure_info":
                    return await self.get_pdb_structure_info(arguments)
                elif name == "search_pathways":
                    return await self.search_pathways(arguments)
                elif name == "get_protein_pathways":
                    return await self.get_protein_pathways(arguments)
                elif name == "search_go_terms":
                    return await self.search_go_terms(arguments)
                elif name == "comprehensive_protein_analysis":
                    return await self.comprehensive_protein_analysis(arguments)
                else:
                    raise Exception(f"Unknown tool: {name}")
            except Exception as e:
                logger.error(f"Tool execution error: {e}")
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"エラー: {str(e)}")],
                    isError=True
                )

        @self.server.list_resources()
        async def handle_list_resources() -> types.ListResourcesResult:
            """利用可能なリソース一覧を返す"""
            return types.ListResourcesResult(
                resources=[
                    types.Resource(
                        uri="promethica://protein/{accession}",
                        name="Protein Information",
                        description="タンパク質の統合情報",
                        mimeType="application/json"
                    ),
                    types.Resource(
                        uri="promethica://sequence/{accession}",
                        name="Protein Sequence",
                        description="タンパク質のアミノ酸配列",
                        mimeType="text/plain"
                    ),
                    types.Resource(
                        uri="promethica://structure/{pdb_id}",
                        name="Protein Structure",
                        description="タンパク質の3D構造情報",
                        mimeType="application/json"
                    )
                ]
            )

        @self.server.read_resource()
        async def handle_read_resource(uri: str) -> types.ReadResourceResult:
            """リソース読み取りハンドラー"""
            if uri.startswith("promethica://protein/"):
                accession = uri.split("/")[-1]
                result = await self.comprehensive_protein_analysis({"accession": accession})
                return types.ReadResourceResult(
                    contents=[result.content[0]]
                )
            elif uri.startswith("promethica://sequence/"):
                accession = uri.split("/")[-1]
                result = await self.get_protein_sequence({"accession": accession})
                return types.ReadResourceResult(
                    contents=[result.content[0]]
                )
            elif uri.startswith("promethica://structure/"):
                pdb_id = uri.split("/")[-1]
                result = await self.get_pdb_structure_info({"pdb_id": pdb_id})
                return types.ReadResourceResult(
                    contents=[result.content[0]]
                )
            else:
                raise Exception(f"Unknown resource URI: {uri}")

async def main():
    """メイン関数"""
    async with PromeithicaServer() as server:
        server.setup_handlers()
        
        # STDIOを使用してMCPサーバーを実行
        async with stdio_server() as streams:
            await server.server.run(*streams)

if __name__ == "__main__":
    asyncio.run(main())
