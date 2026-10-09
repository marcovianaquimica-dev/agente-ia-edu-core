/**
 * COMPORTAMENTO dos filtros de taxonomia — executando o código.
 *
 * Medido no navegador em 2026-10-06: o campo de Conteúdo só funcionava com o
 * código canônico (21 questões), e devolvia zero com o nome. O professor
 * escolhe pelo nome; a requisição continua mandando o código.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const T = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                            'web', 'qbank-taxonomia.js'));

const NOS = [
  { code: 'CHEMISTRY', name: 'Quimica', node_type: 'DISCIPLINE', position: 1 },
  { code: 'BIOLOGY', name: 'Biologia', node_type: 'DISCIPLINE', position: 2 },
  { code: 'CHEMISTRY-PHYSICAL', name: 'Fisico-Quimica', node_type: 'AREA', position: 1 },
  { code: 'CHEMISTRY-GENERAL', name: 'Química Geral', node_type: 'AREA', position: 2 },
  { code: 'BIOLOGY-ECOLOGY', name: 'Ecologia', node_type: 'AREA', position: 1 },
  { code: 'CHEMISTRY-PHYSICAL-STOICHIOMETRY', name: 'Estequiometria e cálculos químicos',
    node_type: 'CONTENT', position: 1 },
  { code: 'CHEMISTRY-GENERAL-BALANCING', name: 'Reações químicas e balanceamento',
    node_type: 'CONTENT', position: 2 },
  { code: 'BIOLOGY-ECOLOGY-POPULATIONS', name: 'Ecologia de populações',
    node_type: 'CONTENT', position: 1 },
  { code: 'CHEMISTRY-SOLUTIONS-CONCENTRATION', name: 'Concentracao',
    node_type: 'SUBCONTENT', position: 1 },
  { code: 'CHEMISTRY-ARQUIVADO', name: 'Nao usar', node_type: 'CONTENT',
    position: 9, active: false },
];

test('o valor e o codigo e o rotulo e o nome', () => {
  const o = T.opcoes(NOS, 'DISCIPLINE');
  assert.deepEqual(o, [
    { value: 'CHEMISTRY', rotulo: 'Quimica' },
    { value: 'BIOLOGY', rotulo: 'Biologia' },
  ]);
});

test('escolher Quimica nao oferece conteudo de Biologia', () => {
  const o = T.opcoes(NOS, 'CONTENT', 'CHEMISTRY').map((x) => x.value);
  assert.ok(o.includes('CHEMISTRY-PHYSICAL-STOICHIOMETRY'));
  assert.ok(!o.includes('BIOLOGY-ECOLOGY-POPULATIONS'));
});

test('a area tambem restringe, e nao so a disciplina', () => {
  const o = T.opcoes(NOS, 'CONTENT', 'CHEMISTRY-PHYSICAL').map((x) => x.value);
  assert.deepEqual(o, ['CHEMISTRY-PHYSICAL-STOICHIOMETRY']);
});

test('o proprio no nao entra na lista dos filhos', () => {
  // `CHEMISTRY-PHYSICAL` nao e conteudo DE `CHEMISTRY-PHYSICAL`.
  const o = T.opcoes(NOS, 'AREA', 'CHEMISTRY-PHYSICAL');
  assert.deepEqual(o, []);
});

test('no inativo nao aparece', () => {
  const o = T.opcoes(NOS, 'CONTENT').map((x) => x.value);
  assert.ok(!o.includes('CHEMISTRY-ARQUIVADO'));
});

test('no sem nome volta com o proprio codigo', () => {
  const o = T.opcoes([{ code: 'X-Y', name: '  ', node_type: 'CONTENT' }], 'CONTENT');
  assert.deepEqual(o, [{ value: 'X-Y', rotulo: 'X-Y' }]);
});

test('sem catalogo nao ha opcao, e nao ha erro', () => {
  assert.deepEqual(T.opcoes(null, 'CONTENT'), []);
  assert.deepEqual(T.opcoes([], 'CONTENT'), []);
});

test('a ordem e estavel', () => {
  assert.deepEqual(T.opcoes(NOS, 'CONTENT'), T.opcoes(NOS, 'CONTENT'));
});

test('trocar de disciplina invalida o conteudo que nao existe mais', () => {
  const deBio = T.opcoes(NOS, 'CONTENT', 'BIOLOGY');
  assert.equal(T.aindaVale('CHEMISTRY-PHYSICAL-STOICHIOMETRY', deBio), false);
  assert.equal(T.aindaVale('BIOLOGY-ECOLOGY-POPULATIONS', deBio), true);
});

test('filtro vazio continua valendo sempre', () => {
  assert.equal(T.aindaVale('', []), true);
  assert.equal(T.aindaVale(null, []), true);
});

test('o mesmo no duas vezes aparece uma so', () => {
  // A arvore de cada disciplina ja traz a propria raiz: concatenar as raizes
  // com as arvores duplicava as quatro disciplinas na lista.
  const repetido = NOS.concat([{ code: 'CHEMISTRY', name: 'Quimica',
                                 node_type: 'DISCIPLINE', position: 1 }]);
  const o = T.opcoes(repetido, 'DISCIPLINE').map((x) => x.value);
  assert.deepEqual(o, ['CHEMISTRY', 'BIOLOGY']);
});
