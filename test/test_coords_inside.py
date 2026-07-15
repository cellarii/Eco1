import requests
import json

response = requests.post(
    'http://localhost:5555/coords_to_map',
    json={
        "latitude": 53.0,
        "longitude": 108.0,
        "radius_km": 30.0,
        "object_type": "biological_entity",
        "species_name": "лиственница"
    }
)

print(f"Статус: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
else:
    print(f"Ошибка: {response.text[:200]}")