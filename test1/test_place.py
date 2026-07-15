import requests
import json

response = requests.post(
    'http://localhost:5555/search/place/objects',  # ← прямой доступ к Flask
    json={
        "place_name": "Байкал",
        "subtypes": ["Достопримечательности"],
        "limit": 5
    }
)

# Проверяем статус
print(f"Статус: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    # ensure_ascii=False — чтобы русский текст отображался нормально
    print(json.dumps(data, indent=2, ensure_ascii=False))
else:
    print(f"Ошибка: {response.text[:200]}")