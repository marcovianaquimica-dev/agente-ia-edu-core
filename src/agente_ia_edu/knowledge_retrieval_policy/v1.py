"""Politica de recuperacao v1 - toda constante que afeta um score mora aqui.

Nenhum destes numeros aparece espalhado pelo codigo. O snapshot integral vai
em toda resposta de busca e, mais tarde, em ``knowledge_packs.retrieval_params``,
tornando todo resultado reproduzivel e explicavel.

CONTRATO ESTAVEL, BACKEND SUBSTITUIVEL (ajuste 1 da Fase 5)
===========================================================

``lexical_backend = "OWN_INVERTED_INDEX"`` e uma DECLARACAO, nao um
compromisso. O indice invertido proprio nao e parte permanente da
arquitetura: foi escolhido para o piloto porque roda identico em SQLite e
PostgreSQL, porque a normalizacao fica versionada em codigo legivel em vez de
DDL de ``TEXT SEARCH CONFIGURATION``, e porque cada parcela do score e
calculada por codigo nosso.

Nenhuma dessas razoes e eterna. Acima de ~10^6 chunks o desenho deixa de
servir, e PostgreSQL FTS, OpenSearch ou outro mecanismo poderao substitui-lo
**sem alterar consumidor algum**: o contrato publico e
``LexicalSearcher.search()`` devolvendo ``(chunk_id, rank, score, explanation)``,
que e exatamente o que o RRF da Fase 7 e o Knowledge Pack consomem. O campo
existe na resposta para que a troca seja um dado OBSERVAVEL - quem comparar
duas medicoes vera qual backend produziu cada uma.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Mapping

POLICY_VERSION = "v1"

#: Propositos de recuperacao declarados. Na Fase 5 o UNICO efeito do proposito
#: e o portao de ``SOLUTION``; boosts por tipo e composicao do Pack sao Fase 7.
RETRIEVAL_PURPOSES: tuple[str, ...] = ("LEARN", "PRACTICE", "ASSESS", "AUTHOR")

#: Proposito ausente. Nao e um valor aceito na entrada - e o que o sistema
#: assume quando ninguem declarou, e ele FECHA ``SOLUTION``.
UNKNOWN_PURPOSE = "UNKNOWN"

#: Stopwords de CORPO: palavras de funcao do portugues. Deliberadamente curta -
#: o idf ja pune termo comum, e uma stoplist grande remove vocabulario que
#: alguem vai querer buscar. "nao" entra porque inverte sentido sem localizar
#: nada.
BODY_STOPWORDS: frozenset[str] = frozenset(
    {
        "ainda", "alem", "ao", "aos", "apos", "aquela", "aquele", "aqui", "assim",
        "ate", "cada", "com", "como", "contra", "cujo", "dai", "dar", "das",
        "dela", "dele", "demais", "dentro", "depois", "desde", "desta", "deste",
        "dos", "ela", "elas", "ele", "eles", "entao", "entre", "era", "essa",
        "esse", "esta", "estao", "este", "eu", "foi", "fora", "foram", "isso",
        "isto", "jaa", "mais", "mas", "mesmo", "meu", "muito", "nao", "nas",
        "nem", "nos", "nossa", "nosso", "num", "numa", "ou", "para", "pela",
        "pelas", "pelo", "pelos", "per", "pode", "podem", "por", "porque",
        "pois", "qual", "quais", "quando", "quanto", "que", "sao", "sem", "sendo",
        "ser", "seu", "seus", "sobre", "sua", "suas", "tambem", "tem", "tendo",
        "ter", "toda", "todas", "todo", "todos", "uma", "umas", "uns", "voce",
    }
)

#: Stopwords de TITULO: boilerplate ESTRUTURAL, que o parser gerou e a obra
#: nao escreveu. Medido no livro real: ``chapter`` aparece em 2.662 dos 2.668
#: headings, e 70,5% dos headings sao apenas ``Chapter N``. Pesar titulo sem
#: remover isto amplificaria ruido do proprio sistema.
#:
#: Nao vale para o corpo: no corpo, "capitulo" e vocabulario da obra.
HEADING_STOPWORDS: frozenset[str] = frozenset(
    {"chapter", "capitulo", "capitulos", "objetivo", "objetivos", "unidade", "secao"}
)

_SOLUTION_VISIBILITY: Mapping[str, bool] = MappingProxyType(
    {
        # Em LEARN e AUTHOR o gabarito e material legitimo: explica o
        # procedimento e serve de referencia para autoria.
        "LEARN": True,
        "AUTHOR": True,
        # Em PRACTICE e ASSESS entregar gabarito destrui o proposito.
        "PRACTICE": False,
        "ASSESS": False,
    }
)

#: Razoes nomeadas para um resultado vazio. STRICT_CORPUS exige que "nada
#: encontrado" seja uma resposta EXPLICADA, nunca uma lista vazia muda e nunca
#: um relaxamento silencioso de filtro.
EMPTY_RESULT_REASONS: tuple[str, ...] = (
    "EMPTY_QUERY",
    "ALL_TERMS_BELOW_MIN_LENGTH",
    "NO_LEXICAL_MATCH",
    "FILTERED_OUT_BY_RIGHTS",
    "FILTERED_OUT_BY_PURPOSE",
    "FILTERED_OUT_BY_CHUNK_TYPE",
    "FILTERED_OUT_BY_SOURCE_KIND",
    "FILTERED_OUT_BY_CONTENT_NODE",
    "EMPTY_INDEX",
)


@dataclass(frozen=True)
class RetrievalPolicyV1:
    version: str = POLICY_VERSION
    #: Versao da NORMALIZACAO lexical. Gravada linha por linha no indice, para
    #: que "este chunk foi indexado com as regras atuais?" seja respondivel
    #: por SQL em vez de por fe.
    normalizer_version: str = "v1"
    retrieval_mode: str = "STRICT_CORPUS"
    #: Ver o docstring do modulo. Contrato estavel, backend substituivel.
    lexical_backend: str = "OWN_INVERTED_INDEX"

    # -- BM25F ----------------------------------------------------------
    k1: float = 1.2
    b: float = 0.75
    #: NEUTRO de proposito. 70,5% dos headings reais sao so "Chapter N";
    #: subir este peso antes do A/B do Calibration Set amplificaria
    #: boilerplate. Sobe quando a medicao mandar, nao quando parecer razoavel.
    heading_weight: float = 1.0

    # -- frase e proximidade, sobre posicoes INTEGRAS -------------------
    #: Folga em palavras entre o espacamento que a CONSULTA pede e o que o
    #: documento oferece. A consulta "concentracao das solucoes" espera 2
    #: posicoes entre os termos; com folga 1, o documento casa com 1, 2 ou 3.
    phrase_slack: int = 1
    phrase_bonus_weight: float = 0.5
    proximity_window: int = 5
    proximity_bonus_weight: float = 0.25

    # -- tokenizacao ----------------------------------------------------
    min_term_length: int = 3
    #: Igual ao VARCHAR(80) de ``knowledge_chunk_terms.term``: o termo e
    #: truncado aqui, nao no banco, para que o indice e a consulta trunquem
    #: identicamente.
    max_term_length: int = 80

    # -- candidatura, paginacao, diversidade ----------------------------
    candidate_cap: int = 2000
    default_limit: int = 20
    max_limit: int = 100
    #: None = NENHUM teto automatico de diversidade (decisao da Fase 5).
    #: Limitar antes de medir esconderia a medicao que a fase produz.
    max_chunks_per_source: int | None = None
    min_distinct_sources: int = 2

    # -- projecao -------------------------------------------------------
    #: Cada nivel do heading_path sai truncado. A Fase 3 produziu titulos
    #: contaminados ("Chapter 23 - ... Objetivos do capitulo - Equacionar...");
    #: sem o corte, um paragrafo escaparia pelo campo de titulo.
    heading_level_max_chars: int = 120

    solution_visibility_by_purpose: Mapping[str, bool] = field(
        default_factory=lambda: _SOLUTION_VISIBILITY
    )

    def snapshot(self) -> dict[str, object]:
        """Dict JSON-serializavel com TUDO que afeta um score."""
        out: dict[str, object] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            out[item.name] = dict(value) if isinstance(value, Mapping) else value
        return out


POLICY = RetrievalPolicyV1()


def solution_is_visible(purpose: str | None) -> bool:
    """``SOLUTION`` pode ser recuperado neste proposito?

    Falha FECHADA. O default do ``.get`` e o fechamento: proposito ausente,
    desconhecido ou recem-inventado nao recupera gabarito. Uma permissao nunca
    nasce de omissao - mesma disciplina de ``rights.may_expose_literal_text``.
    """
    return POLICY.solution_visibility_by_purpose.get(purpose or UNKNOWN_PURPOSE, False)
