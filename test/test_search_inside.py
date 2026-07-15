import requests
import json

response = requests.post(
    'http://localhost:5555/search',  # ← БЕЗ /search в конце
    json={
        "system_parameters": {
            "user_query": "лиственница",
            "limit": 5,
            "use_llm_answer": False,
            "debug": False
        },
        "search_parameters": {
            "modality_type": "Текст",
            "object": {
                "name_synonyms": {"ru": ["лиственница"]}
            }
        }
    }
)

print(f"Статус: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
else:
    print(f"Ошибка: {response.text[:200]}")