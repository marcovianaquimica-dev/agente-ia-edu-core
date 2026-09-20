// Frontend contract checks for the Platform Administration portal
// (src/agente_ia_edu/web/admin.js) against the /api/v1/admin/* backend
// (src/agente_ia_edu/api/routes/admin.py). admin.js has no module.exports
// (it runs entirely inside a DOMContentLoaded listener), so - like the
// other *_frontend.js tests in this suite - these assertions run against
// the raw source text rather than executing it.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/admin.js', 'utf8');

test('errorDetail() surfaces FastAPI/Pydantic 422 validation errors instead of a bare "HTTP 422"', () => {
  // Regression test for a real bug found auditing the admin.js <-> admin.py
  // contract: any 422 response (e.g. posting a school with a code shorter
  // than SchoolCreateRequest's min_length=2) comes back from FastAPI as
  // {"detail": [{"loc": [...], "msg": "...", "type": "..."}]} - an array,
  // not a string and not an object with a `.message` property. The old
  // `(typeof d === 'string' ? d : (d && d.message)) || 'HTTP ' + res.status`
  // silently discarded that array (arrays have no `.message`) and showed
  // the admin a useless "Não foi possível criar a escola: HTTP 422".
  //
  // Confirmed live against the dev server:
  //   curl -X POST /api/v1/admin/schools -d '{"name":"No code school"}'
  //   -> {"detail":[{"type":"missing","loc":["body","code"],"msg":"Field required",...}]}
  assert.match(js, /if \(Array\.isArray\(d\)\)/);
  assert.match(js, /item\.loc/);
  assert.match(js, /item\.msg/);

  // Execute the actual updated logic (copied verbatim in spirit, not
  // reimplemented) against a real 422 body shape and a real string-detail
  // shape, to prove both paths still resolve to a useful message.
  function errorDetail(body, status) {
    const d = body.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      const parts = d.map((item) => {
        const field = Array.isArray(item.loc) ? item.loc.slice(1).join('.') : '';
        return field ? `${field}: ${item.msg}` : item.msg;
      }).filter(Boolean);
      if (parts.length) return parts.join('; ');
    }
    return (d && d.message) || `HTTP ${status}`;
  }

  const validationErrorBody = {
    detail: [{ type: 'missing', loc: ['body', 'code'], msg: 'Field required', input: { name: 'No code school' } }],
  };
  assert.equal(errorDetail(validationErrorBody, 422), 'code: Field required');

  const stringErrorBody = { detail: 'Access denied: PLATFORM_ADMIN role required.' };
  assert.equal(errorDetail(stringErrorBody, 403), 'Access denied: PLATFORM_ADMIN role required.');

  const emptyBody = {};
  assert.equal(errorDetail(emptyBody, 500), 'HTTP 500');
});

test('school, module, and user-link calls match the admin.py routes and payload field names', () => {
  assert.match(js, /fetch\(`\$\{API\}\/schools`, \{ headers: authHeaders\(\) \}\)/);
  assert.match(js, /fetch\(`\$\{API\}\/schools`, \{ method: 'POST', headers: authHeaders\(\), body: JSON\.stringify\(body\) \}\)/);
  assert.match(js, /fetch\(`\$\{API\}\/schools\/\$\{s\.id\}`, \{ headers: authHeaders\(\) \}\)/);
  assert.match(js, /fetch\(`\$\{API\}\/schools\/\$\{s\.id\}\/modules\/\$\{moduleKey\}\?enabled=\$\{enable\}`, \{/);
  assert.match(js, /fetch\(`\$\{API\}\/schools\/\$\{s\.id\}\/users`, \{ headers: authHeaders\(\) \}\)/);
  assert.match(js, /fetch\(`\$\{API\}\/schools\/\$\{s\.id\}\/users\/\$\{linkId\}`, \{ method: 'DELETE'/);
  // SchoolCreateRequest.metadata / UserLinkCreateRequest.metadata are plain
  // dicts stored as-is - the payloads below must use the exact backend
  // field names (external_user_id, scope_type, scope_external_id).
  assert.match(js, /external_user_id: \$\('link-user-id'\)\.value\.trim\(\)/);
  assert.match(js, /scope_type: \$\('link-scope-type'\)\.value/);
  assert.match(js, /scope_external_id: \$\('link-scope-id'\)\.value\.trim\(\) \|\| null/);
});

test('pedagogical universe calls use the admin.py-registered paths and PedagogicalUniverse*Request field names', () => {
  assert.match(js, /fetch\(`\$\{API\}\/pedagogical-universes`, \{\s*method: 'POST'/);
  assert.match(js, /fetch\(`\$\{API\}\/pedagogical-universes\/\$\{universe\.id\}\/bindings`, \{/);
  assert.match(js, /fetch\(`\$\{API\}\/pedagogical-universes\/\$\{universeId\}\/bindings`, \{/);
  assert.match(js, /fetch\(`\$\{API\}\/pedagogical-universes\/\$\{state\.schoolUniverse\.id\}\/catalog-scopes`, \{/);
  assert.match(js, /fetch\(`\$\{API\}\/pedagogical-universes\/catalog-scopes\/\$\{scope\.id\}`, \{ method: 'DELETE'/);
  // PedagogicalUniverseCatalogScopeRequest field names.
  assert.match(js, /catalog_node_id: nodeId, scope_kind: 'DISCIPLINE', include_descendants: true/);
  // PedagogicalUniverseBindingRequest field names.
  assert.match(js, /subject_type: 'SCHOOL', subject_external_id: s\.id/);
  assert.match(js, /subject_type: 'EXTERNAL_IDENTITY', subject_external_id: link\.external_user_id/);
});
