"""CODIGO E DO SISTEMA; NOME E DO PROFESSOR.

O QUE O PROFESSOR VE HOJE NO BANCO DE QUESTOES
===============================================
    CHEMISTRY > CHEMISTRY-PHYSICAL > CHEMISTRY-PHYSICAL-STOICHIOMETRY
    EASY
    placeholder do filtro de conteudo: "codigo curriculum-v2"

E o contrato interno do sistema impresso na tela de quem da aula.

A FONTE JA EXISTE, E NAO E UM DICIONARIO NOVO
==============================================
`catalog_nodes` ja guarda, para cada codigo, um `name` escrito por gente:

    CHEMISTRY                          Quimica
    CHEMISTRY-PHYSICAL                 Fisico-Quimica
    CHEMISTRY-PHYSICAL-STOICHIOMETRY   Estequiometria e calculos quimicos

Nao ha traducao a inventar: ha uma coluna a usar. Um mapa paralelo escrito a
mao divergiria do catalogo no primeiro conteudo novo, e seria uma segunda
verdade sobre o curriculo - exatamente o que o Nucleo nao faz.

O QUE ESTE MODULO NAO FAZ
==========================
Nao traduz, nao adivinha e nao humaniza codigo. Um codigo sem no no catalogo
volta como ele mesmo: mostrar o codigo e honesto, chutar um nome nao.

E nao toca em contrato nenhum. O codigo canonico continua no banco, nos
filtros, nas requisicoes e nas respostas - este modulo so acrescenta COMO
chamar aquilo na tela.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.catalog import CatalogNode

# A UNICA TRADUCAO ESCRITA A MAO deste modulo, e por nao haver fonte:
# `recommended_difficulty` e um enum de tres valores, sem tabela por tras.
DIFICULDADES = {
    "EASY": "Fácil",
    "MEDIUM": "Médio",
    "HARD": "Difícil",
}


def rotulo_de_dificuldade(codigo: str | None) -> str | None:
    """"Fácil" / "Médio" / "Difícil" - ou o proprio codigo, se for outro.

    `None` continua `None`: 514 das 595 questoes do acervo nao tem dificuldade
    atribuida, e inventar uma faixa para elas seria inventar dado.
    """
    if not codigo:
        return None
    return DIFICULDADES.get(codigo, codigo)


class RotulosDaTaxonomia:
    """De codigos canonicos para os nomes que o catalogo ja guarda."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def para(self, codigos: Iterable[str] | None) -> dict[str, str]:
        """{codigo: rotulo} para todos de uma vez.

        UMA consulta, sempre. Uma pagina do banco tem 20 questoes com ate
        quatro codigos cada; resolver um por vez seriam oitenta consultas para
        desenhar uma lista.
        """
        alvos = {c.strip() for c in (codigos or []) if c and str(c).strip()}
        if not alvos:
            return {}
        linhas = (await self._session.execute(
            select(CatalogNode.code, CatalogNode.name)
            .where(CatalogNode.code.in_(alvos))
        )).all()
        achados = {
            codigo: nome.strip()
            for codigo, nome in linhas
            if nome and nome.strip()
        }
        # Quem nao tem no - ou tem no sem nome - volta como ele mesmo.
        return {codigo: achados.get(codigo, codigo) for codigo in alvos}


__all__ = ["DIFICULDADES", "RotulosDaTaxonomia", "rotulo_de_dificuldade"]
