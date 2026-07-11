# Promethica

## Promethicaとは

**次世代AI創薬・バイオ研究支援統合プラットフォーム**

── MCPサーバーとLLM、構造予測エンジンを融合した創薬特化型マルチモーダル基盤 ──

---

## 1. コンセプト

Promethicaは、バイオインフォマティクス領域における**データベース統合・パスウェイのgap検出とノード候補提示・構造情報の統合・既知の作用機序／副作用の検索**までを一貫して行う**AI創薬支援システム**である。

---

## 2. 技術的特長

### MCPベースのAPI統合

- UniProt / PDB / Reactome / GOなどの生体分子DBに対応
- ユーザーの1入力で複数の関数をカスケード的に呼び出し、より自然で柔軟かつ正確な質問が可能

### パスウェイ補完・逆引き機能

- パスウェイ上の**グラフ構造における未接続部分（gap）の検出**（将来的にはネットワーク図として図示）
- gap近傍の既知反応・EC番号・GO term・モチーフ情報から、**「ここに存在しうるノードの候補」を提示**（LLM/モチーフ解析併用による仮説提示）
- **既知物質（既知薬・代謝物）に対する、既知の副作用・関連代謝系の検索・列挙**

### LLM統合型QAインターフェース

- 自然言語での問い合わせから即座に構造やパスウェイ、欠損補完を実行
- Slackなどのチャット環境にBotとして導入可能

---

## 3. 主な機能一覧

| 機能領域 | 内容 |
| --- | --- |
| データ統合 | 複数DB（UniProt／PDB／Reactome／GO／ChEMBL／Open Targets）のAPI連携／Cytoscape形式入出力／オントロジー統合 |
| パスウェイ補完 | グラフ上の未接続（gap）検出（将来的に図示）／gap近傍情報からのノード候補提示／GO term・モチーフ・相互作用に基づく仮説提示 |
| 副作用・作用機序の検索 | 既知物質（既知薬・代謝物）→関連ターゲット・作用パスウェイの検索／既知の副作用・関連代謝系の列挙（ChEMBL／Open Targets連携） |
| チャット連携 | Slackなどから自然言語によるコマンド／結果ビジュアライズ |

---

## 4. 対象ユーザー・市場

- 製薬企業（新規創薬ターゲット探索、副作用予測）
- 大学・研究機関（分子メカニズム解析、システム生物学）
- バイオベンチャー（仮説立案と検証の高速化）
- 医療AI開発企業（統合知識基盤としての活用）

---

## 5. 商業展開

- PromethicaとしてサブスクでコールできるMCPサーバーを構想中
- AlphaFold2搭載AMIをAWS Marketplaceにて提供予定（AFaaS構想）

## 6. 今後の展望

---

- 実験・臨床情報との連携を加えることで、**in silicoからin vitro/in vivoへの橋渡し**を担う
- クラウドサービスを使用することにより、国内外ラボへの導入を加速
- 将来的には**合成生物学・遺伝子編集との連動**を視野に入れ、AI×バイオの未来基盤を担う存在に

## Promethicaのユースケース（簡潔に）

### 研究者A「この新規タンパク質、何者？」

- 入力：UniProt ID
- 出力：名前・機能・関連GO・関与するReactomeパスウェイ

### 研究者B「この構造と似てるやつ他にある？」

- 入力：PDB ID
- 出力：構造的に近い既知タンパク質（RMSDベース）、生物種、結合リガンド情報

### 研究者C「この遺伝子、どんな病気と関係してる？」

- 入力：Gene Symbol
- 出力：関連疾患（Open Targets の target–disease 関連、UniProt disease 注釈）＋治療ターゲット候補の薬剤リスト（ChEMBL／Open Targets の known drugs・作用機序）
- 補足：薬剤リストは公開APIで取得可能。
    - **ChEMBL REST API**（`https://www.ebi.ac.uk/chembl/api/data/`、認証不要、JSON対応）— 医薬品・ターゲット・活性・作用機序・適応
    - **Open Targets Platform GraphQL API**（`https://api.platform.opentargets.org/api/v4/graphql`、認証不要）— target–disease 関連、known drugs、ファーマコビジランス（副作用）情報

