# Zasady dla agentów AI

## Oznaczanie wstawek

- Każdy komentarz dodany lub zmieniony przez agenta zaczynaj od `AI RRRR-MM-DD:`
  z rzeczywistą datą w formacie ISO.
- Składnia zależnie od języka:
  - Python, Shell, YAML: `# AI 2026-10-28: ...`
  - TypeScript, JavaScript, CSS: `// AI 2026-10-28: ...` lub `/* AI 2026-10-28: ... */`
  - TSX/JSX: `{/* AI 2026-10-28: ... */}`
  - HTML, Markdown: `<!-- AI 2026-10-28: ... -->`
  - SQL: `-- AI 2026-10-28: ...`
- W komentarzu wielolinijkowym znacznik umieszczaj tylko w pierwszej linii.
- Nie zmieniaj daty istniejącego znacznika. Nowa zmiana = nowa data.
- Nie oznaczaj kodu, którego nie napisałeś, i nie usuwaj cudzych znaczników.
- Jeśli wstawiasz blok kodu bez komentarza, dodaj bezpośrednio przed nim
  komentarz `AI RRRR-MM-DD: ...`. Dla dokładnego zakresu możesz użyć pary
  `AI-BEGIN RRRR-MM-DD` / `AI-END`; oba znaczniki są wtedy obowiązkowe,
  z tą samą datą i bez zagnieżdżeń.

## Komentarze robocze

- Notatki wewnętrzne, nazwy klientów, adresy wewnętrzne i TODO trafiają do
  `dev/` albo do komunikatu commita, nie do kodu publicznego.
- W kodzie zostaw tylko komentarze, które mogą być opublikowane.

## Przed zakończeniem pracy

- Uruchom `npm run check:ai`.
- Nie commituj i nie pushuj bez wyraźnego polecenia.
- Nie twórz i nie pushuj tagów `v*` bez wyraźnego polecenia — uruchamiają
  publikację pakietów npm oraz mirrora GitHub.
