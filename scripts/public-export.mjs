#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
const root = execFileSync('git', ['rev-parse', '--show-toplevel'], {
  encoding: 'utf8',
}).trim();
process.chdir(root);

function argument(name, fallback) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : fallback;
}

const out = path.resolve(
  argument('--out', `../${path.basename(root)}-public`),
);
const allowDirty = argv.includes('--allow-dirty');
const release = argv.includes('--release');
const requireLicense = argv.includes('--require-license') || release;
const force = argv.includes('--force');
const sourceUrl = argument('--source-url', '');
const tag = argument('--tag', '');

if (out === root || out === path.parse(out).root) {
  console.error('Nie można eksportować do korzenia repo ani do /.');
  process.exit(1);
}

const dirty = execFileSync('git', ['status', '--porcelain'], {
  encoding: 'utf8',
}).trim();
if (dirty && !allowDirty) {
  console.error(
    'Katalog roboczy nie jest czysty. Zrób commit albo użyj --allow-dirty ' +
    '(wtedy eksport i tak powstaje z HEAD).',
  );
  process.exit(1);
}

const commit = execFileSync('git', ['rev-parse', 'HEAD'], {
  encoding: 'utf8',
}).trim();
const tracked = execFileSync(
  'git',
  ['ls-tree', '-r', '--name-only', '-z', 'HEAD'],
  { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 },
)
  .split('\0')
  .filter(Boolean);

function globToRegExp(pattern) {
  if (pattern.endsWith('/')) {
    return new RegExp(`^${escapeRegExp(pattern)}.*$`);
  }
  let expression = '^';
  for (let index = 0; index < pattern.length; index += 1) {
    const character = pattern[index];
    if (character === '*') {
      if (pattern[index + 1] === '*') {
        expression += '.*';
        index += 1;
      } else {
        expression += '[^/]*';
      }
    } else if (character === '?') {
      expression += '[^/]';
    } else {
      expression += escapeRegExp(character);
    }
  }
  return new RegExp(`${expression}$`);
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const patterns = readFileSync('.public-paths', 'utf8')
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter((line) => line && !line.startsWith('#'))
  .map((pattern) => ({ pattern, regexp: globToRegExp(pattern) }));

if (patterns.length === 0) {
  console.error('.public-paths jest pusty.');
  process.exit(1);
}

const denyPatterns = [
  /(^|\/)\.env(\.|$)/,
  /(^|\/)\.npmrc$/,
  /(^|\/)publish\.sh$/,
  /(^|\/)unpublish\.sh$/,
  /(^|\/)id_(rsa|dsa|ecdsa|ed25519)/,
  /\.(pem|key|p12|pfx|tgz)$/,
  /(^|\/)(node_modules|dist|\.next|coverage|notes)\//,
  /^docs\/internal\//,
  /^(dev|backlog)\//,
  /^\.forgejo\//,
  /^\.github\//,
  /(^|\/)secret\//,
  /(^|\/)config\/config\.ini$/,
  /(^|\/)\.(idea|vscode|venv)\//,
];

const errors = [];
const warnings = [];
const usedPatterns = new Set();
const selected = [];

for (const file of tracked) {
  const matching = patterns.find(({ regexp }) => regexp.test(file));
  if (!matching) continue;
  usedPatterns.add(matching.pattern);
  const denied = denyPatterns.find((regexp) => regexp.test(file));
  if (denied) {
    errors.push(`Zabroniona ścieżka w eksporcie: ${file}`);
  } else {
    selected.push(file);
  }
}

for (const { pattern } of patterns) {
  if (!usedPatterns.has(pattern)) {
    warnings.push(`Wzorzec bez dopasowania w HEAD: ${pattern}`);
  }
}

if (selected.length === 0) {
  console.error('Brak plików do eksportu.');
  process.exit(1);
}

if (existsSync(out)) {
  if (!force) {
    console.error(
      `Katalog docelowy już istnieje: ${out}\n` +
      'Użyj --force, żeby go usunąć i wyeksportować ponownie.',
    );
    process.exit(1);
  }
  rmSync(out, { recursive: true, force: true });
}
mkdirSync(out, { recursive: true });

const archive = path.join(os.tmpdir(), `galwork-public-${process.pid}.tar`);
execFileSync(
  'git',
  ['archive', '--format=tar', '-o', archive, 'HEAD', '--', ...selected],
  { stdio: 'inherit' },
);
execFileSync('tar', ['-xf', archive, '-C', out], { stdio: 'inherit' });
rmSync(archive, { force: true });

function relativeFiles(directory) {
  const result = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      result.push(...relativeFiles(absolute));
    } else if (entry.isFile()) {
      result.push(path.relative(out, absolute).split(path.sep).join('/'));
    }
  }
  return result;
}

