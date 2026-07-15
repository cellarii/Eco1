import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

# --- БД ---
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "eco")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Fdf78yh0a4b!")

# --- Пути ---
MAPS_DIR = os.getenv("MAPS_DIR", str(BASE_DIR / "maps"))
DOMAIN = os.getenv("PUBLIC_BASE_URL", "http://localhost")
EMBEDDING_MODEL_PATH = str(BASE_DIR / "embedding_models" / os.getenv("EMBEDDING_MODEL", "bge-m3"))
FAISS_INDEX_PATH = str(BASE_DIR / "knowledge_base_scripts" / "Vector" / "faiss_index")

# Пути для работы с ресурсами
RESOURCES_DIST_PATH = os.getenv("RESOURCES_DIST_PATH", str(BASE_DIR / "json_files" / "resources_dist.json"))
IMAGES_DIR = os.getenv("IMAGES_DIR", str(BASE_DIR / "images"))