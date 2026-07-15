import requests
import json

response = requests.get(
    'http://localhost:5555/test_faiss_search',
    params={
        "query": "лиственница",
        "k": 3,
        "similarity_threshold": 0.5
    }
)

print(f"Статус: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
else:
    print(f"Ошибка: {response.text[:200]}")