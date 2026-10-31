/**
 * VS Code 工作区聊天缓存清理助手（Node 版）
 *
 * 背景：Copilot Chat 会话 .jsonl 里内嵌了巨量 base64 图片，单会话达 381 MB。
 *      VS Code 渲染面板时把整个会话序列化成字符串，超过 V8 上限（约 512 MiB）
 *      → RangeError: Invalid string length，导致聊天面板每条消息都报错。
 *
 * 用法：
 *   node vscode-chat-cleanup.js              # 清理（需先完全退出 VS Code）
 *   node vscode-chat-cleanup.js --dry-run    # 只预览，不动文件
 *   node vscode-chat-cleanup.js --restore    # 按最近一次 manifest 还原
 *   node vscode-chat-cleanup.js --hash <工作区hash>   # 指定别的工作区
 *
 * 原则：只改名归档，绝不删除。所有动作写入 manifest.json，可一键回滚。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const MB = 1048576;
const args = process.argv.slice(2);
const DRY = args.includes('--dry-run');
const RESTORE = args.includes('--restore');

function argValue(name, fallback) {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
}

const WS_HASH = argValue('--hash', '9c9f13b7383c8002232fe6e138f81148');
const THRESHOLD_MB = Number(argValue('--threshold', '5'));
const WS_ROOT = path.join(process.env.APPDATA, 'Code', 'User', 'workspaceStorage', WS_HASH);
const CHAT_DIR = path.join(WS_ROOT, 'chatSessions');
// 会话的伴生缓存可能出现的相对目录（相对工作区 storage 根）
const SIBLINGS = [
  'chatEditingSessions',                          // <id>/ 与 content 快照
  'GitHub.copilot-chat',                          // <id>/、debug-logs/<id>/、chat-session-resources/<id>/
  'GitHub.copilot-chat/transcripts',              // <id>.jsonl（Copilot 完整转录，可达数十 MB）
  'GitHub.copilot-chat/debug-logs',               // <id>/
  'GitHub.copilot-chat/chat-session-resources',   // <id>/
  'ms-python.python',                             // <id>/
];

const c = {
  cyan: s => `\x1b[36m${s}\x1b[0m`,
  green: s => `\x1b[32m${s}\x1b[0m`,
  yellow: s => `\x1b[33m${s}\x1b[0m`,
  red: s => `\x1b[31m${s}\x1b[0m`,
  gray: s => `\x1b[90m${s}\x1b[0m`,
};

function vscodeRunning() {
  // 用 tasklist 检测更可靠：不受 PowerShell 引号转义影响。
  // 关键点：宁可抛出异常也绝不把"查不到"当成"没运行"。
  const names = ['Code.exe', 'Code - Insiders.exe'];
  const found = [];
  let anyChecked = false;
  for (const exe of names) {
    let out = null;
    try {
      out = execSync(`tasklist /FI "IMAGENAME eq ${exe}" /FO CSV /NH`, {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
        windowsHide: true,
      });
    } catch (e) {
      const detail = (e && (e.stderr || e.message) || '').toString().trim();
      // 沙箱限制（EPERM 等）不能当成"VS Code 已关闭"
      console.log(c.yellow(`  [警告] 无法检测 ${exe}：${detail.split('\n')[0].slice(0, 120)}`));
      continue;
    }
    anyChecked = true;
    for (const line of out.split(/\r?\n/)) {
      const m = line.match(/^"([^"]+)","(\d+)"/);
      if (m && m[1].toLowerCase() === exe.toLowerCase()) found.push(`${exe} PID ${m[2]}`);
    }
  }
  if (!anyChecked) {
    throw new Error(
      '无法检测 VS Code 进程（tasklist 不可用）。为安全起见拒绝执行，请在本机 PowerShell 里手动运行本脚本。'
    );
  }
  return found;
}

function walk(dir, depth, hits) {
  if (depth > 4) return hits;
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return hits;
  }
  for (const e of entries) {
    const p = path.join(dir, e.name);
    hits.push({ full: p, isDir: e.isDirectory() });
    if (e.isDirectory()) walk(p, depth + 1, hits);
  }
  return hits;
}

/**
 * 判断会话是否为空：能解析且 requests 数组为 0（或不存在）。
 * 空会话只有约 1.5 KB 元信息，没有对话内容，归档无损失。
 */
function isEmptySession(file) {
  try {
    if (fs.statSync(file).size > 5 * MB) return false; // 大文件不解析，直接判定非空
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    const reqs = data && data.v && Array.isArray(data.v.requests) ? data.v.requests : [];
    return reqs.length === 0;
  } catch {
    return false; // 解析失败一律当作非空，保平安
  }
}

