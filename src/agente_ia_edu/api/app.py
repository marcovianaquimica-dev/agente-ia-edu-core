from pathlib import Path
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes.assessments import router as assessments_router
from .routes.attempts import router as attempts_router
from .routes.health import router as health_router
from .routes.questions import router as questions_router
from .routes.question_bank import question_bank_router
from .routes.exercise_lists import router as exercise_lists_router
from .routes.teacher_materials import router as teacher_materials_router
from .routes.question_modification_proposals import router as question_modification_proposals_router
from .routes.learning_path import practice_router
from .routes.catalog import catalog_router
from .routes.essay_prompts import essay_prompts_router
from .routes.essay_submissions import essay_submissions_router
from .routes.essay_corrections import essay_corrections_router
from .routes.authorial_ingestion import ingestion_router
from .routes.question_extraction import qe_router
from .routes.question_classification import qc_router
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
from .routes.reception import reception_router
from .routes.domain_map import domain_map_router
from .dependencies import reject_reception_only_role
from ..auth.bootstrap import configure_identity_provider_from_env


def create_app() -> FastAPI:
    # No-op unless JWT_AUTH_SECRET is set in the process environment - see
    # auth/bootstrap.py. Every existing deployment and this repository's own
    # test suite leave it unset, so this never changes their behaviour.
    configure_identity_provider_from_env()

    app = FastAPI(title="AGENTE IA EDU")
    app.include_router(health_router)
    reception_only_guard = [Depends(reject_reception_only_role)]
    app.include_router(questions_router, dependencies=reception_only_guard)
    app.include_router(question_bank_router, dependencies=reception_only_guard)
    app.include_router(exercise_lists_router, dependencies=reception_only_guard)
    app.include_router(teacher_materials_router, dependencies=reception_only_guard)
    app.include_router(question_modification_proposals_router, dependencies=reception_only_guard)
    app.include_router(assessments_router, dependencies=reception_only_guard)
    app.include_router(attempts_router, dependencies=reception_only_guard)
    app.include_router(practice_router, dependencies=reception_only_guard)
    app.include_router(catalog_router, dependencies=reception_only_guard)
    app.include_router(essay_prompts_router, dependencies=reception_only_guard)
    app.include_router(essay_submissions_router, dependencies=reception_only_guard)
    app.include_router(essay_corrections_router, dependencies=reception_only_guard)
    app.include_router(ingestion_router, dependencies=reception_only_guard)
    app.include_router(qe_router, dependencies=reception_only_guard)
    app.include_router(qc_router, dependencies=reception_only_guard)
    app.include_router(video_router, dependencies=reception_only_guard)
    app.include_router(discovery_router, dependencies=reception_only_guard)
    app.include_router(student_router, dependencies=reception_only_guard)
    app.include_router(admin_router, dependencies=reception_only_guard)
    app.include_router(teacher_router, dependencies=reception_only_guard)
    app.include_router(coordination_router, dependencies=reception_only_guard)
    app.include_router(pedagogical_context_router, dependencies=reception_only_guard)
    app.include_router(teacher_portal_router, dependencies=reception_only_guard)
    app.include_router(coordination_portal_router, dependencies=reception_only_guard)
    app.include_router(diagnostic_router, dependencies=reception_only_guard)
    app.include_router(domain_map_router, dependencies=reception_only_guard)
    app.include_router(reception_router)

    web_dir = Path(__file__).parent.parent / "web"
    if web_dir.exists():
        app.mount("/student", StaticFiles(directory=str(web_dir), html=True), name="student")
        app.mount("/teacher/assets", StaticFiles(directory=str(web_dir), html=False), name="teacher-assets")
        app.mount("/coordination/assets", StaticFiles(directory=str(web_dir), html=False), name="coordination-assets")
        app.mount("/reception/assets", StaticFiles(directory=str(web_dir), html=False), name="reception-assets")
        app.mount("/question-bank/assets", StaticFiles(directory=str(web_dir), html=False), name="question-bank-assets")
        app.mount("/admin/assets", StaticFiles(directory=str(web_dir), html=False), name="admin-assets")

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

        @app.get("/reception", include_in_schema=False)
        @app.get("/reception/", include_in_schema=False)
        async def serve_reception_portal():
            return FileResponse(web_dir / "reception.html")

        @app.get("/question-bank", include_in_schema=False)
        @app.get("/question-bank/", include_in_schema=False)
        async def serve_question_bank_professor():
            page = web_dir / "question-bank.html"
            if page.exists():
                return FileResponse(page)
            return FileResponse(web_dir / "index.html")

        @app.get("/admin", include_in_schema=False)
        @app.get("/admin/", include_in_schema=False)
        async def serve_admin_portal():
            page = web_dir / "admin.html"
            if page.exists():
                return FileResponse(page)
            return FileResponse(web_dir / "index.html")

    return app


app = create_app()




