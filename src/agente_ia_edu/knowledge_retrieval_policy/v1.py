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

#: Papeis EDITORIAIS - funcao do chunk NA OBRA, ortogonal a ``chunk_type``.
#:
#:     chunk_type      forma e funcao pedagogica LOCAL (prosa, exercicio...)
#:     editorial_role  funcao EDITORIAL na obra (conteudo, gabarito, sumario)
#:
#: ``TEACHER_GUIDE`` e papel proprio, separado de ``ANSWER_KEY``: a regiao do
#: manual docente contem orientacao didatica que NAO e resposta.
#:
#: ``INDEX`` esta declarado e NAO foi observado nas tres obras do piloto -
#: zero ocorrencias de "Indice remissivo". Entra como regra, nao como numero.
EDITORIAL_ROLES: tuple[str, ...] = (
    "CONTENT",
    "TABLE_OF_CONTENTS",
    "INDEX",
    "ANSWER_KEY",
    "TEACHER_GUIDE",
    "REFERENCES",
    "FRONT_MATTER",
    "BACK_MATTER",
    "UNKNOWN",
)

#: Elegibilidade por papel e por proposito.
#:
#: ``ANSWER_KEY`` herda exatamente a regra de ``SOLUTION`` da Fase 3.1 -
#: fechado em PRACTICE e ASSESS, aberto em LEARN e AUTHOR. Sao dimensoes
#: ortogonais que convergem, e basta UMA fechar para fechar.
#:
#: ``UNKNOWN`` e ELEGIVEL em tudo, e a razao e uma assimetria: um falso
#: positivo esconde conteudo legitimo e ninguem percebe; um falso negativo
#: apenas mantem o estado atual.
#:
#: ``TEACHER_GUIDE`` so em AUTHOR: orientacao docente e insumo de autoria,
#: nao material de aprendizagem para o aluno.
_ELIGIBILITY_BY_ROLE: Mapping[str, Mapping[str, bool]] = MappingProxyType(
    {
        "CONTENT": MappingProxyType(
            {"LEARN": True, "PRACTICE": True, "ASSESS": True, "AUTHOR": True}
        ),
        "UNKNOWN": MappingProxyType(
            {"LEARN": True, "PRACTICE": True, "ASSESS": True, "AUTHOR": True}
        ),
        "ANSWER_KEY": MappingProxyType(
            {"LEARN": True, "PRACTICE": False, "ASSESS": False, "AUTHOR": True}
        ),
        "TEACHER_GUIDE": MappingProxyType(
            {"LEARN": False, "PRACTICE": False, "ASSESS": False, "AUTHOR": True}
        ),
        "REFERENCES": MappingProxyType(
            {"LEARN": False, "PRACTICE": False, "ASSESS": False, "AUTHOR": True}
        ),
        "TABLE_OF_CONTENTS": MappingProxyType(
            {"LEARN": False, "PRACTICE": False, "ASSESS": False, "AUTHOR": False}
        ),
        "INDEX": MappingProxyType(
            {"LEARN": False, "PRACTICE": False, "ASSESS": False, "AUTHOR": False}
        ),
        "FRONT_MATTER": MappingProxyType(
            {"LEARN": False, "PRACTICE": False, "ASSESS": False, "AUTHOR": False}
        ),
        "BACK_MATTER": MappingProxyType(
            {"LEARN": False, "PRACTICE": False, "ASSESS": False, "AUTHOR": False}
        ),
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
    "FILTERED_OUT_BY_EDITORIAL_ROLE",
    # Ha chunks indexados, mas NENHUM pertence ao corpus estatistico
    # elegivel. E diferente de indice vazio, e dizer "indice vazio" seria
    # mentir sobre o estado do corpus.
    "EMPTY_ELIGIBLE_CORPUS",
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

    # -- estrutura editorial (Fase 5.1b) --------------------------------
    #: Versao do detector editorial. Gravada POR CHUNK: coluna nula significa
    #: "nao processado por esta versao", e ``UNKNOWN`` com versao preenchida
    #: significa "classificado, e a evidencia nao bastou". Nao se confundem.
    editorial_detector_version: str = "v1"
    eligibility_by_role: Mapping[str, Mapping[str, bool]] = field(
        default_factory=lambda: _ELIGIBILITY_BY_ROLE
    )

    def snapshot(self) -> dict[str, object]:
        """Dict JSON-serializavel com TUDO que afeta um score.

        A conversao e RECURSIVA porque ``eligibility_by_role`` e um mapa de
        mapas: converter so o nivel de cima deixava ``mappingproxy`` dentro, e
        o Pydantic recusava serializar a resposta inteira.
        """

        def plain(value: object) -> object:
            if isinstance(value, Mapping):
                return {key: plain(item) for key, item in value.items()}
            return value

        return {item.name: plain(getattr(self, item.name)) for item in fields(self)}


POLICY = RetrievalPolicyV1()


def editorial_role_is_eligible(role: str | None, purpose: str | None) -> bool:
    """O papel editorial admite recuperacao neste proposito?

    Falha ABERTA, ao contrario de ``solution_is_visible``, e a inversao e
    deliberada. Aqui o erro caro e o oposto: esconder conteudo legitimo e
    invisivel - ninguem percebe um resultado que nao veio -, enquanto deixar
    passar material editorial apenas mantem o estado de hoje. Papel que a
    politica nao conhece e ELEGIVEL, por decisao explicita.

    ``editorial_role`` e um ROTULO, nunca uma exclusao fisica: pagina,
    origem, literal restrito, hashes e rastreabilidade seguem intactos.
    """
    mapa = POLICY.eligibility_by_role.get(role or "UNKNOWN")
    if mapa is None:
        return True
    if purpose is None or purpose == UNKNOWN_PURPOSE:
        # Proposito AUSENTE = contexto mais restritivo entre os declarados.
        #
        # Nao e "fecha tudo" nem "abre tudo". ``CONTENT`` e ``UNKNOWN``, que
        # sao elegiveis nos quatro propositos, continuam elegiveis - fecha-los
        # tornaria toda busca sem proposito vazia. E ``ANSWER_KEY``, fechado em
        # PRACTICE, fica fechado - exatamente a regra da Fase 3.1, em que
        # proposito desconhecido nao entrega gabarito.
        return all(mapa.get(declarado, False) for declarado in RETRIEVAL_PURPOSES)
    return mapa.get(purpose, False)


def statistical_corpus_roles() -> tuple[str, ...]:
    """Papeis que compoem o CORPUS ESTATISTICO - a base de ``df``, ``N`` e
    ``avgdl``.

    DERIVADO da tabela de elegibilidade, nao uma segunda lista: sao os papeis
    com pelo menos um proposito aberto. Duas listas paralelas sairiam de
    sincronia; esta nao pode.

    Consequencia: aparato de navegacao - sumario, indice, front e back matter -
    fica FORA da estatistica, porque nunca e recuperavel em proposito algum.
    Era ele que inflava o ``df`` dos termos topicos: no baseline,
    ``df(estequiometria) = 74`` incluia ocorrencias de sumario.

    A definicao e determinista, versionada com a politica, observavel na
    resposta e participa do ``query_fingerprint``.
    """
    return tuple(
        sorted(
            role
            for role in EDITORIAL_ROLES
            if any(
                editorial_role_is_eligible(role, purpose)
                for purpose in RETRIEVAL_PURPOSES
            )
        )
    )


def solution_is_visible(purpose: str | None) -> bool:
    """``SOLUTION`` pode ser recuperado neste proposito?

    Falha FECHADA. O default do ``.get`` e o fechamento: proposito ausente,
    desconhecido ou recem-inventado nao recupera gabarito. Uma permissao nunca
    nasce de omissao - mesma disciplina de ``rights.may_expose_literal_text``.
    """
    return POLICY.solution_visibility_by_purpose.get(purpose or UNKNOWN_PURPOSE, False)
