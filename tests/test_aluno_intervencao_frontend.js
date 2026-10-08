/**
 * A INTERFACE DA INTERVENÇÃO FORMATIVA.
 *
 * O backend passou a devolver, junto da resposta gravada, `may_advance` e
 * `intervention`. Estes testes travam o que a tela FAZ com isso — e o
 * primeiro deles é o defeito que a validação manual encontrou: o botão
 * "Próxima" avançava mesmo depois de uma resposta errada.
 *
 * Nada aqui decide pedagogia. A ação, a micro-habilidade e o motivo chegam
 * prontos; a tela escolhe o botão e a frase.
 */

const test = require('node:test');
const assert = require('node:assert');
const Intervencao = require('../src/agente_ia_edu/web/aluno-intervencao.js');

const INVESTIGAR = {
  action: 'INVESTIGATE',
  mode: 'FORMATIVE_PRACTICE',
  skill: 'LEITURA_DE_FORMULA',
  content_code: 'CHEMISTRY-PHYSICAL-STOICHIOMETRY',
  question_version_id: 'abc',
  selected_option: 'A',
  may_advance: false,
  reason: 'ha uma cadeia de microperguntas escrita para esta micro-habilidade',
};

const ENSINAR = {
  ...INVESTIGAR,
  action: 'TEACH',
  material_id: 'mat-1',
  material_title: 'Estequiometria: da fórmula até a massa',
};

// ------------------------------------------- o avanço (o defeito do §3)

test('sem intervenção, responder avança para a próxima', () => {
  assert.equal(Intervencao.proximoPasso({ pos: 0, total: 5 }), 'proxima');
});

test('COM intervenção, NÃO avança — é o defeito corrigido', () => {
  assert.equal(
    Intervencao.proximoPasso({ pos: 0, total: 5, intervencao: INVESTIGAR }),
    'intervencao');
});

test('na última questão sem intervenção, conclui', () => {
  assert.equal(Intervencao.proximoPasso({ pos: 4, total: 5 }), 'concluir');
});

test('na última questão COM intervenção, intervém antes de concluir', () => {
  assert.equal(
    Intervencao.proximoPasso({ pos: 4, total: 5, intervencao: INVESTIGAR }),
    'intervencao');
});

test('intervenção vazia não conta como intervenção', () => {
  for (const vazia of [null, undefined, {}]) {
    assert.equal(Intervencao.proximoPasso({ pos: 0, total: 5, intervencao: vazia }),
                 'proxima');
  }
});

// ------------------------------------------------- o que o Edu diz

test('a fala anuncia que vamos entender, sem afirmar o raciocínio dele', () => {
  const f = Intervencao.falaDaIntervencao(INVESTIGAR).toLowerCase();
  assert.ok(f.length > 15, f);
  for (const proibido of ['você esqueceu', 'você errou', 'você não sabe',
                          'você confundiu', 'você calculou errado']) {
    assert.ok(!f.includes(proibido), `${proibido} em "${f}"`);
  }
});

test('e ela não entrega a resposta certa', () => {
  const f = Intervencao.falaDaIntervencao(INVESTIGAR).toLowerCase();
  assert.ok(!f.includes('a resposta é'), f);
  assert.ok(!f.includes('o correto é'), f);
});

test('ensinar e investigar dizem coisas diferentes', () => {
  assert.notEqual(Intervencao.falaDaIntervencao(INVESTIGAR),
                  Intervencao.falaDaIntervencao(ENSINAR));
});

test('ação desconhecida não estoura nem inventa frase', () => {
  const f = Intervencao.falaDaIntervencao({ action: 'ALGO_NOVO' });
  assert.equal(typeof f, 'string');
  assert.ok(f.length > 0);
});

// ------------------------------------------------- o botão que executa

test('investigar abre a investigação, com conteúdo e habilidade', () => {
  const a = Intervencao.acaoDaIntervencao(INVESTIGAR);
  assert.equal(a.acao, 'intervencao-investigar');
  assert.equal(a.content_code, INVESTIGAR.content_code);
  assert.equal(a.skill, INVESTIGAR.skill);
  assert.ok(a.rotulo.length > 3);
});

test('ensinar abre o material, com o id', () => {
  const a = Intervencao.acaoDaIntervencao(ENSINAR);
  assert.equal(a.acao, 'intervencao-estudar');
  assert.equal(a.material_id, 'mat-1');
});

test('o rótulo do botão NÃO é "Próxima"', () => {
  for (const d of [INVESTIGAR, ENSINAR]) {
    assert.ok(!/pr[óo]xima/i.test(Intervencao.acaoDaIntervencao(d).rotulo));
  }
});

test('sem intervenção não há ação', () => {
  assert.equal(Intervencao.acaoDaIntervencao(null), null);
  assert.equal(Intervencao.acaoDaIntervencao({}), null);
});

// -------------------------------------- objetiva x aberta (§11)

test('questão com alternativas pede SÓ alternativas', () => {
  const c = Intervencao.controles({ options: [{ key: 'A', text: '1' },
                                               { key: 'C', text: '3' }] });
  assert.equal(c.modo, 'alternativas');
  assert.equal(c.campoTexto, false);
  assert.equal(c.alternativas.length, 2);
});

test('questão sem alternativas pede SÓ campo de texto', () => {
  const c = Intervencao.controles({ options: [], espera: 'NUMERIC' });
  assert.equal(c.modo, 'texto');
  assert.equal(c.campoTexto, true);
  assert.equal(c.alternativas.length, 0);
});

test('nunca os dois ao mesmo tempo — era o que a tela mostrava', () => {
  for (const q of [{ options: [{ key: 'A', text: '1' }] },
                   { options: [], espera: 'NUMERIC' },
                   { options: [] },
                   {}]) {
    const c = Intervencao.controles(q);
    const temAlternativas = c.alternativas.length > 0;
    assert.ok(!(temAlternativas && c.campoTexto),
              `dois controles concorrentes em ${JSON.stringify(q)}`);
  }
});

test('o modo viaja como dado, para a tela não deduzir', () => {
  assert.ok(['alternativas', 'texto'].includes(
    Intervencao.controles({ options: [] }).modo));
});

// ------------------------------------------------- confirmar (§11)

test('alternativa marcada libera o botão de confirmar', () => {
  const c = Intervencao.controles({ options: [{ key: 'A', text: '1' }] },
                                  { escolha: 'A' });
  assert.equal(c.podeConfirmar, true);
  assert.equal(c.marcada, 'A');
});

test('sem marcar nada o botão fica travado', () => {
  const c = Intervencao.controles({ options: [{ key: 'A', text: '1' }] });
  assert.equal(c.podeConfirmar, false);
});

test('no modo texto, texto em branco não confirma', () => {
  assert.equal(
    Intervencao.controles({ options: [] }, { texto: '   ' }).podeConfirmar,
    false);
  assert.equal(
    Intervencao.controles({ options: [] }, { texto: '17' }).podeConfirmar,
    true);
});
