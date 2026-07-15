import requests
import json

# Пробуем разные типы
types_to_try = [
    "Геологические",
    "Географический объект",
    "Природный объект",
    "Культурное наследие",
    "Достопримечательности"
]

for t in types_to_try:
    print(f"\n🔍 Пробуем тип: {t}")
    response = requests.post(
        'http://localhost:5555/find_off_near_attractions',
        json={
            "area_name": "Байкал",
            "attraction_types": [t],
            "limit": 5
        }
    )
    
    if response.status_code == 200:
        data = response.json()
        if data.get('status') == 'no_attractions':
            print(f"   ❌ Не найдено: {data.get('message')}")
        else:
            print(f"   ✅ НАШЛО! {data.get('status')}")
            print(f"   {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
    else:
        print(f"   ❌ Ошибка: {response.status_code}")