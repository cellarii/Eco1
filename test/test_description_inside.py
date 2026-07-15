import requests
import json

response = requests.post(
    'http://localhost:5555/object/description/',
    json={
        "object_name": "лиственница",
        "query": "Расскажи о лиственнице",
        "limit": 3,
        "use_gigachat_answer": False
    }
)

print(f"Статус: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
else:
    print(f"Ошибка: {response.text[:200]}")