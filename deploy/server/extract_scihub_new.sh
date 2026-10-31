#!/usr/bin/env bash
set -euo pipefail
RT=/data/zoujin/deng/zebrafish_runtime
ZIPS=$RT/gold_ingest/scihub_zips_new
OUT=$RT/gold_ingest/scihub_pdfs_new
INBOX=$RT/gold_ingest/pdfku_inbox/scihub_batch
LOG=$RT/logs/scihub_new_extract.log
PASS=5768
SEVEN=$RT/tools/7zz
mkdir -p "$OUT" "$INBOX" "$RT/logs"
cd "$ZIPS"
for i in $(seq 1 6); do
  f=$(printf "scihub_pdfs_new.zip.%03d" "$i")
  [ -f "$f" ] || { echo "MISSING $f"; exit 2; }
done
echo "=== extract start $(date -Is) ===" | tee -a "$LOG"
"$SEVEN" x -y "-p${PASS}" -o"$OUT" scihub_pdfs_new.zip.001 2>&1 | tee -a "$LOG"
n=$(find "$OUT" -type f -iname '*.pdf' | wc -l)
echo "extracted_pdf_count=$n" | tee -a "$LOG"
find "$OUT" -type f -iname '*.pdf' -print0 | while IFS= read -r -d '' f; do
  bn=$(basename "$f")
  dest="$INBOX/$bn"
  if [ -e "$dest" ]; then
    osz=$(stat -c%s "$dest" 2>/dev/null || echo 0)
    nsz=$(stat -c%s "$f")
    if [ "$nsz" -gt "$osz" ]; then
      rm -f "$dest"
      ln -f "$f" "$dest" 2>/dev/null || cp -f "$f" "$dest"
    fi
  else
    ln -f "$f" "$dest" 2>/dev/null || cp -n "$f" "$dest"
  fi
done
echo "inbox_total=$(find "$INBOX" -type f -iname '*.pdf' | wc -l)" | tee -a "$LOG"
find "$OUT" -type f -iname '*.pdf' -printf '%f\n' | sed -E 's/^([0-9]+).*/\1/' | sort -u > "$RT/logs/scihub_new_entry_nos.txt"
echo "new_entry_nos=$(wc -l < "$RT/logs/scihub_new_entry_nos.txt")" | tee -a "$LOG"
echo "=== extract done $(date -Is) ===" | tee -a "$LOG"
