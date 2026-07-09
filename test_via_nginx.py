#!/usr/bin/env python3
"""
Тестирование через Nginx (порт 80)
Запуск: python test_via_nginx.py
"""

import requests
import json
import time
from typing import Dict, Any
from datetime import datetime

class NginxTester:
    def __init__(self):
        # Все запросы идут через Nginx на порт 80
        self.base_url = "http://localhost"
        
    def test_backend(self, endpoint: str, payload: Dict) -> Dict:
        """Тестирует backend через Nginx"""
        url = f"{self.base_url}/search{endpoint}"
        if not endpoint.startswith('/'):
            url = f"{self.base_url}/search/{endpoint}"
        
        print(f"\n📡 Запрос к backend через Nginx: {url}")
        response = requests.post(url, json=payload, timeout=30)
        return response.json()
    
    def test_core_api(self, payload: Dict) -> Dict:
        """Тестирует core-api через Nginx"""
        url = f"{self.base_url}/core-api/search_pipeline"
        print(f"\n📡 Запрос к core-api через Nginx: {url}")
        response = requests.post(url, json=payload, timeout=30)
        return response.json()
    
    def run_tests(self):
        """Запускает набор тестов"""
        
        print("="*60)
        print("🧪 ТЕСТИРОВАНИЕ ЧЕРЕЗ NGINX")
        print("="*60)
        
        # Тест 1: Поиск через backend
        print("\n📝 Тест 1: Поиск 'лиственница' через backend")
        result = self.test_backend(
            endpoint="/search",
            payload={
                "system_parameters": {
                    "user_query": "лиственница",
                    "limit": 5
                },
                "search_parameters": {
                    "modality_type": "Текст",
                    "object": {
                        "name_synonyms": {"ru_names": ["лиственница"]}
                    }
                }
            }
        )
        print(f"✅ Результат: найдено {len(result.get('result', []))} объектов")
        if result.get('result'):
            print(f"   Первый: {result['result'][0].get('name', 'N/A')}")
        
        # Тест 2: Полный pipeline через core-api
        print("\n📝 Тест 2: Полный pipeline 'Покажи фото лиственницы'")
        result = self.test_core_api({
            "query": "Покажи фото лиственницы",
            "user_id": "test_user",
            "promo_enabled": False
        })
        print(f"✅ Результат: {result.get('type', 'N/A')}")
        if result.get('content'):
            content = result['content']
            if isinstance(content, str):
                print(f"   Содержание: {content[:100]}...")
        
        # Тест 3: Поиск через backend (гео)
        print("\n📝 Тест 3: Геопоиск 'где находится омуль'")
        result = self.test_backend(
            endpoint="/search",
            payload={
                "system_parameters": {
                    "user_query": "где находится омуль",
                    "limit": 5
                },
                "search_parameters": {
                    "modality_type": "Геоданные",
                    "object": {
                        "name_synonyms": {"ru_names": ["омуль"]}
                    }
                }
            }
        )
        print(f"✅ Результат: {result.get('type', 'N/A')}")
        
        # Тест 4: Поиск изображений
        print("\n📝 Тест 4: Поиск изображений 'лиственница зимой'")
        result = self.test_backend(
            endpoint="/search",
            payload={
                "system_parameters": {
                    "user_query": "лиственница зимой",
                    "limit": 5
                },
                "search_parameters": {
                    "modality_type": "Изображение",
                    "object": {
                        "name_synonyms": {"ru_names": ["лиственница"]}
                    },
                    "resource": {
                        "features": {"Время года": "Зима"}
                    }
                }
            }
        )
        print(f"✅ Результат: найдено {len(result.get('result', []))} изображений")
        
        # Тест 5: Места на Байкале
        print("\n📝 Тест 5: Места на Байкале")
        result = self.test_backend(
            endpoint="/place/objects",
            payload={
                "place_name": "Байкал",
                "subtypes": ["Достопримечательности"],
                "buffer_radius_km": 10.0,
                "limit": 5
            }
        )
        print(f"✅ Результат: найдено {len(result.get('objects', []))} объектов")
        
        print("\n" + "="*60)
        print("✅ Тестирование завершено!")
        print("="*60)

# Интерактивный режим
class InteractiveTester:
    def __init__(self):
        self.base_url = "http://localhost"
        self.session = requests.Session()
    
    def send_query(self, query: str):
        """Отправляет запрос через core-api"""
        print(f"\n📝 Запрос: {query}")
        print("-"*40)
        
        try:
            response = self.session.post(
                f"{self.base_url}/core-api/search_pipeline",
                json={
                    "query": query,
                    "user_id": "interactive",
                    "promo_enabled": False
                },
                timeout=30
            )
            data = response.json()
            
            print(f"📊 Тип ответа: {data.get('type', 'N/A')}")
            print(f"📄 Содержание:")
            
            content = data.get('content')
            if isinstance(content, str):
                print(content[:500] + ("..." if len(content) > 500 else ""))
            elif isinstance(content, list):
                print(f"   Найдено объектов: {len(content)}")
                if content:
                    print(f"   Первый: {content[0].get('name', content[0].get('title', 'N/A'))}")
            elif isinstance(content, dict):
                print(json.dumps(content, indent=2, ensure_ascii=False)[:500])
            else:
                print(content)
            
            if data.get('buttons'):
                print(f"🔘 Кнопки: {[b.get('text', '') for b in data['buttons']]}")
            
            if data.get('debug_info'):
                print(f"🐛 Debug: {data['debug_info'][:200]}...")
                
        except Exception as e:
            print(f"❌ Ошибка: {e}")
    
    def run(self):
        """Интерактивный режим"""
        print("\n" + "="*60)
        print("💬 Интерактивный режим тестирования")
        print("Введите запрос или команду:")
        print("  /exit - выход")
        print("  /help - помощь")
        print("="*60)
        
        while True:
            try:
                query = input("\n❓ Введите запрос: ").strip()
                
                if not query:
                    continue
                
                if query.lower() == '/exit':
                    print("👋 До свидания!")
                    break
                
                if query.lower() == '/help':
                    print("\n📖 Команды:")
                    print("  /exit - выход")
                    print("  /help - эта справка")
                    print("  Любой другой текст - отправка в core-api")
                    continue
                
                self.send_query(query)
                
            except KeyboardInterrupt:
                print("\n👋 До свидания!")
                break
            except Exception as e:
                print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        tester = InteractiveTester()
        tester.run()
    else:
        tester = NginxTester()
        tester.run_tests()