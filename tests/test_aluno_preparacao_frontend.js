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
  const ordem = Prep.subpassos(passo()).map((s) => s.rotulo);
  assert.deepEqual(ordem, ['Entender', 'Tentar com ajuda', 'Praticar',
                           'Verificar']);
});

test('o que ja passou fica marcado como cumprido', () => {
  const trilha = Prep.subpassos(passo({ kind: 'PRACTICE' }));
  const cumpridos = trilha.filter((s) => s.cumprido).map((s) => s.rotulo);
  assert.deepEqual(cumpridos, ['Entender', 'Tentar com ajuda']);
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
