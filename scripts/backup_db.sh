#!/usr/bin/env bash
# 斑马鱼库每日逻辑备份
# 用法: backup_db.sh [full|schema]
#   full   —— 全库（含文献表），每周一次
#   schema —— 业务表（跳过两张 GB 级文献原文表），每日
set -uo pipefail

DBHOST=127.0.0.1
DBPORT=3307
DBUSER=zebrafish
DBPASS=zebrafish_remote_2026
DBS=(zebrafish_db zebrafish_archive)

MODE="${1:-schema}"
ROOT=/data/zoujin/deng/backups/mysql
STAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$ROOT"

LOG="$ROOT/backup.log"
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# 空闲检查：磁盘余量低于 50G 直接放弃，避免写满
AVAIL=$(df -BG --output=avail /data/zoujin | tail -1 | tr -dc '0-9')
if [ "${AVAIL:-0}" -lt 50 ]; then
  say "ABORT: /data/zoujin 剩余 ${AVAIL}G < 50G"
  exit 1
fi

IGNORE=()
if [ "$MODE" != "full" ]; then
  IGNORE=(--ignore-table=zebrafish_db.literature_fulltext_gold
          --ignore-table=zebrafish_db.literature_pubmed
          --ignore-table=zebrafish_db.literature_wos)
fi

RC=0
for DB in "${DBS[@]}"; do
  OUT="$ROOT/${DB}_${MODE}_${STAMP}.sql.gz"
  say "start $DB ($MODE) -> $OUT"
  mysqldump -h"$DBHOST" -P"$DBPORT" -u"$DBUSER" -p"$DBPASS" \
    --single-transaction --quick --routines --triggers --events \
    --set-gtid-purged=OFF --default-character-set=utf8mb4 \
    --no-tablespaces "${IGNORE[@]}" "$DB" 2>>"$LOG" | gzip -1 > "$OUT.part"
  if [ ${PIPESTATUS[0]} -ne 0 ]; then
    say "FAIL $DB"; rm -f "$OUT.part"; RC=1; continue
  fi
  mv "$OUT.part" "$OUT"
  SIZE=$(du -h "$OUT" | cut -f1)
  # 完整性自检：尾部必须是 Dump completed
  if zcat "$OUT" 2>/dev/null | tail -3 | grep -q "Dump completed"; then
    say "ok $DB $SIZE"
  else
    say "WARN $DB $SIZE 末尾无 Dump completed 标记"; RC=1
  fi
done

# 保留策略：每日留 14 份，每周一的全备留 8 份
find "$ROOT" -name 'zebrafish_*_schema_*.sql.gz' -mtime +14 -delete 2>/dev/null
find "$ROOT" -name 'zebrafish_*_full_*.sql.gz' -mtime +56 -delete 2>/dev/null
say "cleanup done, remain: $(ls -1 "$ROOT"/*.sql.gz 2>/dev/null | wc -l) files, $(du -sh "$ROOT" 2>/dev/null | cut -f1)"
exit $RC
