// Trusted parser only. Never imports or executes application source.
const fs = require('fs');
const ts = require(process.argv[2]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function fail(message) { throw new Error(message); }
function parse(path, source) {
  const sf = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  if (sf.parseDiagnostics.length) fail('Invalid TSX syntax');
  return sf;
}
function descendants(node, predicate) {
  const found = [];
  function visit(n) { if (predicate(n)) found.push(n); ts.forEachChild(n, visit); }
  visit(node); return found;
}
const sf = parse(input.path, input.source);
const line = n => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;
const literal = n => ts.isStringLiteral(n) || ts.isJsxText(n);
const matches = descendants(sf, n => literal(n) && n.text.trim() === input.label);
const selected = matches.filter(n => line(n) === input.line);
if (selected.length !== 1) fail('Expected one exact JSX label at this source line');
let value = selected[0], control;
if (ts.isJsxAttribute(value.parent) && ['placeholder', 'aria-label'].includes(value.parent.name.text)) {
  control = value.parent.parent.parent;
} else {
  // Only direct child text or literal conditional branches are supported.
  let expression = value;
  while (ts.isConditionalExpression(expression.parent) &&
         [expression.parent.whenTrue, expression.parent.whenFalse].includes(expression)) {
    expression = expression.parent;
  }
  if (ts.isJsxExpression(expression.parent)) expression = expression.parent;
  if (!ts.isJsxElement(expression.parent)) fail('Label is not a direct JSX child or supported attribute');
  control = expression.parent.openingElement;
}
if (!ts.isJsxOpeningElement(control) && !ts.isJsxSelfClosingElement(control)) fail('Not a JSX control');
if (control.attributes.properties.some(ts.isJsxSpreadAttribute)) fail('Call-site spread may override identity');
let owner = control.parent;
while (owner && !(ts.isVariableDeclaration(owner) || ts.isFunctionDeclaration(owner))) owner = owner.parent;
if (!owner || !owner.name || !ts.isIdentifier(owner.name)) fail('Unsupported JSX owner');
if (matches.filter(n => n.pos >= owner.pos && n.end <= owner.end).length !== 1) fail('Ambiguous label in owner');
const tag = control.tagName.getText(sf);
let wrapperPath = null;
if (tag !== input.native_tag) {
  const imports = [];
  for (const statement of sf.statements) {
    if (!ts.isImportDeclaration(statement)) continue;
    const bindings = statement.importClause?.namedBindings;
    if (!bindings || !ts.isNamedImports(bindings)) continue;
    for (const entry of bindings.elements) {
      if (entry.name.text === tag) imports.push({ module: statement.moduleSpecifier.text,
        export: (entry.propertyName || entry.name).text });
    }
  }
  const bindings = input.wrappers.filter(b => imports.some(i => i.module === b.module && i.export === b.export)
    && b.native_tag === input.native_tag);
  if (bindings.length !== 1) fail('Component import needs one configured native wrapper binding');
  const binding = bindings[0];
  const wrapper = parse(binding.path, binding.source);
  const declarations = descendants(wrapper, n => ts.isVariableDeclaration(n) && n.name.getText(wrapper) === binding.export);
  if (declarations.length !== 1) fail('Ambiguous wrapper declaration');
  const declaration = declarations[0];
  const jsx = descendants(declaration, n => ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n));
  if (jsx.length !== 1 || jsx[0].tagName.getText(wrapper) !== input.native_tag) fail('Wrapper is not a single native control');
  // Narrow supported adapter: forwardRef(({..., ...props}, ref) => <native {...props}/>).
  const init = declaration.initializer;
  if (!ts.isCallExpression(init) || init.expression.getText(wrapper) !== 'forwardRef') fail('Unsupported wrapper factory');
  const callback = init.arguments[0];
  if (!callback || !ts.isArrowFunction(callback)) fail('Unsupported wrapper callback');
  const parameter = callback.parameters[0]?.name;
  if (!parameter || !ts.isObjectBindingPattern(parameter)) fail('Wrapper props must use a rest binding');
  const rest = parameter.elements.filter(n => n.dotDotDotToken);
  const attrs = jsx[0].attributes.properties;
  const spreads = attrs.filter(ts.isJsxSpreadAttribute);
  if (rest.length !== 1 || spreads.length !== 1 || attrs[attrs.length - 1] !== spreads[0]
      || spreads[0].expression.getText(wrapper) !== rest[0].name.getText(wrapper)) fail('Wrapper does not forward props last');
  const consumed = parameter.elements.filter(n => !n.dotDotDotToken).map(n => (n.propertyName || n.name).getText(wrapper));
  if (['placeholder','aria-label','children'].some(n => consumed.includes(n))) fail('Wrapper consumes control identity');
  wrapperPath = binding.path;
}
const typeAttr = control.attributes.properties.find(n => ts.isJsxAttribute(n) && n.name.text === 'type');
if (typeAttr && (!typeAttr.initializer || !ts.isStringLiteral(typeAttr.initializer) || typeAttr.initializer.text !== input.observed_type)) {
  fail('Observed control type differs from source');
}
process.stdout.write(JSON.stringify({owner: owner.name.text, owner_start_line: line(owner),
  jsx_tag: tag, native_tag: input.native_tag, wrapper_path: wrapperPath}));
