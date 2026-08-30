from fastapi import FastAPI

from .routes.assessments import router as assessments_router
from .routes.attempts import router as attempts_router
from .routes.health import router as health_router
from .routes.questions import router as questions_router
from .routes.learning_path import practice_router
from .routes.catalog import catalog_router
from .routes.video_engine import video_router


def create_app() -> FastAPI:
    app = FastAPI(title="AGENTE IA EDU")
    app.include_router(health_router)
    app.include_router(questions_router)
    app.include_router(assessments_router)
    app.include_router(attempts_router)
    app.include_router(practice_router)
    app.include_router(catalog_router)
    app.include_router(video_router)
    return app


app = create_app()

