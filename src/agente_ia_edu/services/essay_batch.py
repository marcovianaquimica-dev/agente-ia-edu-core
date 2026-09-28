"""Envio em lote de redacoes fisicas pelo professor.

Spec: docs/superpowers/specs/2026-09-28-envio-lote-redacao-design.md

Este arquivo comeca com as funcoes PURAS (normalizacao, leitura do cabecalho,
match contra a turma) porque sao elas que decidem se uma folha de papel vira
automaticamente a redacao de alguem ou cai na fila do professor - a parte do
sistema que mais precisa ser deterministica e auditavel. A classe de servico
(banco, OCR, storage) vem depois delas.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..providers.contracts import EssayTranscriptionProvider
from ..providers.errors import ProviderError
from ..db.models import EssaySubmissionPage
from .essay_answer_sheet import HEADER_REGION_FRACTION
from .essay_submission import EssaySubmissionService
from .material_storage import MaterialStorage

logger = logging.getLogger(__name__)


def normalize_person_name(value: str | None) -> str:
    """Maiusculas, sem acento, espacos colapsados (spec s4.2).

    NFKD + descarte de combining marks e a mesma tecnica que o resto do
    projeto usa pra comparar texto acentuado - decompoe "Ç" em "C" + cedilha e
    joga fora a cedilha, em vez de depender de uma tabela de substituicao
    manual que sempre esquece alguma letra.
    """
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.upper().split())


def normalize_cpf(value: str | None) -> str:
    """So os digitos - o aluno pode escrever com ponto, com traco ou sem nada."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


# 11 digitos com ate 2 caracteres de pontuacao/espaco entre os grupos: pega
# "123.456.789-00", "123 456 789 00" e "12345678900" igualmente.
_CPF_PATTERN = re.compile(r"\d{3}\D{0,2}\d{3}\D{0,2}\d{3}\D{0,2}\d{2}")

# O rotulo impresso pela folha que o proprio sistema gera e "NOME COMPLETO DO
# PARTICIPANTE", mas o OCR pode comer parte dele, e um professor pode usar uma
# folha antiga onde esta so "NOME:" - as tres formas sao aceitas.
_NAME_LABEL_PATTERN = re.compile(r"^NOME(\s+COMPLETO)?(\s+DO\s+PARTICIPANTE)?\s*:?\s*")

_CPF_LABEL_PATTERN = re.compile(r"^CPF\b\s*:?\s*")

# Rotulos e cabecalhos impressos na propria folha, que nunca sao o nome de
# ninguem - descartados antes do fallback abaixo.
_PRINTED_LABELS = ("FOLHA DE REDACAO", "NOME", "CPF")


def _strip_trailing_cpf(normalized_line: str) -> str:
    """Corta a linha no primeiro sinal de CPF (o rotulo ou os digitos).

    Confirmado como necessario: o OCR de uma REGIAO pequena (o cabecalho) muito
    frequentemente devolve tudo numa unica linha - "NOME COMPLETO DO
    PARTICIPANTE JOAO DA SILVA CPF 123.456.789-00". Sem este corte o nome
    extraido seria "JOAO DA SILVA CPF 123.456.789-00", que nao casa com aluno
    nenhum e manda toda folha bem preenchida pra fila manual.
    """
    cut = len(normalized_line)
    digits = _CPF_PATTERN.search(normalized_line)
    if digits is not None:
        cut = min(cut, digits.start())
    label = re.search(r"\bCPF\b", normalized_line)
    if label is not None:
        cut = min(cut, label.start())
    return normalized_line[:cut].strip()


def _looks_like_a_person_name(normalized_line: str) -> bool:
    """Duas ou mais palavras, so letras e espaco. Deliberadamente estreito: e
    usado apenas no fallback (quando o rotulo NOME nao foi lido), e um falso
    positivo aqui vira uma redacao atribuida ao aluno errado, enquanto um falso
    negativo so manda a pagina pra fila do professor."""
    if any(ch.isdigit() for ch in normalized_line):
        return False
    words = normalized_line.split()
    return len(words) >= 2 and all(word.isalpha() for word in words)


