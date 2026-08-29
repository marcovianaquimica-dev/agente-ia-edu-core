from fastapi import FastAPI

from .routes.assessments import router as assessments_router
from .routes.attempts import router as attempts_router
from .routes.health import router as health_router
from .routes.questions import router as questions_router


def create_app() -> FastAPI:
    app = FastAPI(title="AGENTE IA EDU")
    app.include_router(health_router)
    app.include_router(questions_router)
    app.include_router(assessments_router)
    app.include_router(attempts_router)
    return app


app = create_app()
