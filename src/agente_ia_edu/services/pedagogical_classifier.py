from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import PedagogicalClassification as PedagogicalClassificationModel
from agente_ia_edu.db.models.official import QuestionVersion


class PedagogicalClassifierProvider(Protocol):
    async def classify(
        self,
        *,
        question_text: str,
        question_version_id: uuid.UUID | None = None,
        question_number: int | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        ...


class MockPedagogicalClassifierProvider:
    def __init__(
        self,
        *,
        default_difficulty: str = "MEDIUM",
        default_confidence: float = 0.88,
        default_difficulty_confidence: float = 0.72,
        default_status: str = "CLASSIFIED",
        failing_question_numbers: list[int] | None = None,
        custom_responses: dict[int, dict[str, Any]] | None = None,
    ):
        self.default_difficulty = default_difficulty
        self.default_confidence = default_confidence
        self.default_difficulty_confidence = default_difficulty_confidence
        self.default_status = default_status
        self.failing_question_numbers = set(failing_question_numbers or [])
        self.custom_responses = custom_responses or {}

    async def classify(
        self,
        *,
        question_text: str,
        question_version_id: uuid.UUID | None = None,
        question_number: int | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        del question_text, question_version_id

        if question_number and question_number in self.failing_question_numbers:
            raise RuntimeError(f"Simulated provider failure for question #{question_number}")

        if question_number and question_number in self.custom_responses:
            res = dict(self.custom_responses[question_number])
            res.setdefault("provider", "mock")
            res.setdefault("model_name", model_name or "mock-model")
            res.setdefault("model_version", model_version or "v1")
            res.setdefault("prompt_version", prompt_version or "prompt-v1")
            res.setdefault("input_tokens", 10)
            res.setdefault("output_tokens", 8)
            res.setdefault("total_tokens", 18)
            return res

        return {
            "discipline": "Ciências",
            "content": "Matéria",
            "subcontent": "Estrutura da Matéria",
            "difficulty": self.default_difficulty,
            "difficulty_confidence": self.default_difficulty_confidence,
            "reasoning_type": "conceitual",
            "prerequisites": ["Conceito de matéria"],
            "keywords": ["matéria", "estrutura"],
            "competencies": ["CN01"],
            "skills": ["H01"],
            "classification_confidence": self.default_confidence,
            "status": self.default_status,
            "provider": "mock",
            "model_name": model_name or "mock-model",
            "model_version": model_version or "v1",
            "prompt_version": prompt_version or "prompt-v1",
            "input_tokens": 10,
            "output_tokens": 8,
            "total_tokens": 18,
        }


class PedagogicalClassificationService:
    _valid_statuses = {"CLASSIFIED", "NEEDS_REVIEW", "DRAFT"}
    _valid_difficulties = {"VERY_EASY", "EASY", "MEDIUM", "HARD", "VERY_HARD"}

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_classifications(self, question_version_id: uuid.UUID) -> list[PedagogicalClassificationModel]:
        result = await self.session.execute(
            select(PedagogicalClassificationModel)
            .where(PedagogicalClassificationModel.question_version_id == question_version_id)
            .order_by(PedagogicalClassificationModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def classify_question(
        self,
        question_version_id: uuid.UUID,
        provider: PedagogicalClassifierProvider,
        model_name: str,
        model_version: str,
        prompt_version: str,
        force_review: bool = False,
    ) -> PedagogicalClassificationModel:
        question_version = await self.session.get(QuestionVersion, question_version_id)
        if question_version is None:
            raise ValueError(f"Question version not found: {question_version_id}")

        source_text = (question_version.canonical_text or "").strip()
        if not source_text and question_version.statement:
            source_text = question_version.statement.strip()
        if not source_text:
            raise ValueError("Question content is empty and cannot be classified.")

        raw_payload = await provider.classify(
            question_text=source_text,
            question_version_id=question_version_id,
            model_name=model_name,
            model_version=model_version,
            prompt_version=prompt_version,
        )
        normalized = self._coerce_payload(raw_payload)
        validated = self._validate_payload(normalized)
        if force_review and validated["status"] == "CLASSIFIED":
            validated["status"] = "NEEDS_REVIEW"

        record = PedagogicalClassificationModel(
            question_version_id=question_version.id,
            discipline=validated["discipline"],
            content=validated["content"],
            subcontent=validated["subcontent"],
            difficulty=validated["difficulty"],
            classification_confidence=Decimal(str(validated["classification_confidence"])),
            difficulty_confidence=Decimal(str(validated["difficulty_confidence"])),
            reasoning_type=validated["reasoning_type"],
            prerequisites=validated.get("prerequisites") or [],
            keywords=validated.get("keywords") or [],
            competencies=validated.get("competencies") or [],
            skills=validated.get("skills") or [],
            model_name=model_name,
            model_version=model_version,
            prompt_version=prompt_version,
            provider_name=str(validated.get("provider") or "unknown"),
            input_tokens=int(validated.get("input_tokens", 0) or 0),
            output_tokens=int(validated.get("output_tokens", 0) or 0),
            total_tokens=int(validated.get("total_tokens", 0) or 0),
            status=validated["status"],
            source="ai",
            metadata_={"raw_response": validated},
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    @staticmethod
    def _coerce_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ValueError("Provider returned invalid JSON for pedagogical classification.") from exc
            if not isinstance(parsed, dict):
                raise ValueError("Provider response must be a JSON object.")
            return parsed
        if isinstance(payload, dict):
            return payload
        raise ValueError("Provider response must be a JSON object or valid JSON string.")

    @classmethod
    def _validate_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        required_fields = {
            "discipline",
            "content",
            "subcontent",
            "difficulty",
            "difficulty_confidence",
            "reasoning_type",
            "classification_confidence",
            "status",
        }
        missing = sorted(required_fields - payload.keys())
        if missing:
            raise ValueError(f"Missing required classification fields: {missing}")

        for field in ("discipline", "content", "subcontent", "reasoning_type"):
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Field '{field}' must be a non-empty string.")

        difficulty = str(payload["difficulty"]).upper()
        if difficulty not in cls._valid_difficulties:
            raise ValueError(f"Invalid difficulty value: {payload['difficulty']}")

        status = str(payload["status"]).upper()
        if status not in cls._valid_statuses:
            raise ValueError(f"Invalid classification status: {payload['status']}")

        for field in ("classification_confidence", "difficulty_confidence"):
            value = payload.get(field)
            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError as exc:
                    raise ValueError(f"Field '{field}' must be numeric.") from exc
            if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"Field '{field}' must be between 0 and 1.")

        normalized = dict(payload)
        normalized["difficulty"] = difficulty
        normalized["status"] = status
        normalized["classification_confidence"] = float(normalized["classification_confidence"])
        normalized["difficulty_confidence"] = float(normalized["difficulty_confidence"])
        normalized["prerequisites"] = normalized.get("prerequisites") or []
        normalized["keywords"] = normalized.get("keywords") or []
        normalized["competencies"] = normalized.get("competencies") or []
        normalized["skills"] = normalized.get("skills") or []
        return normalized
