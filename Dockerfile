FROM python:3.10-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt uvicorn[standard] supervisor

# Копируем весь код
COPY . .

# Копируем конфиг supervisor
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Запускаем supervisor (он запустит Flask и FastAPI)
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]