# Promethica

創薬支援のためのタンパク質情報統合 MCP サーバー。UniProt・Reactome・RCSB PDB・Gene Ontology の 4 つの公開 API を 1 つの MCP インターフェースに束ねる。

## 特徴

- **stdio トランスポート**の FastMCP サーバー
- 外部 API ごとに個別のツールを用意（計 12 ツール）
- 接続プール付きの共有 `httpx.AsyncClient`
- 指数バックオフ + `Retry-After` 対応のリトライ（429 / 503 / ネットワーク障害）
- GET レスポンスの TTL キャッシュ（10 分）
- Lucene クエリのエスケープによるインジェクション対策
- URL パスセグメントは全て `quote()` でエスケープ

## インストール

Python 3.13+ が必要。

```bash
uv sync
# もしくは
pip install -e .
```

## 実行

```bash
python promethica.py
```

### Claude Desktop での設定例

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "promethica": {
      "command": "python",
      "args": ["/absolute/path/to/promethica.py"]
    }
  }
}
```

## 提供ツール

### UniProt
- `search_proteins(query, organism, size, format)` — キーワード検索
- `get_protein_info(accession, format)` — アクセッション詳細
- `get_protein_sequence(accession, format)` — アミノ酸配列
- `get_protein_features(accession)` — ドメイン・活性部位
- `search_by_gene(gene, organism, size)` — 遺伝子名検索
- `get_primary_protein_for_gene(gene, organism)` — レビュー済み代表エントリ

### RCSB PDB
- `search_pdb_structures(query, size)` — 構造検索
- `get_pdb_structure_info(pdb_id)` — PDB ID 詳細

### Reactome
- `search_pathways(query, species)` — パスウェイ検索
- `get_protein_pathways(accession)` — タンパク質のパスウェイ（AnalysisService、UniProt へフォールバック）

### Gene Ontology
- `search_go_terms(go_term, query)` — GO 用語検索

### 統合
- `comprehensive_protein_analysis(accession)` — UniProt / Reactome / PDB を並列で取得

## エラー応答

全ツールは JSON 文字列を返す。失敗時は以下の形式：

```json
{
  "error": "人間向けメッセージ",
  "_error": "HTTP 503",
  "_status": 503
}
```

モックデータのフォールバックは行わない（LLM が偽データを本物と誤認しないため）。

## レート制限

各 API の公開レート制限を尊重するため、リクエスト失敗時は指数バックオフ（最大 3 回）。大量アクセスは避けること。

## 開発

```bash
python -m pytest test_promethica.py
```
