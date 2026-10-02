"""Gera dados FICTICIOS de demonstracao para o dashboard SEDUC-PB.

NUNCA use isto contra um snapshot com dados reais que voce queira manter -
`substituir_redacoes` SOBRESCREVE toda a tabela `redacoes`. Para voltar aos
dados reais depois de rodar este script, rode `export_snapshot.py` de novo.

Serve para visualizar o dashboard com um volume realista (varias
escolas/GREs/municipios/turmas) antes da correcao em massa real rodar em
escala. Reaproveita a mesma logica de classificacao e ranking do pipeline
real (faixas.classificar, ranking.calcular_ranking) - so os dados de
entrada sao sinteticos.
"""
from __future__ import annotations

import argparse
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faixas import classificar
from ranking import calcular_ranking
from snapshot_db import create_schema, get_connection, substituir_redacoes

GRES_MUNICIPIOS = [
    ("1a GRE - Joao Pessoa", "Joao Pessoa"),
    ("2a GRE - Guarabira", "Guarabira"),
    ("3a GRE - Campina Grande", "Campina Grande"),
    ("4a GRE - Cuite", "Cuite"),
    ("5a GRE - Monteiro", "Monteiro"),
    ("6a GRE - Patos", "Patos"),
    ("7a GRE - Itaporanga", "Itaporanga"),
]

NOMES_ESCOLA = [
    "EEEFM Lyceu Paraibano",
    "EEEFM Presidente Joao Goulart",
    "EEEFM Olivina Olivia Carneiro da Cunha",
    "EEEFM Dom Ulrico",
    "EEEFM Prof. Jose Batista de Carvalho",
    "EEEFM Solon de Lucena",
    "EEEFM Pedro Americo",
    "EEEFM Severino Cabral",
    "EEEFM Monsenhor Walfredo Leal",
    "EEEFM Argemiro de Figueiredo",
]

TURMAS = ["3o Ano A", "3o Ano B", "3o Ano C"]

PRIMEIROS_NOMES = [
    "Ana", "Bruno", "Carlos", "Daniela", "Eduardo", "Fernanda", "Gabriel",
    "Helena", "Igor", "Julia", "Lucas", "Mariana", "Nicolas", "Olivia",
    "Pedro", "Rafaela", "Samuel", "Tatiana", "Vinicius", "Yasmin",
]
SOBRENOMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Lima", "Pereira", "Costa",
    "Carvalho", "Almeida", "Nascimento", "Araujo", "Ribeiro", "Melo",
    "Barbosa", "Rocha",
]

PONTOS_POSSIVEIS = [0, 40, 80, 120, 160, 200]


def _montar_escolas() -> list[dict]:
    escolas = []
    for indice, nome_escola in enumerate(NOMES_ESCOLA):
        gre_nome, municipio_nome = GRES_MUNICIPIOS[indice % len(GRES_MUNICIPIOS)]
        escolas.append(
            {
                "escola_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, nome_escola)),
                "escola_nome": nome_escola,
                "gre_nome": gre_nome,
                "municipio_nome": municipio_nome,
                "turmas": [
                    {
                        "turma_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{nome_escola}-{turma}")),
                        "turma_nome": turma,
                    }
                    for turma in TURMAS
                ],
            }
        )
    return escolas


def gerar_redacoes_ficticias(total: int, seed: int = 42) -> list[dict]:
    aleatorio = random.Random(seed)
    escolas = _montar_escolas()
    agora = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)

    linhas = []
    for indice in range(total):
        escola = aleatorio.choice(escolas)
        turma = aleatorio.choice(escola["turmas"])
        nome_aluno = f"{aleatorio.choice(PRIMEIROS_NOMES)} {aleatorio.choice(SOBRENOMES)}"
        c1, c2, c3, c4, c5 = (aleatorio.choice(PONTOS_POSSIVEIS) for _ in range(5))
        nota_final = c1 + c2 + c3 + c4 + c5
        linhas.append(
            {
                "id_redacao": str(uuid.uuid4()),
                "id_aluno": str(uuid.uuid4()),
                "nome_aluno": nome_aluno,
                "escola_id": escola["escola_id"],
                "escola_nome": escola["escola_nome"],
                "gre_nome": escola["gre_nome"],
                "municipio_nome": escola["municipio_nome"],
                "turma_id": turma["turma_id"],
                "turma_nome": turma["turma_nome"],
                "nota_final": nota_final,
                "c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5,
                "faixa_classificacao": classificar(nota_final),
                "data_correcao": (agora + timedelta(minutes=indice)).isoformat(),
            }
        )

    ranking = calcular_ranking([(linha["id_redacao"], linha["nota_final"]) for linha in linhas])
    for linha in linhas:
        linha["posicao_geral"] = ranking[linha["id_redacao"]]
    return linhas


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera dados ficticios de demonstracao no snapshot do dashboard SEDUC-PB"
    )
    parser.add_argument(
        "--snapshot-path", default=str(Path(__file__).resolve().parent / "snapshot.db")
    )
    parser.add_argument("--total", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    conn = get_connection(Path(args.snapshot_path))
    create_schema(conn)
    redacoes = gerar_redacoes_ficticias(args.total, seed=args.seed)
    substituir_redacoes(conn, redacoes)
    print(f"{len(redacoes)} redacoes ficticias geradas em {args.snapshot_path}")


if __name__ == "__main__":
    main()
