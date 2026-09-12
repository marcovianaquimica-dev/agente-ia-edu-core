const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('src/agente_ia_edu/web/teacher.html', 'utf8');
const js = fs.readFileSync('src/agente_ia_edu/web/teacher.js', 'utf8');
const css = fs.readFileSync('src/agente_ia_edu/web/styles.css', 'utf8');

test('keeps one shared teacher navigation with an accessible mobile trigger', () => {
  assert.match(html, /id="teacher-navigation"/);
  assert.match(html, /id="teacher-mobile-menu-toggle"/);
  assert.match(html, /aria-label="Abrir menu"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /aria-controls="teacher-navigation"/);
  assert.match(html, /id="teacher-sidebar-backdrop"/);
  assert.match(html, /id="teacher-mobile-menu-close"/);
  assert.match(html, /aria-label="Fechar menu"/);
});

test('mobile menu opens, closes, and reuses switchView navigation', () => {
  assert.match(js, /function setMobileMenu\(open\)/);
  assert.match(js, /sidebar\.classList\.toggle\('is-open', open\)/);
  assert.match(js, /mobileMenuToggle\.setAttribute\('aria-expanded', String\(open\)\)/);
  assert.match(js, /sidebarBackdrop\.addEventListener\('click', closeMobileMenu\)/);
  assert.match(js, /mobileMenuClose\.addEventListener\('click', closeMobileMenu\)/);
  assert.match(js, /event\.key === 'Escape'/);
  assert.match(js, /switchView\(targetView\);\s+closeMobileMenu\(\)/);
});

test('desktop sidebar and mobile drawer responsive rules remain available', () => {
  assert.match(css, /@media \(max-width: 768px\)/);
  assert.match(css, /\.sidebar\.is-open/);
  assert.match(css, /\.sidebar-backdrop\.is-visible/);
  assert.match(css, /width: min\(82vw, 320px\)/);
  assert.match(css, /@media \(min-width: 769px\)/);
  assert.match(css, /\.mobile-menu-toggle \{\s+display: none;/);
});