const generated = new Date().toISOString();
const publishing = [
  '# Public mirror',
  '',
  'This repository is a read-only release mirror of the private Galwork',
  'development repository.',
  '',
  `- Source commit: \`${commit}\``,
  ...(tag ? [`- Release tag: \`${tag}\``] : []),
  ...(sourceUrl ? [`- Source: ${sourceUrl}`] : []),
  `- Generated: ${generated}`,
  '',
  'Do not commit here. Changes are published from tagged releases.',
  '',
].join('\n');
writeFileSync(path.join(out, 'PUBLISHING.md'), publishing);

const licenseNames = [
  'LICENSE', 'LICENSE.md', 'LICENSE.txt', 'COPYING', 'COPYING.md',
];
const hasLicense = licenseNames.some((name) => existsSync(path.join(out, name)));
if (!hasLicense) {
  const message =
    'Brak pliku LICENSE w korzeniu eksportu. ' +
    'Uzupełnij licencję przed publikacją.';
  if (requireLicense) errors.push(message);
  else warnings.push(message);
}

const textExtensions = new Set([
  '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.json', '.md',
  '.py', '.sh', '.yml', '.yaml', '.sql', '.css', '.scss',
  '.html', '.htm', '.txt', '.xml', '.svg',
]);
const sourceExtensions = new Set([
  '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.py', '.sh',
  '.sql', '.css', '.scss', '.html', '.htm',
]);
const secretPatterns = [
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, 'prywatny klucz'],
  [/\bAKIA[0-9A-Z]{16}\b/, 'klucz AWS'],
  [/\bgh[pousr]_[A-Za-z0-9]{36,}\b/, 'token GitHub'],
  [/\bgithub_pat_[A-Za-z0-9_]{20,}\b/, 'GitHub PAT'],
  [/\bglpat-[A-Za-z0-9_-]{20,}\b/, 'token GitLab'],
  [/\bxox[baprs]-[A-Za-z0-9-]{10,}\b/, 'token Slack'],
  [/\b(sk|pk)-[A-Za-z0-9]{20,}\b/, 'klucz API'],
];
const aiPattern = /\bAI(?:\s+|-BEGIN\s+)\d{4}-\d{2}-\d{2}\b/;

const exportedFiles = relativeFiles(out);
const aiHits = [];

for (const file of exportedFiles) {
  const extension = path.extname(file).toLowerCase();
  if (!textExtensions.has(extension)) continue;
  const absolute = path.join(out, file);
  if (statSync(absolute).size > 2 * 1024 * 1024) continue;

  const text = readFileSync(absolute, 'utf8');
  for (const [regexp, label] of secretPatterns) {
    if (regexp.test(text)) errors.push(`${file}: możliwy sekret (${label})`);
  }
  if (sourceExtensions.has(extension) && aiPattern.test(text)) {
    aiHits.push(file);
  }
}

if (aiHits.length > 0) {
  const message =
    `Znaleziono znaczniki AI w eksporcie: ${aiHits.join(', ')}. ` +
    'Przejrzyj i usuń je przed publikacją.';
  if (release) errors.push(message);
  else warnings.push(message);
}

for (const warning of warnings) console.warn(`UWAGA ${warning}`);
for (const error of errors) console.error(`BŁĄD ${error}`);

const totalBytes = exportedFiles.reduce(
  (sum, file) => sum + statSync(path.join(out, file)).size,
  0,
);
console.log(`Eksport: ${out}`);
console.log(`Commit: ${commit}${tag ? ` (${tag})` : ''}`);
console.log(`Pliki: ${exportedFiles.length}, bajty: ${totalBytes}`);

if (errors.length > 0) {
  console.error(`\nEksport przerwany: ${errors.length} błędów.`);
  process.exit(1);
}
