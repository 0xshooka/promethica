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

- UniProt / PDB / Rhea / GO / ChEMBL / Open Targetsなどの生体分子DBに対応（いずれもライセンスが明快な公開API）
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
| データ統合 | 複数DB（UniProt／PDB／Rhea／GO／ChEMBL／Open Targets）のAPI連携／Cytoscape形式入出力／オントロジー統合 |
| 反応ネットワーク補完 | 酵素集合の触媒反応(Rhea)からグラフ構築／グラフ上の未接続（gap）検出（将来的に図示）／gap境界からのノード候補（反応・酵素）提示 |
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
- 出力：名前・機能・関連GO・触媒するRhea反応

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

> **データソース方針（重要）**: 反応・経路情報のソースを **Reactome から Rhea + UniProt に全面移行**した。理由は2点。(1) 運用信頼性——Reactomeの公開ライブAPIが長時間の全面障害を起こし、商用MCPの基盤として依存し続けるのは危険と判断。RheaもUniProtもEBI基盤で信頼性が高い。(2) **ライセンスの純度**——Promethicaが仮に無償でも、その出力を用いた成果物が商用利用される可能性を踏まえ、ライセンス混在（KEGG=商用有償、Pathway Commons=出典混在）を避け、**Rhea・UniProt・ChEBIともにCC-BY 4.0で統一**した。KEGG/Pathway Commonsは採用しない。反応の単位は「Reactomeパスウェイ」から「酵素→Rhea反応→ChEBI代謝物ネットワーク」に変わり、グラフ系ツールの入力もパスウェイIDから**UniProtアクセッションの集合**に変更した。

### 現在地（MVP / Phase 0）

すでに実装済みの基盤。単一のMCPサーバー（`promethica.py`, FastMCP/stdio）上で以下のツールが稼働。

- **UniProt**: `search_proteins` / `get_protein_info` / `get_protein_sequence` / `search_by_gene` / `get_primary_protein_for_gene` / `get_protein_features`
- **PDB**: `search_pdb_structures` / `get_pdb_structure_info`
- **Rhea + UniProt**: `search_reactions` / `get_protein_reactions`
- **GO**: `search_go_terms`
- **統合**: `comprehensive_protein_analysis`（UniProt＋Rhea反応＋PDBの並列統合）
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
| `parse_local_pdb(file, enrich)` | 軽量ヘッダーパーサ（新規依存なし） | D（手元の.pdb解析＋Rhea反応/GO補完） |

**実装メモ**:
- ChEMBL/Open Targetsは**いずれも認証不要の公開API**。既存の`make_api_request`にGraphQL POST用の薄いラッパ（`_graphql_request`）と遺伝子シンボル→Ensembl ID解決（`_resolve_ensembl_id`）を追加して対応。
- Open Targetsの現行v4スキーマでは、ターゲットの薬剤は `knownDrugs` ではなく **`drugAndClinicalCandidates`** に変更されている（イントロスペクションで確認して対応済み）。副作用は `drug.adverseEvents`（FAERSベース、`logLR` が `criticalValue` を超えると有意）。
- `parse_local_pdb` は方針どおり**構造解析ライブラリを導入せず**、HEADER/TITLE/COMPND/SOURCE/EXPDTA/REMARK 2/DBREF を軽量パースし、DBREFのUniProt IDから触媒反応(Rhea)・GO注釈を補完する。全原子解析が必要になった時点で`gemmi`導入を再検討。
- Open Targetsは大量一括取得を推奨しないため、MCPからは「特定Gene/Diseaseへの絞り込みクエリ」に限定してレート制限を回避する。

**完了条件**: ユースケースA〜Eすべてが単一入力で回答可能 → **達成**（実APIに対するスモークテストで全ツール動作確認済み）。

---

### Phase 2: 反応ネットワークのグラフ化とgap検出 ✅ **実装済み**

**目的**: 「未接続部分の検出」機能の実現（図示は将来対応）。

| 実装ツール | 内容 |
| --- | --- |
| `build_reaction_network(accessions, max_reactions, exclude_currency, as_cytoscape)` | 酵素(UniProt)集合の触媒反応(Rhea)から**代謝物⇄反応の二部グラフ**を構築（基質→反応→生成物の有向エッジ） |
| `detect_network_gaps(accessions, max_reactions, exclude_currency)` | グラフを**弱連結成分**に分解し、複数成分に分かれる箇所を**gap候補**として報告。境界代謝物（供給境界／末端）も列挙 |
| `export_network_cytoscape(accessions, max_reactions, exclude_currency)` | **Cytoscape.js形式（elements）でエクスポート**し、外部可視化ツールへ受け渡し可能に |

**実装メモ**:
- 入力は「反応でつなぎたい酵素の集合」（UniProtアクセッションのリスト）。各酵素の触媒反応をUniProtの CATALYTIC ACTIVITY 注釈から取得し、Rhea反応の基質/生成物（htmlequationの左右）で有向グラフを構築。
- **通貨代謝物（H2O/ATP/NAD(+)等）を既定で除外**（`exclude_currency`）。これらは多数の反応に共通し、無意味な連結や見かけ上のブリッジを生むため。
- 連結成分の検出は**新規依存を避けて自前のUnion-Find**（`_UnionFind`）で実装。networkx等は導入しない。
- 反応ごとに1 APIコールのため `max_reactions` で上限を設け（デフォルト40）、`asyncio.gather` で並列取得。分断が反応数上限や通貨除外による見かけ上のものでないか `_meta` で判別可能。
- 図示自体はこのフェーズでは範囲外（Cytoscape側 or 将来のPhase 4フロントで対応）。

