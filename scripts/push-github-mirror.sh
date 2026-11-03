#!/usr/bin/env bash
# publikacja drzewa z scripts/public-export.mjs jako kolejnego
# commita na gałęzi main repozytorium GitHub, razem z tagiem wydania.
# Repozytorium GitHub musi istnieć i mieć gałąź main. Jeśli tag jest już
# na GitHubie (ponowne uruchomienie joba), skrypt niczego nie zmienia.
#
# Użycie: MIRROR_TOKEN=... scripts/push-github-mirror.sh <katalog-eksportu> <właściciel/repo> <tag> <commit-źródłowy>
set -euo pipefail

export_dir="$(cd "${1:?Podaj katalog eksportu}" && pwd)"
repo="${2:?Podaj repozytorium GitHub (właściciel/repo)}"
tag="${3:?Podaj tag wydania}"
source_commit="${4:?Podaj commit źródłowy}"
: "${MIRROR_TOKEN:?Brak MIRROR_TOKEN}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
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
message="Release ${tag} (source ${source_commit})"
if git diff --cached --quiet; then
  echo "Brak zmian w drzewie - publikuję tylko tag."
else
  git commit -q -m "$message"
fi
git tag -a "$tag" -m "$message"
git push --atomic origin HEAD:main "refs/tags/${tag}"
