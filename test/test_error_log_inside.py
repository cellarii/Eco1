import requests
import json

response = requests.post(
    'http://localhost:5555/log_error',
    json={
        "user_query": "тестовый запрос",
        "error_message": "Тестовая ошибка",
        "context": {"step": "test"},
        "additional_info": {"source": "test_script"}
    }
)

print(f"Статус: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
else:
    print(f"Ошибка: {response.text[:200]}")