function findLatestBackup() {  if (!fs.existsSync(WS_ROOT)) return null;
  const dirs = fs
    .readdirSync(WS_ROOT, { withFileTypes: true })
    .filter(e => e.isDirectory() && e.name.startsWith('_dsh_chat_backup_'))
    .map(e => e.name)
    .sort()
    .reverse();
  return dirs.length ? path.join(WS_ROOT, dirs[0]) : null;
}

/** 递归统计路径占用的字节数（文件直接返回大小，目录求和）。 */
function measureSize(p) {
  let st;
  try {
    st = fs.statSync(p);
  } catch {
    return 0;
  }
  if (!st.isDirectory()) return st.size;
  let total = 0;
  for (const e of fs.readdirSync(p, { withFileTypes: true })) {
    total += measureSize(path.join(p, e.name));
  }
  return total;
}

function moveAside(item, kind, backupRoot, manifestItems) {
  const rel = path.relative(WS_ROOT, item.full);
  const dest = path.join(backupRoot, rel);
  const size = measureSize(item.full);

  if (!DRY) {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.renameSync(item.full, dest);
  }
  manifestItems.push({ kind, original: item.full, archivedAt: dest, sizeBytes: size, movedAt: new Date().toISOString() });
  console.log(c.gray(`  归档 ${kind.padEnd(12)} ${(size / MB).toFixed(2).padStart(8)} MB  ${path.basename(item.full)}`));
  return size;
}

function restore() {
  const backup = findLatestBackup();
  if (!backup) {
    console.log(c.red(`找不到 _dsh_chat_backup_* 备份目录（在 ${WS_ROOT}）`));
    process.exit(1);
  }
  const mf = path.join(backup, 'manifest.json');
  if (!fs.existsSync(mf)) {
    console.log(c.red(`${backup} 里没有 manifest.json`));
    process.exit(1);
  }
  let running;
  try {
    running = vscodeRunning();
  } catch (e) {
    console.log(c.red(`[中止] ${e.message}`));
    process.exit(3);
  }
  if (running.length) {
    console.log(c.red(`VS Code 仍在运行：${running.join(', ')}，请完全退出后再还原。`));
    process.exit(1);
  }
  const data = JSON.parse(fs.readFileSync(mf, 'utf8'));
  console.log(c.cyan(`从 ${path.basename(backup)} 还原 ${data.items.length} 项...`));
  let n = 0;
  // 文件先还原，目录后还原
  const ordered = [...data.items].sort((a, b) => Number(a.kind === 'dir') - Number(b.kind === 'dir'));
  for (const it of ordered) {
    if (!fs.existsSync(it.archivedAt)) {
      console.log(c.yellow(`  跳过（备份不存在）：${it.archivedAt}`));
      continue;
    }
    fs.mkdirSync(path.dirname(it.original), { recursive: true });
    if (fs.existsSync(it.original)) {
      console.log(c.yellow(`  跳过（原位置已有同名项）：${it.original}`));
      continue;
    }
    fs.renameSync(it.archivedAt, it.original);
    n++;
    console.log(c.gray(`  还原 ${it.original}`));
  }
  console.log(c.green(`[完成] 还原 ${n} 项。`));
}

