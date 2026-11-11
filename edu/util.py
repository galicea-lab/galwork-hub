# wspólne funkcje pomocnicze modułu edu.

from urllib.parse import quote


def content_disposition(filename: str, inline: bool = False) -> str:
  """Nagłówek Content-Disposition bezpieczny dla nazw z polskimi znakami.

  Nagłówki HTTP muszą być w latin-1, więc nazwę w UTF-8 podajemy w
  `filename*` (RFC 6266), a w `filename` - jej wersję ASCII.
  """
  name = filename.replace('"', '').replace('\r', '').replace('\n', '') or 'plik'
  ascii_name = name.encode('ascii', 'replace').decode('ascii').replace('?', '_')
  kind = 'inline' if inline else 'attachment'
  return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
