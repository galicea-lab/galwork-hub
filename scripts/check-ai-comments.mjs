#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const release = process.argv.includes('--release');
const root = execFileSync('git', ['rev-parse', '--show-toplevel'], {
  encoding: 'utf8',
}).trim();
process.chdir(root);

const sourceExtensions = new Set([
  '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx',
  '.py', '.sh', '.bash', '.yml', '.yaml', '.sql',
  '.css', '.scss', '.html', '.htm',
]);
const ignoredPrefixes = [
  '.git/', 'node_modules/', 'dist/', '.next/', 'coverage/', 'docs/internal/',
  'dev/', 'backlog/',
];
const markerPattern = /\bAI(?:\s+|-BEGIN\s+)(\d{4}-\d{2}-\d{2})\b/g;
const endPattern = /\bAI-END\b/;
const datePattern = /^\d{4}-\d{2}-\d{2}$/;
const today = new Date().toISOString().slice(0, 10);

const errors = [];
const markers = [];

function isCommentContext(line, index) {
  const before = line.slice(0, index);
  const trimmed = line.trimStart();
  if (/^(?:\/\/|#|\/\*|\*|--|<!--|\{\/\*)/.test(trimmed)) return true;
  return /(?:\/\/|#|\/\*|<!--|\{\/\*|--)\s*$/.test(before);
}

// Python: znacznik w docstringu (albo innym napisie w potrójnych cudzysłowach)
// też liczy się jako komentarz. public-export.mjs i tak znajdzie go w
// eksporcie - lepiej zatrzymać wydanie tutaj, zanim zbuduje się obraz.
// Zwraca dla każdej linii zakresy [od, do) leżące wewnątrz takiego napisu.
function pythonTripleStringRanges(lines) {
  const result = [];
  let delimiter = null;
  for (const line of lines) {
    const ranges = [];
    let start = 0;
    let i = 0;
    while (i < line.length) {
      if (delimiter) {
        if (line[i] === '\\') {
          i += 2;
        } else if (line.startsWith(delimiter, i)) {
          ranges.push([start, i]);
          i += 3;
          delimiter = null;
        } else {
          i += 1;
        }
        continue;
      }
      const ch = line[i];
      if (ch === '#') break;
      if (line.startsWith('"""', i) || line.startsWith("'''", i)) {
        delimiter = line.slice(i, i + 3);
        i += 3;
        start = i;
        continue;
      }
      if (ch === '"' || ch === "'") {
        let j = i + 1;
        while (j < line.length && line[j] !== ch) j += line[j] === '\\' ? 2 : 1;
        i = j + 1;
        continue;
      }
      i += 1;
    }
    if (delimiter) ranges.push([start, line.length]);
    result.push(ranges);
  }
  return result;
}

function isValidDate(value) {
  if (!datePattern.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return (
    !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString().slice(0, 10) === value &&
    value >= '2024-01-01' &&
    value <= today
  );
}

const files = execFileSync(
  'git',
  ['ls-files', '-z', '--cached', '--others', '--exclude-standard'],
  { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 },
)
  .split('\0')
  .filter(Boolean);

for (const file of files) {
  if (ignoredPrefixes.some((prefix) => file.startsWith(prefix))) continue;
  if (!sourceExtensions.has(path.extname(file).toLowerCase())) continue;
  if (file === 'scripts/check-ai-comments.mjs') continue;

  let text;
  try {
    text = readFileSync(file, 'utf8');
  } catch {
    continue;
  }

  const openBegins = [];
  const lines = text.split(/\r?\n/);
  const stringRanges = path.extname(file).toLowerCase() === '.py'
    ? pythonTripleStringRanges(lines)
    : null;

  lines.forEach((line, lineIndex) => {
    const lineNumber = lineIndex + 1;
    const inContext = (index) => isCommentContext(line, index) ||
      (stringRanges !== null &&
        stringRanges[lineIndex].some(([from, to]) => index >= from && index < to));
    markerPattern.lastIndex = 0;
    let match;
    while ((match = markerPattern.exec(line)) !== null) {
      if (!inContext(match.index)) continue;
      const date = match[1];
      const isBegin = match[0].startsWith('AI-BEGIN');
      markers.push({ file, line: lineNumber, date, text: line.trim() });

      if (!isValidDate(date)) {
        errors.push(`${file}:${lineNumber}: nieprawidłowa data AI: ${date}`);
      }
      if (isBegin) {
        if (openBegins.length > 0) {
          errors.push(
            `${file}:${lineNumber}: zagnieżdżony AI-BEGIN ` +
            `(poprzedni w linii ${openBegins.at(-1)})`,
          );
        }
        openBegins.push(lineNumber);
      }
    }

    const endIndex = line.search(endPattern);
    if (endIndex >= 0 && inContext(endIndex)) {
      if (openBegins.length === 0) {
        errors.push(`${file}:${lineNumber}: AI-END bez AI-BEGIN`);
      } else {
        openBegins.pop();
      }
    }
  });

  for (const lineNumber of openBegins) {
    errors.push(`${file}:${lineNumber}: brak AI-END dla AI-BEGIN`);
  }
}

if (errors.length > 0) {
  for (const error of errors) console.error(`BŁĄD ${error}`);
  console.error(`\n${errors.length} błędów w znacznikach AI.`);
  process.exit(1);
}

if (release && markers.length > 0) {
  console.error(
    `Znaleziono ${markers.length} nieprzejrzanych znaczników AI. ` +
    'Usuń je, przenieś do docs/internal/ albo przepisz jako zwykłe komentarze.',
  );
  for (const marker of markers) {
    console.error(`  ${marker.file}:${marker.line}: ${marker.text}`);
  }
  process.exit(1);
}

const filesWithMarkers = new Set(markers.map((marker) => marker.file)).size;
console.log(
  `Znaczniki AI: ${markers.length}` +
  (markers.length > 0 ? ` w ${filesWithMarkers} plikach` : ''),
);
if (markers.length > 0 && !release) {
  for (const marker of markers) {
    console.log(`  ${marker.file}:${marker.line}: ${marker.text}`);
  }
}
