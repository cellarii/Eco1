from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes import all_routers

app = FastAPI(
    title="SalutBot FastAPI",
    description="Переписанный сервис на FastAPI",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in all_routers:
    app.include_router(router)

@app.get("/")
async def home():
    return {"message": "SalutBot FastAPI works!"}

@app.get("/health")
async def health():
    return {"status": "ok"}