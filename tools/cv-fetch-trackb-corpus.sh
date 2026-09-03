#!/bin/sh
# Fetch the pinned Track B corpus (LLVM 18 InstCombine tests) and VERIFY it against the manifest.
#
# The corpus is not vendored -- it is ~2 MB of upstream test IR under LLVM's licence -- so it is
# pinned by tag + sha256 instead. This exists because the published 1,705/1,835 figure was measured
# against local unpinned copies that no longer existed, with the file list recorded nowhere: the
# headline could not be regenerated from a clean checkout. A hash mismatch here means the corpus
# moved under you and any number you quote from it is about a different corpus.
#
# Usage: tools/cv-fetch-trackb-corpus.sh <dest-dir>
set -e
DEST="${1:?usage: cv-fetch-trackb-corpus.sh <dest-dir>}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
MANIFEST="$ROOT/tests/fixtures/trackb_corpus_manifest.json"
TAG=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['tag'])" "$MANIFEST")
mkdir -p "$DEST"
python3 -c "import json,sys;print('\n'.join(json.load(open(sys.argv[1]))['files']))" "$MANIFEST" |
while read -r f; do
    want=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['files'][sys.argv[2]]['sha256'])" "$MANIFEST" "$f")
    if [ ! -f "$DEST/$f" ]; then
        curl -sfo "$DEST/$f" \
            "https://raw.githubusercontent.com/llvm/llvm-project/$TAG/llvm/test/Transforms/InstCombine/$f" ||
            { echo "fetch failed: $f" >&2; exit 1; }
    fi
    got=$(shasum -a 256 "$DEST/$f" | cut -d' ' -f1)
    if [ "$got" != "$want" ]; then
        echo "SHA MISMATCH $f: manifest $want, got $got" >&2
        exit 1
    fi
    echo "ok $f"
done
echo "corpus verified at $TAG in $DEST"
