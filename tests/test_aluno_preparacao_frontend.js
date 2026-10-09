/**
 * A PREPARAÇÃO não pode parecer um buraco infinito.
 *
 * O teste humano de 2026-10-05 ficou preso em "Preparação ●" sem saber o que
 * mudava entre uma volta e outra. A barra de quatro etapas continua certa; o
 * que faltava era, DENTRO da preparação, dizer em que sub-passo ele está.
 *
 * Aqui estão as decisões de apresentação disso, executadas de verdade com
 * `node --test` — não varridas do fonte.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const Prep = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                               'web', 'aluno-preparacao.js'));

function passo(extra = {}) {
  return Object.assign({ kind: 'LEARN', content_name: 'Balanceamento' }, extra);
}

test('cada passo da preparacao acende o seu sub-passo', () => {
  const esperado = {
    LEARN: 'Entender',
    GUIDED_PRACTICE: 'Tentar com ajuda',
    PRACTICE: 'Praticar',
    VERIFY: 'Verificar',
  };
  for (const [kind, rotulo] of Object.entries(esperado)) {
    const trilha = Prep.subpassos(passo({ kind }));
    const atual = trilha.filter((s) => s.atual);
    assert.equal(atual.length, 1, `${kind} acendeu ${atual.length} sub-passos`);
    assert.equal(atual[0].rotulo, rotulo);
  }
});

test('os sub-passos vem sempre na mesma ordem', () => {
  // "Localizar" entrou em 2026-10-07, quando a investigação passou a ser o
  // degrau mais alto da escada. A trilha tem cinco degraus porque a escada
  // tem cinco - e a ordem aqui é a de `escada_de_apoio.NIVEIS`, no backend,
  // mais a verificação que fecha o ciclo.
  const ordem = Prep.subpassos(passo()).map((s) => s.rotulo);
  assert.deepEqual(ordem, ['Localizar', 'Entender', 'Tentar com ajuda',
                           'Praticar', 'Verificar']);
});

test('o que ja passou fica marcado como cumprido', () => {
  const trilha = Prep.subpassos(passo({ kind: 'PRACTICE' }));
  const cumpridos = trilha.filter((s) => s.cumprido).map((s) => s.rotulo);
  assert.deepEqual(cumpridos, ['Localizar', 'Entender', 'Tentar com ajuda']);
});

test('fora da preparacao nao ha trilha nenhuma', () => {
  // Diagnóstico e atividade não são sub-passos da preparação. Desenhar a
  // trilha neles diria que o aluno está numa etapa em que ele não está.
  for (const kind of ['DIAGNOSTIC', 'ACTIVITY', 'NONE', 'ESCALATE']) {
    assert.deepEqual(Prep.subpassos(passo({ kind })), [],
                     `${kind} desenhou trilha de preparacao`);
  }
});

test('payload vazio nao quebra', () => {
  assert.deepEqual(Prep.subpassos(null), []);
});

// -- o feedback depois de responder ----------------------------------------

test('a frase do resultado vem do backend quando existe', () => {
  const r = Prep.falaDoResultado(
    { correct_count: 1, question_count: 5 },
    { titulo: 'Isso ainda está travando.', detalhe: 'Vamos rever.',
      tom: 'REVISAR' });
  assert.equal(r.titulo, 'Isso ainda está travando.');
  assert.equal(r.detalhe, 'Vamos rever.');
  assert.equal(r.tom, 'REVISAR');
});

test('a contagem continua visivel, porque o aluno quer saber', () => {
  const r = Prep.falaDoResultado({ correct_count: 1, question_count: 5 },
                                 { titulo: 'x', detalhe: 'y', tom: 'NEUTRO' });
  assert.match(r.placar, /1.*5/);
});

test('sem feedback do backend, a tela NAO inventa pedagogia', () => {
  // O achado 3 foi exatamente isto: a frase pedagógica montada no navegador.
  // Sem o campo, o texto é factual e não conclui nada.
  const r = Prep.falaDoResultado({ correct_count: 1, question_count: 5 }, null);
  assert.match(r.placar, /1.*5/);
  assert.doesNotMatch(`${r.titulo} ${r.detalhe}`.toLowerCase(),
                      /domina|consolidad|precisa de aten|evoluiu/);
});

test('sem placar e sem feedback, ainda ha o que mostrar', () => {
  const r = Prep.falaDoResultado({}, null);
  assert.ok(r.titulo.trim().length > 0);
});

test('o feedback do backend nunca e tratado como HTML', () => {
  // Quem escreve a frase é o backend, mas quem a desenha é a tela: o contrato
  // devolve TEXTO, e escapar é responsabilidade de quem insere.
  const r = Prep.falaDoResultado({ correct_count: 0, question_count: 1 },
                                 { titulo: '<b>oi</b>', detalhe: 'x' });
  assert.equal(r.titulo, '<b>oi</b>', 'o modulo nao deve escapar nem desescapar');
});

// -- revelar o proximo passo do exemplo ------------------------------------

test('revelar leva a vista ao passo recem-revelado, nao ao topo', () => {
  const r = Prep.comoRevelar({ indice: 1, total: 3 });
  assert.equal(r.indice, 1);
  assert.equal(r.block, 'start');
});

test('quem pediu menos movimento recebe o mesmo destino sem percurso', () => {
  assert.equal(Prep.comoRevelar({ indice: 1, total: 3 }).behavior, 'smooth');
  assert.equal(Prep.comoRevelar({ indice: 1, total: 3, reduzido: true }).behavior,
               'auto');
});

test('indice fora da lista nao rola nada', () => {
  // Rolar para um passo que nao existe moveria a pagina para lugar nenhum -
  // que e exatamente o bug que isto corrige, so que ao contrario.
  assert.equal(Prep.comoRevelar({ indice: 3, total: 3 }), null);
  assert.equal(Prep.comoRevelar({ indice: -1, total: 3 }), null);
  assert.equal(Prep.comoRevelar({ total: 3 }), null);
  assert.equal(Prep.comoRevelar(null), null);
});

// ============================== O MATERIAL ABRE NO QUE TRAVOU ============
//
// MEDIDO NO NAVEGADOR, EM 2026-10-07
// ===================================
// Aluno QA, alvo MASSA_MOLAR. Ele acabou de acertar, de primeira, a
// micropergunta de leitura de fórmula — e o material abriu na seção
// "Leitura de fórmulas químicas", que ele tinha acabado de demonstrar.
// Para chegar a massa molar era preciso rolar por uma seção inteira.
//
// A ordem das seções no CONTEÚDO é a do grafo, e está certa: pré-requisito
// antes de quem depende dele. O que estava errado era a tela tratar "ordem
// do material" e "por onde ESTE aluno entra" como a mesma coisa.
//
// O alvo vem do backend (`intervention.skill`); a seção declara a sua em
// `metadata.skill`. Nada aqui decide pedagogia — só a ordem de leitura.

const SECOES = [
  { position: 1, title: 'Leitura de fórmulas químicas',
    blocks: [{ metadata: { skill: 'LEITURA_DE_FORMULA' } }] },
  { position: 2, title: 'Massa molar',
    blocks: [{ metadata: { skill: 'MASSA_MOLAR' } }] },
  { position: 3, title: 'Conversão entre massa e quantidade de matéria',
    blocks: [{ metadata: { skill: 'RELACAO_MASSA_MOL' } }] },
  { position: 4, title: 'Proporção estequiométrica',
    blocks: [{ metadata: { skill: 'PROPORCAO_ESTEQUIOMETRICA' } }] },
];

test('com alvo, a seção dele vem primeiro', () => {
  const r = Prep.ordemDasSecoes(SECOES, 'MASSA_MOLAR');
  assert.equal(r[0].title, 'Massa molar');
});

test('e as outras continuam todas presentes', () => {
  const r = Prep.ordemDasSecoes(SECOES, 'MASSA_MOLAR');
  assert.equal(r.length, SECOES.length);
  assert.deepEqual(new Set(r.map((s) => s.title)),
                   new Set(SECOES.map((s) => s.title)));
});

test('as outras mantêm a ordem do grafo entre si', () => {
  // Pré-requisito antes de quem depende dele — a ordem do conteúdo está
  // certa, e tirar o alvo da fila não pode embaralhar o resto.
  const r = Prep.ordemDasSecoes(SECOES, 'MASSA_MOLAR');
  const resto = r.slice(1).map((s) => s.position);
  assert.deepEqual(resto, [...resto].sort((a, b) => a - b));
});

test('a seção do alvo é marcada como o foco', () => {
  const r = Prep.ordemDasSecoes(SECOES, 'MASSA_MOLAR');
  assert.equal(r[0].foco, true);
  assert.ok(r.slice(1).every((s) => !s.foco));
});

test('sem alvo, a ordem é a do material', () => {
  const r = Prep.ordemDasSecoes(SECOES, null);
  assert.deepEqual(r.map((s) => s.position), [1, 2, 3, 4]);
  assert.ok(r.every((s) => !s.foco));
});

test('alvo que nenhuma seção cobre não embaralha nada', () => {
  const r = Prep.ordemDasSecoes(SECOES, 'CONCEITO_DE_MOL');
  assert.deepEqual(r.map((s) => s.position), [1, 2, 3, 4]);
  assert.ok(r.every((s) => !s.foco));
});

test('material sem seção nenhuma não estoura', () => {
  assert.deepEqual(Prep.ordemDasSecoes([], 'MASSA_MOLAR'), []);
  assert.deepEqual(Prep.ordemDasSecoes(null, 'MASSA_MOLAR'), []);
});

test('seção sem skill declarada nunca é escolhida como foco', () => {
  const sem = [{ position: 1, title: 'Introdução', blocks: [{}] }];
  const r = Prep.ordemDasSecoes(sem, 'MASSA_MOLAR');
  assert.equal(r[0].foco, false);
});

test('a função não altera a lista recebida', () => {
  const antes = JSON.stringify(SECOES);
  Prep.ordemDasSecoes(SECOES, 'MASSA_MOLAR');
  assert.equal(JSON.stringify(SECOES), antes);
});

// ========================= A ESCADA QUE O ALUNO VÊ ======================
//
// Medido no navegador em 2026-10-07: o aluno acabou de responder três
// microperguntas e a sub-trilha da preparação dizia
//
//     Entender (agora) · Tentar com ajuda · Praticar · Verificar
//
// O degrau que ele JÁ tinha subido não aparecia, e a tela contava uma
// jornada mais curta do que a percorrida.

test('a trilha inclui o degrau de localizar', () => {
  assert.equal(Prep.TRILHA[0].kind, 'INVESTIGATE');
});

test('investigar acende o primeiro degrau, e nada antes dele', () => {
  const t = Prep.subpassos({ kind: 'INVESTIGATE' });
  assert.equal(t.length, 5);
  assert.equal(t[0].atual, true);
  assert.ok(t.every((s) => !s.cumprido));
});

test('no ensino, localizar aparece como cumprido', () => {
  const t = Prep.subpassos({ kind: 'LEARN' });
  assert.equal(t[0].cumprido, true);
  assert.equal(t[1].atual, true);
});

test('a ordem é a da escada do backend', () => {
  assert.deepEqual(Prep.TRILHA.map((s) => s.kind),
                   ['INVESTIGATE', 'LEARN', 'GUIDED_PRACTICE', 'PRACTICE',
                    'VERIFY']);
});

test('há sempre no máximo um degrau atual', () => {
  for (const k of ['INVESTIGATE', 'LEARN', 'GUIDED_PRACTICE', 'PRACTICE',
                   'VERIFY']) {
    const t = Prep.subpassos({ kind: k });
    assert.equal(t.filter((s) => s.atual).length, 1, k);
  }
});

test('fora da preparação a trilha continua vazia', () => {
  for (const k of ['DIAGNOSTIC', 'ACTIVITY', 'ESCALATE', 'NONE']) {
    assert.deepEqual(Prep.subpassos({ kind: k }), [], k);
  }
});
