#!/usr/bin/env bash
# 解压 unsent_pdfs.zip.001..007 → 抽出 PDF → 链入 pdfku inbox
set -euo pipefail
RT=/data/zoujin/deng/zebrafish_runtime
ZIPS=$RT/gold_ingest/unsent_zips
OUT=$RT/gold_ingest/unsent_pdfs_extracted
INBOX=$RT/gold_ingest/pdfku_inbox/pdf
LOG=$RT/logs/unsent_extract.log
PASS="${UNSENT_ZIP_PASS:-5768}"
SEVEN=$RT/tools/7zz
mkdir -p "$OUT" "$INBOX" "$RT/logs" "$ZIPS"
cd "$ZIPS"
for i in $(seq 1 7); do
  f=$(printf "unsent_pdfs.zip.%03d" "$i")
  [ -f "$f" ] || { echo "MISSING $f"; exit 2; }
done
echo "=== extract start $(date -Is) ===" | tee -a "$LOG"
# 优先无密码；失败再用 5768（与 scihub 分卷同惯例）
set +e
"$SEVEN" x -y -o"$OUT" unsent_pdfs.zip.001 >>"$LOG" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "retry with password" | tee -a "$LOG"
  "$SEVEN" x -y "-p${PASS}" -o"$OUT" unsent_pdfs.zip.001 >>"$LOG" 2>&1
  rc=$?
fi
set -e
if [ "$rc" -ne 0 ]; then
  echo "EXTRACT_FAIL rc=$rc" | tee -a "$LOG"
  exit "$rc"
fi
n=$(find "$OUT" -type f -iname '*.pdf' | wc -l)
echo "extracted_pdf_count=$n" | tee -a "$LOG"
find "$OUT" -type f -iname '*.pdf' -print0 | while IFS= read -r -d '' f; do
  bn=$(basename "$f")
  # 规范为 {entry_no}.pdf（文件名以数字开头）
  eno=$(echo "$bn" | sed -E 's/^([0-9]+).*/\1/')
  if [[ "$eno" =~ ^[0-9]+$ ]]; then
    dest="$INBOX/${eno}.pdf"
  else
    dest="$INBOX/$bn"
  fi
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
find "$OUT" -type f -iname '*.pdf' -printf '%f\n' | sed -E 's/^([0-9]+).*/\1/' | sort -u > "$RT/logs/unsent_entry_nos.txt"
echo "new_entry_nos=$(wc -l < "$RT/logs/unsent_entry_nos.txt")" | tee -a "$LOG"
echo "=== extract done $(date -Is) ===" | tee -a "$LOG"
