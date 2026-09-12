"""Central validation policy for teacher-authored material drafts."""

import os


class TeacherMaterialPolicy:
    max_questions = int(os.getenv("TEACHER_MATERIAL_MAX_QUESTIONS", "50"))
    difficulty_levels = frozenset({"EASY", "MEDIUM", "HARD"})

    @classmethod
    def validate_configuration(cls, quantity: int, distribution: dict[str, int]) -> dict[str, int]:
        if quantity > cls.max_questions:
            raise ValueError("Essa quantidade e muito alta para uma unica lista.")
        normalized = {level: int(distribution.get(level, 0)) for level in cls.difficulty_levels}
        if quantity <= 0 or any(value < 0 for value in normalized.values()):
            raise ValueError("A quantidade e a distribuicao devem ser positivas.")
        if set(distribution) - cls.difficulty_levels or sum(normalized.values()) != quantity:
            raise ValueError("A distribuicao de dificuldades deve corresponder ao total solicitado.")
        return normalized