# Promethica

創薬・バイオ研究支援のためのタンパク質情報統合 MCP サーバー。UniProt・RCSB PDB・Rhea・Gene Ontology・Open Targets・ChEMBL の公開 API を 1 つの MCP インターフェースに束ね、タンパク質同定から疾患・薬剤・副作用の検索、反応ネットワークの gap 検出・ノード候補提示までを一貫して行う。

全体の構想とロードマップは [`promethica_to_be.md`](promethica_to_be.md) を参照。本 README は現時点の実装（**Phase 0〜3 完了、計 22 ツール**）を記述する。

## 特徴

- **stdio トランスポート**の FastMCP サーバー
- 外部 API ごとに個別のツールを用意（計 22 ツール）
- 接続プール付きの共有 `httpx.AsyncClient`
- 指数バックオフ + `Retry-After` 対応のリトライ（429 / 一過性 5xx / ネットワーク障害）。Cloudflare 由来の 520 / 522 / 524 も対象
- GET レスポンスの TTL キャッシュ（10 分、最大 256 件）
- Lucene クエリのエスケープによるインジェクション対策
- URL パスセグメントは全て `quote()` でエスケープ
- **モックデータのフォールバックは行わない**（LLM が偽データを本物と誤認しないため）

## データソース方針

反応・経路情報のソースは **Rhea + UniProt**（旧 MVP の Reactome から全面移行済み）。理由は 2 点。

1. **運用信頼性** — Reactome の公開ライブ API が長時間の全面障害を起こしたため、商用 MCP の基盤として依存し続けるのは危険と判断。Rhea も UniProt も EBI 基盤で信頼性が高い。
2. **ライセンスの純度** — Promethica の出力を用いた成果物が商用利用される可能性を踏まえ、ライセンス混在を避けて **Rhea・UniProt・ChEBI をいずれも CC-BY 4.0 で統一**。KEGG（商用有償）/ Pathway Commons（出典混在）は採用しない。

反応の単位は「Reactome パスウェイ」から「酵素 → Rhea 反応 → ChEBI 代謝物ネットワーク」に変わり、グラフ系ツールの入力もパスウェイ ID から **UniProt アクセッションの集合**に変更されている。

## インストール

Python 3.13 が必要。

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
- `search_similar_structures(pdb_id, assembly_id, operator, size)` — 3D 形状ベースの類似構造検索（研究者 B）
- `parse_local_pdb(file_path, enrich)` — 手元の `.pdb` ファイルを軽量パースし、DBREF の UniProt ID から Rhea 反応・GO 注釈を補完（研究者 D、新規依存なし）

### Rhea + UniProt（反応）

- `search_reactions(query, limit)` — 生化学反応の検索（代謝物名・EC 番号・ChEBI ID など）
- `get_protein_reactions(accession)` — 酵素が触媒する反応（UniProt 触媒活性注釈 → Rhea）

### Gene Ontology

- `search_go_terms(go_term, query)` — GO 用語検索

### 疾患・薬剤・副作用（Open Targets / ChEMBL）

- `get_disease_associations(gene, size)` — 遺伝子 → 関連疾患（Open Targets GraphQL、研究者 C）
- `get_drugs_for_target(gene, size)` — ターゲット → 既知薬・治療候補・作用機序（Open Targets GraphQL、研究者 C）
- `get_known_adverse_effects(drug, size)` — 既知薬の報告済み副作用（Open Targets / FAERS、研究者 E）
- `get_drug_details(chembl_id)` — 化合物の化学的詳細・作用機序・適応（ChEMBL REST）

### 反応ネットワーク（Phase 2 / 3）

- `build_reaction_network(accessions, max_reactions, exclude_currency, as_cytoscape)` — 酵素集合の触媒反応から**代謝物⇄反応の二部グラフ**を構築（基質 → 反応 → 生成物の有向エッジ）
- `detect_network_gaps(accessions, max_reactions, exclude_currency)` — グラフを弱連結成分に分解し、複数成分に分かれる箇所を **gap 候補**として報告。境界代謝物（供給境界／末端）も列挙
- `export_network_cytoscape(accessions, max_reactions, exclude_currency)` — Cytoscape.js 形式（elements）でエクスポートし、外部可視化ツールへ受け渡し
- `propose_gap_node_candidates(accessions, ...)` — gap 境界の代謝物を Rhea で辿り、置きうる中間ノード（反応と、それを触媒しうる実在酵素）を**根拠付きの仮説**として提示。実在性の断定・新規配列生成は一切行わない

### 統合

- `comprehensive_protein_analysis(accession)` — UniProt / Rhea 反応 / PDB を並列で取得

## ユースケース対応

| ユースケース | 入力 | 主なツール |
| --- | --- | --- |
| A: このタンパク質は何者？ | UniProt ID | `comprehensive_protein_analysis`, `get_protein_info`, `get_protein_reactions` |
| B: 似た構造は他にある？ | PDB ID | `search_similar_structures` |
| C: この遺伝子はどんな病気と関係？ | Gene Symbol | `get_disease_associations`, `get_drugs_for_target` |
| D: 手元の構造ファイルから全情報を | `.pdb` ファイル | `parse_local_pdb` |
| E: この既知物質の副作用は？ | ChEMBL ID / 薬剤名 | `get_known_adverse_effects`, `get_drug_details` |
| gap 検出・ノード候補 | UniProt アクセッションの集合 | `detect_network_gaps`, `propose_gap_node_candidates` |

## エラー応答

全ツールは JSON 文字列を返す。失敗時は以下の形式：

```json
{
  "error": "人間向けメッセージ",
  "_error": "HTTP 503",
  "_status": 503
}
```

## レート制限

各 API の公開レート制限を尊重するため、リクエスト失敗時は指数バックオフ（最大 3 回）。Open Targets 等は大量一括取得を避け、特定の Gene / Disease への絞り込みクエリに限定している。

## 既知の制約

- **Rhea 反応は双方向**。反応式の左右を便宜的に基質／生成物として扱っているだけで、生体内の実際の流れとは限らない。gap のブリッジ判定は方向非依存。
- **通貨代謝物**（H2O / ATP / NAD(+) 等、`_CURRENCY_CHEBI`）を既定で除外。除外セットは主要なもののみで網羅的ではなく、ネットワークが過剰に分断／結合する場合の調整点。
- 単一の多機能酵素は無関係な複数反応を持ちうるため、単一酵素入力でも複数成分に分かれるのは正常（gap の誤検出ではない）。

## 開発

```bash
python -m pytest test_promethica.py
```

> 注: Rhea + UniProt への移行でツール名・入力仕様が変わったため、移行後の回帰テスト整備は今後の課題。

## ロードマップ

- **Phase 0〜3: 完了** — DB 統合、疾患・薬剤・構造類似、反応ネットワークのグラフ化・gap 検出・ノード候補提示。
- **Phase 4: 予定** — Slack Bot 連携、Cytoscape グラフの可視化、商用 MCP ホスティング（認証・レート制限・課金）。
- **Phase 5: 長期構想** — AFaaS（AlphaFold as a Service）、in silico → in vitro/in vivo 橋渡し、合成生物学・遺伝子編集との連動。

詳細は [`promethica_to_be.md`](promethica_to_be.md) を参照。

## ライセンス

[LICENSE](LICENSE) を参照。
</content>
</invoke>
