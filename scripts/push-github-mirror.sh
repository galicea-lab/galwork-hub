#!/usr/bin/env bash
# publikacja drzewa z scripts/public-export.mjs jako kolejnego
# commita na gałęzi main repozytorium GitHub, razem z tagiem wydania.
# Repozytorium GitHub musi istnieć i mieć gałąź main. Jeśli tag jest już
# na GitHubie (ponowne uruchomienie joba), skrypt niczego nie zmienia.
#
# Opis commita i tagu w mirrorze: temat "<projekt> <tag>", treść = adnotacja
# tagu w repozytorium źródłowym (git tag -a <tag> -m/-F "...") - jeśli coś
# do niej wpisano poza samą nazwą tagu - i stopka Source-Commit.
#
# Użycie: MIRROR_TOKEN=... scripts/push-github-mirror.sh <katalog-eksportu> <właściciel/repo> <tag> <commit-źródłowy>
# (uruchamiać z katalogu repozytorium źródłowego - stąd czytana jest adnotacja tagu)
set -euo pipefail

export_dir="$(cd "${1:?Podaj katalog eksportu}" && pwd)"
repo="${2:?Podaj repozytorium GitHub (właściciel/repo)}"
tag="${3:?Podaj tag wydania}"
source_commit="${4:?Podaj commit źródłowy}"
: "${MIRROR_TOKEN:?Brak MIRROR_TOKEN}"

# Opis wydania z adnotacji tagu. actions/checkout nie zawsze pobiera obiekt
# tagu adnotowanego - dociągamy go jawnie (błąd nie przerywa publikacji).
project="${repo##*/}"
git fetch --quiet --force origin "refs/tags/${tag}:refs/tags/${tag}" 2>/dev/null || true
notes=""
if [ "$(git cat-file -t "refs/tags/${tag}" 2>/dev/null)" = "tag" ]; then
  notes="$(git tag -l --format='%(contents)' "$tag" | sed -e '/^-----BEGIN PGP SIGNATURE-----/,$d')"
  notes="$(printf '%s\n' "$notes" | sed -e 's/[[:space:]]*$//' | sed -e '/./,$!d')"
  if [ "$notes" = "$tag" ] || [ "$notes" = "${tag#v}" ]; then
    notes=""
  fi
fi
message_file="$(mktemp)"
{
  printf '%s %s\n' "$project" "$tag"
  if [ -n "$notes" ]; then
    printf '\n%s\n' "$notes"
  fi
  printf '\nSource-Commit: %s\n' "$source_commit"
} > "$message_file"

work="$(mktemp -d)"
trap 'rm -rf "$work" "$message_file"' EXIT
git clone --depth 1 --branch main \
  "https://x-access-token:${MIRROR_TOKEN}@github.com/${repo}.git" "$work/mirror"
cd "$work/mirror"
git config user.name "galicea-mirror"
git config user.email "mirror@galicea.org"

if git ls-remote --exit-code --tags origin "refs/tags/${tag}" >/dev/null; then
  echo "Tag ${tag} jest już w ${repo} - pomijam."
  exit 0
fi

find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "${export_dir}/." .
git add -A
if git diff --cached --quiet; then
  echo "Brak zmian w drzewie - publikuję tylko tag."
else
  git commit -q -F "$message_file"
fi
git tag -a "$tag" -F "$message_file"
git push --atomic origin HEAD:main "refs/tags/${tag}"
