"""Development-only server for exercising the student diagnostic frontend end-to-end."""

import asyncio

import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, Question, QuestionOption, QuestionVersion


async def seed(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id
        content = CatalogNode(parent_id=root.id, root_id=root.id, node_type="CONTENT", name="Soluções", position=1, active=True)
        session.add(content)
        await session.flush()
        for difficulty, text in (("EASY", "Uma solução tem concentração inicial de 10 g/L. Após diluição simples, a concentração diminui."), ("MEDIUM", "Qual relação deve ser preservada ao calcular uma diluição de soluções?"), ("HARD", "Em uma diluição seriada, qual grandeza permite comparar concentrações antes e depois do processo?")):
            question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            session.add(question)
            await session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text=text, content_hash=f"frontend-{difficulty}", recommended_difficulty=difficulty)
            session.add(version)
            await session.flush()
            session.add_all([
                QuestionOption(question_version_id=version.id, option_key="A", position=1, text="A alternativa correta", is_valid_option=True),
                QuestionOption(question_version_id=version.id, option_key="B", position=2, text="Uma alternativa diferente", is_valid_option=False),
                ContentQuestionLink(content_node_id=content.id, question_version_id=version.id),
            ])
        await session.commit()


def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await seed(factory)

    asyncio.run(setup())
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    uvicorn.run(app, host="127.0.0.1", port=8010)


if __name__ == "__main__":
    main()