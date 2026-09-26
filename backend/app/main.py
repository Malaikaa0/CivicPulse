from fastapi import FastAPI

from app.lifecycle import lifespan
from app.logging_config import configure_logging, resolve_log_level
from app.metrics import Metrics
from app.middleware import ObservabilityMiddleware
from app.routes import health, metrics, ready
from app.routes.errors import register_error_handlers


def create_app() -> FastAPI:
    configure_logging(resolve_log_level())
    app = FastAPI(title="CivicPulse API", version="0.1.0", lifespan=lifespan)
    app.state.metrics = Metrics()
    app.add_middleware(ObservabilityMiddleware, metrics=app.state.metrics)
    app.include_router(health.router)
    app.include_router(ready.router)
    app.include_router(metrics.router)
    register_error_handlers(app)
    return app


app = create_app()
