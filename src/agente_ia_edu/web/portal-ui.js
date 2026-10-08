/**
 * PORTAL — as decisões de interface, isoladas para poderem ser testadas.
 *
 * Mesmo arranjo de `aluno-guiada.js`: o arquivo se exporta como módulo quando
 * há `module`, e se pendura no `window` quando é a página que o carrega. Assim
 * `node --test` executa estas funções de verdade, em vez de varrer o fonte.
 *
 * O QUE ELAS DECIDEM: como apresentar. Separar disponíveis de futuros, qual
 * rótulo cada estado recebe, que texto explica um módulo não contratado.
 *
 * O QUE NÃO: quem pode entrar. Isso chega pronto do backend em `can_access` e
 * `route`, e as rotas de cada módulo têm os seus próprios guards.
 */
(function (raiz) {
  'use strict';

  var DISPONIVEL = 'DISPONIVEL';
  var EM_BREVE = 'EM_BREVE';

  /** Separa o que a pessoa pode usar do que ainda não existe. */
  function separar(modulos) {
    var lista = modulos || [];
    return {
      // "Seus módulos" sao os que EXISTEM - inclusive um que a escola nao
      // contratou. Escondê-lo faria a Home negar que o produto o tem, e quem
      // esta avaliando comprar precisa ver que ele existe.
      disponiveis: lista.filter(function (m) { return m.status === DISPONIVEL; }),
      futuros: lista.filter(function (m) { return m.status === EM_BREVE; }),
    };
  }

  /**
   * O estado de um card.
   *
   * Três situações diferentes, e a Home precisa dizer cada uma:
   *   acessível        -> Acessar
   *   não contratado   -> existe, mas não está liberado para esta escola
   *   em breve         -> ainda não existe
   */
  function cartao(m) {
    var modulo = m || {};
    if (modulo.status === EM_BREVE) {
      return { tipo: 'EM_BREVE', rotulo: 'Em breve', acionavel: false,
               nota: null };
    }
    if (modulo.can_access && modulo.route) {
      return { tipo: 'ACESSIVEL', rotulo: 'Acessar', acionavel: true,
               nota: null };
    }
    if (!modulo.contracted) {
      return { tipo: 'NAO_CONTRATADO', rotulo: null, acionavel: false,
               nota: 'Não está habilitado para a sua instituição.' };
    }
    // Contratado pela escola, mas nao para este papel.
    return { tipo: 'SEM_PERMISSAO', rotulo: null, acionavel: false,
             nota: 'Seu perfil não tem acesso a este módulo.' };
  }

  /** Como a pessoa e a instituição aparecem no topo. */
  function contexto(dados) {
    var d = dados || {};
    var u = d.user || {};
    var i = d.institution || {};
    return {
      // Sem vinculo nao ha instituicao, e a Home diz isso em vez de inventar.
      instituicao: i.name || 'Sem instituição vinculada',
      // O NOME quando ha cadastro; o identificador so como ultimo recurso.
      // "aluno_teste_a" numa tela projetada denuncia que e um ambiente de
      // teste - e o nome estava no banco o tempo todo.
      pessoa: u.name || u.external_id || '',
      papel: PAPEIS[u.role] || '',
    };
  }

  var PAPEIS = {
    STUDENT: 'Aluno',
    TEACHER: 'Professor',
    COORDINATOR: 'Coordenação',
    DIRECTOR: 'Direção',
    SECRETARY: 'Secretaria',
    PLATFORM_ADMIN: 'Administração',
  };

  var PortalUI = {
    separar: separar,
    cartao: cartao,
    contexto: contexto,
    PAPEIS: PAPEIS,
    DISPONIVEL: DISPONIVEL,
    EM_BREVE: EM_BREVE,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = PortalUI;
  } else {
    raiz.PortalUI = PortalUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
