import requests
import json

response = requests.post(
    'http://localhost:5555/get_coords',
    json={
        "name": "Байкал"
    }
)

print(f"Статус: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
else:
    print(f"Ошибка: {response.text[:200]}")