def parse_header_text(header_text: str | None) -> tuple[str | None, str | None]:
    """Extrai (nome normalizado, CPF so-digitos) do texto lido por OCR na
    REGIAO de cabecalho da folha.

    Estrategia, em ordem:
      1. A linha que comeca com o rotulo NOME - se tiver conteudo depois do
         rotulo, e esse o nome, cortado no primeiro sinal de CPF (ver
         _strip_trailing_cpf: o OCR do cabecalho costuma devolver tudo numa
         linha so); senao, a proxima linha que nao seja outro rotulo nem um CPF.
      2. Fallback (rotulo ilegivel): a linha mais longa que "parece nome de
         pessoa" e nao e um dos rotulos impressos na propria folha. O titulo da
         proposta tambem e impresso no cabecalho, mas ele quase sempre tem
         digito, artigo ou pontuacao - e, quando nao tem, o caminho 1 ja
         resolveu. Se o fallback errar, a pagina cai na fila manual, que e o
         comportamento seguro.

    Devolve o nome JA NORMALIZADO (e o que vai tanto pro match quanto pra
    coluna ocr_name_raw - ver o docstring de EssayBatchPage).
    """
    lines = [line.strip() for line in (header_text or "").splitlines()]
    normalized_lines = [normalize_person_name(line) for line in lines]
    normalized_lines = [line for line in normalized_lines if line]

    cpf_match = _CPF_PATTERN.search(header_text or "")
    cpf = normalize_cpf(cpf_match.group(0)) if cpf_match else None
    if cpf is not None and len(cpf) != 11:
        cpf = None

    name: str | None = None
    for index, line in enumerate(normalized_lines):
        label = _NAME_LABEL_PATTERN.match(line)
        if label is None:
            continue
        remainder = _strip_trailing_cpf(line[label.end():].strip())
        if remainder:
            name = remainder
        else:
            for candidate in normalized_lines[index + 1:]:
                if _CPF_LABEL_PATTERN.match(candidate) or _CPF_PATTERN.search(candidate):
                    continue
                if _NAME_LABEL_PATTERN.match(candidate):
                    continue
                name = _strip_trailing_cpf(candidate)
                break
        break

    if not name:
        candidates = [
            line for line in normalized_lines
            if _looks_like_a_person_name(line)
            and not any(line.startswith(label) for label in _PRINTED_LABELS)
        ]
        if candidates:
            name = max(candidates, key=len)

    return (name or None), cpf


def match_student(
    name_raw: str | None, roster: Sequence[tuple[uuid.UUID, str]]
) -> uuid.UUID | None:
    """O student_id sse EXATAMENTE UM aluno do roster tem o mesmo nome
    normalizado (spec s4.3). Zero ou dois-ou-mais devolvem None, e a pagina vai
    pra fila de resolucao manual - homonimos na mesma turma nunca sao
    desempatados automaticamente, nem pelo CPF (decisao do brainstorm: o CPF e
    pista pro professor, nunca criterio de match).

    ``roster`` e uma sequencia de (student_id, full_name) - o nome vem de
    Person.full_name, montado pela query de alunos ativos da turma.
    """
    target = normalize_person_name(name_raw)
    if not target:
        return None
    matches = [
        student_id for student_id, full_name in roster
        if normalize_person_name(full_name) == target
    ]
    return matches[0] if len(matches) == 1 else None


def text_from_ocr_tokens(tokens: list[dict] | None) -> str:
    """Reconstroi o texto a partir dos offsets start/end dos proprios tokens.

    Mesma logica de essay_submission._reconstruct_text_from_tokens (e pelo mesmo
    motivo documentado la: concatenar t["text"] cola palavras vizinhas sempre
    que o espaco entre dois tokens nao virou token proprio), porem sobre os
    DICTS que EssaySubmissionPage.ocr_tokens guarda, e nao sobre os dataclasses
    EssayOcrToken. Duplicado de proposito em vez de importado: aquela funcao e
    privada do modulo de submissao e tipada pros dataclasses.
    """
    if not tokens:
        return ""
    length = max(token["end"] for token in tokens)
    buffer = [" "] * length
    for token in tokens:
        for offset, character in enumerate(token["text"]):
            position = token["start"] + offset
            if position < length:
                buffer[position] = character
    return "".join(buffer).strip()


