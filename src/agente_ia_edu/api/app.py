from fastapi import FastAPI

from .routes.health import router as health_router
from .routes.questions import router as questions_router


def create_app() -> FastAPI:
    app = FastAPI(title="AGENTE IA EDU")
    app.include_router(health_router)
    app.include_router(questions_router)
    return app


app = create_app()
