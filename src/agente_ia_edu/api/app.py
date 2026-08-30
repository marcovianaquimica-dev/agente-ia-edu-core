from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes.assessments import router as assessments_router
from .routes.attempts import router as attempts_router
from .routes.health import router as health_router
from .routes.questions import router as questions_router
from .routes.learning_path import practice_router
from .routes.catalog import catalog_router
from .routes.video_engine import video_router
from .routes.discovery import discovery_router
from .routes.student import student_router


def create_app() -> FastAPI:
    app = FastAPI(title="AGENTE IA EDU")
    app.include_router(health_router)
    app.include_router(questions_router)
    app.include_router(assessments_router)
    app.include_router(attempts_router)
    app.include_router(practice_router)
    app.include_router(catalog_router)
    app.include_router(video_router)
    app.include_router(discovery_router)
    app.include_router(student_router)

    web_dir = Path(__file__).parent.parent / "web"
    if web_dir.exists():
        app.mount("/student", StaticFiles(directory=str(web_dir), html=True), name="student")

    return app


app = create_app()