class EssayBatchService:
    """Processa um lote de folhas de redacao fisicas.

    ``correction_factory`` existe pro teste: em producao e
    ``EssayCorrectionService``, e o servico chama
    ``correction_factory(session).correct(submission_id)`` quando uma submissao
    do lote fica pronta. Injetavel pra que nenhum teste deste modulo precise de
    provedor de IA configurado.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: MaterialStorage | None = None,
        transcriber: EssayTranscriptionProvider | None = None,
        correction_factory: Callable[[AsyncSession], Any] | None = None,
    ) -> None:
        self.session = session
        self._storage = storage or MaterialStorage()
        self._transcriber = transcriber
        self._correction_factory = correction_factory

    def _submission_service(self) -> EssaySubmissionService:
        """Uma instancia de EssaySubmissionService usada SO pelo seu _ocr_page:
        as 3 tentativas, o piso de confianca media de 0.6 e a releitura de
        reconciliacao de tokens duvidosos ja estao resolvidos la (confirmados em
        producao ao longo de 2026-09), e reimplementar isso aqui seria copiar a
        parte mais delicada do sistema."""
        return EssaySubmissionService(self.session, transcriber=self._transcriber)

    @staticmethod
    def _crop_regions(image_path: Path, dest_dir: Path) -> tuple[Path, Path]:
        """Separa uma imagem de pagina em (cabecalho, corpo) pela fracao fixa de
        altura que a folha gerada por services/essay_answer_sheet.py usa.

        pymupdf abre um PNG/JPG como documento de uma pagina so; as coordenadas
        da pagina saem em PONTOS (72dpi), nao em pixels, entao a matriz de
        escala abaixo e o que preserva a resolucao original do arquivo - sem ela
        um PNG de 827px de largura sairia recortado com 596px, e o OCR passaria
        a ler uma imagem PIOR do que a que recebemos.
        """
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        try:
            full = _mu.Pixmap(str(image_path))
        except Exception as exc:
            raise ValueError(f"{image_path.name} is not a readable image file") from exc

        doc = _mu.open(str(image_path))
        try:
            page = doc[0]
            rect = page.rect
            scale = full.width / rect.width
            matrix = _mu.Matrix(scale, scale)
            split_y = rect.y0 + rect.height * HEADER_REGION_FRACTION
            header_path = dest_dir / f"{image_path.stem}_header.png"
            body_path = dest_dir / f"{image_path.stem}_body.png"
            page.get_pixmap(
                matrix=matrix, clip=_mu.Rect(rect.x0, rect.y0, rect.x1, split_y)
            ).save(str(header_path))
            page.get_pixmap(
                matrix=matrix, clip=_mu.Rect(rect.x0, split_y, rect.x1, rect.y1)
            ).save(str(body_path))
            return header_path, body_path
        finally:
            doc.close()

    async def _ocr_region(self, image_path: Path) -> str:
        """Texto de UMA regiao ja recortada. Usa uma EssaySubmissionPage
        TRANSITORIA, nunca adicionada a sessao: _ocr_page so escreve em
        ``page.ocr_tokens`` e nunca chama add()/flush(), entao o objeto serve
        aqui apenas como recipiente dos tokens e nada e persistido."""
        sink = EssaySubmissionPage(
            id=uuid.uuid4(), essay_submission_id=uuid.uuid4(),
            page_number=1, storage_uri=str(image_path),
        )
        await self._submission_service()._ocr_page(sink, image_path)
        return text_from_ocr_tokens(sink.ocr_tokens)

    async def read_page_regions(
        self, image_path: Path
    ) -> tuple[str | None, str | None, str]:
        """(nome normalizado, CPF so-digitos, texto do corpo) de uma pagina.

        Uma falha de OCR no CABECALHO nao e fatal: a pagina vai pra fila de
        resolucao manual e o professor identifica o aluno olhando a imagem, mas
        o texto que o aluno escreveu continua aproveitavel. Uma falha no CORPO
        propaga (ProviderError), porque sem texto nao ha redacao nenhuma pra
        corrigir - quem chama trata isso marcando a pagina NEEDS_REVIEW.

        Deliberadamente NAO usa a camada de texto embutida de um PDF digital
        (que _split_pdf_pages sabe extrair): a separacao cabecalho/corpo aqui e
        POSICIONAL, e a camada de texto nao carrega posicao util pra isso. Um
        lote e sempre papel escaneado de qualquer forma.
        """
        scratch_dir = Path(tempfile.mkdtemp(prefix="r4_batch_regions_"))
        try:
            header_path, body_path = await asyncio.to_thread(
                self._crop_regions, image_path, scratch_dir
            )
            try:
                header_text = await self._ocr_region(header_path)
            except ProviderError as exc:
                logger.warning(
                    "OCR do cabecalho falhou para %s, pagina ira para revisao manual: %s",
                    image_path, exc,
                )
                header_text = ""
            body_text = await self._ocr_region(body_path)
            name, cpf = parse_header_text(header_text)
            return name, cpf, body_text.strip()
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)


__all__ = [
    "EssayBatchService",
    "match_student",
    "normalize_cpf",
    "normalize_person_name",
    "parse_header_text",
    "text_from_ocr_tokens",
]
