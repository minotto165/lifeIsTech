import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

# 新しいSDKを用いたAI解析モジュール
def get_ai_response(prompt):
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not api_key:
        return None
    
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        print(f"AI Analysis Error (skipped): {e}")
        return None
