"""Contrato de referencia BNCC - v1, imutavel.

Vive em contrato PROPRIO, e nao dentro do Knowledge Engine nem dentro do
dominio de curriculo, porque os dois precisam dele e nenhum dos dois o possui:

  * o extrator e o chunker (engine) produzem referencias;
  * a semeadura de taxonomia e a curadoria (curriculo) consomem e produzem;
  * o Knowledge Pack ve ``BnccReference`` e nada mais.

A DECISAO NORMATIVA QUE ESTE MODULO FIXA (spec 22.3)
-----------------------------------------------------
``EM13CNT301`` isolado NAO e identidade normativa eterna. O mesmo codigo pode
existir com enunciado diferente em versoes diferentes da BNCC, e tratar o
codigo como chave eterna faria uma curadoria feita sob a BNCC de 2018 parecer
valida sob uma BNCC futura sem que ninguem tenha revisado nada.

A identidade conceitual e a TRIPLA:

    taxonomy_code + taxonomy_version + node_code

``knowledge_chunks.bncc_node_codes`` continua guardando so o codigo - sua
funcao e recuperacao, e o codigo e estavel entre versoes para esse fim. Toda
referencia NORMATIVA ou AUDITAVEL carrega a tripla explicita.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CONTRACT_VERSION = "v1"

#: ``Taxonomy.code``. Ja consultado por ``repositories/questions.py`` e
#: ``services/questions.py`` desde antes desta fase.
BNCC_TAXONOMY_CODE = "bncc"

#: ``Taxonomy.version`` da BNCC do Ensino Medio homologada em 2018.
BNCC_VERSION_EM_2018 = "EM-2018"

#: Prefixo dos codigos de habilidade da area de Ciencias da Natureza e suas
#: Tecnologias. O 8o caractere do codigo (o digito seguinte) e o numero da
#: competencia especifica - ver ``bncc_extraction``.
CNT_AREA_CODE = "EM13CNT"

#: Tipos de relacao entre no de curriculo e habilidade da BNCC.
#: ``PREREQUISITE`` fica FORA de proposito: pre-requisito ja pertence ao grafo
#: curricular (``CatalogNodePrerequisite``), e um tipo novo de relacao BNCC so
#: entra quando houver necessidade pedagogica concreta.
RELATION_TYPES: tuple[str, ...] = ("PRIMARY", "SUPPORTING")

_URN_SEPARATOR = ":"


@dataclass(frozen=True)
class BnccNodeRef:
    """Referencia normativa a um no da BNCC - a tripla completa.

    Falha FECHADA: nao ha default de versao. Uma referencia sem versao nao e
    auditavel, e aceita-la seria pior que recusa-la.
    """

    taxonomy_code: str
    taxonomy_version: str
    node_code: str

    def __post_init__(self) -> None:
        for name in ("taxonomy_code", "taxonomy_version", "node_code"):
            value = getattr(self, name)
            if not value or not str(value).strip():
                raise ValueError(
                    f"BnccNodeRef exige {name}: a identidade normativa e a tripla "
                    "taxonomy_code + taxonomy_version + node_code"
                )
            if _URN_SEPARATOR in str(value):
                raise ValueError(f"{name} nao pode conter {_URN_SEPARATOR!r}")

    def as_urn(self) -> str:
        return _URN_SEPARATOR.join(
            (self.taxonomy_code, self.taxonomy_version, self.node_code)
        )

    @classmethod
    def from_urn(cls, urn: str) -> BnccNodeRef:
        parts = (urn or "").split(_URN_SEPARATOR)
        if len(parts) != 3:
            raise ValueError(
                f"URN de referencia BNCC invalida: {urn!r}; "
                "esperado 'taxonomy_code:taxonomy_version:node_code'"
            )
        return cls(
            taxonomy_code=parts[0], taxonomy_version=parts[1], node_code=parts[2]
        )


@dataclass(frozen=True)
class BnccReference:
    """O que o Knowledge Pack ve em ``topic.bncc[]``.

    NAO carrega identificador de banco algum - nem id de no, nem id de
    taxonomia, nem id de vinculo. O Pack declara a forma; o port de
    ``curriculum_ports`` entrega os dados; a tabela de curadoria e detalhe
    que nenhum dos dois nomeia (spec 22.8).

    A BNCC e ``OFFICIAL_PUBLIC``, entao ``statement`` pode ser citado - ao
    contrario do texto de livro comercial.
    """

    code: str
    statement: str
    competency_code: str
    competency_statement: str
    taxonomy_code: str
    taxonomy_version: str
    relation_type: str
    node_ref: BnccNodeRef = field(init=False)

    def __post_init__(self) -> None:
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(
                f"relation_type invalido: {self.relation_type!r}; "
                f"esperado um de {list(RELATION_TYPES)}"
            )
        object.__setattr__(
            self,
            "node_ref",
            BnccNodeRef(
                taxonomy_code=self.taxonomy_code,
                taxonomy_version=self.taxonomy_version,
                node_code=self.code,
            ),
        )