**完了条件**: 酵素集合の反応グラフを取得し、未接続領域をリスト化して返せる → **達成**（解糖系酵素で実API動作確認済み）。

---

### Phase 3: ノード候補提示（仮説生成） ✅ **実装済み**

**目的**: 検出したgapに対し「存在しうるノードの候補」を**仮説として**提示（実在性の断定はしない）。

| 実装ツール | 内容 |
| --- | --- |
| `propose_gap_node_candidates(accessions, ...)` | gap境界の代謝物をキーにRheaで「その代謝物が関与する他の既知反応」を検索し、置きうる中間ノード（反応と、それを触媒しうる酵素）の候補を列挙。特に**異なる成分の境界代謝物を相互変換する反応**を「ブリッジ候補」として、候補酵素付きで提示 |

**実装メモ**:
- LLMによる配列生成は行わず、**Rheaの既知反応DBに基づくデータ駆動の候補提示**とした（境界ChEBIで `_rhea_reactions_by_chebi` を検索）。ブリッジ候補反応はUniProt(`query=rhea:ID`)で触媒酵素を逆引きし、**候補ノード＝実在する酵素**として提示。
- **Rhea反応は双方向**のため、ブリッジ判定は方向非依存（対辺に別成分の境界代謝物があれば連結できる）とした。当初は左右厳密一致で実装したが解糖系のgap検証で取りこぼしが判明し修正。
- 各候補には**根拠（どの境界代謝物どうしを結ぶか）を必ず併記**。出力に明示的な `disclaimer` を含め、**実在性の断定・新規配列生成は一切行わない**方針を明記。
- ネットワーク自身の反応は候補から除外。`_compute_network_gaps` をPhase 2の `detect_network_gaps` と共用してgap算出ロジックを一元化。
- あわせて `make_api_request` を**一過性5xx（500/502/503/504/520/522/524）でリトライ**するよう改善。

**完了条件**: gap入力に対し、根拠付きの候補ノードリストを仮説として返せる → **達成**。解糖系酵素4種（PGAMを意図的に除外）で検証し、gapのブリッジ反応 RHEA:15901（2-/3-ホスホグリセリン酸の相互変換）と、その候補酵素として**除外したPGAM1(P18669)を正しく提示**することを実APIで確認。

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

---

## 申し送り事項（ハンドオフ）

このセクションはセッション間の引き継ぎ用メモ。実装状況・未決事項・次の一手を集約する。

### 現在の到達点（2026-07時点）

- **Phase 0〜3 実装済み**（合計22ツール）。ユースケースA〜E＋反応ネットワークのgap検出・ノード候補提示まで動作。
- **Reactome → Rhea + UniProt へ全面移行済み**（上記「データソース方針」参照）。コード中のReactome APIコールは完全に除去。
- 全ツールを実API（UniProt / Rhea / PDB / GO / Open Targets / ChEMBL）に対するスモークテストで確認済み。

### ブランチ／PR状況

- 作業はフェーズごとのスタックブランチ: `feat/phase1-disease-drug-structure` → `feat/phase2-pathway-graph-gaps` → `feat/phase3-gap-node-candidates`。
- Rhea+UniProtリファクタは `feat/phase3-...` ブランチ上のコミット `eb668c8`。
- **push / PR作成はリポジトリ所有者が手動で実施**（この環境からは `gh` 未導入・SSH pushが publickey で不可）。**いずれのPRもmainには未マージ**。マージ順序に注意（phase1→2→3の順、またはmainへまとめてスカッシュ）。
- コミットのauthorは自動推定の `shooka@Gungnir.local`（`git config` の user.name/email 未設定）。必要なら設定を。

### 既知の制約・技術メモ（重要）

- **Rhea反応は双方向**。方向性はRheaの反応式の左右を便宜的に基質/生成物として扱っているだけで、生体内の実際の流れとは限らない。gapブリッジ判定は方向非依存にしてある。
- **通貨代謝物**（H2O/ATP/NAD(+)等、`_CURRENCY_CHEBI`）を既定で除外。除外セットは主要なもののみで**網羅的ではない**。ネットワークが過剰に分断/結合する場合はこのセットの調整が要点。
- 単一の多機能酵素は無関係な複数反応を持ちうる（例: GAPDHは解糖反応＋S-ニトロシル化反応）。この場合、単一酵素入力でも複数成分に分かれるのは**正常**（gapの誤検出ではない）。
- グラフ系ツールの入力は**UniProtアクセッションの集合**（旧: Reactomeパスウェイ ID）。「反応でつなぎたい酵素群」を渡す設計。
- 外部APIは一過性5xx（520/522/524等）を含めリトライ実装済み。ただし長時間の全面障害には無力——商用化時はデータダンプ取り込み/自前ホストを検討（下記）。

### 未決事項・次の一手の候補

- **自動テスト未整備**: リファクタでツール名・入力仕様が変わったため、Rhea+UniProt版の回帰テストが無い。まず整備推奨。
- **小改善**: `propose_gap_node_candidates` の候補酵素にEC番号を付与／`build_reaction_network` に遺伝子シンボル入力を許可（内部で `search_by_gene` 経由でaccession解決）／`_CURRENCY_CHEBI` の拡充。
- **Phase 4**（Slack連携・可視化・商用MCPホスティング）への着手。
- **運用信頼性の本命**: 商用化フェーズでは公開ライブAPI依存を減らし、Rhea/UniProt/ChEBIのデータダンプ取り込み or 自前ホストを検討（Reactome障害の教訓）。
- 検討だけして**不採用**にした選択肢: Pathway Commonsフォールバック（ライセンス混在のため不採用で確定）、KEGG（商用有償のため不採用）。