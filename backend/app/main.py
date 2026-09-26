from fastapi import FastAPI

from app.routes import health, meta, ready
from app.routes.errors import register_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="CivicPulse API", version="0.1.0")
    app.include_router(health.router)
    app.include_router(ready.router)
    app.include_router(meta.router)
    register_error_handlers(app)
    return app


app = create_app()
