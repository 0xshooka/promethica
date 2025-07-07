#!/usr/bin/env python3
"""
モックが正しく動作するかの確認スクリプト
"""

import asyncio
import json
import sys
import os

# パスを追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def test_mock_functionality():
    """モック機能のテスト"""
    
    print("🎭 モック動作確認開始...")
    
    try:
        import promethica
        print("✅ promethicaインポート成功")
        
        # 1. 元の関数の動作確認
        print("\n📡 元の関数でAPI呼び出しテスト...")
        try:
            original_result = await promethica.search_proteins("insulin", size=1)
            print(f"✅ 元の関数動作確認: {len(original_result)}文字の結果")
        except Exception as e:
            print(f"❌ 元の関数エラー: {e}")
        
        # 2. モック関数の作成と置き換え
        print("\n🔄 モック関数への置き換え...")
        original_make_api_request = promethica.make_api_request
        
        mock_response = {
            "results": [
                {
                    "primaryAccession": "MOCK123",
                    "uniProtkbId": "MOCK_HUMAN",
                    "entryType": "UniProtKB reviewed (Swiss-Prot)",
                    "proteinDescription": {
                        "recommendedName": {
                            "fullName": {"value": "Mock protein"}
                        }
                    },
                    "organism": {"scientificName": "Homo sapiens"},
                    "sequence": {"length": 100}
                }
            ]
        }
        
        async def mock_make_api_request(url, params=None, json_data=None):
            print(f"🎭 モック関数が呼ばれました: {url}")
            return mock_response
        
        # 関数を置き換え
        promethica.make_api_request = mock_make_api_request
        
        # 3. モック関数での動作確認
        print("\n🧪 モック関数でのテスト...")
        mock_result = await promethica.search_proteins("test_protein", size=1)
        mock_data = json.loads(mock_result)
        
        print(f"✅ モック結果確認:")
        print(f"   - クエリ: {mock_data.get('query')}")
        print(f"   - プロテイン数: {len(mock_data.get('proteins', []))}")
        if mock_data.get('proteins'):
            print(f"   - 最初のアクセッション: {mock_data['proteins'][0].get('accession')}")
        
        # 4. 元の関数を復元
        promethica.make_api_request = original_make_api_request
        print("🔄 元の関数を復元しました")
        
        # 5. 復元後の動作確認
        print("\n📡 復元後の動作確認...")
        try:
            restored_result = await promethica.search_proteins("insulin", size=1)
            print(f"✅ 復元後動作確認: {len(restored_result)}文字の結果")
        except Exception as e:
            print(f"❌ 復元後エラー: {e}")
        
        print("\n🎉 モック動作確認完了!")
        return True
        
    except Exception as e:
        print(f"❌ 全体的なエラー: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_mock_functionality())
    if success:
        print("\n✅ モック機能は正常に動作します！")
    else:
        print("\n❌ モック機能に問題があります。")
