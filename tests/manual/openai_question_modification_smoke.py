"""Manual development-only OpenAI smoke test. Never run as part of pytest."""

import asyncio
import os

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, Question, QuestionVersion
from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.services.question_modification import ModificationType, QuestionModificationAdapter


async def load_chemistry_question(session):
    statement = (
        select(QuestionVersion, CatalogNode)
        .join(Question, Question.id == QuestionVersion.question_id)
        .join(ContentQuestionLink, ContentQuestionLink.question_version_id == QuestionVersion.id)
        .join(CatalogNode, CatalogNode.id == ContentQuestionLink.content_node_id)
        .where(
            Question.status == "PUBLISHED",
            QuestionVersion.version_kind == "official_original",
            QuestionVersion.recommended_difficulty.isnot(None),
            CatalogNode.root_id.in_(
                select(CatalogNode.id).where(
                    or_(
                        CatalogNode.name.ilike("quimica"),
                        CatalogNode.name.ilike("química"),
                    )
                )
            ),
        )
        .options(selectinload(QuestionVersion.options))
        .order_by(QuestionVersion.created_at)
        .limit(1)
    )
    row = (await session.execute(statement)).first()
    if row is None:
        raise RuntimeError("No published Chemistry question with options was found in the configured Question Bank.")
    version, content = row
    options = sorted(version.options, key=lambda option: option.position)
    correct_options = [option.text for option in options if option.is_valid_option]
    if len(options) < 2 or len(correct_options) != 1:
        raise RuntimeError("The selected Question Bank record has no unambiguous objective answer key.")
    return version, content, options, correct_options[0]


async def main() -> None:
    if not os.getenv("OPENAI_API_KEY") or not os.getenv("OPENAI_MODEL"):
        raise SystemExit("Set OPENAI_API_KEY and OPENAI_MODEL in the local environment first.")
    engine = create_engine()
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            version, content, options, correct_option = await load_chemistry_question(session)
        adapter = QuestionModificationAdapter(OpenAIProvider())
        proposal, provider, model = await adapter.propose(
            question_data={
                "statement": version.statement or version.canonical_text,
                "options": [option.text for option in options],
                "correct_option": correct_option,
                "difficulty": version.recommended_difficulty,
                "content": content.name,
                "question_type": "MULTIPLE_CHOICE",
            },
            modification_type=ModificationType.MAKE_EASIER,
            instruction="Deixe esta questão mais fácil, mantendo o mesmo conteúdo químico e o objetivo pedagógico, reduzindo a complexidade necessária para resolvê-la.",
        )
        print({"success": True, "provider": provider, "model": model, "type": proposal.modification_type, "difficulty": proposal.difficulty, "content": content.name})
        print({"original": version.statement or version.canonical_text, "original_options": [option.text for option in options], "original_correct_option": correct_option})
        print({"proposal": proposal.model_dump()})
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())