### 研究者D「手元にある構造ファイル.pdbから全情報を引きたい」

- 入力：.pdbファイル名 or ID
- 出力：分類・構造概要・関連パスウェイ・GO注釈

### 研究者E「この既知物質、どんな副作用が報告されている？」

- 入力：既知薬・化合物のID（ChEMBL ID など）または名称
- 出力：関係する代謝系の列挙、既知の副作用（ChEMBL／Open Targets のファーマコビジランス情報）の検索・列挙
- 注記：DBに未登録の新規分子に対する de novo な副作用「予測・シミュレーション」は対象外。あくまで既知情報の検索・統合にとどめる。

### AI・研究者双方

- 「このパスウェイ、どうしてこの2つが繋がってないの？間に何かあるのでは？」（グラフ上の未接続部分を検出して図示）
- 「ここに仮のノードを置くと機能的に補完されそう。候補として何が考えられる？」（gap近傍情報からノード候補を提示。実在性の断定は行わず、あくまで仮説提示）
- 「相互作用ネットワークの中で、未記載タンパクが入りうる空白は？」（未接続領域の検出）

---

## ロードマップ

TO-BE実現に向けた段階的な開発計画。各フェーズは前フェーズの成果物の上に積み上がる構成とし、フェーズ完了ごとに単体で価値を提供できる状態を目指す。

### 現在地（MVP / Phase 0）

すでに実装済みの基盤。単一のMCPサーバー（`promethica.py`, FastMCP/stdio）上で以下のツールが稼働。

- **UniProt**: `search_proteins` / `get_protein_info` / `get_protein_sequence` / `search_by_gene` / `get_primary_protein_for_gene` / `get_protein_features`
- **PDB**: `search_pdb_structures` / `get_pdb_structure_info`
- **Reactome**: `search_pathways` / `get_protein_pathways`
- **GO**: `search_go_terms`
- **統合**: `comprehensive_protein_analysis`（UniProt＋Reactome＋PDBの並列統合）
- **横断基盤**: 共通HTTPクライアント（接続プール・リトライ・指数バックオフ）、TTLキャッシュ、レスポンスのフィールドフィルタ

これによりユースケースA（タンパク質同定）は概ね充足。

---

### Phase 1: データソース拡充 — 疾患・薬剤・構造類似 ✅ **実装済み**

**目的**: ユースケースB・C・D・Eを既存の検索・統合パターンで満たす。

| 実装ツール | 対応DB | 対応ユースケース |
| --- | --- | --- |
| `get_disease_associations(gene, size)` | Open Targets GraphQL | C（遺伝子→疾患関連） |
| `get_drugs_for_target(gene, size)` | Open Targets GraphQL | C（治療ターゲット→薬剤リスト・作用機序） |
| `get_known_adverse_effects(drug, size)` | Open Targets / FAERS | E（既知副作用の検索・列挙。ChEMBL ID/薬剤名を受付） |
| `get_drug_details(chembl_id)` | ChEMBL REST | 化合物の化学的詳細（構造・作用機序・適応） |
| `search_similar_structures(pdb_id, ...)` | RCSB structure similarity API | B（3D形状ベース類似構造） |
| `parse_local_pdb(file, enrich)` | 軽量ヘッダーパーサ（新規依存なし） | D（手元の.pdb解析＋Reactome/GO補完） |

