/* Compiler-based references from immutable in-memory sources. No repository plugins execute. */
const ts = require('typescript');
const fs = require('node:fs');
const path = require('node:path').posix;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const canonical = p => path.resolve('/repo', p.replaceAll('\\', '/'));
const files = new Map(Object.entries(input.files).map(([p, text]) => [canonical(p), text]));
const host = {
  fileExists: p => files.has(canonical(p)), readFile: p => files.get(canonical(p)),
  directoryExists: p => [...files.keys()].some(f => f.startsWith(canonical(p) + '/')),
  getDirectories: () => [], getCurrentDirectory: () => '/repo',
  getCanonicalFileName: canonical, useCaseSensitiveFileNames: () => true,
  getNewLine: () => '\n', getDefaultLibFileName: () => '/repo/__no_lib__.d.ts', writeFile: () => {},
};
const configName = canonical(input.tsconfig);
const readConfig = ts.readConfigFile(configName, host.readFile);
if (readConfig.error) throw new Error('Invalid config');
function entries(directory) {
  const prefix = canonical(directory) + '/', fileNames = new Set(), directories = new Set();
  for (const file of files.keys()) {
    if (!file.startsWith(prefix)) continue;
    const rest = file.slice(prefix.length), slash = rest.indexOf('/');
    if (slash < 0) fileNames.add(rest); else directories.add(rest.slice(0, slash));
  }
  return {files: [...fileNames].sort(), directories: [...directories].sort()};
}
host.readDirectory = (root, extensions, excludes, includes, depth) =>
  ts.matchFiles(canonical(root), extensions, excludes, includes, true, '/repo', depth, entries, canonical);
const parsed = ts.parseJsonConfigFileContent(readConfig.config, {...host, useCaseSensitiveFileNames: true}, path.dirname(configName));
if (parsed.errors.length) throw new Error('Invalid compiler configuration or no included sources');
const options = {...parsed.options, noEmit: true, noLib: true, plugins: [], types: [], incremental: false};
host.getSourceFile = (name, languageVersion) => {
  const text = host.readFile(name);
  return text === undefined ? undefined : ts.createSourceFile(canonical(name), text, languageVersion, true);
};
const roots = parsed.fileNames;
const program = ts.createProgram(roots, options, host);
if (program.getSyntacticDiagnostics().length) throw new Error('Source syntax errors');
const checker = program.getTypeChecker();
const sources = program.getSourceFiles().filter(s => files.has(canonical(s.fileName)) && /\.(tsx?|jsx?)$/.test(s.fileName));
const nodes = [], edges = new Map(), declarations = new Map(), modules = new Map();
const relative = file => path.relative('/repo', canonical(file));
function edge(source, target, type, properties = {}) {
  if (source === target) return;
  const id = `${type}:${source}->${target}`;
  if (!edges.has(id)) edges.set(id, {id, source, target, type, status: 'CONFIRMED', properties});
}
for (const source of sources) {
  const file = relative(source.fileName), fileID = `file:${file}`, moduleID = `${file}#module`;
  nodes.push({id: fileID, kind: 'CodeFile', name: file, properties: {path: file}});
  nodes.push({id: moduleID, kind: 'CodeSymbol', name: '<module>', properties: {path: file, start_line: 1, end_line: source.getLineAndCharacterOfPosition(source.end).line + 1}});
  modules.set(canonical(source.fileName), moduleID);
  declarations.set(source, moduleID);
  edge(fileID, moduleID, 'DECLARES');
  function collect(node) {
    const eligible = ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node) || ts.isMethodDeclaration(node) || ts.isVariableDeclaration(node);
    if (eligible && node.name && ts.isIdentifier(node.name)) {
      const start = node.getStart(source), id = `${file}#${start}:${node.name.text}`;
      declarations.set(node, id);
      nodes.push({id, kind: 'CodeSymbol', name: node.name.text, properties: {
        path: file, start_line: source.getLineAndCharacterOfPosition(start).line + 1,
        end_line: source.getLineAndCharacterOfPosition(node.end).line + 1, declaration_kind: ts.SyntaxKind[node.kind],
      }});
      edge(fileID, id, 'DECLARES');
    }
    ts.forEachChild(node, collect);
  }
  collect(source);
}
const unresolved = new Set();
for (const source of sources) {
  function visit(node, owner, typeContext = false) {
    owner = declarations.get(node) || owner;
    typeContext = typeContext || ts.isTypeNode(node);
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      if (node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
        const spec = node.moduleSpecifier.text;
        const resolved = ts.resolveModuleName(spec, source.fileName, options, host).resolvedModule;
        const target = resolved && modules.get(canonical(resolved.resolvedFileName));
        if (target) {
          edge(`file:${relative(source.fileName)}`, `file:${relative(resolved.resolvedFileName)}`, 'IMPORTS', {specifier: spec, line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1});
          edge(modules.get(canonical(source.fileName)), target, 'DEPENDS_ON', {method: 'module-import', path: relative(source.fileName)});
        } else unresolved.add(`${relative(source.fileName)}: ${spec}`);
      }
      return;
    }
    if (!typeContext && ts.isIdentifier(node) && !(declarations.has(node.parent) && node.parent.name === node)) {
      let symbol = checker.getSymbolAtLocation(node);
      if (symbol && (symbol.flags & ts.SymbolFlags.Alias)) symbol = checker.getAliasedSymbol(symbol);
      for (const decl of symbol?.declarations || []) {
        const target = declarations.get(decl);
        if (target) edge(owner, target, 'DEPENDS_ON', {method: 'resolved-identifier', path: relative(source.fileName), line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1});
      }
    }
    ts.forEachChild(node, child => visit(child, owner, typeContext));
  }
  visit(source, modules.get(canonical(source.fileName)));
}
process.stdout.write(JSON.stringify({
  nodes: nodes.sort((a,b) => a.id.localeCompare(b.id)), edges: [...edges.values()].sort((a,b) => a.id.localeCompare(b.id)),
  diagnostics: [`Compiler: TypeScript ${ts.version}; static references only, not exhaustive runtime dependencies.`,
    'External packages, computed dynamic imports, reflection and UI/flow mappings are not analyzed.',
    ...[...unresolved].sort().map(s => `Unresolved or external module: ${s}`)],
}));
