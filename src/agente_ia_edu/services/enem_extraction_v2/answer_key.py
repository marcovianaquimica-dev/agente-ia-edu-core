"""Leitura da folha oficial de gabarito (C4). PROTOTIPO EXPERIMENTAL.

A V1 usa ``^\\s*(\\d{2,3})\\s+([A-E])\\s*$`` e perde duas classes distintas:

C4a - **numero de um digito**. Exige 2 ou 3 digitos; os itens 6 a 9 sao
      impressos com um digito e nunca casam. Medido: 4 itens perdidos por
      caderno de dia 1, nos 6 anos.

C4b - **bloco de lingua estrangeira**. Os itens 1-5 do dia 1 tem DUAS
      respostas na mesma linha, uma por idioma. Medido em 2025 D1:

          'INGLÊS ESPANHOL'
          '1 D B'
          '2 D A'

      A regex exige fim de linha depois de uma unica letra, entao ``'1 D B'``
      nunca casa. Relaxar so o numero recupera 6-9 e **nao** recupera 1-5.

Conforme instruido, as duas correcoes ficam SEPARADAS. A leitura simples
produz ``respostas``; a leitura do bloco de idioma produz
``respostas_por_lingua`` e os numeros envolvidos entram em ``ambiguos``.
Nenhuma das duas e fundida: decidir como representar o item 1-5 e desenho de
contrato (C6), nao correcao de regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Uma unica resposta na linha. 1 a 3 digitos (C4a).
PADRAO_SIMPLES = re.compile(r"(?m)^[ \t]*(\d{1,3})[ \t]+([A-E])[ \t]*$")

# Duas respostas na mesma linha (C4b). Deliberadamente so DUAS: tres ou mais
# nunca foi observado, e aceitar N abriria porta para ler uma tabela qualquer.
PADRAO_DUPLO = re.compile(r"(?m)^[ \t]*(\d{1,3})[ \t]+([A-E])[ \t]+([A-E])[ \t]*$")

# Cabecalho que nomeia as duas colunas. A ordem lida aqui e a ordem usada.
PADRAO_CABECALHO_LINGUA = re.compile(
    r"(?mi)^[ \t]*(INGL[EÊ]S|ESPANHOL)[ \t]+(INGL[EÊ]S|ESPANHOL)[ \t]*$")

ANULADA = re.compile(r"(?mi)\banulad")


@dataclass
class Gabarito:
    respostas: dict[int, str] = field(default_factory=dict)
    respostas_por_lingua: dict[int, dict[str, str]] = field(default_factory=dict)
    ambiguos: list[int] = field(default_factory=list)
    ordem_das_linguas: tuple[str, ...] = ()
    menciona_anulacao: bool = False
    texto_bruto_chars: int = 0

    def __len__(self) -> int:
        return len(self.respostas)


def _texto(caminho: Path) -> str:
    """Le a folha com pypdf. Medido: os 12 gabaritos sao lidos sem erro."""
    from pypdf import PdfReader

    bruto = "\n".join(p.extract_text() or "" for p in PdfReader(caminho).pages)
    return bruto.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")


def ler(caminho: Path) -> Gabarito:
    texto = _texto(caminho)
    gabarito = Gabarito(texto_bruto_chars=len(texto),
                        menciona_anulacao=bool(ANULADA.search(texto)))

    cabecalho = PADRAO_CABECALHO_LINGUA.search(texto)
    if cabecalho:
        gabarito.ordem_das_linguas = (cabecalho.group(1).upper().replace("Ê", "E"),
                                      cabecalho.group(2).upper().replace("Ê", "E"))

    for numero, letra in PADRAO_SIMPLES.findall(texto):
        gabarito.respostas[int(numero)] = letra

    for numero, primeira, segunda in PADRAO_DUPLO.findall(texto):
        n = int(numero)
        if gabarito.ordem_das_linguas:
            a, b = gabarito.ordem_das_linguas
        else:
            a, b = "COLUNA_1", "COLUNA_2"
        gabarito.respostas_por_lingua[n] = {a: primeira, b: segunda}
        # NAO entra em `respostas`: duas respostas nao sao uma resposta.
        if n not in gabarito.ambiguos:
            gabarito.ambiguos.append(n)

    gabarito.ambiguos.sort()
    return gabarito
