"""Extracao deterministica da BNCC - area de Ciencias da Natureza (CNT).

PURO: recebe ``page_texts`` e devolve um ``BnccFramework`` congelado. Nao abre
arquivo, nao toca banco. Isso permite que DOIS consumidores usem o mesmo
resultado sem duplicar a leitura (spec 22.1):

  * ``bncc_taxonomy_seed``        -> Taxonomy/TaxonomyNode  (dominio CURRICULO)
  * ``CurriculumFrameworkChunker`` -> knowledge_chunks        (ENGINE)

VERIFICADO NA FASE 0 contra o arquivo real
(``BNCC_EnsinoMedio_embaixa_site_110518.pdf``, 154 paginas, lido por pypdf
sem fallback): 3/3 competencias especificas nas paginas 116, 118 e 120, e
23/23 habilidades nas paginas 117, 119 e 121.

DUAS FONTES PARA A MESMA HIERARQUIA. O numero da competencia esta no PROPRIO
codigo da habilidade (o digito seguinte a ``EM13CNT``), e a paginacao
confirma de forma independente - 6 habilidades sob a CE1, 7 sob a CE2 e 10
sob a CE3. O extrator usa o codigo e CONFERE contra o cabecalho encontrado;
divergencia e erro, nunca escolha silenciosa.

A JANELA DA CNT. O documento tem 27 habilidades de Linguagens, 45 de
Matematica e 31 de Ciencias Humanas, e as quatro areas usam o mesmo cabecalho
"COMPETENCIA ESPECIFICA N". Um extrator que varresse o documento inteiro
pareceria funcionar e produziria norma errada. A janela e derivada das
paginas que contem codigo ``EM13CNT``, com uma margem para tras que alcanca
o cabecalho da competencia que as precede.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Sequence

from ...bncc_contract.v1 import BNCC_TAXONOMY_CODE, CNT_AREA_CODE

EXTRACTOR_VERSION = "v1"

#: Codigo de habilidade da CNT. O grupo 1 e o numero da competencia.
_SKILL = re.compile(rf"\(({CNT_AREA_CODE}(\d)\d\d)\)\s*")
#: Qualquer codigo de habilidade do Ensino Medio, usado so para diagnostico.
_ANY_SKILL = re.compile(r"\(EM13(?:LGG|MAT|CNT|CHS)\d{3}\)")
_COMPETENCY_HEADING = re.compile(
    r"COMPET[EÊ]NCIA\s+ESPEC[IÍ]FICA\s+(\d+)", re.IGNORECASE
)
#: Nome canonico da area. NAO e lido do PDF: o cabecalho corrido
#: "CIENCIAS DA NATUREZA E SUAS TECNOLOGIAS ENSINO MEDIO" repete em TODA
#: pagina da secao, entao procura-lo nao daria pagina significativa - e
#: herdar a tipografia do arquivo faria o no da taxonomia variar com o PDF.
CNT_AREA_NAME = "Ciências da Natureza e suas Tecnologias"
#: Fim do enunciado da competencia: a primeira fronteira de frase. O que vem
#: depois e o comentario explicativo da BNCC, nao o enunciado normativo.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
#: Quebra de linha hifenizada. EXIGE whitespace apos o hifen: composto
#: legitimo aparece sem espaco (``socio-economico``), e juntar seria estragar.
_LINE_BREAK_HYPHEN = re.compile(r"(\w)-\s+(\w)")

#: Quantas paginas antes da primeira habilidade ainda pertencem a janela da
#: CNT. No arquivo real o cabecalho da CE1 esta uma pagina antes das suas
#: habilidades; 3 da folga sem alcancar a area anterior.
_WINDOW_LOOKBACK_PAGES = 3


class BnccExtractionError(ValueError):
    """A extracao nao produziu uma estrutura utilizavel. ``code`` e estavel."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BnccSkill:
    code: str
    statement: str
    competency_number: int
    page: int
    dehyphenations: int
    source_text_sha256: str


@dataclass(frozen=True)
class BnccCompetency:
    number: int
    code: str
    statement: str
    page: int
    dehyphenations: int
    skills: tuple[BnccSkill, ...]


@dataclass(frozen=True)
class BnccFramework:
    taxonomy_code: str
    taxonomy_version: str
    area_code: str
    area_name: str
    area_page: int
    competencies: tuple[BnccCompetency, ...]
    extractor_version: str
    skills: tuple[BnccSkill, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "skills",
            tuple(skill for c in self.competencies for skill in c.skills),
        )


def dehyphenate(text: str) -> tuple[str, int]:
    """Junta quebras de linha hifenizadas. Devolve ``(texto, junções)``.

    A contagem e devolvida para auditoria: sem ela, nao haveria como saber
    quanto o extrator mexeu no texto normativo.
    """
    joined, count = _LINE_BREAK_HYPHEN.subn(r"\1\2", text or "")
    return joined, count


