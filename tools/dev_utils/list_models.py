# encoding: utf-8
import requests
import json
import os

API_KEY = os.getenv("VOLC_API_KEY")
if not API_KEY:
    raise SystemExit("Set VOLC_API_KEY before running this script.")
BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

print("=== 获取可用模型列表 ===\n")

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

try:
    response = requests.get(f"{BASE_URL}/models", headers=headers, timeout=30)
    
    print(f"状态码: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        models = data.get("data", [])
        
        print(f"\n✅ 成功获取到 {len(models)} 个模型:\n")
        print("=" * 80)
        print(f"{'模型ID':<40} {'所有者':<15}")
        print("=" * 80)
        
        for model in models:
            model_id = model.get("id", "")
            owned_by = model.get("owned_by", "")
            
            # 标记可能是视觉模型的
            vlm_tags = ["vision", "vlm", "seed", "vision", "flash"]
            is_vlm = any(tag in model_id.lower() for tag in vlm_tags)
            
            tag = "🔹 视觉模型 " if is_vlm else "   "
            print(f"{tag}{model_id:<40} {owned_by:<15}")
        
        print("=" * 80)
        print("\n提示: 选择带 'seed', 'vision', 'vlm', 'flash' 的模型用于 UI-TARS")
        
    else:
        print(f"\n❌ 失败: {response.text}")
        
except Exception as e:
    print(f"\n❌ 错误: {e}")
    import traceback
    traceback.print_exc()