**実装メモ**:
- ChEMBL/Open Targetsは**いずれも認証不要の公開API**。既存の`make_api_request`にGraphQL POST用の薄いラッパ（`_graphql_request`）と遺伝子シンボル→Ensembl ID解決（`_resolve_ensembl_id`）を追加して対応。
- Open Targetsの現行v4スキーマでは、ターゲットの薬剤は `knownDrugs` ではなく **`drugAndClinicalCandidates`** に変更されている（イントロスペクションで確認して対応済み）。副作用は `drug.adverseEvents`（FAERSベース、`logLR` が `criticalValue` を超えると有意）。
- `parse_local_pdb` は方針どおり**構造解析ライブラリを導入せず**、HEADER/TITLE/COMPND/SOURCE/EXPDTA/REMARK 2/DBREF を軽量パースし、DBREFのUniProt IDから関連パスウェイ・GO注釈を補完する。全原子解析が必要になった時点で`gemmi`導入を再検討。
- Open Targetsは大量一括取得を推奨しないため、MCPからは「特定Gene/Diseaseへの絞り込みクエリ」に限定してレート制限を回避する。

**完了条件**: ユースケースA〜Eすべてが単一入力で回答可能 → **達成**（実APIに対するスモークテストで全ツール動作確認済み）。

---

### Phase 2: パスウェイのグラフ化とgap検出

**目的**: 「未接続部分の検出」機能の実現（図示は将来対応）。

- Reactome/相互作用データからパスウェイを**グラフ構造（ノード＝分子、エッジ＝反応/相互作用）として構築**するモジュールを追加。
- **未接続（gap）検出ロジック**: 期待される連結が欠けている箇所をグラフアルゴリズムで抽出（例: 到達不能ノード、分断コンポーネント間の隙間）。
- **Cytoscape形式（JSON）での入出力**に対応し、外部可視化ツールへ受け渡せるようにする。
- 図示自体はこのフェーズでは範囲外（Cytoscape側 or 将来のPhase 4フロントで対応）。

**新規ツール（案）**: `build_pathway_graph(pathway_id)` / `detect_pathway_gaps(graph)` / `export_cytoscape(graph)`

**完了条件**: 任意パスウェイのグラフを取得し、未接続領域をリスト化して返せる。

---

### Phase 3: ノード候補提示（仮説生成）

**目的**: 検出したgapに対し「存在しうるノードの候補」を**仮説として**提示（実在性の断定はしない）。

- gap近傍のノードから**EC番号・GO term・反応の入出力メタボライト・配列モチーフ**などのコンテキストを収集。
- そのコンテキストをLLM＋モチーフ/オントロジー解析に渡し、**「この隙間を埋めうる機能（例: ◯◯活性を持つ酵素）」の候補を列挙**。
- 出力は必ず「候補・仮説」として提示し、確度の根拠（どのコンテキストから導いたか）を併記。**新規配列そのものの生成・実在主張は行わない。**

**新規ツール（案）**: `propose_gap_node_candidates(gap_context)`

**完了条件**: gap入力に対し、根拠付きの候補ノードリストを仮説として返せる。

---

### Phase 4: インターフェース・提供形態

**目的**: 研究者が日常的に使える形へ。

- **Slack Bot連携**: 自然言語コマンド → MCPツール呼び出し → 結果整形・可視化リンク返却。
- **結果ビジュアライズ**: Cytoscape形式グラフのレンダリング（Web or Slack添付）。
- **商用MCPサーバー**: サブスクリプションでコール可能なホスティング形態の整備（認証・レート制限・課金）。

---

### Phase 5: 拡張構想（長期）

- **AFaaS（AlphaFold as a Service）**: AlphaFold2搭載AMIをAWS Marketplaceで提供。※MCP本体とは責務の異なる別プロダクトとして分離して設計・提供する。
- **in silico → in vitro/in vivo 橋渡し**: 実験・臨床情報との連携。
- **合成生物学・遺伝子編集との連動**: AI×バイオ基盤としての発展。

---

### 依存関係の要約

```
Phase 0 (MVP: DB統合) ──┬─> Phase 1 (疾患・薬剤・構造類似)
                        │
                        └─> Phase 2 (グラフ化・gap検出) ──> Phase 3 (ノード候補提示)

Phase 1〜3 ──> Phase 4 (Slack/可視化/商用化) ──> Phase 5 (AFaaS・実験連携・長期構想)
```

Phase 1とPhase 2は独立して着手可能。Phase 3はPhase 2に、Phase 4は主要機能（1〜3）の成熟に依存する。