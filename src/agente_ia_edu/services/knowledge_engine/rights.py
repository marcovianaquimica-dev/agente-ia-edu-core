"""Politica de direitos e autoridade de uma fonte de conhecimento.

Funcao pura: sem sessao, sem I/O, sem banco. Isso e deliberado - a politica e
a parte do subsistema que menos pode estar implicita dentro de um service, e
a que mais precisa ser legivel e testavel sozinha.

DOIS EIXOS ORTOGONAIS, que nunca devem ser colapsados num campo so:

``rights_class``    - o que podemos FAZER com a fonte. E politica, e por isso
                      tem CheckConstraint no banco.
``authority_level`` - quanto CONFIAMOS na fonte. E vocabulario que ainda vai
                      crescer, e por isso a coluna e string livre; a validacao
                      vive aqui, onde um valor novo custa codigo e nao
                      migracao.

Nenhum se deriva do outro: uma apostila propria (``OWN``/``OTHER``) pode ter
menos autoridade que um livro comercial cujo texto nao podemos reproduzir, e
um artigo academico pode ser ``COMMERCIAL_REFERENCE``/``ACADEMIC``.
"""

from __future__ import annotations

from uuid import UUID

#: Fechado, e espelha exatamente o CheckConstraint de ``knowledge_sources``.
RIGHTS_CLASSES: tuple[str, ...] = (
    "COMMERCIAL_REFERENCE",
    "LICENSED",
    "OWN",
    "PUBLIC_DOMAIN",
    "OFFICIAL_PUBLIC",
)

#: Validado aqui, nao no banco - ver o docstring do modulo.
AUTHORITY_LEVELS: tuple[str, ...] = (
    "OFFICIAL",
    "ACADEMIC",
    "COMMERCIAL_TEXTBOOK",
    "OWN",
    "OTHER",
)

SOURCE_KINDS: tuple[str, ...] = (
    "TEXTBOOK",
    "CURRICULUM_FRAMEWORK",
    "OWN_MATERIAL",
    "ARTICLE",
    "OTHER",
)

#: Limite de citacao literal para fonte NAO comercial (spec 10.4).
NON_COMMERCIAL_EXCERPT_LIMIT = 300

_COMMERCIAL = "COMMERCIAL_REFERENCE"


class KnowledgeRightsViolation(ValueError):
    """A fonte descrita viola a politica de direitos.

    Levantada ANTES de qualquer escrita, para que o chamador receba um motivo
    nomeado. O CheckConstraint do banco continua sendo a ultima linha de
    defesa, nao a primeira - ver
    ``tests/test_knowledge_sources_postgresql.py``.
    """


def validate_source_rights(
    *,
    rights_class: str,
    authority_level: str,
    source_kind: str,
    educational_resource_id: UUID | None = None,
) -> None:
    """Valida a descricao de uma fonte, ou levanta ``KnowledgeRightsViolation``."""
    if rights_class not in RIGHTS_CLASSES:
        raise KnowledgeRightsViolation(
            f"rights_class invalido: {rights_class!r}; esperado um de {list(RIGHTS_CLASSES)}"
        )
    if authority_level not in AUTHORITY_LEVELS:
        raise KnowledgeRightsViolation(
            f"authority_level invalido: {authority_level!r}; "
            f"esperado um de {list(AUTHORITY_LEVELS)}"
        )
    if source_kind not in SOURCE_KINDS:
        raise KnowledgeRightsViolation(
            f"source_kind invalido: {source_kind!r}; esperado um de {list(SOURCE_KINDS)}"
        )

    # A trava central do subsistema. Sem EducationalResource, uma fonte
    # comercial nao tem caminho algum ate o aluno: nao pode ser alvo de
    # ContentResourceLink, nao vira TheoryMaterial, nao vira
    # MaterialAssignment. A regra e TOPOLOGICA, e esta checagem so existe para
    # dar uma mensagem decente antes de o banco dizer a mesma coisa.
    if rights_class == _COMMERCIAL and educational_resource_id is not None:
        raise KnowledgeRightsViolation(
            "uma fonte COMMERCIAL_REFERENCE nao pode ter educational_resource_id: "
            "existir como EducationalResource e o que abriria caminho para ela "
            "chegar ao aluno via TheoryMaterial/MaterialAssignment"
        )


def may_expose_literal_text(rights_class: str) -> bool:
    """A fonte admite citacao literal na saida?

    Falha FECHADA: uma classe desconhecida devolve False. Uma permissao nunca
    pode nascer de omissao.

    Nao usado na Fase 2 (nao existe chunk nem Pack ainda). Esta aqui porque e
    a definicao da politica; o validador do Knowledge Pack passa a consulta-la
    na Fase 8.
    """
    if rights_class not in RIGHTS_CLASSES:
        return False
    return rights_class != _COMMERCIAL


def max_excerpt_chars(rights_class: str) -> int | None:
    """Tamanho maximo de um excerpt, ou ``None`` quando nenhum e permitido.

    ``None`` significa "nenhum excerpt", nao "sem limite" - mesma razao de
    falha fechada de ``may_expose_literal_text``.
    """
    if not may_expose_literal_text(rights_class):
        return None
    return NON_COMMERCIAL_EXCERPT_LIMIT


def may_use_literal_in_processing(rights_class: str) -> bool:
    """O literal pode ser LIDO no processamento interno desta fonte?

    TRES ZONAS, E ELAS NAO SE CONFUNDEM
    ===================================

    A politica de direitos do piloto distingue onde o texto da obra pode
    estar. Confundir as zonas e o erro que vaza obra comercial, e por isso
    cada uma tem a sua funcao:

    1. PROCESSAMENTO INTERNO - extrator, chunker, indexador, embedder e o
       construtor de contexto leem o literal. Sem isso nao ha corpus.
       E esta funcao.

    2. CHAMADA AO PROVIDER - o literal necessario a construcao do contexto
       viaja para o fornecedor de IA, condicionado aos direitos e licencas
       aplicaveis. Ver ``may_send_literal_to_provider``.

    3. SAIDA PUBLICA - resposta de endpoint, artefato de inspecao, planilha,
       relatorio, log nao protegido. Aqui obra comercial NUNCA aparece.
       Ver ``may_expose_literal_text``.

    Falha FECHADA: classe desconhecida nao autoriza leitura. Uma permissao
    nunca nasce de omissao.
    """
    return rights_class in RIGHTS_CLASSES


def may_send_literal_to_provider(rights_class: str) -> bool:
    """O literal desta fonte pode ir ao fornecedor de IA?

    DECISAO EXPLICITA DO PILOTO, e nao um efeito colateral.

    Enviar texto de obra comercial a um terceiro e exposicao, e precisa ser
    dita em voz alta em vez de acontecer por omissao. No piloto ela ja
    ocorre desde a Fase 6 - os 5.911 embeddings foram gerados enviando o
    ``canonical_text`` de todas as fontes ao provider -, e a construcao de
    contexto para geracao repete exatamente o mesmo ato.

    As quatro fontes do piloto admitem esse envio. A funcao existe separada
    de ``may_use_literal_in_processing`` porque sao perguntas diferentes:
    uma licenca pode permitir indexar localmente e proibir transmitir a
    terceiro. Quando essa fonte existir, ela devolve False AQUI, e o
    construtor de contexto passa a usar so metadados para ela - sem que
    nenhuma outra camada precise mudar.

    Falha FECHADA, pela mesma razao das demais.
    """
    return rights_class in RIGHTS_CLASSES
