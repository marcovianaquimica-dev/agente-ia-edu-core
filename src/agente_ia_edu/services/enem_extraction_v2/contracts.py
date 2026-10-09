"""Contratos de dados da EXTRACAO ENEM V2 (PROTOTIPO EXPERIMENTAL).

Nenhum destes tipos e persistido. Nao ha migration. Nao ha tabela.
Sao estruturas em memoria para permitir comparar V1 x V2 e para
sustentar a proposta de contrato ``QuestionCandidate <-> AssetCandidate``.

O vocabulario e deliberadamente de CANDIDATO: nada aqui afirma que a
questao esta correta. Correcao so se estabelece por adjudicacao humana.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- como a camada de texto foi obtida -----------------------------------
MOTOR_PYPDF = "pypdf"
MOTOR_PYMUPDF = "pymupdf"

# --- como o ativo foi detectado ------------------------------------------
ATIVO_RASTER = "RASTER_IMAGE"          # pymupdf get_images + get_image_rects
ATIVO_VETORIAL = "VECTOR_CLUSTER"      # aglomerado de get_drawings

# --- como o ativo foi associado a questao --------------------------------
ASSOC_CONTIDO = "CONTIDO_NA_REGIAO"    # bbox do ativo dentro da regiao do item
ASSOC_SOBREPOSTO = "SOBREPOE_REGIAO"   # interseccao parcial
ASSOC_MESMA_PAGINA = "APENAS_MESMA_PAGINA"   # sem geometria util
ASSOC_NENHUMA = "ORFAO"

# --- por que um candidato nao esta completo ------------------------------
# O nome da constante e o seu valor sao iguais de proposito: quem le
# "ALTERNATIVAS_INCOMPLETAS" num log precisa achar a constante grepando
# exatamente isso. Ha teste que falha se divergirem.
ALTERNATIVAS_INCOMPLETAS = "ALTERNATIVAS_INCOMPLETAS"
GABARITO_AUSENTE = "GABARITO_AUSENTE"
ENUNCIADO_VAZIO = "ENUNCIADO_VAZIO"
NUMERO_DUPLICADO = "NUMERO_DUPLICADO"
GABARITO_AMBIGUO = "GABARITO_AMBIGUO"
TEXTO_ILEGIVEL = "TEXTO_ILEGIVEL"
ULTIMA_ALTERNATIVA_ANOMALA = "ULTIMA_ALTERNATIVA_ANOMALA"
DEPENDENCIA_VISUAL_NAO_RESOLVIDA = "DEPENDENCIA_VISUAL_NAO_RESOLVIDA"
FRAGMENTACAO_DE_PALAVRA_SUSPEITA = "FRAGMENTACAO_DE_PALAVRA_SUSPEITA"


@dataclass(frozen=True)
class Caixa:
    """Retangulo em pontos PDF, origem no canto superior esquerdo."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def largura(self) -> float:
        return self.x1 - self.x0

    @property
    def altura(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.largura) * max(0.0, self.altura)

    def uniao(self, outra: "Caixa") -> "Caixa":
        return Caixa(min(self.x0, outra.x0), min(self.y0, outra.y0),
                     max(self.x1, outra.x1), max(self.y1, outra.y1))

    def intersecta(self, outra: "Caixa", folga: float = 0.0) -> bool:
        return not (self.x1 + folga < outra.x0 or outra.x1 + folga < self.x0
                    or self.y1 + folga < outra.y0 or outra.y1 + folga < self.y0)

    def area_intersecao(self, outra: "Caixa") -> float:
        dx = min(self.x1, outra.x1) - max(self.x0, outra.x0)
        dy = min(self.y1, outra.y1) - max(self.y0, outra.y0)
        return dx * dy if dx > 0 and dy > 0 else 0.0


@dataclass(frozen=True)
class AssetCandidate:
    """Um elemento visual candidato, com geometria e proveniencia."""

    pagina: int
    caixa: Caixa
    tipo: str                      # ATIVO_RASTER | ATIVO_VETORIAL
    metodo_deteccao: str           # chamada concreta que o produziu
    n_primitivas: int = 1          # quantos objetos do PDF formam este ativo
    questao: int | None = None     # numero do item associado, se houver
    metodo_associacao: str = ASSOC_NENHUMA
    confianca_associacao: float = 0.0
    identificador: str = ""        # xref/hash, para rastreabilidade


@dataclass(frozen=True)
class OptionCandidate:
    letra: str
    texto: str
    inicio: int                    # posicao no corpo da questao
    forma: str                     # como o marcador aparecia no texto cru


@dataclass
class QuestionCandidate:
    """Um item candidato. Nada aqui afirma correcao."""

    numero: int
    enunciado: str
    opcoes: list[OptionCandidate] = field(default_factory=list)
    gabarito: str | None = None
    gabarito_por_lingua: dict[str, str] = field(default_factory=dict)
    pagina_inicio: int | None = None
    pagina_fim: int | None = None
    atravessa_pagina: bool = False
    ativos: list[AssetCandidate] = field(default_factory=list)
    motor_texto: str = ""
    forma_alternativas: str = ""
    problemas: list[str] = field(default_factory=list)
    ocorrencia: int = 1            # 1 ou 2, para os itens 1-5 do dia 1 (C6)
    caixa_ancora: Caixa | None = None

    @property
    def tem_cinco_opcoes(self) -> bool:
        return ([o.letra for o in self.opcoes] == list("ABCDE")
                and all(o.texto.strip() for o in self.opcoes))

    @property
    def completo(self) -> bool:
        """Reconstrucao estruturalmente completa. NAO e afirmacao de correcao."""
        return (bool(self.enunciado.strip()) and self.tem_cinco_opcoes
                and self.gabarito is not None and not self.problemas)

    @property
    def alternativas_texto(self) -> str | None:
        if not self.opcoes:
            return None
        return "\n".join(f"{o.letra}) {o.texto}" for o in self.opcoes)


@dataclass
class ResultadoCaderno:
    """Resultado da V2 para um caderno (ano x dia)."""

    ano: int
    dia: int
    caderno: str
    motor_escolhido: str
    motivo_escolha: str
    esperadas: int
    questoes: list[QuestionCandidate] = field(default_factory=list)
    ativos_orfaos: list[AssetCandidate] = field(default_factory=list)
    gabarito_entradas: int = 0
    gabarito_ambiguos: list[int] = field(default_factory=list)
    gabarito_orfaos: list[int] = field(default_factory=list)
    avaliacao_motores: dict = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)

    @property
    def detectadas(self) -> int:
        return len({q.numero for q in self.questoes})

    @property
    def com_cinco_opcoes(self) -> int:
        return sum(1 for q in self.questoes if q.tem_cinco_opcoes)

    @property
    def com_gabarito(self) -> int:
        return sum(1 for q in self.questoes if q.gabarito is not None)

    @property
    def com_ativo(self) -> int:
        return sum(1 for q in self.questoes if q.ativos)

    @property
    def completas(self) -> int:
        return sum(1 for q in self.questoes if q.completo)
