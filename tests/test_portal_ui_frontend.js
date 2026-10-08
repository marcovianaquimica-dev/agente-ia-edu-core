/**
 * COMPORTAMENTO da Home do Portal — executando o código, não lendo-o.
 *
 * Roda com `node --test` e entra na suíte pela ponte em
 * `tests/test_frontend_comportamental.py`.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const PortalUI = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                                   'web', 'portal-ui.js'));

function modulo(extra) {
  return Object.assign({
    key: 'REDACAO_IA', title: 'Redação', description: 'x',
    status: 'DISPONIVEL', route: '/redacao',
    contracted: true, can_access: true,
  }, extra || {});
}

test('separa os modulos que existem dos que ainda nao existem', () => {
  const r = PortalUI.separar([
    modulo(), modulo({ key: 'SAEB', status: 'EM_BREVE', route: null }),
  ]);
  assert.equal(r.disponiveis.length, 1);
  assert.equal(r.futuros.length, 1);
});

test('um modulo nao contratado continua em "Seus modulos", nao some', () => {
  const r = PortalUI.separar([modulo({ contracted: false, can_access: false, route: null })]);
  assert.equal(r.disponiveis.length, 1,
               'escondeu um modulo que o produto tem');
});

test('lista vazia nao quebra', () => {
  const r = PortalUI.separar(null);
  assert.deepEqual(r.disponiveis, []);
  assert.deepEqual(r.futuros, []);
});

test('modulo acessivel vira botao de Acessar', () => {
  const c = PortalUI.cartao(modulo());
  assert.equal(c.tipo, 'ACESSIVEL');
  assert.equal(c.acionavel, true);
  assert.match(c.rotulo, /acessar/i);
});

test('EM BREVE nao e acionavel e nao tem rotulo de acao', () => {
  const c = PortalUI.cartao(modulo({ status: 'EM_BREVE', route: null,
                                     contracted: false, can_access: false }));
  assert.equal(c.tipo, 'EM_BREVE');
  assert.equal(c.acionavel, false);
  assert.match(c.rotulo, /breve/i);
});

test('sem rota, nao ha acao - mesmo com can_access verdadeiro', () => {
  // Defesa contra payload incoerente: a acao depende da ROTA existir.
  const c = PortalUI.cartao(modulo({ route: null }));
  assert.equal(c.acionavel, false);
});

test('nao contratado e dito como nao contratado, nao como em breve', () => {
  const c = PortalUI.cartao(modulo({ contracted: false, can_access: false,
                                     route: null }));
  assert.equal(c.tipo, 'NAO_CONTRATADO');
  assert.equal(c.acionavel, false);
  assert.match(c.nota, /institui/i);
});

test('contratado mas sem permissao do papel tem nota propria', () => {
  const c = PortalUI.cartao(modulo({ contracted: true, can_access: false,
                                     route: null }));
  assert.equal(c.tipo, 'SEM_PERMISSAO');
  assert.match(c.nota, /perfil/i);
});

test('as quatro situacoes tem rotulo ou nota - nenhuma fica muda', () => {
  const casos = [
    modulo(),
    modulo({ status: 'EM_BREVE', route: null, contracted: false, can_access: false }),
    modulo({ contracted: false, can_access: false, route: null }),
    modulo({ contracted: true, can_access: false, route: null }),
  ];
  for (const m of casos) {
    const c = PortalUI.cartao(m);
    assert.ok(c.rotulo || c.nota, `card mudo: ${JSON.stringify(c)}`);
  }
});

test('o contexto traz instituicao, pessoa e papel legiveis', () => {
  const c = PortalUI.contexto({
    user: { external_id: 'aluna', name: 'Aluna Silva', role: 'STUDENT' },
    institution: { name: 'Escola ABC' },
  });
  assert.equal(c.instituicao, 'Escola ABC');
  assert.equal(c.pessoa, 'Aluna Silva');
  assert.equal(c.papel, 'Aluno');
});

test('havendo nome, o identificador tecnico nao aparece', () => {
  // "aluno_teste_a" numa tela projetada denuncia o ambiente de teste.
  const c = PortalUI.contexto({
    user: { external_id: 'aluno_teste_a', name: 'Aluno Teste A' },
    institution: {},
  });
  assert.equal(c.pessoa, 'Aluno Teste A');
});

test('sem nome cadastrado, o identificador e o ultimo recurso', () => {
  const c = PortalUI.contexto({ user: { external_id: 'prof_x' }, institution: {} });
  assert.equal(c.pessoa, 'prof_x');
});

test('sem vinculo, a instituicao e dita ausente - nao inventada', () => {
  const c = PortalUI.contexto({ user: {}, institution: {} });
  assert.match(c.instituicao, /sem institui/i);
});

test('papel desconhecido nao vira codigo interno na tela', () => {
  const c = PortalUI.contexto({ user: { role: 'ALGO_NOVO' }, institution: {} });
  assert.equal(c.papel, '');
});

test('payload vazio nao quebra o topo', () => {
  const c = PortalUI.contexto(null);
  assert.equal(c.pessoa, '');
});
