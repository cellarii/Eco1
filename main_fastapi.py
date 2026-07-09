import uvicorn
from fastapi_app.main import app

if __name__ == "__main__":
    uvicorn.run(
        "main_fastapi:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )