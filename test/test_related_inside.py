import requests
import json

response = requests.post(
    'http://localhost:5555/objects/related',
    json={
        "object_ids": [5339],
        "relation_type": "promo"
    }
)

print(f"Статус: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
else:
    print(f"Ошибка: {response.text[:200]}")