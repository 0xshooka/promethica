#!/usr/bin/env python3
"""
Promethica テストデバッグスクリプト
"""

import asyncio
import sys
import os

# パスを追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def debug_tests():
    """デバッグ用テスト実行"""
    
    print("🔍 Promethica デバッグテスト開始...")
    
    # 1. インポートテスト
    try:
        from promethica import mcp, search_proteins, make_api_request
        print("✅ インポート成功")
    except Exception as e:
        print(f"❌ インポートエラー: {e}")
        return
    
    # 2. FastMCP構造確認
    print(f"\n📋 FastMCP オブジェクト属性:")
    mcp_attrs = [attr for attr in dir(mcp) if not attr.startswith('_')]
    for attr in mcp_attrs[:10]:  # 最初の10個のみ表示
        print(f"   - {attr}")
    
    # 3. ツール登録確認
    tools_found = []
    if hasattr(mcp, '_tools'):
        tools_found = list(mcp._tools.keys())
        print(f"✅ _tools 属性でツール発見: {len(tools_found)}個")
    elif hasattr(mcp, 'tools'):
        tools_found = list(mcp.tools.keys())
        print(f"✅ tools 属性でツール発見: {len(tools_found)}個")
    else:
        print("⚠️  ツール属性が見つかりません")
    
    if tools_found:
        print(f"📝 登録済みツール: {tools_found[:5]}...")  # 最初の5個のみ表示
    
    # 4. 関数シグネチャ確認
    try:
        import inspect
        sig = inspect.signature(search_proteins)
        print(f"✅ search_proteins シグネチャ: {sig}")
    except Exception as e:
        print(f"❌ シグネチャ確認エラー: {e}")
    
    # 5. 実際の関数テスト（詳細）
    try:
        # 空のクエリでエラーハンドリングテスト
        result = await search_proteins("", size=10)
        print(f"✅ エラーハンドリングテスト結果: {result[:100]}...")
        
        # 有効なクエリのテスト（モック無し、実際にはエラーになる）
        print("📡 実際のAPI呼び出しテスト（エラー予想）...")
        try:
            result2 = await search_proteins("insulin", size=1)
            print(f"⚠️  予想外の成功: {result2[:50]}...")
        except Exception as api_error:
            print(f"✅ 予想通りのAPIエラー: {str(api_error)[:100]}...")
            
    except Exception as e:
        print(f"❌ 関数実行エラー: {e}")
    
    # 6. search_proteinsの内部構造確認
    try:
        from promethica import search_proteins
        import inspect
        
        # 関数のソースコード確認（一部）
        try:
            source = inspect.getsource(search_proteins)
            print(f"📝 search_proteins関数の最初の数行:")
            lines = source.split('\n')[:5]
            for line in lines:
                print(f"     {line}")
        except:
            print("⚠️  ソースコード取得不可")
            
    except Exception as e:
        print(f"❌ 関数分析エラー: {e}")
    
    # 7. モックテスト
    print(f"\n🎭 モックテスト:")
    try:
        from unittest.mock import patch, MagicMock
        import json
        
        mock_data = {"results": [{"primaryAccession": "TEST123"}]}
        
        with patch('promethica.make_api_request', return_value=mock_data):
            result = await search_proteins("test", size=5)
            result_obj = json.loads(result)
            print(f"✅ モックテスト成功: {len(result_obj.get('proteins', []))}件の結果")
            
    except Exception as e:
        print(f"❌ モックテストエラー: {e}")
    
    print("\n🎉 デバッグテスト完了!")

if __name__ == "__main__":
    asyncio.run(debug_tests())
