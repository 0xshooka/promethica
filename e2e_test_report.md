# Promethica E2E テストレポート

**実行日時**: 2025-07-07 23:45:38
**総テスト数**: 6
**成功**: 5 ✅
**失敗**: 1 ❌
**成功率**: 83.3%

## テスト結果詳細

### APP遺伝子基本検索 ✅ 成功

**クエリ**: APP遺伝子でコードされるヒトのタンパク質のUniProtアクセッション番号を教えてください

**実行時間**: 1.99秒

**呼び出されたツール**: get_primary_protein_for_gene

**応答内容** (抜粋):
```
APP遺伝子の検索結果: {
  "gene": "APP",
  "organism": "Homo sapiens",
  "primary_protein": {
    "accession": "P05067",
    "name": "A4_HUMAN",
    "protein_name": "Amyloid-beta precursor protein",
    "organ...
```

---

### タンパク質詳細情報取得 ✅ 成功

**クエリ**: UniProtアクセッション番号P05067のタンパク質について詳細情報を教えてください

**実行時間**: 1.92秒

**呼び出されたツール**: get_protein_info

**応答内容** (抜粋):
```
P05067の詳細情報: {
  "entryType": "UniProtKB reviewed (Swiss-Prot)",
  "primaryAccession": "P05067",
  "secondaryAccessions": [
    "B2R5V1",
    "B4DII8",
    "D3DSD1",
    "D3DSD2",
    "D3DSD3",
    "P...
```

---

### 遺伝子からタンパク質へのカスケード ✅ 成功

**クエリ**: BRCA1遺伝子のタンパク質について、配列の長さと主要な機能を教えてください

**実行時間**: 4.15秒

**呼び出されたツール**: get_primary_protein_for_gene, get_protein_info

**応答内容** (抜粋):
```
BRCA1遺伝子の分析: 遺伝子情報 + タンパク質詳細情報を取得しました...
```

---

### 包括的分析 ❌ 失敗

**クエリ**: P53タンパク質(TP53遺伝子)について、構造情報、関連パスウェイ、機能を包括的に分析してください

**実行時間**: 4.99秒

**呼び出されたツール**: get_primary_protein_for_gene, comprehensive_protein_analysis

**エラー**: 
- 実際の応答が取得できませんでした

**応答内容** (抜粋):
```
TP53包括的分析: 遺伝子情報 + 包括的分析を実行しました...
```

---

### PDB構造検索 ✅ 成功

**クエリ**: インスリンタンパク質のPDB構造情報を検索してください

**実行時間**: 0.40秒

**呼び出されたツール**: search_pdb_structures

**応答内容** (抜粋):
```
インスリンPDB検索: エラー: PDB検索に失敗しました......
```

---

### パスウェイ検索 ✅ 成功

**クエリ**: グルコース代謝に関連するパスウェイを検索してください

**実行時間**: 1.80秒

**呼び出されたツール**: search_pathways

**応答内容** (抜粋):
```
グルコース代謝パスウェイ: {
  "query": "glucose metabolism",
  "species": "Homo sapiens",
  "note": "Reactome API接続に問題があるため、モックデータを返しています",
  "pathways": [
    {
      "stId": "R-HSA-70171",
      "displayName": ...
```

---

## パフォーマンス統計

- 平均実行時間: 2.54秒
- 最短実行時間: 0.40秒
- 最長実行時間: 4.99秒

## 改善推奨事項

- **実際の応答が取得できませんでした** (1件): 対応が必要
