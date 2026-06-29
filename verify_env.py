import os
# 検証用スクリプト: API_KEYが正しく読み込めるか確認する

def verify():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        print(f"API Key found: {key[:4]}****")
    else:
        print("API Key NOT FOUND. Please set GEMINI_API_KEY in your environment.")

if __name__ == "__main__":
    verify()