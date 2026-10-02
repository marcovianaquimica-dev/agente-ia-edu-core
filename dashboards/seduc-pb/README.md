# Dashboard SEDUC-PB (v1)

Dashboard gerencial pontual para o Simulado de Redacao da SEDUC-PB.
Isolado do produto principal - nao importa nada de `src/agente_ia_edu`.

Os graficos (Chart.js) sao carregados via CDN (`cdn.jsdelivr.net`) direto
no navegador - sem build step, sem dependencia Python nova. Se a rede do
usuario bloquear o CDN, as telas continuam funcionando normalmente (tabelas,
filtros, plano de acao), so os graficos ficam em branco.

Alem das 4 telas da v1 (Visao Geral, Ranking, Escola, Turma), existem 2
telas de detalhe: `/gres/{nome}` e `/municipios/{nome}` (acessadas a partir
das listas de GREs e Municipios), cada uma com metricas, grafico de
comparacao com o estado e plano de acao.

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

O app recusa subir (`RuntimeError` na importacao) sem uma chave de sessao -
isso evita que o login fique forjavel por causa de uma chave padrao
commitada no repo.

Para rodar localmente, em modo dev (mais rapido para testar na sua
maquina):

    export SEDUC_DASHBOARD_DEV=1
    .venv/bin/uvicorn app:app --reload

Para qualquer coisa alem de um laptop (staging, producao, qualquer ambiente
compartilhado), defina uma chave real em vez do modo dev:

    export SEDUC_DASHBOARD_SECRET_KEY="troque-por-uma-chave-aleatoria-em-producao"
    .venv/bin/uvicorn app:app --reload

Acesse http://localhost:8000/login

### Plano de acao via IA

As telas "Visao Geral", "Escola", "Turma" e as telas de detalhe de GRE e
Municipio tentam gerar um plano de acao curto via IA (OpenAI): 3 blocos -
ponto forte, ponto de atencao e recomendacao - calculados so a partir de
indicadores ja agregados (nunca o texto de uma redacao). Isso exige
`OPENAI_API_KEY` definida no ambiente (variavel padrao do SDK da OpenAI):

    export OPENAI_API_KEY="sk-..."

Sem essa chave (ou se a chamada falhar por qualquer motivo - rede, quota,
JSON invalido), a tela continua funcionando normalmente e mostra o texto
"Plano de acao indisponivel no momento." no lugar - nunca um erro 500. O
resultado e cacheado por corte (geral, por escola, por turma, por GRE, por
municipio) e so regenera quando os indicadores de entrada mudam.

### Cookies de sessao em producao

A sessao usa um cookie com `max_age` de 8 horas e `same_site=lax`. Se for
rodar atras de HTTPS em producao, considere tambem passar
`https_only=True` para o `SessionMiddleware` em `app.py` (nao ligado por
padrao aqui porque quebraria o fluxo local de `uvicorn --reload` em HTTP
puro).
