# encoding: utf-8
import requests
import json
import os


def main():
    API_KEY = os.getenv("VOLC_API_KEY")
    if not API_KEY:
        raise SystemExit("Set VOLC_API_KEY before running this script.")
    BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"

    print("测试API连接...")
    print(f"API端点: {BASE_URL}")

    # 首先测试基础连接
    try:
        health_check_url = BASE_URL.replace("/contents/generations/tasks", "")
        print(f"\n检查基础端点: {health_check_url}")

        # 发送简单的请求头
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }

        print("\n=== 测试连接 ===")

        # 尝试列表模型
        models_url = f"{health_check_url}models"
        print(f"获取模型列表: {models_url}")

        response = requests.get(models_url, headers=headers, timeout=30)
        print(f"状态码: {response.status_code}")
        print(f"响应: {response.text}")

        if response.status_code in [200, 401]:
            print("\n✅ 连接成功！")
        else:
            print(f"\n❌ 连接失败: {response.status_code}")

    except Exception as e:
        print(f"\n❌ 错误: {e}")


if __name__ == "__main__":
    main()