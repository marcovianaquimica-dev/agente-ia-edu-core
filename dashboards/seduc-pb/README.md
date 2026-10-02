# Dashboard SEDUC-PB (v1)

Dashboard gerencial pontual para o Simulado de Redacao da SEDUC-PB.
Isolado do produto principal - nao importa nada de `src/agente_ia_edu`.

## Setup

    cd dashboards/seduc-pb
    python3.13 -m venv .venv
    .venv/bin/pip install -e ".[dev]"

## Rodar os testes

    .venv/bin/pytest -v

Os testes do export job precisam de um Postgres acessivel em
`SEDUC_DASHBOARD_TEST_DATABASE_URL` (por padrao, o Postgres de dev local
na porta 5433) - eles criam e destroem um schema proprio de teste
(`seduc_pb_dashboard_test`), sem tocar nos dados reais.

## Exportar um snapshot

    export SEDUC_DASHBOARD_SOURCE_DATABASE_URL="postgresql://usuario_so_leitura:senha@host:porta/banco"
    .venv/bin/python export_snapshot.py

Gera (ou atualiza) `snapshot.db` na mesma pasta. Rodar de novo a qualquer
momento atualiza o snapshot - e idempotente.

## Criar o primeiro usuario

    .venv/bin/python auth.py --snapshot-path snapshot.db --login gestor --senha "troque-esta-senha"

## Subir o app

    export SEDUC_DASHBOARD_SECRET_KEY="troque-por-uma-chave-aleatoria-em-producao"
    .venv/bin/uvicorn app:app --reload

Acesse http://localhost:8000/login