function cleanup() {
  console.log(c.cyan(`工作区缓存：${WS_ROOT}`));
  if (!fs.existsSync(CHAT_DIR)) {
    console.log(c.red(`找不到聊天会话目录：${CHAT_DIR}`));
    process.exit(1);
  }

  let running;
  try {
    running = vscodeRunning();
  } catch (e) {
    console.log(c.red(`[中止] ${e.message}`));
    console.log(c.yellow('没有做任何改动。'));
    process.exit(3);
  }
  if (running.length) {
    console.log(c.red(`VS Code 仍在运行：${running.join(', ')}`));
    console.log(c.yellow('请完全退出 VS Code（含托盘图标）后重试。没有做任何改动。'));
    process.exit(2);
  }
  console.log(c.green('[OK] VS Code 进程已全部退出'));

  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
  const backupRoot = path.join(WS_ROOT, `_dsh_chat_backup_${stamp}`);
  if (!DRY) fs.mkdirSync(backupRoot, { recursive: true });

  const items = [];
  const sessions = fs
    .readdirSync(CHAT_DIR, { withFileTypes: true })
    .filter(e => e.isFile() && e.name.endsWith('.jsonl'))
    .map(e => ({ full: path.join(CHAT_DIR, e.name), isDir: false, size: fs.statSync(path.join(CHAT_DIR, e.name)).size }))
    .sort((a, b) => b.size - a.size);

  console.log(c.yellow(`\n[1/3] 归档占用较大的会话（>= ${THRESHOLD_MB} MB）`));
  let archivedBytes = 0;
  const kept = [];
  const bigIds = [];
  // 记录已处理的绝对路径，避免同一个伴生项被多个候选路径重复命中
  const seenBig = new Set();
  for (const s of sessions) {
    if (s.size / MB >= THRESHOLD_MB) {
      archivedBytes += moveAside(s, 'big-session', backupRoot, items);
      const id = path.basename(s.full, '.jsonl');
      bigIds.push(id);
      // 连同该会话的附属缓存一并归档（编辑会话、转录、调试日志、资源）
      for (const sib of SIBLINGS) {
        const base = path.join(WS_ROOT, sib);
        if (!fs.existsSync(base)) continue;
        const cands = [
          { full: path.join(base, id), isDir: true, kind: 'companion-dir' },
          { full: path.join(base, `${id}.jsonl`), isDir: false, kind: 'companion-file' },
        ];
        for (const cand of cands) {
          const key = path.normalize(cand.full).toLowerCase();
          if (seenBig.has(key)) continue;
          seenBig.add(key);
          if (!fs.existsSync(cand.full)) continue;
          archivedBytes += moveAside({ full: cand.full, isDir: cand.isDir }, cand.kind, backupRoot, items);
        }
      }
    }
    else kept.push(s);
  }
  console.log(c.yellow(`\n[2/3] 归档空会话（requests 为 0，无任何对话内容）`));
  const emptyIds = sessions.filter(s => isEmptySession(s.full)).map(s => path.basename(s.full, '.jsonl'));
  if (!emptyIds.length) console.log(c.gray('  没有空会话。'));
  // 伴生目录/文件只按精确路径匹配（<类型>/<会话id> 或 <类型>/<id>.jsonl），并去重
  const seen = new Set();
  for (const id of emptyIds) {
    const candidates = [{ full: path.join(CHAT_DIR, `${id}.jsonl`), isDir: false, kind: 'empty-session' }];
    for (const sib of SIBLINGS) {
      const base = path.join(WS_ROOT, sib);
      if (!fs.existsSync(base)) continue;
      candidates.push({ full: path.join(base, id), isDir: true, kind: 'dir' });
      candidates.push({ full: path.join(base, `${id}.jsonl`), isDir: false, kind: 'file' });
    }
    for (const cand of candidates) {
      const key = path.normalize(cand.full).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      if (!fs.existsSync(cand.full)) continue;
      archivedBytes += moveAside({ full: cand.full, isDir: cand.isDir }, cand.kind, backupRoot, items);
    }
  }

  console.log(c.yellow(`\n[3/3] 校验`));
  const manifest = {
    generatedAt: new Date().toISOString(),
    workspaceStorage: WS_ROOT,
    workspaceHash: WS_HASH,
    archiveRoot: backupRoot,
    dryRun: DRY,
    archivedCount: items.length,
    archivedMB: Number((archivedBytes / MB).toFixed(2)),
    keptSessions: kept.map(k => path.basename(k.full)),
    items,
    rollbackCommand: `node "${__filename}" --restore --hash ${WS_HASH}`,
  };

  if (!DRY) {
    fs.writeFileSync(path.join(backupRoot, 'manifest.json'), JSON.stringify(manifest, null, 2), 'utf8');
    const bad = [];
    for (const it of items) {
      if (fs.existsSync(it.original)) bad.push(`未移走：${it.original}`);
      if (!fs.existsSync(it.archivedAt)) bad.push(`备份缺失：${it.archivedAt}`);
    }
    if (bad.length) {
      console.log(c.red('[校验失败]'));
      bad.forEach(b => console.log(c.red(`  ${b}`)));
      process.exit(1);
    }
  }

  const remainBytes = (function sum(d) {
    let t = 0;
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      try {
        t += e.isDirectory() ? sum(p) : fs.statSync(p).size;
      } catch { /* locked file, skip */ }
    }
    return t;
  })(WS_ROOT);

  console.log('\n================================');
  console.log(`归档项   : ${items.length} 个`);
  console.log(`已移出   : ${(archivedBytes / MB).toFixed(1)} MB`);
  console.log(`剩余     : ${(remainBytes / MB).toFixed(1)} MB`);
  console.log(`保留会话 : ${kept.length} 个${kept.length ? '（' + kept.map(k => path.basename(k.full).slice(0, 8)).join(', ') + '）' : ''}`);
  if (DRY) {
    console.log(c.yellow('\n[DRY-RUN] 未做任何实际改动。'));
  } else {
    console.log(`备份目录 : ${backupRoot}`);
    console.log(c.green('[校验通过] 原路径已空、备份均存在'));
    console.log(c.yellow('\n回滚命令：'));
    console.log(`  ${manifest.rollbackCommand}`);
  }
}

if (RESTORE) restore();
else cleanup();
