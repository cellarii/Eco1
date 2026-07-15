import requests
import json

response = requests.post(
    'http://localhost/search/search',
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

data = response.json()
print(json.dumps(data, indent=2, ensure_ascii=False))