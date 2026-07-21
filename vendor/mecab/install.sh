#!/usr/bin/env bash
set -euo pipefail

engine_archive="mecab-0.996-ko-0.9.2.tar.gz"
dictionary_archive="mecab-ko-dic-2.1.1-20180720.tar.gz"

printf '%s  %s\n' \
  'd0e0f696fc33c2183307d4eb87ec3b17845f90b81bf843bd0981e574ee3c38cb' \
  "$engine_archive" | sha256sum --check --strict
printf '%s  %s\n' \
  'fd62d3d6d8fa85145528065fabad4d7cb20f6b2201e71be4081a4e9701a5b330' \
  "$dictionary_archive" | sha256sum --check --strict

tar --extract --gzip --file "$engine_archive"
pushd mecab-0.996-ko-0.9.2
./configure --prefix=/usr/local
make --jobs="$(nproc)"
make install
popd
ldconfig

tar --extract --gzip --file "$dictionary_archive"
pushd mecab-ko-dic-2.1.1-20180720
autoreconf --force --install
./configure --with-mecab-config=/usr/local/bin/mecab-config
make --jobs="$(nproc)"
make install
popd

mkdir -p \
  /mecab-runtime/usr/local/bin \
  /mecab-runtime/usr/local/etc \
  /mecab-runtime/usr/local/lib
cp /usr/local/bin/mecab /usr/local/bin/mecab-config /mecab-runtime/usr/local/bin/
cp /usr/local/etc/mecabrc /mecab-runtime/usr/local/etc/
cp -a /usr/local/lib/libmecab.so* /mecab-runtime/usr/local/lib/
cp -a /usr/local/lib/mecab /mecab-runtime/usr/local/lib/
