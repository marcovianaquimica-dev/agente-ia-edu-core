# YouTube — o que falta para ligar a busca real

> **Estado em 2026-10-08:** a busca **não está ligada**. Falta a credencial.
> O que depende dela está isolado numa chamada; todo o resto — consulta,
> validação, descarte e recorte — está implementado e testado.

O §8 da especificação termina com a instrução que governa este documento:

> "Se a integração atual não permitir busca e validação confiáveis,
> identifique a limitação e implemente apenas o que puder ser feito
> corretamente, sem inventar resultados."

Este arquivo é a identificação da limitação.

---

## 1. O que falta, em ordem

1. **`YOUTUBE_API_KEY` no ambiente do servidor.** Não existe em `.env` neste
   ambiente, e nenhum teste a usa.
2. **YouTube Data API v3 habilitada** no projeto do Google Cloud da chave.
3. **Cota diária revisada.** Cada busca custa **duas** chamadas
   (`search.list` + `videos.list`), e `search.list` é a operação caríssima da
   API v3 em unidades de cota. Uma recomendação por aluno por conteúdo não é
   barata.
4. **Curadoria humana antes de qualquer recomendação.** Hoje o fluxo de
   descoberta já nasce em `DISCOVERED → PENDING_REVIEW → ...`, e isso não deve
   mudar: candidato achado pela API **não é** candidato aprovado. O §8.4 exige
   correção científica e adequação pedagógica, e nenhuma das duas é verificável
   por uma resposta de API.

`services/youtube_busca.disponibilidade()` responde isso em tempo de execução,
e é o que qualquer rota deve usar para dizer a verdade ao chamador.

---

## 2. O que já está feito, e é confiável

Tudo em `src/agente_ia_edu/services/youtube_busca.py`, com testes em
`tests/test_youtube_busca_real.py` — exercitados com respostas reais da API v3
**injetadas**, sem rede e sem chave.

| §8 | Regra | Onde |
|----|-------|------|
| 2 | não inventar título, URL, ID, duração ou conteúdo | `candidatos()` descarta o incompleto; `duracao_em_segundos()` devolve `None`, nunca `0` |
| 3 | priorizar vídeos curtos e específicos | `videoDuration=medium` na consulta, e ordenação por duração |
| 8 | exibir incorporado | `videoEmbeddable=true`, e `status.embeddable is not True` é descarte |
| 9 | não iniciar reprodução automaticamente | `url_de_embutir()` sem `autoplay` |
| 13 | tempo assistido não é evidência | o módulo não conhece aluno nem progresso, e há varredura na fonte |
| — | não enviar dado pessoal nas consultas | `ConsultaComDadoPessoal`: a consulta é **recusada**, não limpada em silêncio |
| — | sem legenda, não afirmar conhecer um trecho | `transcricao_disponivel` só é `True` quando a API diz que sim |

### Duas chamadas, e não uma

`search.list` **não devolve duração nem o estado de incorporação**. Um sistema
que recomendasse só com ela estaria chutando os dois — e é por isso que existe
a segunda chamada a `videos.list`.

### A diferença que foi corrigida

Até 2026-10-08 `YouTubeDiscoveryProvider.search` devolvia `[]` em dois casos
diferentes: **sem credencial** e **sem resultado**. Agora sem credencial ele
levanta `BuscaIndisponivel`, com o motivo. `discover_candidates` já captura
exceção de provedor e segue com os outros, então a descoberta não cai — a
falha passa a aparecer como erro no log, que é o que ela é.

---

## 3. O que NÃO foi implementado, e por quê

**A superfície do aluno não existe.** O §8 descreve como o vídeo é apresentado
— um vídeo principal (§8.6), alternativas só quando pedidas (§8.7),
incorporado (§8.8), sem autoplay (§8.9), sem interromper para aplicar
perguntas (§8.10), preservando o contexto da dificuldade (§8.12). Nada disso
tem tela: `web/aluno.js` não mostra vídeo nenhum hoje, e o
`VideoRecommendationEngine` que existe serve o lado do professor.

Construir essa tela é trabalho de produto, não de integração, e implementá-la
agora significaria entregar um player alimentado por um acervo que ninguém
curou. Fica registrado como pendência, com as regras já mapeadas acima.

**A validação pedagógica continua humana.** Nenhuma das regras do §8.4
(correção científica, adequação pedagógica) é decidível por metadado de API.
O caminho que existe — candidato → revisão → aprovação — é o caminho certo.

---

## 4. Como ligar, quando a credencial existir

```bash
# no ambiente do servidor, nunca no repositório
export YOUTUBE_API_KEY="..."
```

Nada mais muda: `YouTubeDiscoveryProvider(api_key=...)` passa a buscar de
verdade pelo mesmo código que os testes já exercitam. Antes de recomendar
qualquer coisa ao aluno, os candidatos ainda precisam passar pela revisão.
