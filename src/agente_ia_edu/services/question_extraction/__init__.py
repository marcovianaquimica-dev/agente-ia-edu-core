"""PHASE 27 - Question Extraction Engine.

A deterministic, AI-agnostic pipeline turning a PDF into structured,
reviewable questions: PDF structural analysis -> reading order ->
question boundary detection -> classification/content extraction ->
asset association -> validation. See ``engine.extract_questions`` for the
single entry point.

Decoupled from PHASE 26's authorial material ingestion (DOCUMENT ->
MATERIAL) and from the ENEM-specific official pipeline (both untouched) -
this package answers DOCUMENT -> QUESTIONS only.
"""

from .engine import ExtractionResult, ExtractedQuestionResult, extract_questions

__all__ = ["ExtractionResult", "ExtractedQuestionResult", "extract_questions"]
