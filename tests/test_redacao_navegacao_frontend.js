/**
 * A NAVEGAÇÃO INTERNA DA REDAÇÃO — executando o código, não lendo-o.
 *
 * O QUE EXISTIA
 * =============
 *   Propostas | Fila de Revisão | Evolução | Dashboard | 🗑️ Lixeira
 *
 * Cinco abas no mesmo peso visual, misturando três coisas diferentes: áreas
 * de trabalho (Propostas, Evolução), uma fila operacional (Fila de Revisão) e
 * uma lixeira — que é uma operação secundária e destrutiva, com emoji, ao
 * lado das áreas principais.
 *
 * E o "Dashboard", que é a resposta à primeira pergunta do professor ("como
 * estão meus alunos?"), era a quarta aba e não a entrada.
 *
 * O QUE NÃO EXISTIA
 * =================
 * "Enviar em lote" não existe neste módulo — procurei em todo o `web/`. O
 * único "lote" do repositório é classificação em lote no Banco de Questões.
 * Não há o que mover, e inventar a ação seria criar botão decorativo.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const Nav = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                              'web', 'redacao-navegacao.js'));

test('as quatro areas principais, nesta ordem', () => {
  assert.deepEqual(Nav.areas().map((a) => a.rotulo),
                   ['Visão geral', 'Propostas', 'Correções', 'Evolução']);
});

test('a visao geral e a entrada', () => {
  assert.equal(Nav.areas()[0].aba, Nav.ABA_INICIAL);
  assert.equal(Nav.ABA_INICIAL, 'dashboard');
});

test('nenhum destino foi perdido', () => {
  // As cinco abas antigas continuam alcançáveis: quatro como área, a lixeira
  // no menu secundário. Simplificar navegação não é apagar função.
  const destinos = Nav.areas().map((a) => a.aba)
    .concat(Nav.secundarios().map((s) => s.aba));
  for (const antiga of ['prompts', 'queue', 'evolution', 'dashboard', 'trash']) {
    assert.ok(destinos.includes(antiga), `destino perdido: ${antiga}`);
  }
});

test('a lixeira saiu do primeiro nivel, mas continua alcancavel', () => {
  const principais = Nav.areas().map((a) => a.aba);
  assert.ok(!principais.includes('trash'),
            'lixeira disputando hierarquia com as areas principais');
  assert.ok(Nav.secundarios().some((s) => s.aba === 'trash'),
            'lixeira ficou inacessivel');
});

test('os nomes tecnicos nao aparecem para o usuario', () => {
  for (const item of Nav.areas().concat(Nav.secundarios())) {
    assert.doesNotMatch(item.rotulo, /prompts|queue|trash|dashboard/i,
                        `rotulo com nome interno: ${item.rotulo}`);
  }
});

test('nenhum rotulo carrega emoji', () => {
  for (const item of Nav.areas().concat(Nav.secundarios())) {
    assert.doesNotMatch(item.rotulo, /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u,
                        `rotulo com emoji: ${item.rotulo}`);
  }
});

test('a area ativa e marcada, e so uma', () => {
  for (const aba of ['dashboard', 'prompts', 'queue', 'evolution']) {
    const ativas = Nav.areas(aba).filter((a) => a.ativa);
    assert.equal(ativas.length, 1, `${aba} acendeu ${ativas.length} areas`);
    assert.equal(ativas[0].aba, aba);
  }
});

test('estando na lixeira, nenhuma area principal finge estar ativa', () => {
  assert.equal(Nav.areas('trash').filter((a) => a.ativa).length, 0);
  assert.ok(Nav.secundarios('trash').find((s) => s.aba === 'trash').ativa);
});

test('aba desconhecida nao quebra e nao acende nada', () => {
  assert.equal(Nav.areas('inexistente').filter((a) => a.ativa).length, 0);
  assert.equal(Nav.areas().length, 4);
});

// -- os filtros globais do professor ---------------------------------------

test('os filtros globais somem onde nao fazem nada', () => {
  // Medido no navegador: na view da Redação, "Turma" e "Período" do cabeçalho
  // do professor continuavam visíveis e NÃO afetavam nada - enquanto a
  // própria Redação tinha a sua Turma. Filtro inerte é pior que filtro
  // duplicado: ele promete recalcular e não recalcula.
  assert.equal(Nav.filtrosGlobaisValem('essay-review'), false);
  assert.equal(Nav.filtrosGlobaisValem('dashboard'), true);
  assert.equal(Nav.filtrosGlobaisValem('contents'), true);
});

test('view desconhecida mantem os filtros, por seguranca', () => {
  // Esconder por engano um filtro que funciona e pior que mostrar um inerte.
  assert.equal(Nav.filtrosGlobaisValem('algo-novo'), true);
  assert.equal(Nav.filtrosGlobaisValem(undefined), true);
});
