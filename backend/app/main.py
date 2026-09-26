from fastapi import FastAPI

from app.routes import health, ready


def create_app() -> FastAPI:
    app = FastAPI(title="CivicPulse API", version="0.1.0")
    app.include_router(health.router)
    app.include_router(ready.router)
    return app


app = create_app()
