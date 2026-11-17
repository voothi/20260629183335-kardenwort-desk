const fs = require('fs');
const assert = require('assert');
const acorn = require('U:/voothi/20260406202446-openspec/node_modules/acorn');
const tree = acorn.parse(fs.readFileSync('web/js/app.js', 'utf8'), {ecmaVersion: 'latest'});
const names = new Set(['rowHighlightGroup', 'collectCompoundVisualIndices', 'rowHighlightTokenData', 'sourceSpanIsRendered', 'collectRowHighlightOccurrences', 'indexHighlightGroups', 'boundGroupVisuals', 'claimHighlightGroups', 'resolveRowHighlightRoles', 'applyRowHighlightRole']);
const pure = new Set(['indexHighlightGroups', 'boundGroupVisuals', 'claimHighlightGroups', 'resolveRowHighlightRoles', 'rowHighlightTokenData']);
const measured = {};
function children(node) {
  return Object.values(node).flatMap(v => Array.isArray(v) ? v : [v]).filter(v => v && typeof v.type === 'string');
}
function inspect(fn, label, requirePure) {
  let cc = 1;
  function walk(node) {
    if (node !== fn && /Function/.test(node.type)) {
      inspect(node, label + '/callback@' + node.start, requirePure);
      return;
    }
    if (['IfStatement', 'ForStatement', 'ForInStatement', 'ForOfStatement', 'WhileStatement', 'DoWhileStatement', 'CatchClause', 'ConditionalExpression', 'LogicalExpression'].includes(node.type)) cc++;
    if (node.type === 'SwitchCase' && node.test) cc++;
    if (requirePure && node.type === 'Identifier') assert(!['window', 'document', 'fetch', 'localStorage', 'AppState'].includes(node.name), `${label}: impure ${node.name}`);
    children(node).forEach(walk);
  }
  walk(fn);
  measured[label] = cc;
  assert(cc <= 7, `${label}: CC=${cc}`);
}
function find(node) {
  if (node.type === 'FunctionDeclaration' && names.has(node.id.name)) inspect(node, node.id.name, pure.has(node.id.name));
  children(node).forEach(find);
}
find(tree);
assert([...names].every(n => n in measured));
console.log(JSON.stringify(measured, null, 2));
console.log('All new JavaScript helpers and callbacks CC <= 7; pure resolver tree has no browser/global state references.');
