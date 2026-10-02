"""Mapeamento provisorio de escola -> GRE/Municipio.

FICTICIO: a SEDUC-PB ainda nao forneceu a planilha oficial de
escola -> municipio -> GRE (ver spec, "Riscos e decisoes em aberto").
Enquanto isso nao chega, qualquer escola fora do mapeamento abaixo cai no
fallback "nao classificada" - nada quebra, so fica sem agrupamento
regional ate o dado real chegar.
"""
from __future__ import annotations

NAO_CLASSIFICADA_GRE = "GRE nao classificada"
NAO_CLASSIFICADA_MUNICIPIO = "Municipio nao classificado"

MAPEAMENTO_ESCOLA_GRE_MUNICIPIO: dict[str, tuple[str, str]] = {
    "6f26cd3c-63d5-4509-a041-13714f75e53e": ("1a GRE - Joao Pessoa", "Joao Pessoa"),
}


def gre_de(escola_id: str) -> str:
    return MAPEAMENTO_ESCOLA_GRE_MUNICIPIO.get(
        escola_id, (NAO_CLASSIFICADA_GRE, NAO_CLASSIFICADA_MUNICIPIO)
    )[0]


def municipio_de(escola_id: str) -> str:
    return MAPEAMENTO_ESCOLA_GRE_MUNICIPIO.get(
        escola_id, (NAO_CLASSIFICADA_GRE, NAO_CLASSIFICADA_MUNICIPIO)
    )[1]
