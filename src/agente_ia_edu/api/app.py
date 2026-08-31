from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
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
from .routes.admin import admin_router
from .routes.teaching_context import (
    teacher_router,
    coordination_router,
    pedagogical_context_router,
)
from .routes.teacher_portal import teacher_portal_router
from .routes.coordination_portal import coordination_portal_router
from .routes.diagnostic import diagnostic_router


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
    app.include_router(admin_router)
    app.include_router(teacher_router)
    app.include_router(coordination_router)
    app.include_router(pedagogical_context_router)
    app.include_router(teacher_portal_router)
    app.include_router(coordination_portal_router)
    app.include_router(diagnostic_router)

    web_dir = Path(__file__).parent.parent / "web"
    if web_dir.exists():
        app.mount("/student", StaticFiles(directory=str(web_dir), html=True), name="student")
        app.mount("/teacher/assets", StaticFiles(directory=str(web_dir), html=False), name="teacher-assets")
        app.mount("/coordination/assets", StaticFiles(directory=str(web_dir), html=False), name="coordination-assets")

        @app.get("/teacher", include_in_schema=False)
        @app.get("/teacher/", include_in_schema=False)
        async def serve_teacher_portal():
            teacher_html = web_dir / "teacher.html"
            if teacher_html.exists():
                return FileResponse(teacher_html)
            return FileResponse(web_dir / "index.html")

        @app.get("/coordination", include_in_schema=False)
        @app.get("/coordination/", include_in_schema=False)
        async def serve_coordination_portal():
            coord_html = web_dir / "coordination.html"
            if coord_html.exists():
                return FileResponse(coord_html)
            return FileResponse(web_dir / "index.html")

    return app


app = create_app()




