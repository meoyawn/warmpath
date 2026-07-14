#!/usr/bin/env sh
set -eu

PACKAGE="warmpath"
TOKEN_FILE="${PYPI_TOKEN_FILE:-.pypi-token}"
export UV_CACHE_DIR="${RELEASE_UV_CACHE_DIR:-${TMPDIR:-/tmp}/warmpath-uv-cache}"

if test "$#" -ne 0; then
  echo "usage: $0" >&2
  exit 2
fi

version="$(uv version --short)"
tag="v${version}"

if ! test -f "$TOKEN_FILE"; then
  echo "$TOKEN_FILE is required for publishing." >&2
  exit 1
fi

token="$(tr -d '\r\n\t ' < "$TOKEN_FILE")"
test -n "$token"
case "$token" in
  pypi-*) ;;
  *) echo "$TOKEN_FILE must contain a PyPI token." >&2; exit 1 ;;
esac

rm -rf dist
if test -n "$(git status --porcelain --untracked-files=all)"; then
  echo "Working tree is not clean; commit before release." >&2
  exit 1
fi

if git show-ref --verify --quiet "refs/tags/$tag"; then
  echo "Tag $tag already exists locally." >&2
  exit 1
fi

remote_tag="$(git ls-remote --tags origin "refs/tags/$tag")"
if test -n "$remote_tag"; then
  echo "Tag $tag already exists on origin." >&2
  exit 1
fi

echo "Building artifacts"
uv build --no-sources

sdist="dist/${PACKAGE}-${version}.tar.gz"
wheel="dist/${PACKAGE}-${version}-py3-none-any.whl"

echo "Verifying artifacts"
test -f "$sdist"
test -f "$wheel"

tar tf "$sdist" | grep -q "^${PACKAGE}-${version}/${PACKAGE}/cli.py$"
uv run python -m zipfile -l "$wheel" | grep -q "${PACKAGE}/cli.py"

if tar tf "$sdist" | grep -q '\.pypi-token'; then
  echo ".pypi-token leaked into sdist." >&2
  exit 1
fi

if tar tf "$sdist" | grep -q '\.uv-cache'; then
  echo ".uv-cache leaked into sdist." >&2
  exit 1
fi

if uv run python -m zipfile -l "$wheel" | grep -q '\.pypi-token'; then
  echo ".pypi-token leaked into wheel." >&2
  exit 1
fi

if uv run python -m zipfile -l "$wheel" | grep -q '\.uv-cache'; then
  echo ".uv-cache leaked into wheel." >&2
  exit 1
fi

echo "Tagging ${PACKAGE} ${version}"
git tag -a "$tag" -m "${PACKAGE} ${version}"
git push origin "$tag"

echo "Publishing ${PACKAGE} ${version}"
UV_PUBLISH_TOKEN="$token" uv publish

echo "Smoke-testing uvx ${PACKAGE}"
uvx --refresh-package "$PACKAGE" "$PACKAGE" --help