def _normalize(text: str) -> tuple[str, int]:
    joined, count = dehyphenate(text)
    return " ".join(joined.split()), count


def extract_bncc_cnt(
    page_texts: Sequence[str], *, taxonomy_version: str
) -> BnccFramework:
    """Extrai area, competencias e habilidades da CNT."""
    if not taxonomy_version or not taxonomy_version.strip():
        raise ValueError(
            "taxonomy_version e obrigatorio: a identidade normativa e a tripla "
            "taxonomy_code + taxonomy_version + node_code"
        )
    pages = list(page_texts or [])
    if not pages:
        raise BnccExtractionError("NO_CNT_SKILLS_FOUND", "documento vazio")

    skill_pages = [
        index for index, text in enumerate(pages, start=1) if _SKILL.search(text or "")
    ]
    if not skill_pages:
        other = sum(len(_ANY_SKILL.findall(text or "")) for text in pages)
        raise BnccExtractionError(
            "NO_CNT_SKILLS_FOUND",
            f"nenhum codigo {CNT_AREA_CODE}### encontrado "
            f"({other} codigos de outras areas no documento)",
        )

    window_start = max(1, min(skill_pages) - _WINDOW_LOOKBACK_PAGES)
    window_end = max(skill_pages)

    competency_headings = _competency_headings(pages, window_start, window_end)
    skills_by_competency = _skills(pages, window_start, window_end)

    orphans = sorted(set(skills_by_competency) - set(competency_headings))
    if orphans:
        raise BnccExtractionError(
            "ORPHAN_SKILL",
            f"habilidades sem cabecalho de competencia correspondente: "
            f"competencias {orphans} ausentes na janela "
            f"p.{window_start}-{window_end}",
        )

    competencies = tuple(
        BnccCompetency(
            number=number,
            code=f"CNT-CE{number}",
            statement=heading["statement"],
            page=heading["page"],
            dehyphenations=heading["dehyphenations"],
            skills=tuple(skills_by_competency.get(number, ())),
        )
        for number, heading in sorted(competency_headings.items())
    )

    # A pagina da AREA e a da primeira competencia: e onde a estrutura
    # normativa comeca, e e inequivoca. Ver a nota de CNT_AREA_NAME.
    return BnccFramework(
        taxonomy_code=BNCC_TAXONOMY_CODE,
        taxonomy_version=taxonomy_version,
        area_code=CNT_AREA_CODE,
        area_name=CNT_AREA_NAME,
        area_page=competencies[0].page,
        competencies=competencies,
        extractor_version=EXTRACTOR_VERSION,
    )


def _competency_headings(
    pages: Sequence[str], window_start: int, window_end: int
) -> dict[int, dict]:
    """Cabecalhos de competencia DENTRO da janela da CNT.

    O enunciado e a primeira frase apos o cabecalho: o que vem depois e o
    comentario explicativo da BNCC, que nao e normativo.
    """
    found: dict[int, dict] = {}
    for page_number in range(window_start, window_end + 1):
        text = pages[page_number - 1] or ""
        for match in _COMPETENCY_HEADING.finditer(text):
            number = int(match.group(1))
            if number in found:
                continue
            tail = text[match.end() :]
            statement, dehyphenations = _normalize(_first_sentence(tail))
            if not statement:
                continue
            found[number] = {
                "statement": statement,
                "page": page_number,
                "dehyphenations": dehyphenations,
            }
    return found


def _first_sentence(text: str) -> str:
    flat = " ".join((text or "").split())
    parts = _SENTENCE_END.split(flat, maxsplit=1)
    return parts[0] if parts else flat


def _skills(
    pages: Sequence[str], window_start: int, window_end: int
) -> dict[int, list[BnccSkill]]:
    by_competency: dict[int, list[BnccSkill]] = {}
    for page_number in range(window_start, window_end + 1):
        text = pages[page_number - 1] or ""
        matches = list(_SKILL.finditer(text))
        for position, match in enumerate(matches):
            code = match.group(1)
            competency_number = int(match.group(2))
            end = (
                matches[position + 1].start()
                if position + 1 < len(matches)
                else len(text)
            )
            statement, dehyphenations = _normalize(text[match.end() : end])
            by_competency.setdefault(competency_number, []).append(
                BnccSkill(
                    code=code,
                    statement=statement,
                    competency_number=competency_number,
                    page=page_number,
                    dehyphenations=dehyphenations,
                    source_text_sha256=hashlib.sha256(
                        statement.encode("utf-8")
                    ).hexdigest(),
                )
            )
    for skills in by_competency.values():
        skills.sort(key=lambda skill: skill.code)
    return by_competency
