import requests
import json

response = requests.post(
    'http://localhost:5555/search/place/objects',
    json={
        "place_name": "Байкал",
        "subtypes": ["Достопримечательности"],
        "limit": 5
    }
)

print(f"Статус: {response.status_code}")
print(f"Текст: {response.text[:200]}")

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))