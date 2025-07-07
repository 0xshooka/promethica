#!/usr/bin/env python3
"""
Reactome 公式AnalysisService APIのテスト
"""

import asyncio
import sys
import os
import json

# パスを追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TEST_ENDPOINTS: [str] = [
    "https://reactome.org/ContentService/data/species/main",
    "https://reactome.org/ContentService/data/database/info",
    "https://reactome.org/ContentService/data/pathways/top/9606",  # Human
]

async def test_official_reactome() -> None:
    """公式Reactome AnalysisService APIのテスト"""
    
    print("🔍 Reactome 公式AnalysisService テスト開始...")
    
    try:
        import promethica
        
        # 1. グルコース関連パスウェイ検索（AnalysisService使用）
        print("\n📡 グルコース関連パスウェイ検索（AnalysisService）...")
        result1 = await promethica.search_pathways("glucose")
        print("✅ グルコース検索結果:")
        try:
            data1 = json.loads(result1)
            if "pathways" in data1:
                pathways = data1["pathways"]
                print(f"   見つかったパスウェイ数: {len(pathways)}")
                for pathway in pathways[:3]:
                    print(f"   - {pathway.get('name', 'Unknown')} (p-value: {pathway.get('p_value', 'N/A')})")
            if "method" in data1:
                print(f"   使用方法: {data1['method']}")
        except:
            print(f"   結果: {result1[:300]}...")
        
        # 2. 特定タンパク質のパスウェイ分析（P53）
        print("\n📡 P53タンパク質パスウェイ分析...")
        result2 = await promethica.get_protein_pathways("P04637")
        print("✅ P53パスウェイ結果:")
        try:
            data2 = json.loads(result2)
            if "pathways" in data2:
                pathways = data2["pathways"]
                print(f"   関連パスウェイ数: {len(pathways)}")
                for pathway in pathways[:3]:
                    if isinstance(pathway, dict):
                        print(f"   - {pathway.get('name', pathway.get('text', 'Unknown'))}")
            if "analysis_token" in data2:
                print(f"   分析トークン: {data2['analysis_token'][:20]}...")
            if "method" in data2:
                print(f"   使用方法: {data2['method']}")
        except:
            print(f"   結果: {result2[:300]}...")
        
        # 3. インスリン関連検索
        print("\n📡 インスリン関連パスウェイ検索...")
        result3 = await promethica.search_pathways("insulin")
        print("✅ インスリン検索結果:")
        try:
            data3 = json.loads(result3)
            if "pathways" in data3:
                print(f"   見つかったパスウェイ数: {len(data3['pathways'])}")
            elif "matching_pathways" in data3:
                print(f"   マッチしたパスウェイ数: {data3.get('total_found', 0)}")
        except:
            print(f"   結果: {result3[:200]}...")
        
        # 4. 直接AnalysisService テスト
        print("\n📡 直接AnalysisService テスト...")
        import httpx
        async with httpx.AsyncClient() as client:
            try:
                # テスト用のUniProt IDでPOSTリクエスト
                response = await client.post(
                    "https://reactome.org/AnalysisService/identifiers",
                    params={
                        "interactors": "false",
                        "pageSize": "5",
                        "page": "1"
                    },
                    data="P04637",  # P53
                    headers={"Content-Type": "text/plain"}
                )
                
                print(f"   AnalysisService直接テスト: {response.status_code}")
                if response.status_code == 200:
                    direct_result = response.json()
                    if "pathways" in direct_result:
                        print(f"   直接取得パスウェイ数: {len(direct_result['pathways'])}")
                    if "summary" in direct_result:
                        print(f"   分析サマリー: 見つかった要素数 {direct_result['summary'].get('found', 0)}")
                else:
                    print(f"   エラーレスポンス: {response.text[:200]}...")
                    
            except Exception as e:
                print(f"   直接テストエラー: {e}")
    
    except Exception as e:
        print(f"❌ テストエラー: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n🎉 公式API テスト完了!")

    async with httpx.AsyncClient(timeout=30.0) as client:

        # 1. Reactome のステータス確認
        print("\n📡 Reactome サーバーステータス確認...")
        try:
            resp_main = await client.get("https://reactome.org/")
            resp_cs   = await client.get("https://reactome.org/ContentService/")
            resp_api  = await client.get("https://reactome.org/ContentService/data/")

            print(f"Reactome メインサイト        : {resp_main.status_code}")
            print(f"Reactome ContentService     : {resp_cs.status_code}")
            print(f"ContentService /data/ エンド: {resp_api.status_code}")
        except Exception:
            print("❌ 直接 HTTP チェックで例外発生")
            traceback.print_exc()

        # 2. 既知 API エンドポイントを順次テスト
        print("\n📡 既知 API エンドポイントテスト...")
        for url in TEST_ENDPOINTS:
            try:
                r = await client.get(url)
                ok = "✅" if r.status_code == 200 else "⚠️"
                print(f"{ok} {url}: {r.status_code}")
                if r.status_code == 200:
                    print(f"   サンプル: {r.text[:100]}...")
            except Exception:
                print(f"❌ {url} で例外発生")
                traceback.print_exc()

    # 3. Promethica の関数テスト
    print("\n📡 Promethica 関数テスト...")
    try:
        import promethica

        print("🧪 パスウェイ検索テスト...")
        res1 = await promethica.search_pathways("glucose")
        print(f"パスウェイ結果 (先頭): {str(res1)[:300]}")

        print("\n🧪 タンパク質パスウェイテスト...")
        res2 = await promethica.get_protein_pathways("P04637")
        print(f"タンパク質パスウェイ結果 (先頭): {str(res2)[:300]}")
    except Exception:
        print("❌ Promethica 関数呼び出しで例外発生")
        traceback.print_exc()

    # 4. 代替 API 案
    print("\n💡 代替案:")
    print(" 1. UniProt cross-references からパスウェイ取得")
    print(" 2. KEGG REST API（商用利用は要確認）")
    print(" 3. WikiPathways REST API")
    print(" 4. モックデータでデモを継続")

    print("\n🎉 デバッグ完了!")

if __name__ == "__main__":
    asyncio.run(test_official_reactome())
