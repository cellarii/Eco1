#!/usr/bin/env python3
"""
Тестирование через Nginx с увеличенным таймаутом
Запуск: python test_working.py
"""

import requests
import json
import time

class WorkingTester:
    def __init__(self):
        self.base_url = "http://localhost"
        self.session = requests.Session()
    
    def test_core_api(self, query, user_id="test_user"):
        """Тестирует core-api через Nginx с таймаутом 60 секунд"""
        url = f"{self.base_url}/core-api/search_pipeline"
        
        payload = {
            "query": query,
            "user_id": user_id,
            "promo_enabled": False
        }
        
        print(f"\n📝 Запрос: {query}")
        print(f"📍 URL: {url}")
        print("-" * 40)
        
        try:
            start = time.time()
            # ⚠️ Увеличил таймаут до 120 секунд
            response = self.session.post(url, json=payload, timeout=120)
            elapsed = time.time() - start
            
            print(f"✅ Статус: {response.status_code}")
            print(f"⏱️  Время: {elapsed:.2f}с")
            
            if response.status_code == 200:
                data = response.json()
                
                # Показываем результат
                print(f"\n📊 Результат:")
                
                if data.get('result'):
                    result = data['result']
                    if isinstance(result, dict):
                        if 'answer' in result:
                            print(f"   Ответ: {result['answer'][:300]}...")
                        if 'objects' in result:
                            print(f"   Объектов: {len(result['objects'])}")
                    elif isinstance(result, list):
                        print(f"   Найдено: {len(result)} объектов")
                        if result:
                            first = result[0]
                            if isinstance(first, dict):
                                print(f"   Первый: {first.get('name', first.get('title', 'N/A'))}")
                
                # Слоты
                if data.get('slots'):
                    slots = data['slots']
                    print(f"\n🔍 Слоты:")
                    print(f"   Тип: {slots.get('object_type', 'N/A')}")
                    print(f"   Синоним: {slots.get('synonym', 'N/A')}")
                    print(f"   Модальность: {slots.get('modality', 'N/A')}")
                
                # Proactive
                if data.get('proactive'):
                    proactive = data['proactive']
                    print(f"\n💡 Проактивные предложения:")
                    for key, value in proactive.items():
                        print(f"   {key}: {value}")
                
                # Тайминги
                if data.get('timing'):
                    timing = data['timing']
                    print(f"\n⏱️  Тайминги:")
                    print(f"   Классификация: {timing.get('classify_ms', 0)/1000:.2f}с")
                    print(f"   Поиск: {timing.get('search_ms', 0)/1000:.2f}с")
                    print(f"   Всего: {timing.get('total_ms', 0)/1000:.2f}с")
                
                return data
            else:
                print(f"❌ Ошибка: {response.text[:200]}")
                
        except requests.exceptions.Timeout:
            print("❌ Таймаут (сервер не отвечает больше 120 секунд)")
        except requests.exceptions.ConnectionError:
            print("❌ Ошибка соединения")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
        
        return None
    
    def run_tests(self):
        """Запускает набор тестов"""
        print("=" * 60)
        print("🧪 ТЕСТИРОВАНИЕ ЧЕРЕЗ NGINX")
        print("=" * 60)
        
        test_queries = [
            "лиственница",
            "покажи фото лиственницы",
            "где находится омуль",
            "что такое нерпа",
            "какие достопримечательности на байкале"
        ]
        
        for query in test_queries:
            self.test_core_api(query)
            print("\n" + "-" * 40)

# Интерактивный режим с таймаутом 120 секунд
class InteractiveTester:
    def __init__(self):
        self.base_url = "http://localhost"
        self.session = requests.Session()
    
    def send_query(self, query):
        url = f"{self.base_url}/core-api/search_pipeline"
        
        print(f"\n📝 Запрос: {query}")
        print("-" * 40)
        
        try:
            start = time.time()
            response = self.session.post(
                url,
                json={"query": query, "user_id": "interactive"},
                timeout=120  # ⬅️ Увеличил
            )
            elapsed = time.time() - start
            
            print(f"✅ Готово за {elapsed:.2f}с")
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('result'):
                    result = data['result']
                    if isinstance(result, dict) and 'answer' in result:
                        print(f"\n📄 Ответ:\n{result['answer']}")
                    elif isinstance(result, list):
                        print(f"\n📋 Найдено: {len(result)} объектов")
                        for i, obj in enumerate(result[:5]):
                            name = obj.get('name', obj.get('title', 'Без имени'))
                            print(f"   {i+1}. {name}")
                        if len(result) > 5:
                            print(f"   ... и еще {len(result) - 5}")
                else:
                    print(f"\n📄 Ответ:\n{json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
            else:
                print(f"❌ Ошибка: {response.status_code}")
                
        except requests.exceptions.Timeout:
            print("❌ Таймаут (запрос выполняется дольше 120 секунд)")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
    
    def run(self):
        print("\n" + "=" * 60)
        print("💬 ИНТЕРАКТИВНЫЙ РЕЖИМ (таймаут 120 секунд)")
        print("Введите запрос или команду:")
        print("  /exit - выход")
        print("  /help - помощь")
        print("=" * 60)
        
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
        tester = WorkingTester()
        tester.run_tests()