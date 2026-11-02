const messagesEl = document.getElementById("messages");
const chatForm = document.getElementById("chatForm");
const questionInput = document.getElementById("questionInput");
const sendBtn = document.getElementById("sendBtn");
const statusBar = document.getElementById("statusBar");
const dbStats = document.getElementById("dbStats");
const detailEmpty = document.getElementById("detailEmpty");
const agentTraceBlock = document.getElementById("agentTraceBlock");
const agentTraceEl = document.getElementById("agentTrace");
const detailContent = document.getElementById("detailContent");
const detailTaskType = document.getElementById("detailTaskType");
const geneBlock = document.getElementById("geneBlock");
const geneCard = document.getElementById("geneCard");
const sqlBlock = document.getElementById("sqlBlock");
const detailSql = document.getElementById("detailSql");
const dataBlock = document.getElementById("dataBlock");
const dataTable = document.getElementById("dataTable");
const rowCount = document.getElementById("rowCount");
const contextBlock = document.getElementById("contextBlock");
const detailContext = document.getElementById("detailContext");
const toolGeneInput = document.getElementById("toolGeneInput");
const toolLookupBtn = document.getElementById("toolLookupBtn");
const toolSearchInput = document.getElementById("toolSearchInput");
const toolSearchBtn = document.getElementById("toolSearchBtn");
const toolResult = document.getElementById("toolResult");
const blastInput = document.getElementById("blastInput");
const blastBtn = document.getElementById("blastBtn");
const blastPasteBtn = document.getElementById("blastPasteBtn");
const enrichInput = document.getElementById("enrichInput");
const enrichBtn = document.getElementById("enrichBtn");
const keggInput = document.getElementById("keggInput");
const keggBtn = document.getElementById("keggBtn");
const uploadInput = document.getElementById("uploadInput");
const uploadBtn = document.getElementById("uploadBtn");
const idResolveInput = document.getElementById("idResolveInput");
const idResolveBtn = document.getElementById("idResolveBtn");
const idResolvePanel = document.getElementById("idResolvePanel");
const idBatchInput = document.getElementById("idBatchInput");
const idBatchBtn = document.getElementById("idBatchBtn");
const seqGeneInput = document.getElementById("seqGeneInput");
const seqExtractBtn = document.getElementById("seqExtractBtn");
const seqCdna = document.getElementById("seqCdna");
const seqPromoter = document.getElementById("seqPromoter");
const seqGenomic = document.getElementById("seqGenomic");
const annotateInput = document.getElementById("annotateInput");
const annotateBtn = document.getElementById("annotateBtn");
const workflowPreset = document.getElementById("workflowPreset");
const workflowInput = document.getElementById("workflowInput");
const workflowRunBtn = document.getElementById("workflowRunBtn");
const workflowSteps = document.getElementById("workflowSteps");
const workflowOutput = document.getElementById("workflowOutput");
const tenantHint = document.getElementById("tenantHint");

/* 2026-10-27：问答只保留一种行为。原「Auto/Hub 多 Agent」「数据库查询」「知识库问答」
   「多模型融合」「基因详情」五选一的模式概念整体删除 —— Hub 融合路径本身就同时覆盖
   知识库问答与数据库查询，页面上再让用户选只是把内部实现暴露给用户。
   固定为 "hub"：payload.answer_scope=hub，走 run_scoped_chat 全链路 + 流式输出。 */
let currentMode = "hub";
const SCOPE_KEY = "zfqa_answer_scope";
const DEPTH_KEY = "zfqa_answer_depth";
const SCOPE_LABELS = {
  literature: "仅文献 RAG",
  gene_db: "仅基因/数据库",
  id_map: "仅 ID 映射",
  hub: "Hub 全链路",
  retrieval_debug: "检索调试摘要",
};
const DEPTH_LABELS = {
  partial: "部分回答",
  full: "整体回答",
};
/** 回答范围 → 工作流预置（与后端 PRESET_BY_SCOPE 一致） */
const SCOPE_TO_PRESET = {
  literature: "literature_only",
  gene_db: "gene_enrichment",
  id_map: "id_only",
  hub: "full_hub",
  retrieval_debug: "retrieval_debug",
};
/** 工作流预置 → 回答范围 */
const PRESET_TO_SCOPE = {
  literature_only: "literature",
  gene_id_literature: "literature",
  gene_enrichment: "gene_db",
  id_only: "id_map",
  full_hub: "hub",
  retrieval_debug: "retrieval_debug",
};
/** 回答范围 → 分析工具页签 */
const SCOPE_TO_TOOL = {
  literature: "retrieval",
  gene_db: "gene",
  id_map: "idresolve",
  hub: "annotate",
  retrieval_debug: "retrieval",
};
const TOOL_TO_SCOPE = {
  retrieval: "retrieval_debug",
  gene: "gene_db",
  idresolve: "id_map",
  annotate: "hub",
};
// 公网 Hub 强制默认「Hub 全链路」+「整体回答」：覆盖旧的仅文献/部分回答设置
let currentScope = "hub";
let currentDepth = "full";
try {
  sessionStorage.setItem(SCOPE_KEY, "hub");
  sessionStorage.setItem(DEPTH_KEY, "full");
} catch {}
if (!DEPTH_LABELS[currentDepth]) currentDepth = "full";
if (!SCOPE_LABELS[currentScope]) currentScope = "hub";
/** 防止芯片 / 工作流 / 工具页签互相 set 时形成反馈环 */
let _syncingAnswerPrefs = false;
/** 每次改范围/策略递增，请求回包时比对，避免过期结果覆盖 UI */
let answerPrefsEpoch = 0;
let isSending = false;
let chatRequestSeq = 0;
let payloadEncrypt = false;
let uploadLimits = { max_bytes: 5 * 1024 * 1024, max_files_in_library: 300 };
let authRole = "admin";
let currentTenantSlug = "default";
/** 文献 FAISS / 模型预热中禁止发送，避免 Failed to fetch */
let systemWarming = false;
let chatBusy = false;
// 多机并行后：并发是否打满、有多少人在排队/在跑（用于状态栏与发送按钮提示）
let chatQueued = 0;
let chatRunning = 0;
let lastHealthOk = false;
/** 连续对话历史（发给后端展开指代/追问），只保留最近几轮，避免旧话题污染新问题 */
const chatHistory = [];
const CHAT_HISTORY_MAX = 4;
/** 回答进行中仍可输入：新问题进入待处理队列，当前回答完成后再自动发送 */
const pendingQueue = [];
let queueProcessing = false;
const TENANT_KEY = "zfqa_tenant_slug";
const ACCESS_TOKEN_KEY = "zfqa_access_token";
const HUB_SESSION_KEY = "zfqa_hub_session";
const HUB_MODE_KEY = "zfqa_hub_mode"; // guest | user
const HUB_USER_KEY = "zfqa_hub_user";
const HUB_USER_RE = /^[a-zA-Z0-9_\u4e00-\u9fff]{2,32}$/;
const BLOCKED_UPLOAD_EXT = [
  ".exe", ".bat", ".cmd", ".com", ".scr", ".pif", ".msi", ".dll", ".sys",
  ".sh", ".bash", ".ps1", ".vbs", ".wsf", ".js", ".mjs", ".jar",
  ".php", ".asp", ".html", ".htm", ".svg", ".xml", ".zip", ".rar", ".7z",
];

function apiHeaders(extra = {}) {
  const h = { ...extra };
  const token = sessionStorage.getItem(ACCESS_TOKEN_KEY);
  if (token) h["X-Access-Token"] = token;
  const hubSess = sessionStorage.getItem(HUB_SESSION_KEY);
  if (hubSess) h["X-Hub-Session"] = hubSess;
  const tenant = sessionStorage.getItem(TENANT_KEY) || currentTenantSlug || "default";
  h["X-Tenant-Id"] = tenant;
  // 问答车道标识：后端据此做「同一会话串行、不同会话并行」的多机并发调度。
  // 每台机器/每个标签页一条车道，互不等候（并发上限由服务端统一保护）。
  h["X-Session-Id"] = sessionId();
  return h;
}

/* 会话 ID：每标签页一个，持久化在本标签内（刷新不变，换页换车道） */
function sessionId() {
  try {
    let sid = sessionStorage.getItem("zfqa_lane_id");
    if (!sid) {
      sid =
        (window.crypto && crypto.randomUUID
          ? crypto.randomUUID()
          : `s${Date.now()}${Math.random().toString(16).slice(2, 10)}`);
      sessionStorage.setItem("zfqa_lane_id", sid);
    }
    return sid;
  } catch {
    return "anon";
  }
}

async function apiFetch(url, options = {}) {
  const token = sessionStorage.getItem(ACCESS_TOKEN_KEY) || "";
  const opts = {
    credentials: "same-origin",
    ...options,
    headers: apiHeaders(options.headers || {}),
  };

  const isChatPost =
    payloadEncrypt &&
    token &&
    window.PayloadCrypto &&
    (options.method || "GET").toUpperCase() === "POST" &&
    url.includes("/api/chat") &&
    opts.body &&
    typeof opts.body === "string";

  if (isChatPost) {
    try {
      const raw = JSON.parse(opts.body);
      opts.body = JSON.stringify(await PayloadCrypto.encryptPayload(raw, token, token));
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-Enc-Version"] = "v1";
    } catch {
      /* 加密失败则发明文 */
    }
  }

  const res = await fetch(url, opts);
  if (res.status === 401) {
    // API 密码锁拒绝（无通行证 / 密钥不对）：不是用户令牌问题，不能弹「输令牌」框，
    // 否则会把正常访客挡在门外。提示刷新即可，刷新后首页会重新下发通行证。
    const data = await res.json().catch(() => ({}));
    if (data.code === "api_key_required" || data.code === "api_key_invalid") {
      throw new Error(data.detail || "接口通行证已过期，请刷新页面后重试");
    }
    showAuthGate("访问令牌无效或缺失，请输入管理员提供的令牌");
    throw new Error("需要访问令牌");
  }
  if (res.status === 403) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "无权限访问此功能");
  }
  if (res.status === 429) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "请求过于频繁，请稍后再试");
  }
  if (res.status === 503) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "服务暂时繁忙，请稍候再试");
  }
  return res;
}

async function parseJsonResponse(res, url) {
  // 先读文本再解析：网关/反向代理的错误页是 HTML，直接 res.json() 会把
  // `Unexpected token '<', "<!DOCTYPE "... is not valid JSON` 这种原始报错甩给用户。
  // Hub 掉线（cloudflared 502/520）时线上就是这个场景，必须给出人话。
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    const head = String(text || "").trim().slice(0, 40).replace(/\s+/g, " ");
    const looksHtml = head.startsWith("<");
    throw new Error(
      res.ok
        ? `服务返回了非 JSON 响应${looksHtml ? "（像是网关/反向代理的错误页）" : ""}：${head}`
        : `请求失败（HTTP ${res.status}）${looksHtml ? "：服务返回了网页而非常规接口响应，通常是网关错误页或服务未启动" : `，响应开头：${head}`}`
    );
  }
  if (
    payloadEncrypt &&
    window.PayloadCrypto &&
    data &&
    data.enc === "v1" &&
    url.includes("/api/chat")
  ) {
    const token = sessionStorage.getItem(ACCESS_TOKEN_KEY) || "";
    return PayloadCrypto.decryptPayload(data, token, token);
  }
  return data;
}

function showAuthGate(msg) {
  const gate = document.getElementById("authGate");
  if (!gate) return;
  gate.classList.remove("hidden");
  const hint = document.getElementById("authGateHint");
  if (hint && msg) hint.textContent = msg;
}

/* ── 欢迎门锁态（2026-10-28 重写）────────────────────────────────────────
   规则：没入场（sessionStorage 里没有 zfqa_hub_mode）就一直锁着；
   只有「快速入门 / 登录成功 / 注册成功」这三条路能解锁。
   锁着的时候 <html> 带 zf-locked，CSS 把 #appRoot 整体隐藏、门顶到前台，
   所以路由怎么切、点页面哪里都只是在门后面变化，不会「点一下就进去了」。
   唯一例外是使用说明 / 使用协议 / 数据来源这几个文档页：
   门上的链接要能打开来读；读完去别的页面会自动重新上锁。 */
const GATE_LOCK_CLASS = "zf-locked";
let gateForceOpen = false;   // 已入场的人主动点开「账户」时，门要能再次出现

function enteredSession() {
  try { return !!sessionStorage.getItem(HUB_MODE_KEY); } catch { return false; }
}

function gateExemptRoute() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  return raw === "guide" || raw.indexOf("guide/") === 0;
}

/* 把「该不该锁」算一次并落到 <html> 上；返回是否处于锁态。 */
function syncGateLock() {
  const lock = gateForceOpen || (!enteredSession() && !gateExemptRoute());
  document.documentElement.classList.toggle(GATE_LOCK_CLASS, lock);
  const gate = document.getElementById("welcomeGate");
  if (gate) gate.classList.toggle("hidden", !lock);
  return lock;
}

function hideWelcomeGate() {
  gateForceOpen = false;
  syncGateLock();
}

function hubStoreGet(key) {
  try {
    return localStorage.getItem(key) || sessionStorage.getItem(key) || "";
  } catch {
    return sessionStorage.getItem(key) || "";
  }
}

function hubStoreSet(key, value) {
  sessionStorage.setItem(key, value);
  // 会话 token / 入场模式 / 用户信息都只放 sessionStorage：
  //   · token 用 HttpOnly Cookie + session 存储，降低 XSS 持久窃取面；
  //   · 入场模式（guest|user）如果落进 localStorage，关掉浏览器再打开也会自动进入，
  //     用户看到的就是「没点任何按钮直接进了 chat」（2026-10-28 报的问题）。
  if (key === HUB_SESSION_KEY || key === HUB_MODE_KEY || key === HUB_USER_KEY) {
    try { localStorage.removeItem(key); } catch { /* ignore */ }
    return;
  }
  try { localStorage.setItem(key, value); } catch { /* ignore quota */ }
}

function hubStoreRemove(key) {
  sessionStorage.removeItem(key);
  try { localStorage.removeItem(key); } catch { /* ignore */ }
}

function hydrateHubStorage() {
  // 2026-10-28：入场状态（guest / user）改成**只活在当前标签页**。
  // 以前这里会把 localStorage 里的 mode/user 回灌到 sessionStorage，于是「关掉浏览器
  // 再打开」也直接进了问答页 —— 用户的原话是「没点击任何按钮为什么直接进入」。
  // 现在的规则：新会话一律先看到欢迎门，点 快速入门 / 登录 / 注册 才进 chat；
  // 同一标签页内刷新不受影响（sessionStorage 还在）。
  for (const key of [HUB_MODE_KEY, HUB_USER_KEY, HUB_SESSION_KEY]) {
    try { localStorage.removeItem(key); } catch { /* ignore */ }
  }
}

function setWelcomeHint(text, isError) {
  const hint = document.getElementById("welcomeFormHint");
  if (!hint) return;
  hint.textContent = text || "";
  hint.classList.toggle("is-error", !!isError);
}

function hubErrorText(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return "请检查用户名（2～32 位）和密码（6～72 位）";
  }
  if (status === 429) return "尝试过于频繁，请稍后再试";
  return `请求失败（HTTP ${status}）`;
}

function showWelcomeGate() {
  gateForceOpen = true;
  document.getElementById("welcomeFormPanel")?.classList.add("hidden");
  setWelcomeHint("", false);
  syncGateLock();
}

function clearHubAccount() {
  hubStoreRemove(HUB_SESSION_KEY);
  hubStoreRemove(HUB_USER_KEY);
  hubStoreRemove(HUB_MODE_KEY);
}

function updateUserChip() {
  const chip = document.getElementById("userChip");
  const nameEl = document.getElementById("userChipName");
  const logoutBtn = document.getElementById("userLogoutBtn");
  const accountBtn = document.getElementById("userAccountBtn");
  if (!chip || !nameEl) return;
  const mode = sessionStorage.getItem(HUB_MODE_KEY);
  if (!mode) {
    chip.classList.add("hidden");
    return;
  }
  chip.classList.remove("hidden");
  if (mode === "user") {
    let label = "已登录";
    try {
      const u = JSON.parse(sessionStorage.getItem(HUB_USER_KEY) || "{}");
      label = u.display_name || u.username || "已登录";
    } catch { /* ignore */ }
    nameEl.textContent = label;
    logoutBtn?.classList.remove("hidden");
    accountBtn?.classList.add("hidden");
  } else {
    nameEl.textContent = "访客 · 快速入门";
    logoutBtn?.classList.add("hidden");
    accountBtn?.classList.remove("hidden");
  }
}

// 入场后的落点：在首页 / 欢迎页进入 → 直接进问答（点「快速入门」的意图就是开始对话）；
// 从功能页深链或被门挡住的刷新进入 → 留在原来那个功能页（他是为那一页来的，
// 再把他拽回 chat 等于丢了刚才的页面）。
function goChatAfterEnter() {
  const hash = location.hash || "";
  const homeish = !hash || hash === "#/" || hash === "#/home" || hash === "#/news";
  if (homeish) location.hash = "#/chat";
  if (!location.hash.startsWith("#/chat")) return;
  // 哈希相同时不会触发 hashchange，这里补一次焦点，落地即可直接输入
  setTimeout(() => {
    document.getElementById("questionInput")?.focus({ preventScroll: true });
  }, 160);
}

function enterAsGuest() {
  hubStoreSet(HUB_MODE_KEY, "guest");
  hubStoreRemove(HUB_SESSION_KEY);
  hubStoreRemove(HUB_USER_KEY);
  hideWelcomeGate();
  updateUserChip();
  initConversationUI();
  goChatAfterEnter();
}

function enterAsUser(sess) {
  hubStoreSet(HUB_MODE_KEY, "user");
  if (sess?.session_token) hubStoreSet(HUB_SESSION_KEY, sess.session_token);
  if (sess?.user) hubStoreSet(HUB_USER_KEY, JSON.stringify(sess.user));
  hideWelcomeGate();
  updateUserChip();
  initConversationUI();
  goChatAfterEnter();
}

async function refreshAuthRole() {
  try {
    const res = await apiFetch("/api/auth/whoami");
    if (res.ok) {
      const data = await parseJsonResponse(res, res.url || "");
      authRole = data.role || "admin";
      sessionStorage.setItem("zfqa_auth_role", authRole);
    }
  } catch {
    authRole = "admin";
  }
}

async function continueAfterWelcome(cfg) {
  applyUploadPolicy(cfg);
  updateUserChip();
  if (!cfg.auth_required) {
    authRole = "admin";
    return;
  }
  const saved = sessionStorage.getItem(ACCESS_TOKEN_KEY);
  if (saved) {
    const test = await fetch("/api/health", { headers: apiHeaders() });
    if (test.ok) {
      await refreshAuthRole();
      applyUploadPolicy(cfg);
      return;
    }
  }
  showAuthGate("公网/受保护模式：请输入访问令牌后继续使用");
}

async function initAuth() {
  hydrateHubStorage();
  let cfg = {};
  try {
    const res = await fetch("/api/auth/config");
    cfg = await parseJsonResponse(res, res.url || "");
    applyUploadPolicy(cfg);
  } catch {
    cfg = { auth_required: false, guest_full_access: true, accounts_enabled: true };
  }

  const mode = sessionStorage.getItem(HUB_MODE_KEY);
  if (mode === "guest") {
    hideWelcomeGate();
    updateUserChip();
    initConversationUI();
    await continueAfterWelcome(cfg);
    return;
  }
  if (mode === "user") {
    const token = sessionStorage.getItem(HUB_SESSION_KEY);
    if (token) {
      try {
        const me = await fetch("/api/hub/me", { headers: apiHeaders(), credentials: "same-origin" });
        if (me.status >= 500) throw new Error("hub me unavailable");
        const data = await me.json();
        if (data.ok && data.user) {
          hubStoreSet(HUB_USER_KEY, JSON.stringify(data.user));
          hideWelcomeGate();
          updateUserChip();
          initConversationUI();
          await continueAfterWelcome(cfg);
          return;
        }
      } catch {
        hideWelcomeGate();
        updateUserChip();
        initConversationUI();
        await continueAfterWelcome(cfg);
        return;
      }
    }
    clearHubAccount();
  }

  /* 走到这里说明没有有效入场态。默认上锁（门在台上），
     但如果当前深链就是文档页（使用说明 / 协议 / 数据来源），允许先看文档，
     不强行盖住 —— 判据统一交给 syncGateLock。 */
  gateForceOpen = false;
  document.getElementById("welcomeFormPanel")?.classList.add("hidden");
  setWelcomeHint("", false);
  syncGateLock();
  updateUserChip();
}

let welcomeFormMode = "login"; // login | register

function openWelcomeForm(mode) {
  welcomeFormMode = mode;
  const panel = document.getElementById("welcomeFormPanel");
  const title = document.getElementById("welcomeFormTitle");
  const display = document.getElementById("hubDisplayInput");
  const pass2 = document.getElementById("hubPass2Input");
  const pass = document.getElementById("hubPassInput");
  const submit = document.getElementById("welcomeSubmitBtn");
  const sw = document.getElementById("welcomeSwitchBtn");
  const isReg = mode === "register";
  panel?.classList.remove("hidden");
  if (title) title.textContent = isReg ? "注册" : "登录";
  if (display) display.classList.toggle("hidden", !isReg);
  if (pass2) pass2.classList.toggle("hidden", !isReg);
  if (pass) pass.autocomplete = isReg ? "new-password" : "current-password";
  if (submit) submit.textContent = isReg ? "注册并进入" : "登录";
  if (sw) sw.textContent = isReg ? "已有账号？去登录" : "没有账号？去注册";
  setWelcomeHint("", false);
}

function validateWelcomeForm(username, password) {
  if (!HUB_USER_RE.test(username)) {
    return "用户名需 2～32 位（字母、数字、下划线或中文）";
  }
  if (password.length < 6 || password.length > 72) return "密码需 6～72 位";
  if (welcomeFormMode === "register") {
    const again = document.getElementById("hubPass2Input")?.value || "";
    if (again !== password) return "两次输入的密码不一致";
  }
  return "";
}

document.getElementById("quickStartBtn")?.addEventListener("click", async () => {
  enterAsGuest();
  try {
    const res = await fetch("/api/auth/config");
    await continueAfterWelcome(await res.json());
  } catch {
    authRole = "admin";
  }
  loadHealth();
});

document.getElementById("showLoginBtn")?.addEventListener("click", () => openWelcomeForm("login"));
document.getElementById("showRegisterBtn")?.addEventListener("click", () => openWelcomeForm("register"));
document.getElementById("welcomeSwitchBtn")?.addEventListener("click", () => {
  openWelcomeForm(welcomeFormMode === "register" ? "login" : "register");
});
document.getElementById("welcomeBackBtn")?.addEventListener("click", () => {
  document.getElementById("welcomeFormPanel")?.classList.add("hidden");
  setWelcomeHint("", false);
  if (sessionStorage.getItem(HUB_MODE_KEY)) hideWelcomeGate();
});
document.getElementById("welcomeFormPanel")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    document.getElementById("welcomeSubmitBtn")?.click();
  }
});

document.getElementById("welcomeSubmitBtn")?.addEventListener("click", async () => {
  const username = (document.getElementById("hubUserInput")?.value || "").trim();
  const password = document.getElementById("hubPassInput")?.value || "";
  const display_name = (document.getElementById("hubDisplayInput")?.value || "").trim();
  const submit = document.getElementById("welcomeSubmitBtn");
  const invalid = validateWelcomeForm(username, password);
  if (invalid) {
    setWelcomeHint(invalid, true);
    return;
  }
  const url = welcomeFormMode === "register" ? "/api/hub/register" : "/api/hub/login";
  const body = { username, password };
  if (welcomeFormMode === "register" && display_name) body.display_name = display_name;
  if (submit) submit.disabled = true;
  try {
    setWelcomeHint("提交中…", false);
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(hubErrorText(data, res.status));
    const pass2 = document.getElementById("hubPass2Input");
    if (document.getElementById("hubPassInput")) document.getElementById("hubPassInput").value = "";
    if (pass2) pass2.value = "";
    enterAsUser(data);
    const cfgRes = await fetch("/api/auth/config");
    await continueAfterWelcome(cfgRes.ok ? await cfgRes.json() : {});
    loadHealth();
  } catch (e) {
    setWelcomeHint(e.message || "失败，请重试", true);
  } finally {
    if (submit) submit.disabled = false;
  }
});

document.getElementById("userAccountBtn")?.addEventListener("click", () => {
  showWelcomeGate();
});

document.getElementById("userLogoutBtn")?.addEventListener("click", async () => {
  const token = sessionStorage.getItem(HUB_SESSION_KEY);
  try {
    if (token) {
      await fetch("/api/hub/logout", {
        method: "POST",
        headers: { "X-Hub-Session": token },
        credentials: "same-origin",
      });
    }
  } catch { /* ignore */ }
  clearHubAccount();
  updateUserChip();
  conversations = [];
  activeConvId = null;
  clearChatHistory();
  setChatEmptyVisible(true);
  clearDetailPanel();
  renderHistoryList();
  showWelcomeGate();
});

function applyUploadPolicy(cfg) {
  payloadEncrypt = !!cfg.payload_encrypt;
  uploadLimits = cfg.upload_limits || uploadLimits;
  authRole = sessionStorage.getItem("zfqa_auth_role") || "admin";

  const uploadTab = document.querySelector('.tool-tab[data-tool="upload"]');
  const hint = document.getElementById("uploadHint");
  const canUpload = cfg.uploads_enabled && authRole !== "readonly";
  if (!canUpload) {
    uploadTab?.classList.add("hidden");
    if (hint) {
      hint.textContent = cfg.uploads_enabled
        ? "当前令牌为只读，无法上传（需 admin/upload 令牌）"
        : "文件上传已禁用（WEB_ALLOW_UPLOAD=0）";
    }
    return;
  }
  uploadTab?.classList.remove("hidden");
  const lim = cfg.upload_limits || {};
  const mb = Math.round((lim.max_bytes || 5242880) / 1024 / 1024);
  if (hint) {
    hint.textContent = `仅 PDF/TXT/MD，≤${mb}MB，UTF-8；库内最多 ${lim.max_files_in_library || 300} 个文件；服务端魔数+脚本扫描`;
  }
}

const UPLOAD_ALLOWED_EXT = [".pdf", ".txt", ".md"];

function validateUploadFileClient(file) {
  if (!file) return "请选择文件";
  const name = (file.name || "").toLowerCase();
  if (/[\\/:<>|"?*\x00]/.test(name)) return "文件名含非法字符";
  const parts = name.split(".");
  if (parts.length <= 1) return "文件名须包含扩展名（仅支持 .pdf / .txt / .md）";
  const exts = parts.slice(1).map((p) => "." + p);
  const finalExt = exts[exts.length - 1];
  if (!UPLOAD_ALLOWED_EXT.includes(finalExt)) return "仅允许 PDF / TXT / MD 文件";
  for (const ext of exts) {
    if (BLOCKED_UPLOAD_EXT.includes(ext)) return `检测到危险扩展名：${ext}`;
  }
  if (exts.length >= 2 && BLOCKED_UPLOAD_EXT.includes(exts[exts.length - 2])) {
    return "检测到双扩展名伪装，已拒绝";
  }
  const maxBytes = uploadLimits.max_bytes || 5 * 1024 * 1024;
  if (file.size > maxBytes) {
    return `文件超过 ${Math.round(maxBytes / 1024 / 1024)}MB 上限`;
  }
  if (file.size === 0) return "文件为空";
  return null;
}

async function validateUploadFileDeep(file) {
  const basic = validateUploadFileClient(file);
  if (basic) return basic;
  const name = (file.name || "").toLowerCase();
  const ext = name.slice(name.lastIndexOf("."));
  const buf = await file.slice(0, 8192).arrayBuffer();
  const bytes = new Uint8Array(buf);
  if (ext === ".pdf") {
    const head = String.fromCharCode(...bytes.slice(0, 4));
    if (head !== "%PDF") return "文件内容与 PDF 格式不符";
    const text = String.fromCharCode(...bytes.slice(0, 512)).toLowerCase();
    if (text.includes("/javascript") || text.includes("/launch")) {
      return "PDF 含可疑脚本对象，已拒绝";
    }
  } else {
    try {
      new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      return "文本文件须为 UTF-8 编码";
    }
    if (bytes.includes(0)) return "文本文件含二进制空字节";
  }
  return null;
}

document.getElementById("authGateBtn")?.addEventListener("click", async () => {
  const input = document.getElementById("authGateInput");
  const token = (input?.value || "").trim();
  if (!token) return;
  sessionStorage.setItem(ACCESS_TOKEN_KEY, token);
  try {
    const res = await apiFetch("/api/health", { headers: apiHeaders() });
    if (!res.ok) throw new Error("令牌无效");
    await refreshAuthRole();
    document.getElementById("authGate")?.classList.add("hidden");
    const cfgRes = await fetch("/api/auth/config");
    if (cfgRes.ok) applyUploadPolicy(await cfgRes.json());
    loadHealth();
    loadDbStats();
  } catch {
    sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    showAuthGate("令牌验证失败，请向管理员索取 logs/web_access_token.txt 中的密钥");
  }
});

/* 等待提示：不再出现「Hub 编排中」「多模型融合推理中」这类把内部实现说给用户听的措辞，
   统一说「在做什么」而不是「用哪个模式」。
   2026-10-28：直接按界面语言出双语，不再依赖 i18n 正则事后翻译（整句带参数匹配不稳）。 */
function chatWaitHint(mode, scope, depth) {
  if (uiLang() === "en") {
    const dlab = depth === "partial" ? "Partial answer" : depth === "full" ? "Full answer" : "";
    const p = dlab ? dlab + " · " : "";
    if (scope === "literature") return `${p}searching literature…`;
    if (scope === "gene_db") return `${p}querying the local gene database…`;
    if (scope === "id_map") return `${p}resolving IDs across databases…`;
    if (scope === "retrieval_debug") return `${p}retrieval debug (no LLM)…`;
    return `${p}searching the local DB & literature…`;
  }
  const dlab = DEPTH_LABELS[depth] || "";
  const p = dlab ? dlab + " · " : "";
  if (scope === "literature") return `${p}检索文献并生成…`;
  if (scope === "gene_db") return `${p}查询本地基因库…`;
  if (scope === "id_map") return `${p}跨库解析 ID…`;
  if (scope === "retrieval_debug") return `${p}检索调试（不调用大模型）…`;
  return `${p}检索本地库与文献并生成…`;
}

/* 等待后缀（心跳里拼在 chatWaitHint 后面）：同样双语直出 */
function waitSuffixText(waitSec) {
  return uiLang() === "en"
    ? `waited ${waitSec}s — the first literature load can be slow`
    : `已等待 ${waitSec}s，文献库首载可能较慢`;
}

function snapshotAnswerPrefs() {
  return {
    scope: currentScope,
    depth: currentDepth,
    epoch: answerPrefsEpoch,
    scopeLabel: SCOPE_LABELS[currentScope] || currentScope,
    depthLabel: DEPTH_LABELS[currentDepth] || currentDepth,
  };
}

function updateSyncHint() {
  const el = document.getElementById("answerSyncHint");
  if (!el) return;
  const d = DEPTH_LABELS[currentDepth] || currentDepth;
  const wf = workflowPreset?.value || "full_hub";
  el.textContent = `聊天：${d} · Hub 全链路；工作流预置 ${wf}（不改聊天范围）`;
}

function applyScopeUI() {
  document.querySelectorAll(".scope-chip").forEach((b) => {
    b.classList.toggle("active", b.dataset.scope === currentScope);
  });
  document.querySelectorAll(".depth-chip").forEach((b) => {
    b.classList.toggle("active", b.dataset.depth === currentDepth);
  });
  updateSyncHint();
  // 使用策略影响检索调试默认字符预算（用户手改过则不覆盖）
  const maxCharsEl = document.getElementById("retrievalMaxChars");
  if (maxCharsEl && !maxCharsEl.dataset.userEdited) {
    maxCharsEl.value = String(currentDepth === "partial" ? 24000 : 48000);
  }
}

function syncWorkflowFromScope() {
  if (!workflowPreset || _syncingAnswerPrefs) return;
  const want = SCOPE_TO_PRESET[currentScope];
  if (!want) return;
  const has = Array.from(workflowPreset.options || []).some((o) => o.value === want);
  if (has && workflowPreset.value !== want) {
    _syncingAnswerPrefs = true;
    try {
      workflowPreset.value = want;
    } finally {
      _syncingAnswerPrefs = false;
    }
  }
}

function syncToolFromScope() {
  if (_syncingAnswerPrefs || typeof switchToolTab !== "function") return;
  const tool = SCOPE_TO_TOOL[currentScope];
  if (!tool) return;
  const active = document.querySelector(".tool-tab.active");
  if (active?.dataset?.tool === tool) return;
  _syncingAnswerPrefs = true;
  try {
    switchToolTab(tool, { keepResults: true, fromSync: true });
  } finally {
    _syncingAnswerPrefs = false;
  }
}

function setAnswerScope(scope, source = "chip") {
  if (!SCOPE_LABELS[scope]) return;
  const changed = scope !== currentScope;
  if (changed) {
    currentScope = scope;
    sessionStorage.setItem(SCOPE_KEY, currentScope);
    answerPrefsEpoch += 1;
  }
  applyScopeUI();
  if (source !== "workflow") syncWorkflowFromScope();
  if (source !== "tool") syncToolFromScope();
}

function setAnswerDepth(depth, source = "chip") {
  if (!DEPTH_LABELS[depth]) return;
  const changed = depth !== currentDepth;
  if (changed) {
    currentDepth = depth;
    sessionStorage.setItem(DEPTH_KEY, currentDepth);
    answerPrefsEpoch += 1;
  }
  applyScopeUI();
  if (source !== "workflow") syncWorkflowFromScope();
}

document.querySelectorAll(".scope-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    setAnswerScope(btn.dataset.scope || "literature", "chip");
  });
});
document.querySelectorAll(".depth-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    setAnswerDepth(btn.dataset.depth || "partial", "chip");
  });
});
applyScopeUI();
syncWorkflowFromScope();
syncToolFromScope();

/* 2026-10-27：模式指示行与模式按钮一起删除（见文件顶部 currentMode 的说明）。
   这里保留一个空实现，供旧调用点（applyFeatureStatic 等）无副作用地调用。 */
function syncModeIndicator() {}

/* 历史侧栏：搜索框平时收起，点放大镜才展开（参考主流对话产品的做法，省一行版面）。 */
(function initHistSearch() {
  const btn = document.getElementById("histSearchToggle");
  const field = document.getElementById("histSearchField");
  if (!btn || !field) return;
  btn.addEventListener("click", () => {
    const open = field.classList.toggle("is-collapsed") === false;
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const input = document.getElementById("historySearch");
      if (input) setTimeout(() => input.focus(), 30);
    }
  });
})();

document.querySelectorAll(".example-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    questionInput.value = btn.textContent;
    questionInput.focus();
  });
});

questionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

/** 将工具/工作流结果写入聊天与回答详情（与 /api/chat 同路径渲染） */
function publishScopedResult(question, data, prefs) {
  if (question) beginConversationTurn(question);
  else ensureActiveConversation();
  if (question) appendMessage("user", question);
  const answer = data.answer || "未生成有效回答";
  appendMessage("bot", answer);
  const enriched = {
    ...data,
    answer_scope: data.answer_scope || prefs.scope,
    answer_depth: data.answer_depth || prefs.depth,
  };
  renderDetail(enriched);
  applyAutoTools(enriched);
  if (question) pushChatHistory("user", question);
  pushChatHistory("assistant", answer);
  finishConversationTurn(answer, enriched);
  return enriched;
}

/** 当前界面语言（en|zh）：随对话请求发给后端，用于决定**回答语言**。
 *  规则（2026-10-11 用户要求）：英文界面 → 全英文；中文界面 → 除基因符号/ID/库名等
 *  专有名词外全中文。后端把它作为最高优先级的语言指令，优先于「按提问语言猜测」。 */
function uiLang() {
  const l = (document.documentElement.getAttribute("lang") || "en").toLowerCase();
  return l.indexOf("zh") === 0 ? "zh" : "en";
}

async function runScopedChatRequest(question, prefs) {
  const payload = {
    question,
    mode: "auto",
    lang: uiLang(),
    answer_scope: prefs.scope,
    answer_depth: prefs.depth,
    usage_strategy: prefs.depth,
    history: prefs.useHistory === false ? [] : chatHistory.slice(-CHAT_HISTORY_MAX),
  };
  const res = await apiFetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await parseJsonResponse(res, "/api/chat");
  if (!res.ok) throw new Error(data.detail || "请求失败");
  if (prefs.useHistory !== false) {
    pushChatHistory("user", question);
    pushChatHistory("assistant", data.answer || "");
  }
  return data;
}

/**
 * 流式对话：POST /api/chat/stream，SSE 边生成边回调。
 * @returns {Promise<object>} 最终完整 result（done 事件），用于详情渲染
 */
async function streamScopedChatRequest(payload, { onDelta, onQueued }) {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: apiHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (res.status === 401) {
    const d = await res.json().catch(() => ({}));
    if (d.code === "api_key_required" || d.code === "api_key_invalid") {
      throw new Error(d.detail || "接口通行证已过期，请刷新页面后重试");
    }
    showAuthGate("访问令牌无效或缺失，请输入管理员提供的令牌");
    throw new Error("需要访问令牌");
  }
  if (res.status === 403) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || "无权限访问此功能");
  }
  if (res.status === 429) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || "请求过于频繁，请稍后再试");
  }
  if (res.status === 503) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || "服务暂时繁忙，请稍候再试");
  }
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || "请求失败");
  }
  if (!res.body) {
    // 无流式支持时退化为普通 /api/chat
    return null;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let finalData = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const rawEvent = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (!rawEvent) continue;
      const lines = rawEvent.split("\n");
      for (const line of lines) {
        const t = line.trim();
        if (!t.startsWith("data:")) continue;
        let obj;
        try {
          obj = JSON.parse(t.slice(5).trim());
        } catch {
          continue;
        }
        if (obj.type === "delta" && onDelta && obj.text) {
          onDelta(obj.text);
        } else if (obj.type === "queued" && onQueued) {
          onQueued(obj);
        } else if (obj.type === "done") {
          finalData = obj.result;
        } else if (obj.type === "error") {
          throw new Error(obj.detail || "处理出错");
        }
      }
    }
  }
  return finalData;
}

/* 不该进入上文的内容：失败/占位提示、极短回答（多半是空壳）。
   之前这些会被写进 apiHistory，刷新后仍影响后续追问（例如模型看到"未生成有效回答"）。 */
const HISTORY_NOISE_RE = /^(未生成有效回答|请求失败|预热中请稍候|处理中|已等待|无相关数据|未找到|无权限|访问令牌无效)/;

function pushChatHistory(role, content) {
  const text = String(content || "").trim();
  if (!text) return;
  if (HISTORY_NOISE_RE.test(text)) return;
  if (role === "assistant" && text.length < 12) return;
  // 只保留最近内容片段（足够追问用，且不会让历史过大串扰新话题）
  chatHistory.push({ role, content: text.slice(0, 900) });
  while (chatHistory.length > CHAT_HISTORY_MAX) chatHistory.shift();
  updateContextChip();
}

function clearChatHistory() {
  chatHistory.length = 0;
  updateContextChip();
}

/** 上文指示条：让用户知道带了几轮、并能一键清空（模型被上文带偏时很有用） */
function updateContextChip() {
  const chip = document.getElementById("ctxChip");
  const label = document.getElementById("ctxChipText");
  if (!chip || !label) return;
  const rounds = Math.floor(chatHistory.length / 2);
  const half = chatHistory.length % 2;
  const shown = rounds + half;
  chip.classList.toggle("hidden", shown === 0);
  label.textContent = rounds > 0
    ? `已带入上文 ${rounds} 轮`
    : (half ? "已带入上文 1 条" : "已带入上文 0 轮");
}

/* ========== 左侧历史 / New Chat（对标 Cursor） ========== */
const CONV_MAX = 100;
const CONV_MSG_MAX = 160;
const CONV_STORE_VER = "v2";
/* 宽屏下左侧历史栏是否被 ☰ 收起（记住偏好，别每次刷新又弹回来） */
const SIDEBAR_HIDE_KEY = "zfqa_sidebar_hidden";
let conversations = [];
let activeConvId = null;
let historySearchQuery = "";

function uuidLite() {
  if (window.crypto?.randomUUID) return crypto.randomUUID();
  return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function deviceId() {
  let id = localStorage.getItem("zfqa_device_id");
  if (!id) {
    id = uuidLite();
    localStorage.setItem("zfqa_device_id", id);
  }
  return id;
}

function conversationOwnerKey() {
  const mode = sessionStorage.getItem(HUB_MODE_KEY);
  if (mode === "user") {
    try {
      const u = JSON.parse(sessionStorage.getItem(HUB_USER_KEY) || "{}");
      return `u:${u.user_id || u.username || "user"}`;
    } catch {
      return "u:user";
    }
  }
  return `g:${deviceId()}`;
}

function conversationStoreKey() {
  return `zfqa_conversations_${CONV_STORE_VER}:${conversationOwnerKey()}`;
}

/* 会话标题
   以前是「把第一句问题截到 48 字」：长问题会变成一整行半截话，短问题又可能
   只有「功能体现」这种看不出在聊什么的标签。现在按内容压成一句能认出来的短语：
   先匹配已知主题（能力 / 文献 / 坐标 / 表达…），再试基因符号，最后才退回
   「去掉客套 + 取第一个分句 + 截断」。 */
const TITLE_MAX_CJK = 18;
const TITLE_MAX_LATIN = 40;
const TITLE_RULES = [
  // 必须带上「你 / 平台 / 这些功能」这类指向助手的主语，
  // 否则「这个基因能做什么」也会被归成能力自述
  { re: /((你|您|助手|平台|系统|这里|这些功能|上面这些).{0,6}(能|会|可以)?(做|干|查|支持)(什么|啥|哪些)|有什么功能|支持哪些功能|能力(边界|范围|清单)|(你|这些功能).{0,10}(真|到底)?(能|可以)(实现|做到)|靠不靠谱|哪些做不到|what can you do|what can't you do|what are you capable)/i,
    zh: "能力自述：能做什么", en: "Capabilities: what it does" },
  { re: /(更新文献|文献更新|增量更新|抓取新文献|文献库更新|文献采集)/, zh: "文献更新", en: "Literature update" },
  { re: /(装配对照|liftover|坐标换算|坐标转换|换算到|换算成|转到\s*grcz|chain\s*文件)/i, zh: "坐标换算与装配对照", en: "Liftover & assembly" },
  { re: /(哪些组织表达|在哪里表达|表达谱|表达模式|单细胞)/, zh: "基因表达谱", en: "Expression profile" },
  { re: /(表型|phenotype)/i, zh: "基因与表型", en: "Gene & phenotype" },
  { re: /(sgrna|引物|primer|morpholino|敲除|敲低|转基因)/i, zh: "敲除与引物设计", en: "Knockout & primer design" },
  { re: /(富集|enrichment|kegg|go\s*分析)/i, zh: "GO / KEGG 富集", en: "GO / KEGG enrichment" },
  { re: /((重命名|删除).{0,4}(对话|会话|标题)|(对话|会话|标题).{0,4}(重命名|删除)|侧栏|历史记录|按钮.{0,4}(点不|没反应)|点击没反应)/, zh: "界面交互修复", en: "UI interaction fix" },
  { re: /(文献|引用|参考文献).{0,8}(检索|查询|格式|角标|脚注)/, zh: "文献检索与引用", en: "Literature search & citations" },
  { re: /(blast|序列比对)/i, zh: "BLAST 比对", en: "BLAST search" },
];

function _titlesAreEnglish() {
  try {
    return (document.documentElement.getAttribute("lang") || "").toLowerCase().indexOf("en") === 0;
  } catch (e) {
    return false;
  }
}

/* 去掉客套与引导语，只留下问题本身 */
function _stripTitleFillers(s) {
  let t = s;
  const lead = /^(请问一下|请问|你好|您好|麻烦问一下|麻烦|帮我|帮忙|我想知道|想知道|想问一下|想问|能不能|可不可以|可以|请|你|那|那么)\s*[，,、：:]?\s*/;
  for (let i = 0; i < 4 && lead.test(t); i += 1) t = t.replace(lead, "");
  return t
    .replace(/[（(][^）)]{0,30}[）)]\s*$/, "")   // 去掉结尾的括注
    .replace(/[吗呢啊呀吧哦~～]+\s*$/, "")
    .trim();
}

function _truncateTitle(s) {
  const limit = _titlesAreEnglish() ? TITLE_MAX_LATIN : TITLE_MAX_CJK;
  const chars = Array.from(s);
  if (chars.length <= limit) return s;
  // 截断位置常常停在「有 / 和 / 的」这种没有意义的字上，剪掉再说
  let cut = chars.slice(0, limit).join("").replace(/[\s的有和与及跟对把将了在是从]+$/, "");
  if (!cut) cut = chars.slice(0, limit).join("");
  return cut + "…";
}

function titleFromQuestion(q) {
  const raw = String(q || "").replace(/\s+/g, " ").trim();
  if (!raw) return "New Chat";
  const en = _titlesAreEnglish();

  // 1) 已知主题：这类问题在侧栏里反复出现，固定短名比截断问题好认
  for (const rule of TITLE_RULES) {
    if (rule.re.test(raw)) return en ? rule.en : rule.zh;
  }

  // 2) 基因符号：只在上下文确实在问基因时才用，避免把 python3 / utf8 当基因
  const geneish = /(基因|表达|功能|突变|敲除|敲低|同源|序列|蛋白|转录|甲基化|原位杂交)/.test(raw);
  if (geneish) {
    const m = raw.match(/(?:^|[^A-Za-z0-9])([a-z]{2,6}\d{1,3}[a-z]?(?:\d{1,2})?)(?![A-Za-z0-9])/i);
    if (m) return (en ? "Gene: " : "基因：") + m[1].toLowerCase();
  }

  // 3) 兜底：去客套 → 取第一个分句 → 截断
  let s = _stripTitleFillers(raw).split(/[，,。；;！!？?\n]/)[0].trim()
    .replace(/[的有和与及跟对把将]+\s*$/, "")
    .trim();
  if (!s) s = raw;
  // 一整句里如果前半段太短（「我有个问题」），取第二分句更具体
  if (Array.from(s).length < 4) {
    const parts = raw.split(/[，,。；;！!？?\n]/).map((x) => x.trim()).filter(Boolean);
    if (parts[1]) s = parts[1];
  }
  return _truncateTitle(s);
}

function startOfDay(ts = Date.now()) {
  const d = new Date(ts);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function historyGroupOf(ts) {
  const day = startOfDay(ts || Date.now());
  const today = startOfDay();
  const diff = today - day;
  if (diff <= 0) return "今天";
  if (diff <= 86400000) return "昨天";
  if (diff <= 7 * 86400000) return "近 7 天";
  if (diff <= 30 * 86400000) return "近 30 天";
  return "更早";
}

function slimDetail(detail) {
  if (!detail || typeof detail !== "object") return null;
  const ctx = detail.context ? String(detail.context) : "";
  return {
    task_type: detail.task_type || null,
    answer_scope: detail.answer_scope || null,
    answer_depth: detail.answer_depth || null,
    answer: detail.answer ? String(detail.answer).slice(0, 20000) : null,
    sql: detail.sql ? String(detail.sql).slice(0, 8000) : null,
    context: ctx ? ctx.slice(0, 12000) : null,
    row_count: detail.row_count ?? null,
    data: Array.isArray(detail.data) ? detail.data.slice(0, 40) : null,
    gene_detail: detail.gene_detail || null,
    agent_trace: Array.isArray(detail.agent_trace) ? detail.agent_trace.slice(0, 30) : null,
    tools_invoked: detail.tools_invoked || null,
  };
}

function makeConversation(partial = {}) {
  const now = Date.now();
  return {
    id: partial.id || uuidLite(),
    title: partial.title || "New Chat",
    titleManual: !!partial.titleManual,
    createdAt: partial.createdAt || now,
    updatedAt: partial.updatedAt || now,
    messages: Array.isArray(partial.messages) ? partial.messages : [],
    apiHistory: Array.isArray(partial.apiHistory) ? partial.apiHistory : [],
    lastDetail: partial.lastDetail || null,
    draft: typeof partial.draft === "string" ? partial.draft : "",
  };
}

function migrateLegacyConversations() {
  try {
    const legacyKey = `zfqa_conversations_v1:${conversationOwnerKey()}`;
    const legacy = localStorage.getItem(legacyKey);
    if (!legacy || localStorage.getItem(conversationStoreKey())) return;
    const data = JSON.parse(legacy);
    const list = (data.conversations || []).map((c) => makeConversation(c));
    localStorage.setItem(
      conversationStoreKey(),
      JSON.stringify({ activeId: data.activeId || list[0]?.id || null, conversations: list }),
    );
  } catch { /* ignore */ }
}

function loadConversationsFromStorage() {
  migrateLegacyConversations();
  try {
    const raw = localStorage.getItem(conversationStoreKey());
    if (!raw) {
      conversations = [];
      activeConvId = null;
      return;
    }
    const data = JSON.parse(raw);
    conversations = Array.isArray(data.conversations)
      ? data.conversations.map((c) => makeConversation(c))
      : [];
    activeConvId = data.activeId || conversations[0]?.id || null;
  } catch {
    conversations = [];
    activeConvId = null;
  }
}

function saveConversationsToStorage() {
  try {
    // 只保留有内容的会话 + 当前空会话
    const keep = [];
    for (const c of conversations) {
      const hasMsg = (c.messages || []).length > 0;
      if (hasMsg || c.id === activeConvId) keep.push(c);
    }
    conversations = keep.slice(0, CONV_MAX);
    localStorage.setItem(
      conversationStoreKey(),
      JSON.stringify({ activeId: activeConvId, conversations }),
    );
  } catch (e) {
    console.warn("conversation save failed", e);
  }
  scheduleCloudConversationSync();
}

let _cloudSyncTimer = null;
function scheduleCloudConversationSync() {
  if (sessionStorage.getItem(HUB_MODE_KEY) !== "user") return;
  if (!sessionStorage.getItem(HUB_SESSION_KEY) && !document.cookie.includes("hub_session=")) {
    // cookie 可能 HttpOnly 读不到；仍尝试同步（凭 cookie）
  }
  clearTimeout(_cloudSyncTimer);
  _cloudSyncTimer = setTimeout(() => {
    syncConversationsToCloud().catch(() => {});
  }, 800);
}

async function syncConversationsToCloud() {
  if (sessionStorage.getItem(HUB_MODE_KEY) !== "user") return;
  const payload = {
    activeId: activeConvId,
    conversations: conversations.slice(0, CONV_MAX).map((c) => ({
      id: c.id,
      title: c.title,
      titleManual: !!c.titleManual,
      createdAt: c.createdAt,
      updatedAt: c.updatedAt,
      messages: (c.messages || []).slice(-80).map((m) => ({
        role: m.role,
        content: String(m.content || "").slice(0, 12000),
        ts: m.ts,
      })),
      draft: c.draft ? String(c.draft).slice(0, 2000) : "",
    })),
  };
  const res = await apiFetch("/api/hub/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.status === 401) return;
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    console.warn("cloud sync failed", d.detail || res.status);
  }
}

async function pullConversationsFromCloud() {
  if (sessionStorage.getItem(HUB_MODE_KEY) !== "user") return false;
  try {
    const res = await apiFetch("/api/hub/conversations");
    if (!res.ok) return false;
    const body = await parseJsonResponse(res, res.url || "");
    const data = body && body.data;
    if (!data || !Array.isArray(data.conversations) || !data.conversations.length) {
      return false;
    }
    conversations = data.conversations.map((c) => makeConversation(c));
    activeConvId = data.activeId || conversations[0]?.id || null;
    // 写回本机该账号键，离线也能看
    try {
      localStorage.setItem(
        conversationStoreKey(),
        JSON.stringify({ activeId: activeConvId, conversations }),
      );
    } catch { /* ignore */ }
    return true;
  } catch {
    return false;
  }
}

function getActiveConversation() {
  return conversations.find((c) => c.id === activeConvId) || null;
}

function bumpConversation(conv) {
  if (!conv) return;
  conv.updatedAt = Date.now();
  conversations = [conv, ...conversations.filter((c) => c.id !== conv.id)];
  activeConvId = conv.id;
}

function abortActiveChatWork() {
  chatRequestSeq += 1;
  pendingQueue.length = 0;
  queueProcessing = false;
  setSending(false);
}

function snapshotComposerDraft() {
  const cur = getActiveConversation();
  if (!cur) return;
  cur.draft = questionInput?.value || "";
  cur.apiHistory = chatHistory.map((m) => ({ ...m }));
}

function restoreComposerDraft(conv) {
  if (!questionInput) return;
  questionInput.value = conv?.draft || "";
  questionInput.dispatchEvent(new Event("input"));
}

function clearDetailPanel() {
  if (detailEmpty) detailEmpty.classList.remove("hidden");
  if (detailContent) detailContent.classList.add("hidden");
  if (agentTraceBlock) agentTraceBlock.classList.add("hidden");
  if (geneBlock) geneBlock.classList.add("hidden");
  if (sqlBlock) sqlBlock.classList.add("hidden");
  if (dataBlock) dataBlock.classList.add("hidden");
  if (contextBlock) contextBlock.classList.add("hidden");
  if (detailTaskType) detailTaskType.textContent = "-";
  if (detailSql) detailSql.textContent = "";
  if (detailContext) detailContext.textContent = "";
  if (dataTable) dataTable.innerHTML = "";
  if (geneCard) geneCard.innerHTML = "";
  if (agentTraceEl) agentTraceEl.innerHTML = "";
}

function setChatEmptyVisible(empty) {
  const emptyEl = document.getElementById("chatEmptyState");
  emptyEl?.classList.toggle("hidden", !empty);
  messagesEl?.classList.toggle("hidden", !!empty);
  if (empty && messagesEl) messagesEl.innerHTML = "";
}

function syncApiHistoryFromConv(conv) {
  clearChatHistory();
  const src = conv?.apiHistory || [];
  for (const m of src.slice(-CHAT_HISTORY_MAX * 2)) {
    const text = String(m?.content || "").trim();
    if (!m?.role || !text) continue;
    if (HISTORY_NOISE_RE.test(text)) continue;          // 清洗旧版本存进来的脏条目
    if (m.role === "assistant" && text.length < 12) continue;
    chatHistory.push({ role: m.role, content: text.slice(0, 900) });
  }
  while (chatHistory.length > CHAT_HISTORY_MAX) chatHistory.shift();
  updateContextChip();
}

function renderConversationMessages(conv) {
  const msgs = conv?.messages || [];
  if (!msgs.length) {
    setChatEmptyVisible(true);
    clearDetailPanel();
    restoreComposerDraft(conv);
    return;
  }
  setChatEmptyVisible(false);
  if (messagesEl) messagesEl.innerHTML = "";
  for (const m of msgs) {
    if (!m?.content) continue;
    appendMessage(m.role === "user" ? "user" : "bot", m.content);
  }
  if (conv.lastDetail) renderDetail(conv.lastDetail);
  else clearDetailPanel();
  restoreComposerDraft(conv);
  if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
}

/* 2026-10-27：侧栏重命名 / 删除不再用 window.prompt / window.confirm。
   原生弹窗有两个硬伤：
   1) 浏览器拦截后（用户勾过「阻止此页面创建更多对话框」，或某些内嵌浏览器 / WebView 直接不支持）
      这两个按钮就永久「点了没反应」，而页面其它交互一切正常，极难自查；
   2) 样式与整站割裂，移动端 prompt 还会被键盘顶到看不见的位置。
   改成侧栏内联编辑 + 行内二次确认，全部是页面内 DOM，不依赖任何原生弹窗。 */
let historyEditingId = null;     // 正在行内重命名的会话 id
let historyConfirmDelId = null;  // 等待二次确认删除的会话 id
let historyConfirmTimer = 0;
let historyFocusPending = false; // 下一帧需要把焦点放进重命名输入框
let historyOutsideHandler = null; // 点空白处退出重命名（避免编辑态一直挂着）

function resetHistoryInlineState() {
  historyEditingId = null;
  historyConfirmDelId = null;
  historyFocusPending = false;
  if (historyConfirmTimer) {
    clearTimeout(historyConfirmTimer);
    historyConfirmTimer = 0;
  }
  if (historyOutsideHandler) {
    document.removeEventListener("pointerdown", historyOutsideHandler, true);
    historyOutsideHandler = null;
  }
}

/** 点输入框以外的地方就退出重命名，别让那一行一直停在输入框状态 */
function bindRenameOutsideClick() {
  unbindRenameOutsideClick();
  historyOutsideHandler = (e) => {
    if (!historyEditingId) {
      unbindRenameOutsideClick();
      return;
    }
    const inRow = e.target && e.target.closest && e.target.closest(".history-item.is-editing");
    if (inRow) return;
    cancelHistoryInline("rename");
  };
  // 捕获阶段监听，先于行点击等业务逻辑
  document.addEventListener("pointerdown", historyOutsideHandler, true);
}

function unbindRenameOutsideClick() {
  if (!historyOutsideHandler) return;
  document.removeEventListener("pointerdown", historyOutsideHandler, true);
  historyOutsideHandler = null;
}

/** 提交行内重命名：空标题视为取消，不做任何改动 */
function commitConversationTitle(id, raw) {
  const conv = conversations.find((c) => c.id === id);
  historyEditingId = null;
  historyFocusPending = false;
  unbindRenameOutsideClick();
  if (!conv) {
    renderHistoryList();
    return;
  }
  const title = String(raw == null ? "" : raw).trim().replace(/\s+/g, " ");
  if (title && title !== conv.title) {
    conv.title = title.slice(0, 80);
    conv.titleManual = true; // 手动改名后不再被首句问题覆盖
    conv.updatedAt = Date.now();
    saveConversationsToStorage();
  }
  renderHistoryList();
}

/** 进入行内重命名态 */
function renameConversation(id) {
  const conv = conversations.find((c) => c.id === id);
  if (!conv) return;
  historyEditingId = id;
  historyConfirmDelId = null;
  historyFocusPending = true;
  if (historyConfirmTimer) {
    clearTimeout(historyConfirmTimer);
    historyConfirmTimer = 0;
  }
  renderHistoryList();
  bindRenameOutsideClick();
}

/** 删除第一步：行内二次确认（5 秒后自动撤回，避免误触后一直停在那里） */
function askDeleteConversation(id) {
  const conv = conversations.find((c) => c.id === id);
  if (!conv) return;
  unbindRenameOutsideClick();
  historyConfirmDelId = id;
  historyEditingId = null;
  historyFocusPending = false;
  renderHistoryList();
  if (historyConfirmTimer) clearTimeout(historyConfirmTimer);
  historyConfirmTimer = setTimeout(() => {
    if (historyConfirmDelId === id) {
      historyConfirmDelId = null;
      renderHistoryList();
    }
  }, 5000);
}

function cancelHistoryInline(kind) {
  if (kind === "delete") historyConfirmDelId = null;
  else {
    historyEditingId = null;
    unbindRenameOutsideClick();
  }
  historyFocusPending = false;
  if (historyConfirmTimer) {
    clearTimeout(historyConfirmTimer);
    historyConfirmTimer = 0;
  }
  renderHistoryList();
}

function renderHistoryList() {
  const list = document.getElementById("historyList");
  if (!list) return;
  const q = historySearchQuery.trim().toLowerCase();
  // Cursor：侧栏只展示有内容的聊天；当前空 New Chat 不占历史位
  const items = conversations.filter((c) => {
    if (!(c.messages || []).length) return false;
    if (!q) return true;
    if ((c.title || "").toLowerCase().includes(q)) return true;
    return (c.messages || []).some((m) => String(m.content || "").toLowerCase().includes(q));
  });

  if (!items.length) {
    /* 2026-10-27：不再写「发送第一条消息后会出现在这里」这类教学文案，
       侧栏空着就空着，上面「开启新对话」已经说明了一切。只在搜索无结果时给一句。 */
    list.innerHTML = q ? `<div class="history-empty">无匹配聊天</div>` : "";
    return;
  }

  const order = ["今天", "昨天", "近 7 天", "近 30 天", "更早"];
  const groups = new Map(order.map((k) => [k, []]));
  for (const c of items) {
    const g = historyGroupOf(c.updatedAt);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(c);
  }

  list.innerHTML = "";
  for (const label of order) {
    const arr = groups.get(label) || [];
    if (!arr.length) continue;
    const lab = document.createElement("div");
    lab.className = "history-group-label";
    lab.textContent = label;
    list.appendChild(lab);
    for (const c of arr) {
      const editing = historyEditingId === c.id;
      const confirming = historyConfirmDelId === c.id;
      const row = document.createElement("div");
      row.className = "history-item"
        + (c.id === activeConvId ? " active" : "")
        + (editing ? " is-editing" : "")
        + (confirming ? " is-confirming" : "");
      row.dataset.id = c.id;
      row.title = c.title || "New Chat";

      if (editing) {
        /* 行内重命名：输入框 + 确定/取消，不再弹原生 prompt */
        row.innerHTML = `
          <input class="history-rename-input" type="text" maxlength="80" autocomplete="off" spellcheck="false" aria-label="重命名对话" />
          <div class="history-item-actions">
            <button type="button" class="history-icon-btn rename-ok" title="确定">✓</button>
            <button type="button" class="history-icon-btn rename-cancel" title="取消">✕</button>
          </div>
        `;
        const input = row.querySelector(".history-rename-input");
        input.value = c.title || "";
        row.addEventListener("click", (e) => e.stopPropagation());
        input.addEventListener("mousedown", (e) => e.stopPropagation());
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commitConversationTitle(c.id, input.value);
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancelHistoryInline("rename");
          }
        });
        row.querySelector(".rename-ok")?.addEventListener("click", (e) => {
          e.stopPropagation();
          commitConversationTitle(c.id, input.value);
        });
        row.querySelector(".rename-cancel")?.addEventListener("click", (e) => {
          e.stopPropagation();
          cancelHistoryInline("rename");
        });
      } else if (confirming) {
        /* 行内二次确认：删除是不可逆操作，保留一次确认，但不用原生 confirm */
        row.innerHTML = `
          <div class="history-item-title history-del-ask">删除这个对话？</div>
          <div class="history-item-actions">
            <button type="button" class="history-text-btn do-delete" title="确认删除">删除</button>
            <button type="button" class="history-text-btn cancel-delete" title="取消">取消</button>
          </div>
        `;
        row.addEventListener("click", (e) => e.stopPropagation());
        row.querySelector(".do-delete")?.addEventListener("click", (e) => {
          e.stopPropagation();
          historyConfirmDelId = null;
          if (historyConfirmTimer) {
            clearTimeout(historyConfirmTimer);
            historyConfirmTimer = 0;
          }
          deleteConversation(c.id);
        });
        row.querySelector(".cancel-delete")?.addEventListener("click", (e) => {
          e.stopPropagation();
          cancelHistoryInline("delete");
        });
      } else {
        row.innerHTML = `
          <div class="history-item-title"></div>
          <div class="history-item-actions">
            <button type="button" class="history-icon-btn rename" title="重命名">✎</button>
            <button type="button" class="history-icon-btn delete" title="删除">✕</button>
          </div>
        `;
        row.querySelector(".history-item-title").textContent = c.title || "New Chat";
        row.addEventListener("click", (e) => {
          if (e.target.closest(".history-icon-btn")) return;
          switchConversation(c.id);
          closeHistoryDrawer();
        });
        row.querySelector(".rename")?.addEventListener("click", (e) => {
          e.stopPropagation();
          renameConversation(c.id);
        });
        row.querySelector(".delete")?.addEventListener("click", (e) => {
          e.stopPropagation();
          askDeleteConversation(c.id);
        });
      }
      list.appendChild(row);
      if (editing && historyFocusPending) {
        historyFocusPending = false;
        const input = row.querySelector(".history-rename-input");
        if (input) {
          input.focus();
          input.select();
        }
      }
    }
  }
}

function ensureActiveConversation() {
  if (getActiveConversation()) return getActiveConversation();
  const conv = makeConversation();
  conversations.unshift(conv);
  activeConvId = conv.id;
  saveConversationsToStorage();
  return conv;
}

/** 发送用户问题时立刻写入历史（对标 Cursor：一提问就进侧栏） */
function beginConversationTurn(userText) {
  const conv = ensureActiveConversation();
  setChatEmptyVisible(false);
  conv.messages.push({ role: "user", content: String(userText), at: Date.now() });
  while (conv.messages.length > CONV_MSG_MAX) conv.messages.shift();
  if (!conv.titleManual) conv.title = titleFromQuestion(userText);
  conv.draft = "";
  bumpConversation(conv);
  saveConversationsToStorage();
  renderHistoryList();
  return conv;
}

/** 回答完成/失败后写入 bot 消息与详情 */
function finishConversationTurn(botText, detail) {
  const conv = ensureActiveConversation();
  if (botText != null && String(botText).length) {
    conv.messages.push({ role: "bot", content: String(botText), at: Date.now() });
  }
  while (conv.messages.length > CONV_MSG_MAX) conv.messages.shift();
  conv.apiHistory = chatHistory.map((m) => ({ ...m }));
  if (detail) conv.lastDetail = slimDetail(detail);
  bumpConversation(conv);
  saveConversationsToStorage();
  renderHistoryList();
}

/** 兼容旧调用名 */
function persistConversationTurn(userText, botText, detail) {
  if (userText) beginConversationTurn(userText);
  finishConversationTurn(botText, detail);
}

function switchConversation(id) {
  /* 切走时把行内重命名 / 待确认删除的状态收掉，
     否则换回来时那一行还是输入框或「删除这个对话？」 */
  resetHistoryInlineState();
  if (!id || id === activeConvId) {
    renderHistoryList();
    return;
  }
  abortActiveChatWork();
  snapshotComposerDraft();
  const next = conversations.find((c) => c.id === id);
  if (!next) return;
  activeConvId = id;
  syncApiHistoryFromConv(next);
  renderConversationMessages(next);
  saveConversationsToStorage();
  renderHistoryList();
  questionInput?.focus();
}

function createNewChat() {
  // Cursor：随时可开新对话；旧请求作废，不阻塞
  resetHistoryInlineState();
  abortActiveChatWork();
  snapshotComposerDraft();

  const cur = getActiveConversation();
  if (cur && !(cur.messages || []).length) {
    // 已在空白 New Chat：只清空输入并聚焦。
    // 这一步原本完全是静默的，点了「新对话」看不到任何变化 → 会被当成按钮坏了，所以给个明确反馈。
    cur.draft = "";
    renderConversationMessages(cur);
    renderHistoryList();
    questionInput?.focus();
    closeHistoryDrawer();
    window.ZFUi?.toast?.("已经在新的对话里（输入框已清空）", "info");
    return;
  }

  // 丢掉其它空白草稿，只保留一个 New Chat
  conversations = conversations.filter((c) => (c.messages || []).length > 0);
  const conv = makeConversation();
  conversations.unshift(conv);
  activeConvId = conv.id;
  clearChatHistory();
  setChatEmptyVisible(true);
  clearDetailPanel();
  if (questionInput) {
    questionInput.value = "";
    questionInput.dispatchEvent(new Event("input"));
  }
  saveConversationsToStorage();
  renderHistoryList();
  questionInput?.focus();
  closeHistoryDrawer();
}

function deleteConversation(id) {
  const target = conversations.find((c) => c.id === id);
  if (!target) return;
  /* 二次确认已在侧栏行内完成（askDeleteConversation），这里直接执行删除。
     原来用 window.confirm：一旦浏览器拦截原生弹窗，删除就永久失效。 */
  const wasActive = activeConvId === id;
  conversations = conversations.filter((c) => c.id !== id);
  if (wasActive) {
    abortActiveChatWork();
    const next = conversations.find((c) => (c.messages || []).length > 0) || null;
    if (next) {
      activeConvId = next.id;
      syncApiHistoryFromConv(next);
      renderConversationMessages(next);
    } else {
      const conv = makeConversation();
      conversations.unshift(conv);
      activeConvId = conv.id;
      clearChatHistory();
      setChatEmptyVisible(true);
      clearDetailPanel();
      if (questionInput) questionInput.value = "";
    }
  }
  saveConversationsToStorage();
  resetHistoryInlineState();
  renderHistoryList();
  window.ZFUi?.toast?.("已删除对话", "ok");
}

function openHistoryDrawer() {
  document.getElementById("historyPanel")?.classList.add("open");
  document.getElementById("historyBackdrop")?.classList.remove("hidden");
}

function closeHistoryDrawer() {
  document.getElementById("historyPanel")?.classList.remove("open");
  document.getElementById("historyBackdrop")?.classList.add("hidden");
}

function initConversationUI() {
  loadConversationsFromStorage();
  if (!conversations.length) {
    const conv = makeConversation();
    conversations = [conv];
    activeConvId = conv.id;
    saveConversationsToStorage();
  }
  if (!getActiveConversation()) activeConvId = conversations[0].id;
  const active = getActiveConversation();
  syncApiHistoryFromConv(active);
  renderConversationMessages(active);
  renderHistoryList();
  // 登录用户：后台拉云端专属历史（覆盖本机同账号缓存）
  if (sessionStorage.getItem(HUB_MODE_KEY) === "user") {
    pullConversationsFromCloud().then((ok) => {
      if (!ok) return;
      if (!getActiveConversation() && conversations[0]) activeConvId = conversations[0].id;
      const cur = getActiveConversation();
      syncApiHistoryFromConv(cur);
      renderConversationMessages(cur);
      renderHistoryList();
    });
  }
}

document.getElementById("newChatBtn")?.addEventListener("click", () => createNewChat());
document.getElementById("historySearch")?.addEventListener("input", (e) => {
  historySearchQuery = e.target.value || "";
  renderHistoryList();
});
/* 历史抽屉的唤出入口（2026-10-28）
   页眉左上角的三道杠已删除：它在宽屏下会「收起侧栏 + 把路由切到 #/chat」，
   用户点一下就发现界面变了，像是坏了。现在宽屏侧栏常驻；窄屏用问答页里的
   「历史」小按钮（[data-history-toggle]）唤出抽屉。 */
function toggleHistoryUI() {
  const panel = document.getElementById("historyPanel");
  if (window.matchMedia("(min-width: 981px)").matches) {
    // 宽屏：侧栏常驻，这里只保证历史可见（不切路由、不改布局）
    document.body.classList.remove("chat-sidebar-hidden");
    return;
  }
  // 窄屏 / 平板是抽屉模式：不在问答页先切回来，再滑出
  if (!(location.hash || "").startsWith("#/chat")) {
    location.hash = "#/chat";
    setTimeout(() => openHistoryDrawer(), 80);
    return;
  }
  if (panel?.classList.contains("open")) closeHistoryDrawer();
  else openHistoryDrawer();
}
document.querySelectorAll("[data-history-toggle]").forEach((b) => {
  b.addEventListener("click", toggleHistoryUI);
});
document.getElementById("historyBackdrop")?.addEventListener("click", () => closeHistoryDrawer());

document.addEventListener("keydown", (e) => {
  // Cursor 风格：Ctrl/Cmd+Shift+L 新对话（避免与浏览器新窗口冲突）
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "l") {
    e.preventDefault();
    createNewChat();
  }
});

/** 处理下一个待发送问题（队列） */
function processQueue() {
  if (queueProcessing || isSending) return;
  if (!pendingQueue.length) return;
  const next = pendingQueue.shift();
  if (!next) return;
  queueProcessing = true;
  setSending(true);
  sendQuestion(next)
    .catch(() => {})
    .finally(() => {
      queueProcessing = false;
      // 若还有排队问题，继续处理
      if (pendingQueue.length) {
        processQueue();
      } else {
        setSending(false);
        loadHealth();
      }
    });
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const question = questionInput.value.trim();
  if (!question) return;

  if (systemWarming) {
    appendMessage("bot", "核心模型仍在预热，请稍候再发送（状态栏显示「仍在加载」且发送按钮灰掉时）。").setAttribute("data-i18n-live", "1");
    return;
  }
  if (!lastHealthOk) {
    appendMessage("bot", "服务暂时无响应，请刷新页面或稍候再试。").setAttribute("data-i18n-live", "1");
    return;
  }

  // 回答进行中：把问题入队，当前完成后自动发送
  if (isSending || queueProcessing) {
    if (pendingQueue.length < 8) {
      pendingQueue.push(question);
      questionInput.value = "";
      appendMessage(
        "bot",
        `📥 已加入队列（剩余 ${pendingQueue.length} 个）：${question}`,
      ).setAttribute("data-i18n-live", "1");
    } else {
      appendMessage("bot", "排队已满（最多 8 个），请等当前回答完成后再发。").setAttribute("data-i18n-live", "1");
    }
    return;
  }
  // 后端并发已打满：不再本地压队列，直接发出去——
  // 服务端会 FIFO 排队并实时回传「第几位/预计还需多久」，体验同 DeepSeek。
  // （同一用户连发多问仍走上面的本地队列，保证自己的问题按序回答。）

  questionInput.value = "";
  setSending(true);
  sendQuestion(question)
    .catch(() => {})
    .finally(() => {
      setSending(false);
      // 若期间有新问题排队，自动继续处理
      if (pendingQueue.length) {
        processQueue();
      } else {
        loadHealth();
      }
    });
});

async function sendQuestion(question) {
  const prefs = snapshotAnswerPrefs();
  beginConversationTurn(question);
  appendMessage("user", question);

  const typingEl = appendMessage("bot", chatWaitHint(currentMode, prefs.scope, prefs.depth), true);
  // 聊天区默认不被 i18n 改写（保护答案），但这是状态占位文案 → 显式放行
  typingEl.setAttribute("data-i18n-live", "1");
  if (window.HubI18n && window.HubI18n.refresh) window.HubI18n.refresh();
  const bubbleEl = typingEl.querySelector(".bubble");
  let reqId = ++chatRequestSeq;
  let waitSec = 0;
  let streamedAnswer = "";
  let rafPending = false;
  // 最终答案已经写回气泡后置 true：挡住尾部 requestAnimationFrame 用旧内容覆盖
  // （否则最后一帧会把溯源面板和「复制/重新生成」按钮一起抹掉）
  let streamFinalized = false;
  // 多机并行：并发打满时后端会让新问排队，这里实时显示「第几位 / 预计还需多久」
  let queueInfo = null;

  const queueHintText = () => {
    if (!queueInfo) return null;
    const en = uiLang() === "en";
    const pos = queueInfo.position || 1;
    const waited = queueInfo.waited || 0;
    const eta = queueInfo.eta || 0;
    const etaTxt = eta > 0 ? (en ? `, ~${eta}s left` : `，约还需 ${eta}s`) : "";
    if (en) {
      return pos > 1
        ? `Queued · ${pos - 1} ahead (waited ${waited}s${etaTxt})`
        : `Queued · starting now (waited ${waited}s${etaTxt})`;
    }
    return pos > 1
      ? `排队中 · 前面还有 ${pos - 1} 位（已等 ${waited}s${etaTxt}）`
      : `排队中 · 即将开始（已等 ${waited}s${etaTxt}）`;
  };

  // requestAnimationFrame 节流：保证每帧渲染一次，避免同帧多次赋值只显示最后一次
  const scheduleRender = () => {
    if (rafPending || !bubbleEl) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      if (streamFinalized) return;
      if (!bubbleEl || !typingEl.isConnected) return;
      if (streamedAnswer) {
        bubbleEl.classList.add("md-body");
        bubbleEl.innerHTML = mdToHtml(streamedAnswer);
        typingEl.classList.remove("typing");
      }
      messagesEl.scrollTop = messagesEl.scrollHeight;
    });
  };

  const renderStreamed = scheduleRender;

  const heartbeat = setInterval(() => {
    if (reqId !== chatRequestSeq || !typingEl.isConnected) return;
    waitSec += 15;
    if (!streamedAnswer) {
      const q = queueHintText();
      bubbleEl.textContent = q
        ? q
        : `${chatWaitHint(currentMode, prefs.scope, prefs.depth)}（${waitSuffixText(waitSec)}）`;
    }
  }, 15000);

  try {
    const payload = { question, mode: currentMode, lang: uiLang() };
    // Hub 多 Agent / 自动：带回答范围 + 使用策略；其它 mode 页签走旧单任务路径
    if (!currentMode || currentMode === "auto" || currentMode === "hub") {
      // 聊天固定 Hub 全链路。工作流下拉只作用于「运行工作流」，禁止把聊天绑成 id_only。
      payload.answer_scope = "hub";
      payload.answer_depth = prefs.depth;
      payload.usage_strategy = prefs.depth;
      payload.history = chatHistory.slice(-CHAT_HISTORY_MAX);
    }

    // 流式输出已启用：回答边生成边显示（感知更快，不用干等完整回答）
    const useStream = !currentMode || currentMode === "auto" || currentMode === "hub";
    let data = null;
    if (useStream) {
      data = await streamScopedChatRequest(payload, {
        onDelta: (text) => {
          if (reqId !== chatRequestSeq) return;
          typingEl.classList.remove("typing");
          queueInfo = null; // 已开始出字，不再显示排队提示
          streamedAnswer += text;
          renderStreamed();
        },
        onQueued: (info) => {
          if (reqId !== chatRequestSeq) return;
          queueInfo = info;
          const txt = queueHintText();
          if (txt && bubbleEl && !streamedAnswer) bubbleEl.textContent = txt;
        },
      });
      // 流式不可用（后端无流式端点）时回退普通请求
      if (data === null) {
        const res = await apiFetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        data = await parseJsonResponse(res, "/api/chat");
        if (!res.ok) throw new Error(data.detail || "请求失败");
        streamedAnswer = data.answer || "";
      }
    } else {
      const res = await apiFetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      data = await parseJsonResponse(res, "/api/chat");
      if (!res.ok) throw new Error(data.detail || "请求失败");
      streamedAnswer = data.answer || "";
    }
    if (reqId !== chatRequestSeq) return;

    // done.result.answer 已把正文编号收成和文末同一套。
    // 必须先写回 streamedAnswer，否则尾部 requestAnimationFrame 会用流式旧编号盖掉这一帧。
    const finalAnswer = data && data.answer ? data.answer : streamedAnswer;
    streamedAnswer = finalAnswer;
    if (finalAnswer && typingEl.isConnected) {
      // 生成结束必须摘掉 typing，否则 .msg.typing .bubble::after 的打字光标会一直闪
      // （它是 bubble 的 ::after，位置在「复制/重新生成」按钮之后，看起来就是末尾一根绿竖杠）
      streamFinalized = true;
      typingEl.classList.remove("typing");
      bubbleEl.classList.add("md-body");
      bubbleEl.innerHTML = mdToHtml(finalAnswer);
      appendProvenancePanel(bubbleEl, finalAnswer, data); // 流式分支不走 appendMessage，面板单独补
      attachMsgActions(typingEl, finalAnswer); // 流式渲染覆盖 innerHTML 后，补回操作按钮
    } else {
      typingEl.remove();
      appendMessage("bot", finalAnswer || "未生成有效回答");
    }
    pushChatHistory("user", question);
    pushChatHistory("assistant", finalAnswer || "");
    const enriched = {
      ...data,
      answer: finalAnswer,
      answer_scope: data.answer_scope || prefs.scope,
      answer_depth: data.answer_depth || prefs.depth,
    };
    renderDetail(enriched);
    applyAutoTools(data);
    finishConversationTurn(finalAnswer || "未生成有效回答", enriched);
    loadHealth();
  } catch (err) {
    if (reqId !== chatRequestSeq) return;
    typingEl.remove();
    const msg = String(err && err.message ? err.message : err);
    let failText;
    if (/Failed to fetch|NetworkError|fetch/i.test(msg)) {
      failText = systemWarming
        ? "预热中请稍候：服务正在加载文献 FAISS，暂时无法连接。请等状态栏预热结束后再问。"
        : "请求失败：无法连接服务（可能仍在加载或刚重启）。请稍后重试。";
    } else if (/^请求失败/.test(msg)) {
      // 服务端未给 detail 时，msg 本身就是"请求失败" —— 直接拼会造成"请求失败：请求失败"。
      // 这里给出可操作信息，而不是重复一遍。
      failText = "请求失败：服务端未返回具体原因，请重试；细节见右侧「回答详情」。";
    } else {
      failText = `请求失败：${msg}`;
    }
    // 失败提示也是 UI 文案（不是答案），英文模式下必须跟着切
    appendMessage("bot", failText).setAttribute("data-i18n-live", "1");
    if (window.HubI18n && window.HubI18n.refresh) window.HubI18n.refresh();
    finishConversationTurn(failText, null);
  } finally {
    clearInterval(heartbeat);
    // 兜底：不论本轮走到哪条分支（正常 / 空答案 / 异常），占位消息都不该再挂 typing，
    // 否则 .bubble::after 的打字光标会一直闪下去。
    streamFinalized = true;
    if (typingEl.isConnected) typingEl.classList.remove("typing");
  }
}

document.querySelectorAll(".tool-tab").forEach((btn) => {
  btn.addEventListener("click", () => switchToolTab(btn.dataset.tool));
});

function switchToolTab(tool, options = {}) {
  const keepResults = options.keepResults === true;
  const fromSync = options.fromSync === true;
  const routeMap = {
    gene: "gene", blast: "blast", enrich: "enrich", kegg: "kegg",
    idresolve: "id", upload: "upload", sequence: "sequence",
    annotate: "annotate", retrieval: "literature",
  };
  // 一功能一界面：用户主动切工具 → 跳独立页；同步/联动只改 tab 状态
  if (!fromSync && routeMap[tool]) {
    location.hash = "#/" + routeMap[tool];
    return;
  }
  document.querySelectorAll(".tool-tab").forEach((b) => {
    b.classList.toggle("active", b.dataset.tool === tool);
  });
  if (!keepResults) {
    toolResult.classList.add("hidden");
    idResolvePanel.classList.add("hidden");
    document.querySelectorAll(".tool-tab").forEach((b) => b.classList.remove("auto-linked"));
  }
  if (tool === "blast" && blastInput) {
    setTimeout(() => blastInput.focus(), 0);
  }
}

/* 2026-10-31：BLAST 示例序列（BLAST_SAMPLE_SEQ / BLAST_SAMPLE_BATCH）随「填入 3 条示例」一起删除。 */

function normalizeBlastInput(raw) {
  return String(raw || "")
    .split(/\r?\n/)
    .filter((line) => line.trim() && !line.trim().startsWith(">"))
    .join("")
    .replace(/\s+/g, "")
    .toUpperCase();
}

/* 切成多条查询：有 ">" 按 FASTA 记录切；没有就整体当一条裸序列。 */
function parseBlastInputs(raw) {
  const text = String(raw || "").trim();
  if (!text) return [];
  if (!text.includes(">")) {
    const seq = normalizeBlastInput(text);
    return seq ? [{ label: "序列 1", seq }] : [];
  }
  const out = [];
  let header = "";
  let parts = [];
  const flush = () => {
    const seq = parts.join("").replace(/[^A-Za-z]/g, "").toUpperCase();
    if (seq) out.push({ label: header || "序列 " + (out.length + 1), seq });
    parts = [];
  };
  text.split(/\r?\n/).forEach((line) => {
    const t = line.trim();
    if (!t) return;
    if (t.startsWith(">")) {
      flush();
      header = t.slice(1).trim().split(/\s+/)[0] || "";
    } else {
      parts.push(t);
    }
  });
  flush();
  return out;
}

async function pasteBlastFromClipboard() {
  if (!blastInput) return;
  try {
    if (navigator.clipboard?.readText) {
      const text = await navigator.clipboard.readText();
      if (text?.trim()) {
        // 多序列 FASTA 原样保留：去掉 ">" 会把批量合成一条
        blastInput.value = text.includes(">") ? text.trim() : (normalizeBlastInput(text) || text.trim());
        blastInput.focus();
        return;
      }
    }
  } catch {
    /* 无剪贴板权限时回退到手动 Ctrl+V */
  }
  blastInput.focus();
  toolResult.classList.remove("hidden");
  toolResult.innerHTML =
    "<p class='tool-hint warn'>无法自动读剪贴板：请先<strong>用鼠标点一下</strong>输入框，再按 Ctrl+V；本机/HTTPS 下可用「从剪贴板粘贴」按钮。</p>";
}

blastPasteBtn?.addEventListener("click", () => { pasteBlastFromClipboard().then(() => setTimeout(updateBlastCount, 60)); });

/* 批量识别计数：输入/粘贴/导入文件后实时告诉用户"这次会跑几条" */
const blastCountEl = document.getElementById("blastCount");
function updateBlastCount() {
  if (!blastCountEl) return;
  const list = parseBlastInputs(blastInput?.value || "");
  const total = list.reduce((n, q) => n + q.seq.length, 0);
  blastCountEl.textContent = list.length
    ? `已识别 ${list.length} 条序列 · 合计 ${total.toLocaleString()} bp · 一次最多 20 条`
    : "已识别 0 条序列 · 一次最多 20 条";
  blastCountEl.classList.toggle("warn", list.length > 20);
}
blastInput?.addEventListener("input", updateBlastCount);
// 2026-10-31：「填入 3 条示例」按钮与 BLAST_SAMPLE_BATCH 示例数据整体删除（用户要求去掉示例填充功能）。

/* 本地 FASTA 文件导入：不经过服务器，读完直接放进输入框，用户仍可编辑 */
document.getElementById("blastFileInput")?.addEventListener("change", (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file || !blastInput) return;
  if (file.size > 2 * 1024 * 1024) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = "<p class='tool-hint warn'>文件超过 2 MB，请先拆分后再导入</p>";
    e.target.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const text = String(reader.result || "").trim();
    blastInput.value = text.includes(">") ? text : (normalizeBlastInput(text) || text);
    updateBlastCount();
    blastInput.focus();
  };
  reader.readAsText(file, "utf-8");
  e.target.value = "";
});

document.getElementById("blastClearBtn")?.addEventListener("click", () => {
  if (blastInput) blastInput.value = "";
  updateBlastCount();
  if (toolResult) {
    toolResult.classList.add("hidden");
    toolResult.innerHTML = "";
  }
});

blastInput?.addEventListener("paste", () => {
  setTimeout(() => {
    if (!blastInput?.value) return;
    if (blastInput.value.includes(">")) { updateBlastCount(); return; }   // 多条 FASTA 不动
    const normalized = normalizeBlastInput(blastInput.value);
    if (normalized) blastInput.value = normalized;
    updateBlastCount();
  }, 0);
});
updateBlastCount();

blastBtn.addEventListener("click", async () => {
  const queries = parseBlastInputs(blastInput.value);
  if (!queries.length) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML =
      "<p class='tool-hint warn'>请先粘贴或填入 ≥20 bp 的核酸序列（ATCG）；多条请用 FASTA（每条以 &gt; 开头）</p>";
    return;
  }
  if (queries.length > 20) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = "<p class='tool-hint warn'>一次最多 20 条查询序列，请分批提交</p>";
    return;
  }
  const tooShort = queries.findIndex((q) => q.seq.length < 20);
  if (tooShort >= 0) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML =
      `<p class='tool-hint warn'>第 ${tooShort + 1} 条只有 ${queries[tooShort].seq.length} bp，至少需要 20 bp</p>`;
    return;
  }

  // 命中条数固定 10：原来的「每条序列前 N 条」下拉已撤，这一页不再让用户选。
  const topK = 10;
  toolResult.classList.remove("hidden");
  toolResult.innerHTML = "<span class='tool-loading'>BLAST 运行中…</span>";
  blastBtn.disabled = true;

  const runs = [];
  // 502/503/504 = 网关拿不到后端（Hub 正在重启，或隧道抖了一下）。
  // 这时逐条重试没有意义，只会在页面上刷三条同样的红字，改为整批中止 + 一句人话。
  let gatewayDown = false;
  try {
    for (let i = 0; i < queries.length; i += 1) {
      const q = queries[i];
      toolResult.innerHTML =
        `<span class='tool-loading'>BLAST 运行中… ${i}/${queries.length}（${escapeHtml(q.label)}）</span>`;
      try {
        const res = await apiFetch("/api/tools/blast", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sequence: q.seq, top_k: topK, evalue: 0.01 }),
        });
        const data = await parseJsonResponse(res, res.url || "");
        if (!res.ok) throw new Error(data.detail || "失败");
        runs.push({ label: q.label, seq: q.seq, data });
      } catch (err) {
        const msg = String(err.message || err);
        if (/\bHTTP 50[234]\b/.test(msg) || /网关|Gateway|not valid JSON/i.test(msg)) {
          gatewayDown = true;
          break;
        }
        runs.push({ label: q.label, seq: q.seq, error: msg });
      }
    }
    const head = gatewayDown
      ? "<p class='tool-hint warn'>服务正在重启或繁忙（网关返回 502），这批 BLAST 已中止 —— 等 10 秒再点一次「运行 BLAST」即可，不用改输入。</p>"
      : "";
    toolResult.innerHTML = head + (runs.length ? renderBlastRuns(runs) : "");
  } finally {
    blastBtn.disabled = false;
  }
});

function blastAccession(h) {
  const title = String(h.subject_title || "");
  const pid = title.match(/\[protein_id=([^\]]+)\]/i);
  if (pid) return pid[1];
  const sid = String(h.subject_id || "");
  const cds = sid.match(/cds_([A-Z]{2}_[0-9.]+)/i);
  if (cds) return cds[1];
  return sid.replace(/^lcl\|/, "");
}

/* 单条查询的结果表（批量时由 renderBlastRuns 复用） */
function renderBlastTable(r) {
  const hits = (r && r.hits) || [];
  if (!hits.length) return "<p class='tool-hint'>E-value 阈值内没有命中。</p>";
  let html = `<table class="pub-table"><thead><tr>
    <th>#</th><th>基因</th><th>登录号</th><th>Ident%</th><th>Len</th><th>E-value</th><th>Bit</th><th>Qcov</th>
  </tr></thead><tbody>`;
  hits.forEach((h, i) => {
    const e = h.evalue < 0.01 ? Number(h.evalue).toExponential(1) : Number(h.evalue).toPrecision(3);
    const gene = h.gene_symbol || "";
    const acc = blastAccession(h);
    html += `<tr><td>${i + 1}</td><td title="${escapeHtml(h.subject_title || acc)}">${escapeHtml(gene || "—")}</td>
      <td>${escapeHtml(acc)}</td>
      <td>${h.identity_percent}</td><td>${h.alignment_length}</td><td>${e}</td><td>${h.bitscore}</td><td>${h.query_coverage_percent ?? "-"}</td></tr>`;
  });
  return html + "</tbody></table>";
}

/* 批量结果：按查询分组，每组一个标题 + 状态行 + 表；TSV 可整批复制 */
function renderBlastRuns(runs) {
  if (!runs.length) return "<p class='tool-hint'>没有可运行的结果。</p>";
  const tsvAll = [];
  let html = runs.length > 1
    ? `<div class="tool-summary">批量 BLAST：${runs.length} 条查询 · 逐条提交 · 库 GRCz12tu</div>`
    : "";
  runs.forEach((run, idx) => {
    const r = run.data?.result || {};
    const head = run.data
      ? `${r.engine || "BLAST+"} · 查询 ${r.query_length || run.seq.length} bp · 库 ${String(r.database || "").split(/[\\/]/).pop() || "—"} · ${r.assembly || "GRCz12tu"} · ${(r.hits || []).length} 条命中`
      : `运行失败：${escapeHtml(run.error || "未知错误")}`;
    html += `<section class="blast-run">
      <h4 class="blast-run-title">${idx + 1}. ${escapeHtml(run.label)} <span class="blast-run-len">${run.seq.length} bp</span></h4>
      <p class="tool-hint">${head}</p>`;
    if (run.data) {
      html += renderBlastTable(r);
      if (r.note) html += `<p class="tool-hint">${escapeHtml(r.note)}</p>`;
      if (run.data.tsv) tsvAll.push(run.data.tsv);
    }
    html += "</section>";
  });
  if (tsvAll.length) {
    html += `<button type="button" class="export-tsv-btn" data-tsv="${encodeURIComponent(tsvAll.join("\n"))}">复制 TSV</button>`;
  }
  return html;
}

function renderBlastResult(data) {
  return renderBlastRuns([{ label: "序列 1", seq: "", data }]);
}

enrichBtn.addEventListener("click", async () => {
  const raw = enrichInput.value.trim();
  if (!raw) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = "<p class='tool-hint warn'>请输入至少一个基因，如 tp53,hsp70,myl7</p>";
    return;
  }
  const geneList = raw.split(/[,，;；\s]+/).filter(Boolean);
  toolResult.classList.remove("hidden");
  toolResult.innerHTML = "<span class='tool-loading'>ORA 富集分析中…</span>";
  enrichBtn.disabled = true;
  try {
    /* 2026-10-31：本页只做 GO。原来 type=both，结果里还带一整块 KEGG 通路，
       等于在 GO 主题页里塞了「通路」页的功能，用户要求只在各自主题里做各自的事。
       通路请走「通路」页（/api/tools/kegg）。 */
    const res = await apiFetch(
      `/api/tools/enrichment?genes=${encodeURIComponent(geneList.join(","))}&type=go&padj=0.05&top_n=15`
    );
    const data = await parseJsonResponse(res, res.url || "");
    if (!res.ok) throw new Error(data.detail || "富集失败");
    toolResult.innerHTML = renderEnrichResult(data);
  } catch (err) {
    toolResult.textContent = String(err.message);
  } finally {
    enrichBtn.disabled = false;
  }
});

/* ============================ 富集结果渲染 ============================
 * 设计目标（2026-10-10 用户反馈）：
 *  1. 一眼看出「富的是哪一方面」——GO 的 BP/CC/MF 与通路来源库（KEGG/WikiPathways/Reactome）
 *     每条都带方面徽标，顶部还有「本次看的是哪些方面」的分布。
 *  2. 看得清楚——不再把长文本塞进 <pre>，改成结构化卡片 + 高对比度指标标签。
 * 字段来自 /api/tools/enrichment → enrichment_toolkit（后端已补 aspect/source/url）。
 * ==================================================================== */
const EZ_ASPECT = {
  BP: { cls: "bp", text: "BP 生物学过程" },
  CC: { cls: "cc", text: "CC 细胞组分" },
  MF: { cls: "mf", text: "MF 分子功能" },
};
const EZ_SOURCE = {
  KEGG: "kegg",
  WikiPathways: "wiki",
  Reactome: "reactome",
};

function ezNum(v, digits) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return escapeHtml(String(v));
  if (n !== 0 && (Math.abs(n) < 1e-3 || Math.abs(n) >= 1e5)) return n.toExponential(2);
  return n.toFixed(digits ?? 2);
}

function ezAspectBadge(t) {
  const raw = String(t.aspect || "NA").toUpperCase();
  const info = EZ_ASPECT[raw];
  const label = t.aspect_label || (info ? info.text : "方面未标注");
  const cls = info ? info.cls : "na";
  return `<span class="ez-badge ez-${cls}">${escapeHtml(label)}</span>`;
}

function ezSourceBadge(p) {
  const key = String(p.source_code || "").trim();
  const label = p.source_label || "来源未标注";
  const cls = EZ_SOURCE[key] || "na";
  return `<span class="ez-badge ez-src-${cls}">${escapeHtml(label)}</span>`;
}

function ezMetric(k, v) {
  return `<span class="ez-metric"><i>${escapeHtml(k)}</i><b>${v}</b></span>`;
}

function ezScopeCard(go, kegg) {
  const chips = (rows, kind) =>
    (rows || [])
      .map(
        (r) =>
          `<span class="ez-scope-chip ez-${kind}">${escapeHtml(r.label)}<b>${r.count}</b></span>`
      )
      .join("");
  const parts = [];
  if (go && (go.terms || []).length) {
    parts.push(
      `<div class="ez-scope-row"><span class="ez-scope-k">GO 三个方面</span>${chips(
        go.aspect_summary && go.aspect_summary.length
          ? go.aspect_summary
          : [{ label: "方面未标注", count: go.terms.length }],
        "go"
      )}<span class="ez-scope-note">Fisher 精确检验 + BH 校正，q≤${go.padj_cutoff ?? 0.05}</span></div>`
    );
  }
  if (kegg && (kegg.pathways || []).length) {
    parts.push(
      `<div class="ez-scope-row"><span class="ez-scope-k">通路来源库</span>${chips(
        kegg.source_summary && kegg.source_summary.length
          ? kegg.source_summary
          : [{ label: "来源未标注", count: kegg.pathways.length }],
        "pw"
      )}<span class="ez-scope-note">背景基因 ${kegg.universe_size ?? "?"}（GeneRatio = 命中/前景）</span></div>`
    );
  }
  if (!parts.length) return "";
  return `<section class="ez-scope"><h5>这次富集看的是哪些方面</h5>${parts.join("")}</section>`;
}

function ezGoCard(t, i) {
  const name = t.term_name || t.go_term || "(无名称)";
  const gid = t.go_id || "";
  const link = t.url
    ? `<a class="ez-link" href="${escapeHtml(t.url)}" target="_blank" rel="noopener">QuickGO ↗</a>`
    : "";
  const warn = t.name_missing
    ? `<span class="ez-warn">名称未收录，可点 QuickGO 查看</span>`
    : "";
  const genes = (t.genes || [])
    .map((g) => `<span class="ez-gene">${escapeHtml(g)}</span>`)
    .join("");
  return `<li class="ez-item">
    <div class="ez-line1"><span class="ez-idx">${i}</span>${ezAspectBadge(t)}
      <span class="ez-name">${escapeHtml(name)}</span>
      ${gid ? `<span class="ez-gid">${escapeHtml(gid)}</span>` : ""}${link}${warn}</div>
    <div class="ez-line2">
      ${ezMetric("q 值(padj)", t.padj != null ? Number(t.padj).toExponential(2) : "—")}
      ${ezMetric("p 值", t.pvalue != null ? Number(t.pvalue).toExponential(2) : "—")}
      ${ezMetric("GeneRatio", escapeHtml(String(t.gene_ratio ?? "—")))}
      ${ezMetric("BgRatio", escapeHtml(String(t.bg_ratio ?? "—")))}
      ${ezMetric("富集倍数", ezNum(t.fold_enrichment, 1))}
      ${ezMetric("命中/条目", `${t.gene_count ?? "—"}/${t.term_size ?? "—"}`)}
    </div>
    ${genes ? `<div class="ez-line3"><span class="ez-k">命中基因</span>${genes}</div>` : ""}
  </li>`;
}

function ezPathCard(p, i) {
  const link = p.url
    ? `<a class="ez-link" href="${escapeHtml(p.url)}" target="_blank" rel="noopener">${escapeHtml(
        p.source_code || "查看"
      )} ↗</a>`
    : "";
  const genes = (p.overlap_genes || [])
    .map((g) => `<span class="ez-gene">${escapeHtml(g)}</span>`)
    .join("");
  return `<li class="ez-item">
    <div class="ez-line1"><span class="ez-idx">${i}</span>${ezSourceBadge(p)}
      <span class="ez-name">${escapeHtml(p.pathway_name || "（未命名通路）")}</span>
      <span class="ez-gid">${escapeHtml(p.pathway_id || "")}</span>${link}</div>
    <div class="ez-line2">
      ${ezMetric("q 值(padj)", p.padj != null ? Number(p.padj).toExponential(2) : "—")}
      ${ezMetric("p 值", p.pvalue != null ? Number(p.pvalue).toExponential(2) : "—")}
      ${ezMetric("GeneRatio", escapeHtml(String(p.gene_ratio ?? "—")))}
      ${ezMetric("BgRatio", escapeHtml(String(p.bg_ratio ?? "—")))}
      ${ezMetric("富集倍数", ezNum(p.fold_enrichment, 1))}
      ${ezMetric("命中/通路", `${p.gene_count ?? "—"}/${p.pathway_gene_count ?? "—"}`)}
      ${p.category ? ezMetric("KEGG 类目", escapeHtml(String(p.category).slice(0, 24))) : ""}
    </div>
    ${genes ? `<div class="ez-line3"><span class="ez-k">命中基因</span>${genes}</div>` : ""}
  </li>`;
}

function renderEnrichResult(data) {
  const errs = [];
  if (data.go && data.go.success === false && data.go.error) errs.push(`GO: ${data.go.error}`);
  if (data.kegg && data.kegg.success === false && data.kegg.error) errs.push(`KEGG: ${data.kegg.error}`);
  const go = data.go?.terms || [];
  const kegg = data.kegg?.pathways || [];
  if (errs.length && !go.length && !kegg.length) {
    return `<p class="tool-hint warn">${escapeHtml(errs.join("；"))}</p>`;
  }

  const goSig = data.go?.significant_count ?? go.length;
  const goTot = data.go?.total_tested ?? "?";
  const pwSig = data.kegg?.significant_count ?? kegg.length;
  const pwTot = data.kegg?.total_tested ?? "?";
  /* 2026-10-31：结果头里的「通路显著 x/y」只在返回了 KEGG 时显示。
     GO 页现在只请求 type=go，不该再出现一个永远 0/? 的通路计数。 */
  const hasKegg = !!data.kegg;

  let html = `<div class="ez-head">
    <span class="ez-method">ORA（Fisher 精确检验）+ Benjamini–Hochberg 校正</span>
    <span class="ez-count">GO 显著 <b>${goSig}</b>/${goTot}</span>${hasKegg
      ? `\n    <span class="ez-count">通路显著 <b>${pwSig}</b>/${pwTot}</span>`
      : ""}
  </div>`;
  html += ezScopeCard(data.go, data.kegg);

  if (go.length) {
    html += `<h4 class="pub-h4">GO 富集 · 每条已标注方面（BP/CC/MF）</h4>
      <ol class="ez-list">${go.map((t, i) => ezGoCard(t, i + 1)).join("")}</ol>`;
  } else if (data.go && data.go.success) {
    html += `<p class="tool-hint">GO：无显著条目（q≤${data.go.padj_cutoff ?? 0.05}）。可放宽阈值或扩充 GO 注释。</p>`;
  }

  if (kegg.length) {
    html += `<h4 class="pub-h4">通路富集 · 每条已标注来源库</h4>
      <ol class="ez-list">${kegg.map((p, i) => ezPathCard(p, i + 1)).join("")}</ol>`;
  } else if (data.kegg && data.kegg.success) {
    html += `<p class="tool-hint">通路：无显著条目（q≤${data.kegg.padj_cutoff ?? 0.05}）。可到「通路」页按关键词检索。</p>`;
  }

  if (data.summary) {
    html += `<details class="ez-raw"><summary>查看纯文本原始结果</summary>
      <pre class="enrich-summary">${escapeHtml(data.summary)}</pre></details>`;
  }
  if (data.tsv) {
    html += `<button type="button" class="export-tsv-btn" data-tsv="${encodeURIComponent(data.tsv)}">复制 TSV（发表用）</button>`;
  }
  return html || escapeHtml(JSON.stringify(data, null, 2));
}

/* 2026-10-27：剪贴板不可用时的降级。原来降级用 window.prompt 显示 TSV，
   而 prompt 被浏览器拦截时就把内容吞了（用户看到的是「点了没反应」）。
   改用隐藏 textarea + execCommand，全程在页面内完成。 */
function copyTextFallback(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".export-tsv-btn");
  if (!btn) return;
  const tsv = decodeURIComponent(btn.dataset.tsv || "");
  const done = () => { btn.textContent = "已复制 TSV"; };
  const fail = () => window.ZFUi?.toast?.("复制失败，请手动选择表格内容", "err");
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(tsv).then(done).catch(() => {
      copyTextFallback(tsv) ? done() : fail();
    });
  } else {
    copyTextFallback(tsv) ? done() : fail();
  }
});

keggBtn.addEventListener("click", async () => {
  const q = keggInput.value.trim();
  if (!q) return;
  toolResult.classList.remove("hidden");
  toolResult.innerHTML = "<span class='tool-loading'>检索 KEGG 通路…</span>";
  keggBtn.disabled = true;
  try {
    const res = await apiFetch(`/api/tools/kegg?q=${encodeURIComponent(q)}`);
    const data = await parseJsonResponse(res, res.url || "");
    if (!res.ok) throw new Error(data.detail || "检索失败");
    toolResult.innerHTML = renderKeggResult(data);
  } catch (err) {
    toolResult.textContent = String(err.message);
  } finally {
    keggBtn.disabled = false;
  }
});

function renderKeggResult(data) {
  if (!data.success) {
    return `<p class="tool-hint warn">${escapeHtml(data.error || "KEGG 数据不可用")}</p>`;
  }
  const paths = data.pathways || [];
  if (!paths.length) {
    return "<p class=\"tool-hint\">未找到相关通路，可试：p53、Wnt、cell cycle</p>";
  }
  const EZ_SRC = { KEGG: "kegg", WikiPathways: "wiki", Reactome: "reactome" };
  const srcChips = (data.source_summary || [])
    .map((s) => `<span class="ez-scope-chip ez-pw">${escapeHtml(s.label)}<b>${s.count}</b></span>`)
    .join("");
  let html = `<p class="tool-summary">匹配 ${paths.length} 条通路（最多 15 条）</p>`;
  if (srcChips) {
    html += `<div class="ez-scope"><h5>命中的通路来自哪些库</h5><div class="ez-scope-row">${srcChips}</div></div>`;
  }
  html += `<table class="pub-table"><thead><tr>
    <th>来源</th><th>ID</th><th>通路</th><th>类别</th><th>基因（节选）</th></tr></thead><tbody>`;
  paths.forEach((p) => {
    const pid = p.pathway_id || "";
    // 优先用后端按来源拼好的 url（KEGG→kegg.jp、Reactome→reactome.org、WikiPathways→wikipathways.org）
    const url = p.url || (pid ? `https://www.genome.jp/pathway/${pid}` : null);
    const idCell = url ? extLink(url, pid) : escapeHtml(pid);
    const srcCls = EZ_SRC[p.source_code] || "na";
    const srcCell = `<span class="ez-badge ez-src-${srcCls}">${escapeHtml(p.source_label || "来源未标注")}</span>`;
    const genes = (p.genes_in_pathway || []).slice(0, 10).join(", ");
    const more = (p.genes_in_pathway || []).length > 10 ? "…" : "";
    html += `<tr><td>${srcCell}</td><td>${idCell}</td><td>${escapeHtml(p.pathway_name || "")}</td>
      <td>${escapeHtml(p.category || "")}</td><td title="${escapeHtml((p.genes_in_pathway || []).join(", "))}">${escapeHtml(genes)}${more}</td></tr>`;
  });
  html += "</tbody></table>";
  // 2026-10-31：删掉「在『富集』页输入基因列表可做 ORA 统计显著性」这类跨页指引 + 页面口径说明，
  // 各功能页一律不再挂这类说明文字（用户要求全部去掉）。
  return html;
}

uploadBtn.addEventListener("click", async () => {
  const file = uploadInput.files?.[0];
  if (sessionStorage.getItem(HUB_MODE_KEY) !== "user") {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = `<p class="tool-hint warn">入库需先登录账号（保护专属知识库）。请点右上角登录/注册。</p>`;
    return;
  }
  const clientErr = validateUploadFileClient(file);
  if (clientErr) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = `<p class="tool-hint warn">${escapeHtml(clientErr)}</p>`;
    return;
  }
  const deepErr = await validateUploadFileDeep(file);
  if (deepErr) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = `<p class="tool-hint warn">${escapeHtml(deepErr)}</p>`;
    return;
  }
  if (authRole === "readonly") {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = `<p class="tool-hint warn">只读令牌无法上传文件</p>`;
    return;
  }
  toolResult.classList.remove("hidden");
  toolResult.textContent = "上传并重建索引中...";
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await apiFetch("/api/knowledge/upload", { method: "POST", body: form });
    const data = await parseJsonResponse(res, res.url || "");
    if (!res.ok) throw new Error(data.detail || "上传失败");
    toolResult.innerHTML = `<p class="tool-summary">✓ ${escapeHtml(data.message || "入库成功")}</p>
      <p class="tool-hint">文件：${escapeHtml(data.saved || file.name)}</p>`;
    uploadInput.value = "";
    loadHealth();
  } catch (err) {
    toolResult.textContent = String(err.message);
  }
});

toolLookupBtn.addEventListener("click", () => toolLookupGene());
toolGeneInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); toolLookupGene(); }
});
toolSearchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); toolSearchBtn.click(); }
});
keggInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); keggBtn.click(); }
});
enrichInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); enrichBtn.click(); }
});

async function toolLookupGene() {
  const name = toolGeneInput.value.trim();
  if (!name) return;
  const prefs = snapshotAnswerPrefs();
  /* 2026-10-31 边界重划：本页的定位是「给基因 → 坐标 / 基因体」，只读结构化注释。
     原来这里先看聊天范围，scope 命中 gene_db / hub 就去跑一轮 scoped chat：
     查个坐标要等十几秒，回来还是自然语言回答，与「位置查找」这个标题不符。
     现在固定走 /api/gene/<name>（毫秒级、字段确定）；需要 LLM 解读的去聊天页。 */
  toolResult.classList.remove("hidden");
  toolResult.textContent = "查询中...";
  try {
    const res = await apiFetch(`/api/gene/${encodeURIComponent(name)}`);
    const data = await parseJsonResponse(res, res.url || "");
    if (!res.ok) throw new Error(data.detail || "未找到");
    toolResult.innerHTML = renderToolGeneSummary(data);
    renderGeneCard(data);
    detailEmpty.classList.add("hidden");
    detailContent.classList.remove("hidden");
    geneBlock.classList.remove("hidden");
    detailTaskType.textContent = `gene · ${prefs.depthLabel} · ${prefs.scopeLabel}`;
  } catch (err) {
    toolResult.textContent = String(err.message);
  }
}

toolSearchBtn.addEventListener("click", async () => {
  const q = toolSearchInput.value.trim();
  if (!q) return;
  toolResult.classList.remove("hidden");
  toolResult.innerHTML = "<span class='tool-loading'>搜索中…</span>";
  try {
    const res = await apiFetch(`/api/genes/search?q=${encodeURIComponent(q)}`);
    const data = await parseJsonResponse(res, res.url || "");
    const rows = data.results || [];
    if (!rows.length) {
      toolResult.innerHTML = "<p class='tool-hint'>无匹配基因</p>";
      return;
    }
    let html = `<p class="tool-summary">找到 ${rows.length} 条（点击详情查看完整卡片）</p>`;
    html += `<table class="pub-table"><thead><tr><th>基因</th><th>Chr</th><th>功能/GO</th><th></th></tr></thead><tbody>`;
    rows.forEach((g) => {
      const summary = geneSearchSummary(g);
      html += `<tr><td><strong>${escapeHtml(g.gene_name)}</strong> ${escapeHtml(g.gene_symbol || "")}</td>
        <td>${escapeHtml(g.chromosome || "-")}</td>
        <td>${escapeHtml(summary)}</td>
        <td><button type="button" class="gene-pick-btn" data-name="${escapeHtml(g.gene_name)}">详情</button></td></tr>`;
    });
    html += "</tbody></table>";
    toolResult.innerHTML = html;
    toolResult.querySelectorAll(".gene-pick-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        toolGeneInput.value = btn.dataset.name;
        toolLookupBtn.click();
      });
    });
  } catch (err) {
    toolResult.textContent = String(err.message);
  }
});

idResolveBtn.addEventListener("click", () => resolveGeneId());
idResolveInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); resolveGeneId(); }
});
idBatchBtn?.addEventListener("click", () => resolveGeneIdBatch());

function extLink(url, label) {
  if (!url) return escapeHtml(label || "");
  return `<a class="ext-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`;
}

function ensemblIdDisplay(v) {
  const base = v.id_base || v.ensembl_id || "";
  if (v.version_num) return `${base}.${v.version_num}`;
  return v.ensembl_id || base || "?";
}

/** 原名/原版本 vs 标准名/标准版本（与 gene_formatter.collect_gene_unification_groups 对齐） */
function collectGeneUnificationGroups(data) {
  const c = data.canonical || {};
  const standard = data.standard || {};
  const query = String(data.query || "").trim();
  const mapped = Boolean(standard.mapped && String(standard.gene_symbol || "").trim());
  const stdName = mapped ? String(standard.gene_symbol || "").trim() : "";
  const stdLower = stdName.toLowerCase();
  const origNames = [];
  const seen = new Set();

  const addName = (name, note = "") => {
    const n = String(name || "").trim();
    if (!n || n === "-") return;
    const key = n.toLowerCase();
    if ((stdLower && key === stdLower) || seen.has(key)) return;
    seen.add(key);
    origNames.push({ name: n, note });
  };

  if (query) addName(query, "查询输入");
  addName(c.gene_name, "GRCz11 / Ensembl");
  addName(c.gene_symbol, "gene_basic.gene_symbol");
  (data.aliases || []).forEach((a) => {
    addName(a.alias, a.alias_type || a.source || "");
  });

  const stdNames = [];
  const role = String(standard.role || "").trim();
  if (mapped && stdName) {
    stdNames.push({
      name: stdName,
      role: role || `RefSeq ${standard.refseq || "GCF_049306965.1"} 标准名`,
    });
  } else {
    const fallback = String(standard.gene_symbol || c.gene_name || query || "").trim();
    stdNames.push({
      name: fallback || "（无）",
      role: role || "本条 RefSeq GCF_049306965.1 无唯一注释，沿用本地主名",
    });
  }

  const stdVersions = [];
  if (mapped) {
    stdVersions.push({
      id: standard.refseq || "GCF_049306965.1",
      release: standard.assembly || "GRCz12tu",
      source: "refseq",
    });
    if (standard.ncbi_gene_id) {
      stdVersions.push({ id: `NCBI Gene ${standard.ncbi_gene_id}`, release: null, source: "ncbi_gene" });
    }
  }

  const origVersions = [];
  const seenVer = new Set();
  (data.ensembl_versions || []).forEach((v) => {
    const id = ensemblIdDisplay(v);
    if (!id || seenVer.has(id)) return;
    seenVer.add(id);
    origVersions.push({ id, release: v.ensembl_release, source: v.source || "ensembl" });
  });
  if (c.ensembl_id && !seenVer.has(c.ensembl_id)) {
    origVersions.push({ id: c.ensembl_id, release: null, source: "gene_basic" });
  }

  return { origNames, stdNames, origVersions, stdVersions };
}

function pubmedLink(pmid, title) {
  const t = title ? String(title).slice(0, 60) : `PMID ${pmid}`;
  return extLink(`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`, t);
}

function splitIdLines(text) {
  return text.split(/[\n,;，；、]+/).map((s) => s.trim()).filter(Boolean);
}

const ID_CONV_VERSIONS = [
  { key: "zfin", label: "ZFIN" },
  { key: "ncbi", label: "NCBI Gene" },
  { key: "ensembl", label: "Ensembl" },
  { key: "GRCz12tu", label: "RefSeq 符号" },
  { key: "uniprot", label: "UniProt" },
  { key: "GRCz11", label: "GRCz11 符号" },
];

let lastIdConvertData = null;
let idConvertMode = "xref";

/** 跨组装可选目标（UI）：不含 GRCz13ab；12tu／12ab 可作目标不作来源。 */
const ID_ASM_TARGETS = [
  "GRCz12ab", "GRCz12tu", "GRCz11", "GRCz10",
  "Zv9", "Zv8", "Zv7", "Zv6", "Zv5", "Zv4", "Zv3",
];

function syncIdTargetRadiosFromUi() {
  let key = "GRCz12tu";
  if (idConvertMode === "assembly") {
    key = (document.getElementById("idTargetAsm")?.value || "GRCz12tu").trim();
    if (!ID_ASM_TARGETS.includes(key)) key = "GRCz12tu";
  } else {
    key = (document.getElementById("idTargetDb")?.value || "zfin").trim();
  }
  const radio = document.querySelector(`input[name="idTargetVersion"][value="${key}"]`);
  if (radio) radio.checked = true;
  else if (idConvertMode !== "assembly") {
    const fallback = document.querySelector('input[name="idTargetVersion"][value="GRCz12tu"]');
    if (fallback) fallback.checked = true;
  }
}

function getIdTargetVersion() {
  if (idConvertMode === "assembly") {
    const asm = (document.getElementById("idTargetAsm")?.value || "GRCz12tu").trim();
    return ID_ASM_TARGETS.includes(asm) ? asm : "GRCz12tu";
  }
  syncIdTargetRadiosFromUi();
  const el = document.querySelector('input[name="idTargetVersion"]:checked');
  return (el && el.value) || "GRCz12tu";
}

function bindIdModeTabs() {
  document.querySelectorAll(".id-mode-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      idConvertMode = btn.getAttribute("data-id-mode") || "xref";
      document.querySelectorAll(".id-mode-tab").forEach((b) => {
        const on = b === btn;
        b.classList.toggle("active", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      document.getElementById("idModeXref")?.classList.toggle("hidden", idConvertMode !== "xref");
      document.getElementById("idModeAssembly")?.classList.toggle("hidden", idConvertMode !== "assembly");
      syncIdTargetRadiosFromUi();
      if (lastIdConvertData) paintIdConvertResult(lastIdConvertData);
    });
  });
  document.getElementById("idTargetDb")?.addEventListener("change", () => {
    syncIdTargetRadiosFromUi();
    if (lastIdConvertData) paintIdConvertResult(lastIdConvertData);
  });
  document.getElementById("idTargetAsm")?.addEventListener("change", () => {
    syncIdTargetRadiosFromUi();
    if (lastIdConvertData) paintIdConvertResult(lastIdConvertData);
  });
}
bindIdModeTabs();

function pickIdXref(r, dbNames) {
  const names = Array.isArray(dbNames) ? dbNames : [dbNames];
  for (const name of names) {
    const hit = (r.xrefs || []).find((x) => String(x.db_name || "") === name && x.db_id);
    if (hit) return String(hit.db_id);
  }
  return "";
}

function pickIdVersionValue(r, versionKey) {
  const c = r.canonical || {};
  const s = r.standard || {};
  if (versionKey === "GRCz12tu") {
    return String(s.gene_symbol || "").trim()
      || (s.mapped === false ? "" : String(c.gene_name || "").trim());
  }
  if (versionKey === "GRCz11") return String(c.gene_name || "").trim();
  if (versionKey === "ensembl") return String(c.ensembl_id || pickIdXref(r, "ensembl") || "").trim();
  if (versionKey === "zfin") return String(c.zfin_id || pickIdXref(r, "zfin") || "").trim();
  if (versionKey === "ncbi") {
    return String(s.ncbi_gene_id || pickIdXref(r, ["ncbi_gene", "ncbi_gene_grcz12tu"]) || "").trim();
  }
  if (versionKey === "uniprot") {
    const five = r.five_db || {};
    return String(
      five.uniprot
      || c.uniprot_id
      || pickIdXref(r, ["uniprot", "UniProt", "uniprotkb"])
      || ""
    ).trim().split(",")[0].trim();
  }
  return "";
}

function pickIdFunctionLabel(r) {
  const aliases = r.aliases || [];
  const full = aliases.find((a) => a.alias_type === "full_name" && a.alias);
  if (full) return String(full.alias);
  const other = aliases.find((a) => a.alias_type === "other" && a.alias && /protein|antigen|tumor|receptor|kinase/i.test(a.alias));
  if (other) return String(other.alias);
  const syn = aliases.find((a) => a.alias_type === "gene_symbol" || a.alias_type === "gene_basic.gene_symbol");
  if (syn && syn.alias) return String(syn.alias);
  return "";
}

function buildIdConvertTsv(data, targetKey) {
  const headers = ["Input ID", ...ID_CONV_VERSIONS.map((v) => v.label), "Function", "status", "target_version"];
  const lines = [headers.join("\t")];
  (data.results || []).forEach((r) => {
    const vals = ID_CONV_VERSIONS.map((v) => pickIdVersionValue(r, v.key));
    lines.push([
      r.query || "",
      ...vals,
      pickIdFunctionLabel(r),
      r.status || (r.resolved ? "unique" : "not_found"),
      targetKey,
    ].join("\t"));
  });
  return lines.join("\n");
}

function renderBatchIdTable(data, targetKey) {
  const target = targetKey || getIdTargetVersion();
  const count = (data.results || []).length;
  const hit = (data.results || []).filter((r) => r.resolved || r.status === "unique").length;
  let html = `<div class="id-conv-result-wrap">`;
  if (idConvertMode === "assembly" && target !== "GRCz11" && target !== "GRCz12tu") {
    html += `<p class="id-conv-summary id-conv-status-bad">跨组装对照表目前仅有 GRCz11 ↔ GRCz12tu（gene_assembly_xref）。目标 <strong>${escapeHtml(
      target
    )}</strong>（含 GRCz13ab／GRCz12ab／GRCz10／Zv 等）缺对照表 → 明确失败，不瞎填符号／ID。</p>`;
  }
  html += `<p class="id-conv-summary">已转换 ${count} 条 · 命中 ${hit} 条 · 主输出版本 <strong>${escapeHtml(
    ID_CONV_VERSIONS.find((v) => v.key === target)?.label || target
  )}</strong>（五库：ZFIN／NCBI Gene／Ensembl／RefSeq 符号／UniProt）</p>`;
  html += `<table class="id-conv-table"><thead><tr><th>输入 ID</th>`;
  ID_CONV_VERSIONS.forEach((v) => {
    const cls = v.key === target ? ' class="id-conv-col-target"' : "";
    html += `<th${cls}>${escapeHtml(v.label)}${v.key === target ? " ★" : ""}</th>`;
  });
  html += `<th>功能</th><th>状态</th></tr></thead><tbody>`;
  (data.results || []).forEach((r) => {
    const status = r.status || (r.resolved ? "unique" : "not_found");
    const statusCls = status === "unique" || r.resolved ? "id-conv-status-ok" : "id-conv-status-bad";
    html += `<tr><td>${escapeHtml(r.query || "")}</td>`;
    ID_CONV_VERSIONS.forEach((v) => {
      const val = pickIdVersionValue(r, v.key);
      const cls = [v.key === target ? "id-conv-col-target" : "", val ? "" : "id-conv-miss"].filter(Boolean).join(" ");
      html += `<td${cls ? ` class="${cls}"` : ""}>${escapeHtml(val || "—")}</td>`;
    });
    html += `<td>${escapeHtml(pickIdFunctionLabel(r) || "—")}</td>`;
    html += `<td class="${statusCls}">${escapeHtml(status)}</td></tr>`;
  });
  html += `</tbody></table>`;
  const tsv = buildIdConvertTsv(data, target);
  html += `<div class="id-conv-actions"><button type="button" class="export-tsv-btn" data-tsv="${encodeURIComponent(tsv)}">复制 TSV</button>`;
  if (data._fallback) {
    html += `<span class="tool-hint">提示：Web 服务版本较旧，已用逐条解析。</span>`;
  }
  html += `</div></div>`;
  return html;
}

async function fetchBatchIdResults(text) {
  let res = await apiFetch("/api/tools/id-convert/text", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (res.ok) return res.json();
  if (res.status === 404 || res.status === 405) {
    const ids = splitIdLines(text).slice(0, 200);
    const qs = encodeURIComponent(ids.join(","));
    res = await apiFetch(`/api/tools/id-convert/batch?ids=${qs}`);
    if (res.ok) {
      const data = await parseJsonResponse(res, res.url || "");
      return { ...data, _fallback: true };
    }
    const results = await Promise.all(
      ids.map(async (q) => {
        try {
          const r = await apiFetch(`/api/tools/gene-resolve?q=${encodeURIComponent(q)}`);
          if (!r.ok) return { query: q, resolved: false, canonical: null };
          return r.json();
        } catch {
          return { query: q, resolved: false, canonical: null };
        }
      })
    );
    return { results, count: results.length, _fallback: true };
  }
  const data = await res.json().catch(() => ({}));
  throw new Error(data.detail || "批量解析失败");
}

function syncIdResolveInputFromBatch() {
  const text = idBatchInput?.value?.trim() || "";
  const first = splitIdLines(text)[0] || "";
  if (idResolveInput) idResolveInput.value = first;
}

function paintIdConvertResult(data) {
  lastIdConvertData = data;
  toolResult.innerHTML = renderBatchIdTable(data, getIdTargetVersion());
}

async function resolveGeneIdBatch() {
  const text = idBatchInput?.value?.trim();
  if (!text) return;
  syncIdResolveInputFromBatch();
  toolResult.classList.remove("hidden");
  idResolvePanel.classList.add("hidden");
  toolResult.innerHTML = "<span class='tool-loading'>转换 ID 中…</span>";
  try {
    const data = await fetchBatchIdResults(text);
    paintIdConvertResult(data);
  } catch (err) {
    lastIdConvertData = null;
    toolResult.textContent = String(err.message);
  }
}

document.querySelectorAll('input[name="idTargetVersion"]').forEach((el) => {
  el.addEventListener("change", () => {
    if (lastIdConvertData) paintIdConvertResult(lastIdConvertData);
  });
});

function splitIdTokens(raw) {

  return raw.split(/[、,;，；\n\r\t]+|\s+/).map((s) => s.trim()).filter(Boolean);
}

async function resolveGeneId() {
  const q = idResolveInput.value.trim();
  if (!q) return;
  const tokens = splitIdTokens(q);
  // 多个 ID 自动走批量解析（避免整串当作一个无效符号）
  if (tokens.length > 1) {
    if (idBatchInput) idBatchInput.value = tokens.join("\n");
    return resolveGeneIdBatch();
  }
  const prefs = snapshotAnswerPrefs();
  if (prefs.scope === "id_map" || prefs.scope === "hub") {
    toolResult.classList.remove("hidden");
    idResolvePanel.classList.add("hidden");
    toolResult.innerHTML = "<span class='tool-loading'>按当前使用策略做 ID 映射…</span>";
    try {
      const data = await runScopedChatRequest(tokens[0] || q, {
        ...prefs,
        scope: "id_map",
        scopeLabel: SCOPE_LABELS.id_map,
        useHistory: false,   // 工具页查询不污染对话上文
      });
      publishScopedResult(tokens[0] || q, data, {
        ...prefs,
        scope: "id_map",
        scopeLabel: SCOPE_LABELS.id_map,
      });
      if (data.id_mapping) {
        renderIdResolve(data.id_mapping);
        idResolvePanel.classList.remove("hidden");
      }
      toolResult.innerHTML = data.id_mapping
        ? renderIdResolveSummaryHtml(data.id_mapping)
        : `<pre class="enrich-summary">${escapeHtml((data.answer || "").slice(0, 1200))}</pre>`;
    } catch (err) {
      toolResult.textContent = String(err.message || err);
    }
    return;
  }
  toolResult.classList.add("hidden");
  idResolvePanel.classList.remove("hidden");
  idResolvePanel.querySelectorAll(".id-canonical, .id-chain, .id-grid, .id-unify-split").forEach((el) => {
    el.style.opacity = "0.5";
  });
  try {
    const res = await apiFetch(`/api/tools/gene-resolve?q=${encodeURIComponent(tokens[0] || q)}`);
    const data = await parseJsonResponse(res, res.url || "");
    if (!res.ok) throw new Error(data.detail || "解析失败");
    renderIdResolve(data);
    detailTaskType.textContent = `id_map · ${prefs.depthLabel} · ${prefs.scopeLabel}`;
    detailEmpty.classList.add("hidden");
    detailContent.classList.remove("hidden");
  } catch (err) {
    toolResult.classList.remove("hidden");
    toolResult.textContent = String(err.message);
  } finally {
    idResolvePanel.querySelectorAll(".id-canonical, .id-chain, .id-grid, .id-unify-split").forEach((el) => {
      el.style.opacity = "";
    });
  }
}

function renderIdResolve(data) {
  const ref = data.reference || {};
  document.getElementById("idRefBadge").textContent =
    `标准名 RefSeq ${ref.ncbi_reference_refseq || "GCF_049306965.1"}（${ref.ncbi_reference_assembly || "GRCz12tu"}）· 本地库 ${ref.assembly || "GRCz11"}`
    + (data.status ? ` · ${data.status}${data.match_kind ? `/${data.match_kind}` : ""}` : "");

  const chainEl = document.getElementById("idChain");
  const chain = data.id_chain || [];
  chainEl.innerHTML = chain.map((node, i) => {
    const arrow = i > 0 ? '<span class="id-chain-arrow">↔</span>' : "";
    const idHtml = node.url ? extLink(node.url, node.id || "") : escapeHtml(node.id || "");
    return `${arrow}<div class="id-chain-node"><strong>${escapeHtml(node.label || node.type)}</strong>${idHtml}</div>`;
  }).join("") || '<span class="id-chain-node">未建立 ID 链</span>';

  const candEl = document.getElementById("idCandidates");
  const cands = data.candidates || [];
  candEl.innerHTML = "";
  if (cands.length > 1 || data.status === "ambiguous") {
    candEl.classList.remove("hidden");
    const label = document.createElement("label");
    label.textContent = data.status === "ambiguous"
      ? "歧义候选（点击选定；不会自动消歧）"
      : "多候选基因（点击切换）";
    candEl.appendChild(label);
    cands.slice(0, 8).forEach((c) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "id-cand-btn";
      btn.innerHTML = `${escapeHtml(c.gene_name || "?")} <small>${escapeHtml(c.ensembl_id || "")}</small>`;
      btn.addEventListener("click", () => {
        idResolveInput.value = c.gene_name || "";
        resolveGeneId();
      });
      candEl.appendChild(btn);
    });
  } else {
    candEl.classList.add("hidden");
  }

  const disEl = document.getElementById("idDisambig");
  if (data.disambiguation || data.status === "ambiguous") {
    disEl.textContent = data.disambiguation
      || `歧义（${data.gene_id_count || (data.candidates || []).length} 个候选）：请点选，不会自动选定。`;
    disEl.classList.remove("hidden");
  } else {
    disEl.classList.add("hidden");
  }

  const groups = collectGeneUnificationGroups(data);
  const origUl = document.getElementById("idOrigList");
  const origItems = [];
  groups.origNames.forEach((item, i) => {
    const note = item.note ? ` <span style="color:var(--muted)">(${escapeHtml(item.note)})</span>` : "";
    origItems.push(`<li>${i + 1}. ${escapeHtml(item.name)}${note}</li>`);
  });
  groups.origVersions.forEach((item, i) => {
    const rel = item.release ? ` <span style="color:var(--muted)">r${escapeHtml(String(item.release))}</span>` : "";
    origItems.push(`<li>${groups.origNames.length + i + 1}. ${escapeHtml(item.id)}${rel}</li>`);
  });
  origUl.innerHTML = origItems.join("") || "<li>无（查询即标准名）</li>";

  const c = data.canonical || {};
  const standard = data.standard || {};
  const stdShow = String(standard.gene_symbol || c.gene_name || "").trim();
  const canonEl = document.getElementById("idCanonical");
  if ((data.resolved || data.status === "unique") && c.gene_name) {
    const role = standard.role || (standard.mapped ? "RefSeq GCF_049306965.1 标准名" : "本条 RefSeq GCF_049306965.1 无唯一注释，沿用本地主名");
    canonEl.innerHTML = `
      <div class="name">${escapeHtml(stdShow || "（无）")}</div>
      <div class="meta">${escapeHtml(role)}${standard.refseq ? ` · ${escapeHtml(standard.refseq)}` : ""}</div>
      <div class="meta">本地 ${escapeHtml(c.gene_name || "-")} · Chr ${escapeHtml((standard.chromosome || c.chromosome) || "-")} · ${escapeHtml(c.ensembl_id || "-")} · ${escapeHtml(c.zfin_id || "-")}</div>
      <button type="button" class="gene-pick-btn id-open-gene" data-name="${escapeHtml(c.gene_name)}">在基因卡片中打开</button>`;
    canonEl.querySelector(".id-open-gene")?.addEventListener("click", () => {
      location.hash = "#/gene/" + encodeURIComponent(c.gene_name);
    });
  } else {
    canonEl.innerHTML = `<div class="name">未解析</div><div class="meta">${escapeHtml((data.notes || []).join("；") || "本地库无匹配")}</div>`;
  }

  const stdEnsUl = document.getElementById("idStdEnsList");
  stdEnsUl.innerHTML = groups.stdVersions.slice(0, 5).map((item) => {
    const rel = item.release ? ` <span style="color:var(--muted)">${escapeHtml(String(item.release))}</span>` : "";
    return `<li>${escapeHtml(item.id)}${rel}</li>`;
  }).join("") || `<li>本条无 RefSeq ${escapeHtml((data.standard && data.standard.refseq) || "GCF_049306965.1")} 组装对照</li>`;

  const xrefUl = document.getElementById("idXrefList");
  xrefUl.innerHTML = (data.xrefs || []).slice(0, 12).map((x) => {
    const url = x.url || (x.db_name === "ncbi_gene" ? `https://www.ncbi.nlm.nih.gov/gene/${x.db_id}` :
      x.db_name === "ensembl" ? `https://www.ensembl.org/Danio_rerio/Gene/Summary?g=${String(x.db_id).split(".")[0]}` :
      x.db_name === "zfin" ? `https://zfin.org/${x.db_id}` :
      x.db_name === "uniprot" ? `https://www.uniprot.org/uniprotkb/${x.db_id}/entry` : null);
    const idPart = url ? extLink(url, x.db_id) : escapeHtml(x.db_id);
    return `<li>${escapeHtml(x.db_name)}: ${idPart}</li>`;
  }).join("") || "<li>无</li>";

  const tissue = data.tissue_expression || [];
  const tissueBlock = document.getElementById("idTissueBlock");
  const barsEl = document.getElementById("idTissueBars");
  if (tissue.length) {
    tissueBlock.classList.remove("hidden");
    const max = Math.max(...tissue.map((t) => Number(t.expression_level) || 0), 1);
    barsEl.innerHTML = tissue.slice(0, 10).map((t) => {
      const val = Number(t.expression_level) || 0;
      const pct = Math.round((val / max) * 100);
      return `<div class="tissue-bar-row">
        <span title="${escapeHtml(t.tissue)}">${escapeHtml(String(t.tissue).slice(0, 14))}</span>
        <div class="tissue-bar-track"><div class="tissue-bar-fill" style="width:${pct}%"></div></div>
        <span>${val.toFixed(0)}</span>
      </div>`;
    }).join("");
  } else {
    tissueBlock.classList.add("hidden");
    barsEl.innerHTML = "";
  }

  document.getElementById("idNotes").innerHTML = (data.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  idResolvePanel.querySelectorAll(".id-canonical, .id-chain, .id-grid, .id-unify-split").forEach((el) => {
    el.style.opacity = "1";
  });
}

function renderIdResolveSummaryHtml(data) {
  const c = data.canonical || {};
  if (!data.resolved || !c.gene_name) {
    return `<p class="tool-hint">${escapeHtml((data.notes || []).join("；") || "未解析")}</p>`;
  }
  const groups = collectGeneUnificationGroups(data);
  const origParts = [];
  groups.origNames.forEach((item) => origParts.push(escapeHtml(item.name)));
  groups.origVersions.forEach((item) => origParts.push(escapeHtml(item.id)));
  const stdName = (data.standard && data.standard.gene_symbol)
    ? data.standard.gene_symbol
    : ((data.canonical && data.canonical.gene_name) || "（无）");
  const stdVer = groups.stdVersions[0]?.id || (data.standard && data.standard.refseq) || "GCF_049306965.1";
  const xrefs = (data.xrefs || []).slice(0, 4).map((x) => {
    const url = x.url || (x.db_name === "ncbi_gene" ? `https://www.ncbi.nlm.nih.gov/gene/${x.db_id}` :
      x.db_name === "ensembl" ? `https://www.ensembl.org/Danio_rerio/Gene/Summary?g=${String(x.db_id).split(".")[0]}` :
      x.db_name === "zfin" ? `https://zfin.org/${x.db_id}` :
      x.db_name === "uniprot" ? `https://www.uniprot.org/uniprotkb/${x.db_id}/entry` : null);
    const idPart = url ? extLink(url, x.db_id) : escapeHtml(x.db_id);
    return `${escapeHtml(x.db_name)}: ${idPart}`;
  }).join(" · ");
  return `<div class="tool-gene-summary">
    ${origParts.length ? `<p class="muted"><strong>原名/原版本：</strong>${origParts.map((n, i) => `${i + 1}. ${n}`).join("； ")}</p>` : ""}
    <p><strong>标准名/标准版本：</strong>${escapeHtml(stdName)} · ${escapeHtml(stdVer)}</p>
    <div class="meta">Chr ${escapeHtml(c.chromosome || "-")} · ${escapeHtml(c.ensembl_id || "-")} · ${escapeHtml(c.zfin_id || "-")}</div>
    ${xrefs ? `<p class="muted">${xrefs}</p>` : ""}
    <p class="muted">完整原名/标准名对照请切到「ID解析」标签</p>
  </div>`;
}

function lastUserQuestionFor(wrap) {
  let q = "";
  for (const el of messagesEl.children) {
    if (el === wrap) break;
    if (el.classList.contains("user")) {
      q = (el.querySelector(".bubble")?.textContent || "").trim();
    }
  }
  return q;
}

/** 升级：给 AI 消息追加「复制 / 重新生成」操作按钮（常显；已存在则不重复加） */
function copyTextFallback(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
  ta.remove();
  return ok;
}

function attachMsgActions(wrap, text) {
  if (!wrap || wrap.querySelector(".msg-actions")) return;
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const mkBtn = (label) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "msg-action-btn"; b.textContent = label;
    return b;
  };
  const copyBtn = mkBtn("复制");
  copyBtn.addEventListener("click", async () => {
    const payload = String(text || "").trim();
    if (!payload) return;
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(payload);
        ok = true;
      }
    } catch (_) { ok = false; }
    if (!ok) ok = copyTextFallback(payload);
    copyBtn.textContent = ok ? "已复制" : "复制失败";
    setTimeout(() => { copyBtn.textContent = "复制"; }, 1200);
  });
  const regenBtn = mkBtn("重新生成");
  regenBtn.addEventListener("click", () => {
    const q = lastUserQuestionFor(wrap);
    if (!q) return;
    questionInput.value = q;
    questionInput.focus();
    chatForm.requestSubmit();
  });
  actions.appendChild(copyBtn);
  actions.appendChild(regenBtn);
  wrap.querySelector(".bubble").appendChild(actions);
}

/* ── 回答溯源徽标：告诉用户这条回答用了哪些库、点得进去 ──────────────── */
/* 规则：只有拿到明确 ID 才生成可点记录链接；只提到库名时给该库首页链接。
   注意不要用宽泛正则（例如 \d{7,9} 会命中 GO:0006915，gene/ 会命中站内 #/gene/）。 */
const ANSWER_SOURCE_RULES = [
  { db: "ensembl", re: /ensembl\.org\/Danio_rerio\/[^\s)"']*?g=(ENSDARG\d{11})/i },
  { db: "ensembl", re: /(ENSDARG\d{11})/ },
  { db: "zfin", re: /(ZDB-GENE-\d{6}-\d+)/ },
  { db: "ncbi_gene", re: /ncbi\.nlm\.nih\.gov\/gene\/(\d{3,9})/i },
  { db: "uniprot", re: /uniprotkb\/([A-Z0-9]{6,10})/i },
  { db: "bgee", re: /bgee\.org\/gene\/(ENSDARG\d{11})/i },
  { db: "atlas", re: /gxa\/genes\/(ENSDARG\d{11})/i },
  { db: "quickgo", re: /(GO:\d{7})/ },
  { db: "kegg", re: /(?:kegg\.jp\/(?:entry|pathway)\/)?(dre\d{5})/i },
  { db: "wikipathways", re: /wikipathways\.org\/pathways\/(WP\d+)/i },
  { db: "pubmed", re: /pubmed\.ncbi\.nlm\.nih\.gov\/(\d{7,9})/i },
  { db: "pubmed", re: /PMID[:\s#]*(\d{7,9})/i }
];

/* 只提到库名（无 ID）时给首页链接：库名 → 首页 */
const ANSWER_SOURCE_MENTIONS = [
  { db: "ensembl", re: /Ensembl/i, home: "https://www.ensembl.org/Danio_rerio/" },
  { db: "zfin", re: /ZFIN/i, home: "https://zfin.org/" },
  { db: "ncbi_gene", re: /NCBI\s*Gene|Entrez/i, home: "https://www.ncbi.nlm.nih.gov/gene/" },
  { db: "uniprot", re: /UniProt/i, home: "https://www.uniprot.org/" },
  { db: "bgee", re: /Bgee/i, home: "https://www.bgee.org/" },
  { db: "atlas", re: /Expression\s*Atlas/i, home: "https://www.ebi.ac.uk/gxa/home" },
  { db: "quickgo", re: /QuickGO/i, home: "https://www.ebi.ac.uk/QuickGO/" },
  { db: "kegg", re: /KEGG/i, home: "https://www.kegg.jp/" },
  { db: "wikipathways", re: /WikiPathways/i, home: "https://www.wikipathways.org/" },
  { db: "pubmed", re: /PubMed|PMID/i, home: "https://pubmed.ncbi.nlm.nih.gov/" }
];

function answerSourceBadges(text) {
  if (!text || !window.ZFBadges) return "";
  const raw = String(text);
  const found = [];
  ANSWER_SOURCE_RULES.forEach((r) => {
    const m = raw.match(r.re);
    const id = m && m[1] ? m[1] : null;
    if (!id) return;
    if (found.some((f) => f.db === r.db && f.id === id)) return;
    found.push({ db: r.db, id });
  });
  ANSWER_SOURCE_MENTIONS.forEach((r) => {
    if (found.some((f) => f.db === r.db)) return;
    if (r.re.test(raw)) found.push({ db: r.db, url: r.home });
  });
  if (!found.length && /本地|镜像库|全文库/.test(raw)) found.push({ db: "local" });
  if (!found.length) return "";
  return `<div class="src-row prov-answer-badges" data-i18n-live="1"><span class="metric-unit">本条信息来自</span>${window.ZFBadges.html(found)}</div>`;
}

function appendBadgeRow(bubbleEl, text) {
  if (!bubbleEl) return;
  const old = bubbleEl.querySelector(".prov-answer-badges");
  if (old) old.remove();
  const html = answerSourceBadges(text);
  if (html) bubbleEl.insertAdjacentHTML("beforeend", html);
}

/* ── 工具结果卡溯源：结果来自哪个库 + 这一页按什么口径算的 ─────────────
   来源列表直接复用 provenance.js 的注册表（单一事实来源），避免两处写法不一致。 */
const TOOL_PANEL_OF = {
  gene: "toolGene", blast: "toolBlast", enrich: "toolEnrich", kegg: "toolKegg",
  idresolve: "toolIdResolve", sequence: "toolSequence", annotate: "toolAnnotate",
  retrieval: "toolRetrieval", upload: "toolUpload",
  genomes: "toolGenomes", expression: "toolExpression", phenotype: "toolPhenotype",
  about: "toolAbout",
};

const TOOL_METHOD = {
  gene: "身份/坐标 = Ensembl GRCz11 r116；符号 = NCBI RefSeq GRCz12tu；Bgee 为 0–100 表达存在性置信分，Expression Atlas 才是 TPM",
  blast: "BLAST+ 2.17.0+，比对库 = GRCz12tu RefSeq CDS/基因组（不做 GRCz11 隐式回退）",
  enrich: "ORA：Fisher 精确检验 + Benjamini–Hochberg 校正，报告校正后 q 值；背景集 = 本地注释库",
  kegg: "通路编号直接采用 KEGG dre / WikiPathways 原始编号，便于与原站对照",
  idresolve: "三层解析：唯一命中 / 一对多（列出候选）/ 未命中（不猜测）",
  sequence: "cDNA = GRCz12tu CDS；启动子/基因组区 = GRCz11 坐标（含链方向），已标注所用装配版本",
  annotate: "汇总身份、表达、GO、通路、同源、文献；缺记录处写「无记录」，不填充推测值",
  retrieval: "粗排 500 篇 → cross-encoder 精排 100 篇 → 按 48,000 字符打包；分数为 adjusted_L2 = raw_L2 / 章节权重",
  upload: "统一清洗/切分后入库；需重建向量分片才能被检索命中，未索引文件会在结果中标注",
  genomes: "染色体长度 / GC = 本机 GRCz12tu FASTA 实测（实时扫描一次后缓存）；基因数 = 本地注释库 GRCz11 r116 按 chromosome 字段统计，两套坐标系不同已在页面标明；文库大小 = 本地文件字节数",
  expression: "Bgee 表达分（0–100，存在性置信分，按解剖实体）与 Expression Atlas TPM（按胚胎分期，仅 marker 基因子集）单位不同、分列呈现；表达谱按分值降序，不跨组比较",
  phenotype: "表型 = ZFIN 官方 phenoGeneCleanData_fish.txt 本地解析；一行一条「表型–基因–基因型–分期」注释，按表型/基因分组去重；文献号 = ZFIN ZDB-PUB；文献命中来自本地文献库题名",
};

const TOOL_METHOD_EN = {
  gene: "Identity / coordinates = Ensembl GRCz11 r116; symbols = NCBI RefSeq GRCz12tu; Bgee is a 0–100 expression-existence confidence score — only Expression Atlas reports TPM",
  blast: "BLAST+ 2.17.0+, search DB = GRCz12tu RefSeq CDS / genome (no implicit GRCz11 fallback)",
  enrich: "ORA: Fisher's exact test + Benjamini–Hochberg correction, adjusted q values reported; background = local annotation library",
  kegg: "Pathway IDs keep the original KEGG dre / WikiPathways numbering so they can be checked against the source sites",
  idresolve: "Three-level resolution: unique hit / one-to-many (candidates listed) / not found (never guessed)",
  sequence: "cDNA = GRCz12tu CDS; promoter / genomic region = GRCz11 coordinates (strand included), and the assembly used is labelled",
  annotate: "Collects identity, expression, GO, pathways, orthologs and literature; missing entries say “no record” and are never filled with guesses",
  retrieval: "Coarse top 500 papers → cross-encoder rerank top 100 → packed to 48,000 characters; score = adjusted_L2 = raw_L2 / section weight",
  upload: "Unified cleaning / chunking before ingest; uploaded files are searchable only after the vector shards are rebuilt — unindexed files are marked",
  genomes: "Chromosome length / GC = measured on the local GRCz12tu FASTA (one scan, then cached); gene counts = local annotation library GRCz11 r116 grouped by the chromosome field, and the two coordinate systems are labelled on the page; library sizes = local file bytes",
  expression: "Bgee expression scores (0–100 expression-existence confidence, by anatomical entity) and Expression Atlas TPM (by embryonic stage, marker-gene subset only) use different units and are shown in separate blocks — profiles are sorted by score, never compared across blocks",
  phenotype: "Phenotypes = the official ZFIN phenoGeneCleanData_fish.txt parsed locally; one row per phenotype–gene–genotype–stage annotation, grouped by phenotype / gene; publication IDs are ZFIN ZDB-PUB; literature hits come from local paper titles",
  about: "All figures on this page are measured snapshots of the local libraries; sources and algorithms are listed one by one and can be checked at the source sites; the citation template includes an access date",
};

function currentToolTabKey() {
  // 先看真正显示出来的面板（Genomes / Expression / Phenotype 这类新页没有旧版标签条），
  // 再看标签条：旧版标签条默认「基因」是 active，直接读它会张冠李戴。
  const vis = Array.from(document.querySelectorAll(".tool-panel")).find((p) => !p.classList.contains("hidden"));
  if (vis) {
    const key = Object.keys(TOOL_PANEL_OF).find((k) => TOOL_PANEL_OF[k] === vis.id);
    if (key) return key;
  }
  const t = document.querySelector(".tool-tab.active");
  if (t && t.dataset.tool) return t.dataset.tool;
  return null;
}

function toolSourceRowHtml() {
  /* 2026-10-31：功能页结果卡底部那行「结果来自｜本地库 GRCz11 r116｜Ensembl GRCz11 r116｜ZFIN ZDB｜NCBI RefSeq …」
     整行删除（用户点名要去掉，且各页都有）。来源口径统一收在「使用说明 → 数据来源」，
     结果卡只呈现结论。要恢复就把下面这行 return 去掉，改为重新拼徽章。 */
  return "";
}

function ensureToolSourceRow() {
  if (!toolResult || toolResult.classList.contains("hidden")) return;
  if (toolResult.querySelector(".tool-source-row")) return;
  const txt = (toolResult.textContent || "").trim();
  if (txt.length < 40) return;                       // 空态/占位不加
  if (/中…|加载|失败|请先|请输入|需先登录|只读令牌/.test(txt)) return;  // 提示态不加
  const html = toolSourceRowHtml();
  if (html) toolResult.insertAdjacentHTML("beforeend", html);
}

let toolSourceTimer = null;
function scheduleToolSourceSweep() {
  if (toolSourceTimer) return;
  toolSourceTimer = setTimeout(() => {
    toolSourceTimer = null;
    try { ensureToolSourceRow(); } catch (e) { /* 兜底逻辑不影响主流程 */ }
  }, 350);
}

/* 兜底：回答气泡有多条渲染路径（流式收尾 / appendMessage / 会话恢复），
   任何一条都可能覆盖 innerHTML。用 MutationObserver 保证“只要气泡定稿就补徽章”。 */
function ensureAnswerBadges() {
  if (!messagesEl) return;
  messagesEl.querySelectorAll(".msg.bot .md-body").forEach((bubble) => {
    if (bubble.querySelector(".prov-panel")) return;        // 已有面板就不再加徽章行
    if (bubble.querySelector(".prov-answer-badges")) return;
    const wrapMsg = bubble.closest(".msg");
    if (wrapMsg && wrapMsg.classList.contains("typing")) return;
    // 只在“已定稿”的气泡上补（操作按钮已就位），避免流式期间抢先建轻量面板
    if (!wrapMsg || !wrapMsg.querySelector(".msg-actions")) return;
    const text = bubble.textContent || "";
    if (text.trim().length < 60) return;
    if (/请求失败|预热中请稍候|已等待|处理中|Hub 编排中/.test(text)) return;
    // 恢复的历史回答（没有 tool_results）也能拿到面板：源徽章 + 引用条目
    appendProvenancePanel(bubble, text, null);
  });
}

let badgeObserverTimer = null;
function scheduleBadgeSweep() {
  if (badgeObserverTimer) return;
  badgeObserverTimer = setTimeout(() => {
    badgeObserverTimer = null;
    try { ensureAnswerBadges(); } catch (e) { /* 兜底逻辑不影响主流程 */ }
    try { updateChatTools(); } catch (e) { /* 同上 */ }
  }, 400);
}

/* ── 引用与溯源面板（M3-3） ────────────────────────────────────────────
   回答末尾的可折叠面板：本次编排步骤 + 来源徽章 + 引用条目 + 检索统计。
   与 CSS 里被隐藏的旧版「回答详情」不同，这个是聊天页可见的。 */
function parseCitations(text) {
  const out = [];
  for (const line of String(text || "").split("\n")) {
    const m = line.match(/^\s*\[(\d{1,2})\]\s+(\S.{6,})$/);
    if (!m) continue;
    out.push({ n: Number(m[1]), text: m[2].trim() });
  }
  return out.slice(0, 20);
}

const CIT_DB_LINKS = [
  { re: /Bgee/i, db: "bgee", home: "https://www.bgee.org/" },
  { re: /Expression\s*Atlas|E-ERAD/i, db: "atlas", home: "https://www.ebi.ac.uk/gxa/home" },
  { re: /Ensembl/i, db: "ensembl", home: "https://www.ensembl.org/Danio_rerio/" },
  { re: /ZFIN/i, db: "zfin", home: "https://zfin.org/" },
  { re: /UniProt/i, db: "uniprot", home: "https://www.uniprot.org/" },
  { re: /QuickGO/i, db: "quickgo", home: "https://www.ebi.ac.uk/QuickGO/" },
  { re: /KEGG/i, db: "kegg", home: "https://www.kegg.jp/" },
  { re: /WikiPathways/i, db: "wikipathways", home: "https://www.wikipathways.org/" },
  { re: /PubMed|PMID/i, db: "pubmed", home: "https://pubmed.ncbi.nlm.nih.gov/" },
];

function citationLink(text) {
  const raw = String(text || "");
  const pmid = (raw.match(/PMID[:\s]*?(\d{7,9})/i) || [])[1]               // 只在本条引用里找 PMID
    || (raw.match(/pubmed[^\d]{0,12}(\d{7,9})/i) || [])[1];
  if (pmid && window.ZFBadges) return window.ZFBadges.badge({ db: "pubmed", id: pmid });
  const hit = CIT_DB_LINKS.find((d) => d.re.test(raw));
  if (!hit || !window.ZFBadges) return "";
  const ens = (raw.match(/ENSDARG\d{11}/) || [])[0];
  const item = (ens && ["bgee", "atlas", "ensembl"].includes(hit.db)) ? { db: hit.db, id: ens } : { db: hit.db, url: hit.home };
  return window.ZFBadges.badge(item);
}

function citationsHtml(list) {
  if (!list.length) return "";
  const items = list.map((c) => {
    const link = citationLink(c.text);
    return `<li><span class="cit-n">[${c.n}]</span><span>${escapeHtml(c.text)}</span>${link ? ` <span class="cit-src">${link}</span>` : ""}</li>`;
  }).join("");
  return `<div class="prov-block"><p class="prov-block-title">引用条目（${list.length}）</p><ol class="cit-list">${items}</ol></div>`;
}

function retrievalStatsHtml(data) {
  const rd = data && data.retrieval_debug;
  if (!rd || !Object.keys(rd).length) return "";
  const kv = [];
  if (rd.candidates || rd.coarse_top_k) kv.push(["粗排", rd.candidates || rd.coarse_top_k]);
  if (rd.rerank_top_k || rd.top_k) kv.push(["精排", rd.rerank_top_k || rd.top_k]);
  if (rd.pack_chars) kv.push(["打包字符", rd.pack_chars]);
  if (rd.context_chars) kv.push(["上下文", rd.context_chars]);
  if (!kv.length) return "";
  return `<div class="prov-block"><p class="prov-block-title">检索统计</p><p class="prov-kv">${kv.map(([k, v]) => `${escapeHtml(k)} <b>${escapeHtml(String(v))}</b>`).join(" · ")}</p></div>`;
}

/** 面板：任何路径下的定稿回答都能挂（流式 / 普通 / 恢复） */
function appendProvenancePanel(bubbleEl, text, data) {
  if (!bubbleEl) return;
  const old = bubbleEl.querySelector(".prov-panel");
  if (old) old.remove();
  const raw = String(text || "");
  const cites = parseCitations(raw);
  const stepsHtml = data ? renderAutoStepsHtml(autoToolSteps(data), data.tools_invoked || []) : "";
  const badgeRow = answerSourceBadges(raw + " " + (bubbleEl.innerHTML || ""));
  const inner = [stepsHtml, badgeRow ? `<div class="prov-block">${badgeRow.replace('class="src-row prov-answer-badges"', 'class="src-row"')}</div>` : "", citationsHtml(cites), retrievalStatsHtml(data)].join("");
  if (!inner.trim()) return;
  bubbleEl.insertAdjacentHTML("beforeend",
    `<details class="prov-panel" data-i18n-live="1"><summary>引用与溯源</summary>${inner}</details>`);
}

/* ── 会话导出 / 分享（M3-4） ──────────────────────────────────────────
   游客会话只存在本机 localStorage，所以“分享”用导出文件/剪贴板实现；
   真正的云端分享链接需要登录（服务端会话同步）——界面上已如实说明。 */
function conversationToMarkdown(conv) {
  const msgs = (conv?.messages || []).filter((m) => m && m.content);
  const title = msgs.find((m) => m.role === "user")?.content?.slice(0, 60) || "会话";
  const head = [
    `# ${title}`,
    "",
    `- 导出时间：${new Date().toLocaleString()}`,
    `- 消息数：${msgs.length}`,
    `- 来源：Zebrafish Hub（本地注释库 GRCz11 + 公开组学库；数值均带单位与来源）`,
    "",
  ].join("\n");
  const body = msgs.map((m) => {
    const who = m.role === "user" ? "**问**" : "**答**";
    return `${who}\n\n${String(m.content).trim()}\n`;
  }).join("\n---\n\n");
  return head + "\n" + body;
}

function conversationToJson(conv) {
  const msgs = (conv?.messages || []).filter((m) => m && m.content);
  return JSON.stringify({
    app: "Zebrafish Hub",
    exported_at: new Date().toISOString(),
    title: conv?.title || (msgs[0]?.content || "").slice(0, 60),
    message_count: msgs.length,
    messages: msgs.map((m) => ({ role: m.role, content: m.content })),
    api_history: conv?.apiHistory || [],
  }, null, 2);
}

function downloadText(filename, text) {
  try {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
    return true;
  } catch (e) {
    return false;
  }
}

function safeFilename(base) {
  return String(base || "conversation").replace(/[\\/:*?"<>|\s]+/g, "-").slice(0, 40);
}

function updateChatTools() {
  const bar = document.getElementById("chatTools");
  if (!bar) return;
  const conv = typeof getActiveConversation === "function" ? getActiveConversation() : null;
  const n = (conv?.messages || []).filter((m) => m && m.content).length;
  bar.classList.toggle("hidden", n === 0);
}

function bindChatExport() {
  /* ⚠ 历史 bug：异步处理器里 `e.currentTarget` 在 await 之后会变成 null，
     导致 flash(null, ...) 报 “Cannot read properties of null (reading 'textContent')”
     —— 用户点"复制全文"就直接静默失败。必须一进 handler 就把按钮取出来。 */
  const flash = (btn, text, kind) => {
    if (btn) {
      const old = btn.textContent;
      btn.textContent = text;
      setTimeout(() => { btn.textContent = old; }, 1200);
    }
    if (window.ZFUi && window.ZFUi.toast) window.ZFUi.toast(text, kind);
  };
  document.getElementById("exportMdBtn")?.addEventListener("click", (e) => {
    const btn = e.currentTarget;
    const conv = getActiveConversation();
    if (!conv) return;
    const md = conversationToMarkdown(conv);
    const stamp = new Date().toISOString().slice(0, 10);
    if (downloadText(`zebrafish-hub-${safeFilename(conv.title)}-${stamp}.md`, md)) flash(btn, "已导出 Markdown", "ok");
    else flash(btn, "导出失败", "err");
  });
  document.getElementById("exportJsonBtn")?.addEventListener("click", (e) => {
    const btn = e.currentTarget;
    const conv = getActiveConversation();
    if (!conv) return;
    const stamp = new Date().toISOString().slice(0, 10);
    if (downloadText(`zebrafish-hub-${safeFilename(conv.title)}-${stamp}.json`, conversationToJson(conv))) flash(btn, "已导出 JSON", "ok");
    else flash(btn, "导出失败", "err");
  });
  document.getElementById("copyAllBtn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;                 // 必须在 await 之前取
    const conv = getActiveConversation();
    if (!conv) return;
    const md = conversationToMarkdown(conv);
    let ok = false;
    try { await navigator.clipboard.writeText(md); ok = true; } catch { ok = false; }
    if (!ok) ok = copyTextFallback(md);
    flash(btn, ok ? "已复制全文" : "复制失败（请手动选择文本）", ok ? "ok" : "err");
  });
}

/* ── 运营看板（M3-5）：定时任务 + 文献推送规模 + 聚合访问统计 ─────────────
   计划表按服务器 crontab 的事实写明；运行状态读服务端只读接口，
   接口不通就如实显示“读取失败”，不编造数字。 */
const OPS_SCHEDULE = [
  { name: "文献周更（PubMed / Europe PMC）", when: "每周日 03:20", src: "crontab · scripts/job_weekly_lit_refresh.py" },
  { name: "图文库刷新", when: "每 3 天 04:10", src: "crontab · scripts/job_gallery_seed.py" },
  { name: "Hub 统计重算", when: "每小时第 5 分钟", src: "crontab · scripts/job_refresh_hub_stats.py" },
  { name: "Hub / 隧道存活守护", when: "每分钟", src: "crontab · deploy/hub_guard.sh" },
];

function opsScheduleHtml() {
  const rows = OPS_SCHEDULE.map((j) => `<tr><td>${escapeHtml(j.name)}</td><td>${escapeHtml(j.when)}</td>
      <td class="muted">${escapeHtml(j.src)}</td></tr>`).join("");
  return `<div class="prov-block"><p class="prov-block-title">定时任务计划（服务器 crontab）</p>
    <table class="pub-table"><thead><tr><th>任务</th><th>计划</th><th>实现</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

const OPS_JOB_LABEL = { literature: "文献周更", gallery: "图文库刷新", stats: "统计重算", all: "全量" };

function opsFmtInterval(sec) {
  const s = Number(sec) || 0;
  if (s >= 604800) return `${(s / 604800).toFixed(s % 604800 ? 1 : 0)} 周`;
  if (s >= 86400) return `${(s / 86400).toFixed(s % 86400 ? 1 : 0)} 天`;
  if (s >= 3600) return `${(s / 3600).toFixed(s % 3600 ? 1 : 0)} 小时`;
  if (s >= 60) return `${(s / 60).toFixed(s % 60 ? 1 : 0)} 分钟`;
  return `${s} 秒`;
}

function opsFmtTs(ts) {
  const n = Number(ts) || 0;
  if (!n) return "尚未运行";
  try { return new Date(n * 1000).toLocaleString(); } catch { return String(n); }
}

/** 服务端维护状态（真实字段：enabled/intervals_sec/running/due/state） */
function opsJobsHtml(st) {
  if (!st || typeof st !== "object") return "";
  const iv = st.intervals_sec || {};
  const running = st.running || {};
  const due = st.due || {};
  const state = st.state || {};
  const keys = Object.keys(iv);
  if (!keys.length) return "";
  const rows = keys.map((k) => {
    const st2 = running[k] ? "运行中" : (due[k] ? "待运行" : "正常");
    const lastKey = `${k}_last_ts`;
    const resKey = `${k}_last_result`;
    const res = state[resKey];
    const resText = res == null ? "—"
      : (typeof res === "object" ? (res.message || res.summary || JSON.stringify(res).slice(0, 60)) : String(res).slice(0, 60));
    return `<tr><td>${escapeHtml(OPS_JOB_LABEL[k] || k)}</td><td>${escapeHtml(opsFmtInterval(iv[k]))}</td>
      <td>${escapeHtml(st2)}</td><td>${escapeHtml(opsFmtTs(state[lastKey]))}</td>
      <td class="muted">${escapeHtml(resText)}</td></tr>`;
  }).join("");
  const enabled = st.enabled === false ? "已关闭" : "已启用";
  return `<div class="prov-block"><p class="prov-block-title">维护任务状态（服务端 · ${enabled}）</p>
    <p class="prov-kv">服务端时间 <b>${escapeHtml(String(st.now || "-"))}</b></p>
    <table class="pub-table"><thead><tr><th>任务</th><th>间隔</th><th>状态</th><th>最近运行</th><th>最近结果</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function opsLitHtml(st) {
  const a = st && st.audit;
  if (!a) return "";
  const kd = a.knowledge_d || {};
  const cat = a.catalog || {};
  const kv = [];
  if (kd.file_count != null) kv.push(["knowledge.d 文件", kd.file_count]);
  if (cat.lines != null) kv.push(["文献目录行", cat.lines]);
  if (a.progress_pmids != null) kv.push(["进度 PMID", a.progress_pmids]);
  if (!kv.length) return "";
  return `<div class="prov-block"><p class="prov-block-title">文献与知识库规模</p>
    <p class="prov-kv">${kv.map(([k, v]) => `${escapeHtml(k)} <b>${escapeHtml(String(v))}</b>`).join(" · ")}</p></div>`;
}

function opsVisitHtml(v) {
  if (!v || !v.ok) return "";
  const w = v.window || {};
  const routes = (v.top_routes || []).map((r) => `<tr><td class="prov-mono">${escapeHtml(r.route)}</td><td>${r.count}</td></tr>`).join("");
  const stat = Object.entries(v.status_classes || {}).map(([k, c]) => `${escapeHtml(k)} <b>${c}</b>`).join(" · ");
  return `<div class="prov-block"><p class="prov-block-title">访问统计（聚合，仅计数）</p>
    <p class="prov-kv">窗口 <b>${w.access_lines ?? 0}</b> 条访问日志（扫描 ${w.lines_scanned ?? 0} 行）· 独立来源 <b>${v.unique_clients ?? 0}</b> · 状态 ${stat}</p>
    <p class="prov-kv">日志更新时间 <b>${escapeHtml(String(w.log_mtime || "-"))}</b></p>
    <table class="pub-table"><thead><tr><th>路由</th><th>次数</th></tr></thead><tbody>${routes}</tbody></table>
    <p class="tool-source-note">${escapeHtml(v.privacy_note || "")}</p></div>`;
}

async function renderOpsPanel() {
  const host = document.getElementById("opsHost");
  if (!host) return;
  host.innerHTML = "<span class='tool-loading'>读取服务端状态…</span>";
  let status = null;
  let visits = null;
  let err1 = null;
  let err2 = null;
  try {
    const r = await apiFetch("/api/hub/maintenance/status");
    if (r.ok) status = await r.json();
    else err1 = `HTTP ${r.status}`;
  } catch (e) { err1 = String(e.message || e); }
  try {
    const r = await apiFetch("/api/hub/visit-stats");
    if (r.ok) visits = await r.json();
    else err2 = `HTTP ${r.status}`;
  } catch (e) { err2 = String(e.message || e); }
  const parts = [opsScheduleHtml(), opsJobsHtml(status), opsLitHtml(status), opsVisitHtml(visits)];
  if (err1) parts.push(`<div class="prov-block"><p class="prov-block-title">维护状态</p><p class="tool-hint warn">读取失败：${escapeHtml(err1)}</p></div>`);
  if (err2) parts.push(`<div class="prov-block"><p class="prov-block-title">访问统计</p><p class="tool-hint warn">读取失败：${escapeHtml(err2)}</p></div>`);
  host.innerHTML = `<div class="orch-steps">${parts.join("")}</div>`;
}

function appendMessage(role, text, isTyping = false) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}${isTyping ? " typing" : ""}`;
  const isBot = role !== "user";
  // AI 回答按 Markdown 渲染；用户消息纯文本
  const body = isBot ? mdToHtml(text) : escapeHtml(text);
  wrap.innerHTML = `
    <div class="avatar">${role === "user" ? "你" : "AI"}</div>
    <div class="bubble md-body">${body}</div>
  `;
  if (isBot && !isTyping) {
    // 来源徽章与引用条目统一放进「引用与溯源」面板，避免两处重复
    appendProvenancePanel(wrap.querySelector(".bubble"), text, null);
    attachMsgActions(wrap, text);
  }
  messagesEl.appendChild(wrap);
  messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: isTyping ? "auto" : "smooth" });
  return wrap;
}

function renderSequenceResult(data) {
  if (!data.success) {
    return `<p class="tool-hint warn">${escapeHtml(data.error || "序列提取失败")}</p>`;
  }
  // 状态行自己拼（后端 summary 是中文，英文界面下会中英混排）
  const blocks = Object.entries(data.results || {}).filter(([, b]) => b && b.success);
  const failed = Object.entries(data.results || {}).filter(([, b]) => b && !b.success);
  const kindLabel = { cdna: "cDNA", promoter: "启动子", genomic: "基因组区" };
  let html = "";
  if (blocks.length) {
    html += `<div class="tool-summary">${escapeHtml(data.gene || "")} · ${blocks.length} 段序列${failed.length ? `（${failed.length} 段失败）` : ""}</div>`;
  }
  blocks.forEach(([k, block]) => {
    // 蛋白号要标出来：NP_/XP_ 是蛋白 accession，不是核酸号（2026-10-28）
    const acc = block.accession
      ? block.accession + (block.accession_type === "protein" ? "（蛋白号）" : "")
      : "";
    const meta = [block.assembly, block.source, acc].filter(Boolean).join(" · ");
    html += `<details class="tool-json-details" open><summary>${escapeHtml(kindLabel[k] || k)} FASTA（${block.length} bp${meta ? ` · ${escapeHtml(meta)}` : ""}）</summary>`;
    html += `<pre>${escapeHtml(block.fasta || block.sequence || "")}</pre></details>`;
  });
  failed.forEach(([k, block]) => {
    html += `<p class="tool-hint warn">${escapeHtml(kindLabel[k] || k)}：${escapeHtml(block.error || "失败")}</p>`;
  });
  return html;
}

/* 按染色体区间取序列（GRCz12tu 默认 / GRCz11 可选） */
function renderRegionResult(d) {
  if (!d || !d.success) {
    return `<p class="tool-hint warn">${escapeHtml((d && d.error) || "区间提取失败")}</p>`;
  }
  const meta = [d.assembly, d.source, d.accession, d.region].filter(Boolean).join(" · ");
  return `<div class="tool-summary">chr${escapeHtml(d.chromosome)}:${d.start}-${d.end}（${escapeHtml(d.strand)}）· ${d.length} bp</div>
    <p class="tool-hint">${escapeHtml(meta)}</p>
    <pre class="seq-fasta">${escapeHtml(d.fasta || d.sequence || "")}</pre>`;
}

document.querySelectorAll(".seq-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const name = tab.getAttribute("data-seq-tab");
    document.querySelectorAll(".seq-tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".seq-pane").forEach((p) => {
      p.classList.toggle("hidden", p.getAttribute("data-seq-pane") !== name);
    });
    // hero 里的按钮组只服务于「按区间」；切到「按基因」就退回说明文字
    if (window.ZFPageChips) window.ZFPageChips.onPaneChange(name);
  });
});

/* 页内 tab 现在会被路由直接驱动（#/sequence-region 落「按区间」、旧 #/sequence/基因 落「按基因」），
   所以把「切到某个 tab」提取成函数，走同一套 click 逻辑，不再另写一份显示/隐藏。 */
function setSeqTab(name) {
  const tab = document.querySelector('.seq-tab[data-seq-tab="' + name + '"]');
  if (tab && !tab.classList.contains("active")) tab.click();
}

/* sgRNA / 引物共用一个面板：路由决定默认落在哪个 tab。
   2026-10-28：原先靠 `setTimeout(() => tab.click(), 30)` 触发，但 crispr.js 是 defer，
   网络慢一点（线上过 Cloudflare 单程就 50–100ms）时 30ms 内还没绑定 click，
   点击落空 —— 结果停在 sgRNA 面板，hero 的胶囊也停在 PAM 那组。
   改成自己切类名（不依赖别人先绑定），再通知 crispr.js 刷新跳转区。 */
function setCrisprTab(name) {
  const tab = document.querySelector('.crispr-tab[data-crispr-tab="' + name + '"]');
  if (!tab) return;
  /* 2026-10-31：每个主题页只留本主题那一半 ----------------------------------------
     sgRNA 页不该出现「引物设计」tab 与引物面板，引物页同理（用户点名）。两者共用
     #toolCrispr，所以按路由把非本主题的 tab 按钮收起来；只剩一个按钮时整条 tab 条
     一起收起——单颗 tab 既不能切也没信息量，页面标题已经写明主题。
     不删节点：crispr.js 的 bind()、结果区的按钮都还在找它们，删了会静默断链。 */
  document.querySelectorAll(".crispr-tab").forEach((t) => {
    const own = t.getAttribute("data-crispr-tab") === name;
    if (own) {
      t.style.removeProperty("display");
      t.removeAttribute("aria-hidden");
    } else {
      t.style.setProperty("display", "none", "important");
      t.setAttribute("aria-hidden", "true");
    }
    t.classList.toggle("active", own);
  });
  document.querySelectorAll(".crispr-tabs").forEach((bar) => {
    bar.style.setProperty("display", "none", "important");
  });
  document.querySelectorAll(".crispr-pane").forEach((p) => {
    p.classList.toggle("hidden", p.getAttribute("data-crispr-pane") !== name);
  });
  if (window.ZFPageChips) window.ZFPageChips.onPaneChange(name);
  document.dispatchEvent(new CustomEvent("zf-crispr-pane", { detail: { pane: name } }));
}
/* Liftover 结果卡上的「按这段坐标取序列」会把目标区间塞进 sessionStorage，
   这里回填到「按染色体区间」并直接跑一次，避免用户再抄一遍坐标。 */
function applyLiftRegionToSeq() {
  let payload = null;
  try {
    const raw = sessionStorage.getItem("zf_seq_region");
    if (raw) payload = JSON.parse(raw);
    sessionStorage.removeItem("zf_seq_region");
  } catch (_) {
    payload = null;
  }
  if (!payload || !payload.chrom) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el && v != null && v !== "") el.value = v; };
  set("seqRegionChrom", payload.chrom);
  set("seqRegionStart", payload.start);
  set("seqRegionEnd", payload.end);
  if (payload.assembly) set("seqRegionAsm", payload.assembly);
  setTimeout(() => document.getElementById("seqRegionBtn")?.click(), 80);
}

document.getElementById("seqRegionBtn")?.addEventListener("click", async () => {
  const chrom = document.getElementById("seqRegionChrom")?.value.trim();
  const start = document.getElementById("seqRegionStart")?.value;
  const end = document.getElementById("seqRegionEnd")?.value;
  const strand = document.getElementById("seqRegionStrand")?.value || "+";
  const assembly = document.getElementById("seqRegionAsm")?.value || "GRCz12tu";
  toolResult.classList.remove("hidden");
  if (!chrom || !start || !end) {
    toolResult.innerHTML = "<p class='tool-hint warn'>请填写染色体、起点、终点</p>";
    return;
  }
  toolResult.innerHTML = "<span class='tool-loading'>按坐标取序列中…</span>";
  try {
    const res = await apiFetch(
      `/api/tools/sequence/region?chrom=${encodeURIComponent(chrom)}&start=${encodeURIComponent(start)}` +
      `&end=${encodeURIComponent(end)}&strand=${encodeURIComponent(strand)}&assembly=${encodeURIComponent(assembly)}`
    );
    const data = await parseJsonResponse(res, res.url || "");
    toolResult.innerHTML = renderRegionResult(data);
  } catch (err) {
    toolResult.innerHTML = `<p class="tool-hint warn">${escapeHtml(String(err.message || err))}</p>`;
  }
});

function renderAnnotatorResult(data) {
  if (!data.success) {
    return `<p class="tool-hint warn">${escapeHtml(data.error || "注释失败")}</p>`;
  }
  let html = `<pre class="enrich-summary">${escapeHtml(data.summary || "")}</pre>`;
  const go = data.go_annotations || [];
  if (go.length) {
    html += `<h4 class="pub-h4">GO</h4><ul>${go.slice(0, 8).map((g) =>
      `<li>${escapeHtml(g.go_term || "")}</li>`).join("")}</ul>`;
  }
  return html;
}

seqExtractBtn?.addEventListener("click", async () => {
  const raw = (seqGeneInput?.value || "").trim();
  if (!raw) return;
  const genes = Array.from(new Set(raw.split(/[,，;；\s]+/).map((s) => s.trim()).filter(Boolean)));
  if (!genes.length) return;
  if (genes.length > 20) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = "<p class='tool-hint warn'>一次最多 20 个基因，请分批提取</p>";
    return;
  }
  const types = [];
  if (seqCdna?.checked) types.push("cdna");
  if (seqPromoter?.checked) types.push("promoter");
  if (seqGenomic?.checked) types.push("genomic");
  if (!types.length) {
    toolResult.classList.remove("hidden");
    toolResult.innerHTML = "<p class='tool-hint warn'>请至少勾选一个区段（cDNA / 启动子 / 基因体）</p>";
    return;
  }
  toolResult.classList.remove("hidden");
  toolResult.innerHTML = "<span class='tool-loading'>序列提取中…</span>";
  seqExtractBtn.disabled = true;

  const runs = [];
  try {
    for (let i = 0; i < genes.length; i += 1) {
      const gene = genes[i];
      if (genes.length > 1) {
        toolResult.innerHTML = `<span class='tool-loading'>序列提取中… ${i + 1}/${genes.length}（${escapeHtml(gene)}）</span>`;
      }
      try {
        const res = await apiFetch(
          `/api/tools/sequence?gene=${encodeURIComponent(gene)}&types=${encodeURIComponent(types.join(","))}`
        );
        const data = await parseJsonResponse(res, res.url || "");
        if (!res.ok) throw new Error(data.detail || "提取失败");
        runs.push({ gene, data });
      } catch (err) {
        runs.push({ gene, error: String(err.message || err) });
      }
    }
    if (runs.length === 1) {
      toolResult.innerHTML = renderSequenceResult(runs[0].data || { success: false, error: runs[0].error });
    } else {
      let html = `<div class="tool-summary">批量提取：${runs.length} 个基因 · 区段 ${types.join(" / ")}</div>`;
      runs.forEach((run, idx) => {
        html += `<section class="blast-run"><h4 class="blast-run-title">${idx + 1}. ${escapeHtml(run.gene)}</h4>`;
        html += run.data
          ? renderSequenceResult(run.data)
          : `<p class="tool-hint warn">${escapeHtml(run.error || "提取失败")}</p>`;
        html += "</section>";
      });
      toolResult.innerHTML = html;
    }
  } finally {
    seqExtractBtn.disabled = false;
  }
});

/* 批量区间：每行 chr:start-end（也接受 chr start end），逐条取回 */
document.getElementById("seqRegionBatchBtn")?.addEventListener("click", async () => {
  const area = document.getElementById("seqRegionBatch");
  const lines = String(area?.value || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  toolResult.classList.remove("hidden");
  if (!lines.length) {
    toolResult.innerHTML = "<p class='tool-hint warn'>请每行填一条区间，如 8:1000-2000</p>";
    return;
  }
  if (lines.length > 50) {
    toolResult.innerHTML = "<p class='tool-hint warn'>一次最多 50 条区间，请分批提交</p>";
    return;
  }
  const strand = document.getElementById("seqRegionStrand")?.value || "+";
  const assembly = document.getElementById("seqRegionAsm")?.value || "GRCz12tu";
  const parsed = [];
  for (const line of lines) {
    const m = line.match(/^(?:chr)?([0-9]+|[XYM]+)\s*[:\-,\s]\s*([\d,]+)\s*[-–~,\s]\s*([\d,]+)$/i);
    if (!m) { parsed.push({ line, error: "格式无法识别（应为 8:1000-2000）" }); continue; }
    parsed.push({ line, chrom: m[1], start: m[2].replace(/,/g, ""), end: m[3].replace(/,/g, "") });
  }
  const runs = [];
  for (let i = 0; i < parsed.length; i += 1) {
    const p = parsed[i];
    if (p.error) { runs.push(p); continue; }
    toolResult.innerHTML = `<span class='tool-loading'>按坐标取序列… ${i + 1}/${parsed.length}</span>`;
    try {
      const res = await apiFetch(
        `/api/tools/sequence/region?chrom=${encodeURIComponent(p.chrom)}&start=${encodeURIComponent(p.start)}` +
        `&end=${encodeURIComponent(p.end)}&strand=${encodeURIComponent(strand)}&assembly=${encodeURIComponent(assembly)}`
      );
      const data = await parseJsonResponse(res, res.url || "");
      runs.push({ line: p.line, data });
    } catch (err) {
      runs.push({ line: p.line, error: String(err.message || err) });
    }
  }
  let html = `<div class="tool-summary">批量区间：${runs.length} 条 · ${assembly} · ${strand} 链</div>`;
  runs.forEach((run, idx) => {
    html += `<section class="blast-run"><h4 class="blast-run-title">${idx + 1}. ${escapeHtml(run.line)}</h4>`;
    html += run.data
      ? renderRegionResult(run.data)
      : `<p class="tool-hint warn">${escapeHtml(run.error || "提取失败")}</p>`;
    html += "</section>";
  });
  toolResult.innerHTML = html;
});

annotateBtn?.addEventListener("click", async () => {
  const gene = annotateInput?.value.trim();
  if (!gene) return;
  toolResult.classList.remove("hidden");
  toolResult.innerHTML = "<span class='tool-loading'>基因注释中…</span>";
  try {
    const res = await apiFetch(`/api/tools/annotate?gene=${encodeURIComponent(gene)}`);
    const data = await parseJsonResponse(res, res.url || "");
    if (!res.ok) throw new Error(data.detail || "注释失败");
    toolResult.innerHTML = renderAnnotatorResult(data);
  } catch (err) {
    toolResult.textContent = String(err.message);
  }
});

/* ── 工具编排（M3-2）：把"自动联动"从一条横幅变成可读步骤 + 可跳转深挖 ──── */
const TOOL_TAB_ROUTE = {
  gene: "gene", blast: "blast", enrich: "enrich", kegg: "kegg",
  idresolve: "id", sequence: "sequence-region", annotate: "annotate",
  // 2026-10-28：retrieval 不再有独立页面（News 直接落首页），不给「打开」链接，
  // 否则点了会回到首页，等于一个失效跳转。
  upload: "upload",
};
const TOOL_TAB_LABEL = {
  gene: "基因位置", blast: "BLAST", enrich: "GO 富集", kegg: "KEGG 通路",
  idresolve: "ID 映射", sequence: "位置查找序列", annotate: "基因位置",
  retrieval: "News", upload: "入库",
};

/** 从各工具返回里抽取"命中摘要"，抽不到就不显示，绝不编数字 */
function autoToolSteps(data) {
  const tr = data.tool_results || {};
  const steps = [];
  steps.push({
    label: "意图与范围",
    meta: [data.task_type, data.answer_scope, data.answer_depth].filter(Boolean).join(" · ") || "自动路由",
    tab: null,
  });
  const idr = tr.id_resolve;
  if (idr) {
    const canon = idr.canonical || {};
    const status = idr.status || idr.match_kind || "";
    const cand = (idr.candidates || idr.ambiguous || []).length;
    steps.push({
      label: "实体解析",
      meta: canon.gene_name
        ? `→ ${canon.gene_name}${status ? `（${status}）` : ""}`
        : (cand ? `一对多候选 ${cand} 个` : (status || "已解析")),
      tab: "idresolve",
    });
  }
  if (tr.gene?.detail) {
    const b = tr.gene.detail.basic || {};
    steps.push({ label: "基因详情", meta: b.gene_name ? `${b.gene_name}｜Chr ${b.chromosome || "-"}` : "已取基因卡片", tab: "gene" });
  }
  if (tr.annotator?.success) {
    const n = (tr.annotator.go_annotations || []).length;
    steps.push({ label: "一键注释", meta: n ? `GO ${n} 条` : "已汇总", tab: "annotate" });
  }
  if (tr.enrichment) {
    const go = (tr.enrichment.go?.terms || []).length;
    const kg = (tr.enrichment.kegg?.pathways || []).length;
    steps.push({ label: "GO/KEGG 富集", meta: `GO ${go} 项 · KEGG ${kg} 条（padj≤0.05）`, tab: "enrich" });
  }
  if (tr.kegg) {
    const n = (tr.kegg.pathways || []).length;
    steps.push({ label: "通路检索", meta: n ? `命中 ${n} 条通路` : "无匹配通路", tab: "kegg" });
  }
  if (tr.blast?.summary || tr.blast?.result) {
    const hits = (tr.blast.hits || tr.blast.results || []).length;
    steps.push({ label: "BLAST 比对", meta: hits ? `命中 ${hits} 条` : "已比对", tab: "blast" });
  }
  if (tr.sequence?.results) {
    const keys = Object.keys(tr.sequence.results || {});
    steps.push({ label: "序列提取", meta: keys.length ? keys.join(" / ") : "已提取", tab: "sequence" });
  }
  const rd = data.retrieval_debug;
  if (rd) {
    const parts = [];
    if (rd.candidates || rd.coarse_top_k) parts.push(`粗排 ${rd.candidates || rd.coarse_top_k}`);
    if (rd.rerank_top_k || rd.top_k) parts.push(`精排 ${rd.rerank_top_k || rd.top_k}`);
    if (rd.pack_chars) parts.push(`打包 ${rd.pack_chars} 字符`);
    steps.push({ label: "文献检索", meta: parts.join(" → ") || "已检索", tab: "retrieval" });
  }
  return steps;
}

function renderAutoStepsHtml(steps, invoked) {
  if (!steps.length) return "";
  const rows = steps.map((s, i) => {
    const open = s.tab && TOOL_TAB_ROUTE[s.tab]
      ? ` <a class="orch-open" href="#/${TOOL_TAB_ROUTE[s.tab]}">打开${TOOL_TAB_LABEL[s.tab] || ""} →</a>`
      : "";
    return `<li class="orch-step"><span class="orch-idx">${i + 1}</span>
      <span class="orch-label">${escapeHtml(s.label)}</span>
      <span class="orch-meta">${escapeHtml(s.meta || "")}${open}</span></li>`;
  }).join("");
  const says = invoked.length ? `<p class="orch-say">后端报告已调用：${escapeHtml(invoked.join("、"))}</p>` : "";
  return `<div class="orch-steps"><p class="orch-title">本次编排（${steps.length} 步）</p><ol class="orch-list">${rows}</ol>${says}</div>`;
}

function applyAutoTools(data) {
  const tr = data.tool_results;
  const invoked = data.tools_invoked || [];
  if (!tr || !Object.keys(tr).length) return;

  const sections = [];
  const linkedTabs = [];
  const addSection = (title, tabKey, bodyHtml) => {
    if (!bodyHtml) return;
    const route = tabKey ? TOOL_TAB_ROUTE[tabKey] : null;
    const link = route
      ? ` <a class="auto-tool-open" href="#/${route}" title="到该工具页继续手工深挖">在本页打开 →</a>`
      : "";
    sections.push(`<section class="auto-tool-section"><h4 class="auto-tool-title">${title}${link}</h4>${bodyHtml}</section>`);
    if (tabKey) linkedTabs.push(tabKey);
  };

  if (tr.gene?.detail) {
    toolGeneInput.value = tr.gene.gene || "";
    addSection("🧬 基因详情", "gene", renderToolGeneSummary(tr.gene.detail));
  }
  if (tr.id_resolve) {
    if (tr.id_resolve.query || tr.id_resolve.canonical?.gene_name) {
      idResolveInput.value = tr.id_resolve.query || tr.id_resolve.canonical.gene_name;
    }
    renderIdResolve(tr.id_resolve);
    addSection("🔗 ID 解析", "idresolve", renderIdResolveSummaryHtml(tr.id_resolve));
  }
  if (tr.enrichment) {
    const genes = tr.enrichment.go?.input_genes || tr.enrichment.kegg?.input_genes;
    if (genes?.length) enrichInput.value = genes.join(",");
    addSection("📊 GO/KEGG 富集", "enrich", renderEnrichResult({
      summary: tr.enrichment.summary,
      go: tr.enrichment.go,
      kegg: tr.enrichment.kegg,
    }));
  }
  if (tr.kegg) {
    const geneHint = tr.gene?.gene || tr.id_resolve?.canonical?.gene_name || "";
    if (geneHint) keggInput.value = geneHint;
    addSection("🛤 KEGG 通路", "kegg", renderKeggResult(tr.kegg));
  }
  if (tr.blast?.summary || tr.blast?.result) {
    addSection("🧪 BLAST 比对", "blast", renderBlastResult(tr.blast));
  }
  if (tr.sequence?.results) {
    const g = tr.sequence.gene || tr.gene?.gene || "";
    if (g) seqGeneInput.value = g;
    addSection("🧬 序列/启动子", "sequence", renderSequenceResult(tr.sequence));
  }
  if (tr.annotator?.success) {
    const g = tr.annotator.gene || tr.gene?.gene || "";
    if (g) annotateInput.value = g;
    addSection("📋 基因注释", "annotate", renderAnnotatorResult(tr.annotator));
  }

  if (!sections.length) return;

  document.querySelectorAll(".tool-tab").forEach((b) => {
    b.classList.toggle("auto-linked", linkedTabs.includes(b.dataset.tool));
  });

  const html = `<p class="tool-hint auto-tool-banner">🔬 本次已自动联动 ${linkedTabs.length} 项</p>${renderAutoStepsHtml(autoToolSteps(data), invoked)}${sections.join("")}`;

  switchToolTab(linkedTabs[0] || "gene", { keepResults: true, fromSync: true });
  const chatHost = document.getElementById("chatToolResultHost");
  if (chatHost && toolResult.parentElement !== chatHost) chatHost.appendChild(toolResult);
  toolResult.classList.remove("hidden");
  toolResult.innerHTML = html;
}

function renderAgentTrace(trace) {
  if (!agentTraceBlock || !agentTraceEl) return;
  if (!trace || !trace.length) {
    agentTraceBlock.classList.add("hidden");
    agentTraceEl.innerHTML = "";
    return;
  }
  agentTraceBlock.classList.remove("hidden");
  agentTraceEl.innerHTML = trace
    .map((step) => {
      const st = step.status || "ok";
      const cls = { ok: "ok", miss: "miss", skip: "skip", error: "err" }[st] || "";
      return `<li class="agent-step ${cls}"><span class="agent-name">${escapeHtml(step.label || step.agent)}</span><span class="agent-sum">${escapeHtml(step.summary || "")}</span><span class="agent-ms">${step.ms || 0}ms</span></li>`;
    })
    .join("");
}

function renderDetail(data) {
  detailEmpty.classList.add("hidden");
  detailContent.classList.remove("hidden");
  const scopeTxt = data.answer_scope
    ? (SCOPE_LABELS[data.answer_scope] || data.answer_scope)
    : "";
  const depthTxt = data.answer_depth
    ? (DEPTH_LABELS[data.answer_depth] || data.answer_depth)
    : "";
  const extras = [scopeTxt, depthTxt].filter(Boolean).join(" · ");
  detailTaskType.textContent = extras
    ? `${data.task_type || "-"} · ${extras}`
    : (data.task_type || "-");
  renderAgentTrace(data.agent_trace);

  if (data.gene_detail) {
    geneBlock.classList.remove("hidden");
    renderGeneCard(data.gene_detail);
  } else {
    geneBlock.classList.add("hidden");
  }

  if (data.sql) {
    sqlBlock.classList.remove("hidden");
    detailSql.textContent = data.sql;
  } else sqlBlock.classList.add("hidden");

  if (data.data && data.data.length) {
    dataBlock.classList.remove("hidden");
    rowCount.textContent = `(共 ${data.row_count ?? data.data.length} 条)`;
    renderTable(data.data);
  } else {
    dataBlock.classList.add("hidden");
    dataTable.innerHTML = "";
  }

  if (data.context) {
    contextBlock.classList.remove("hidden");
    detailContext.textContent = data.context;
  } else contextBlock.classList.add("hidden");

  // Phase B：资料库 / 映射面板
  let resBlock = document.getElementById("resourcePanelBlock");
  if (!resBlock) {
    resBlock = document.createElement("div");
    resBlock.id = "resourcePanelBlock";
    resBlock.className = "detail-block hidden";
    detailContent.appendChild(resBlock);
  }
  const rp = data.resource_panel || {};
  const dbs = rp.databases_used || [];
  const maps = data.name_mapping || rp.mappings || [];
  if (dbs.length || maps.length) {
    resBlock.classList.remove("hidden");
    const dbHtml = dbs.map((d) => {
      const href = d.url || "#";
      const ext = String(href).startsWith("http");
      return `<a class="res-chip" href="${escapeHtml(href)}"${ext ? ' target="_blank" rel="noopener"' : ""}>${escapeHtml(d.name || d.id || "")}</a>`;
    }).join("");
    const mapHtml = maps.slice(0, 12).map((m) => {
      const local = m.local ? `<a href="${escapeHtml(m.local)}">本库</a>` : "";
      const ext = m.external ? `<a href="${escapeHtml(m.external)}" target="_blank" rel="noopener">原站</a>` : "";
      return `<tr><td>${escapeHtml(m.role || "")}</td><td><code>${escapeHtml(m.value || "")}</code></td><td>${local} ${ext}</td></tr>`;
    }).join("");
    resBlock.innerHTML = `
      <label>本次用到的组学库 / 跨库映射</label>
      <div class="res-chip-row">${dbHtml || "<span class='muted'>—</span>"}</div>
      ${mapHtml ? `<div class="table-wrap"><table class="map-table"><thead><tr><th>来源</th><th>标识</th><th>链接</th></tr></thead><tbody>${mapHtml}</tbody></table></div>` : ""}
    `;
  } else {
    resBlock.classList.add("hidden");
    resBlock.innerHTML = "";
  }
}

function exprBadgesHtml(dbs, geneId) {
  try {
    if (window.ZFBadges && typeof window.ZFBadges.html === "function") {
      // ZFBadges 需要 {db:...} 对象；传裸字符串会退化成默认的“本地库”
      return window.ZFBadges.html(
        dbs.map((db) => (["bgee", "atlas", "ensembl", "uniprot", "quickgo"].includes(db) ? { db, id: geneId } : { db }))
      );
    }
  } catch (e) { /* 徽章模块未加载时静默降级 */ }
  return "";
}

function renderExpressionChart(rows, containerId, geneId) {
  if (!rows || !rows.length) return "";
  const bgee = rows.filter((e) => e.data_source === "bgee").slice(0, 12);
  const atlas = rows.filter((e) => e.data_source !== "bgee").slice(0, 8);
  if (!bgee.length && !atlas.length) return "";
  const row = (label, val, unit, pct) =>
    `<div class="tissue-bar-row"><span title="${escapeHtml(label)}">${escapeHtml(String(label).slice(0, 22))}</span>
        <div class="tissue-bar-track"><div class="tissue-bar-fill" style="width:${pct}%"></div></div><span class="metric-unit">${val}${escapeHtml(unit)}</span></div>`;
  const block = (title, badges, note, items, unit, fixedMax) => {
    if (!items.length) return "";
    const max = fixedMax || Math.max(...items.map((t) => Number(t.expression_level) || 0), 1);
    const bars = items.map((t) => {
      const val = Number(t.expression_level) || 0;
      const tissue = t.tissue || t.anatomical_entity || t.expression_stage || "?";
      const stage = String(t.expression_stage || "");
      let label = tissue;
      if (unit === " TPM" && stage && stage !== tissue) {
        label = /whole embryo|embryo|胚胎/i.test(String(tissue))
          ? `${stage}（整胚）`
          : `${stage}·${tissue}`;
      }
      const pct = fixedMax
        ? Math.max(2, Math.min(100, Math.round((val / fixedMax) * 100)))
        : Math.max(2, Math.min(100, Math.round((val / max) * 100)));
      const shown = unit === " TPM" ? val.toFixed(1) : val.toFixed(1);
      return row(label, shown, unit, pct);
    }).join("");
    return `<div class="gene-expr-block">
        <div class="gene-expr-head"><strong>${escapeHtml(title)}</strong>${exprBadgesHtml(badges, geneId)}</div>
        <p class="gene-expr-note">${note}</p>${bars}</div>`;
  };
  const lead = (bgee.length && atlas.length)
    ? `<p class="gene-expr-lead">两组数值<b>单位与来源不同，不能直接比较</b>：
       <span class="metric-info" title="Bgee 的 Expression Score 是 0–100 的表达存在性置信分：越高只表示越确定该组织/时期有表达，不代表表达丰度或拷贝数。要比较丰度请看 Expression Atlas 的 TPM。">ⓘ</span></p>`
    : "";
  return lead
    + block("Bgee 组织 / 发育表达", ["bgee"],
        "单位：Expression Score（0–100，表达存在性置信分，<b>非丰度</b>）· 按分数排序",
        bgee, "/100", 100)
    + block("Expression Atlas 表达量", ["atlas"],
        "单位：TPM（转录本每百万映射读段数）· 以整胚发育时间序列为主，含少量标注组织样本",
        atlas, " TPM", 0);
}

function geneSearchSummary(g) {
  if (g.go_preview) return String(g.go_preview).slice(0, 80);
  const f = g.gene_function || "";
  if (f && !isGenericFunction(f)) return f.slice(0, 80);
  return "（点击详情查看 GO）";
}

function isGenericFunction(text) {
  if (!text) return true;
  const t = String(text).trim().toLowerCase();
  if (t.length < 40) return true;
  const generic = ["protein_coding", "lncrna", "mirna", "rrna", "ensembl", "unknown", "·"];
  return generic.some((g) => t.includes(g)) && !t.includes("tp53") && !t.includes("tumor");
}

function functionSummary(detail) {
  const b = detail.basic || {};
  const go = detail.go || [];
  const func = b.gene_function || "";
  const LANG_EN = (document.documentElement.getAttribute("lang") || "en").toLowerCase().indexOf("zh") !== 0;
  const T = (zh, en) => (LANG_EN ? en : zh);
  if (func && !isGenericFunction(func)) {
    // 只有真的超长才截断，并且带省略号——不能让人以为库里的数据本来就断了
    return func.length > 500 ? func.slice(0, 500).trimEnd() + "…" : func;
  }
  if (go.length) {
    const terms = go.slice(0, 3).map((r) => (r.go_term || "").trim()).filter(Boolean);
    if (terms.length) {
      const suffix = go.length > terms.length
        ? T(` 等共 ${go.length} 条 GO`, ` — ${go.length} GO terms in total`)
        : "";
      return terms.join(T("；", "; ")) + suffix;
    }
  }
  return func || T("（Ensembl 无详细功能文本，见下方 GO）", "(no function text in Ensembl — see GO below)");
}

/* GO 全量渲染：库里有多少条就列多少条。
   以前这里 slice(0,5) + 一个「…」，用户没法判断到底是省略还是解析不全；
   条数多时靠滚动容器收纳，而不是把数据丢掉。 */
function goListHtml(go) {
  const rows = (go || []).filter((r) => (r.go_term || "").trim());
  if (!rows.length) return "";
  const items = rows.map((r) => {
    const cat = String(r.go_category || "").trim().toUpperCase();
    const badge = cat ? `<span class="go-cat">${escapeHtml(cat)}</span>` : "";
    return `<div class="go-row">${badge}<span>${escapeHtml(r.go_term)}</span></div>`;
  }).join("");
  return `<div class="tool-go-list${rows.length > 14 ? " is-long" : ""}">${items}</div>`;
}

/* 其余字段（别名 / KEGG / 同源 / 文献 / 表达）全部塞进一个默认收起的折叠块：
   既不让结果卡变成长篇，也不再出现「完整数据见右侧」这种在工作台页并不成立的指路。 */
function geneMoreHtml(detail) {
  const b = detail.basic || {};
  const LANG_EN = (document.documentElement.getAttribute("lang") || "en").toLowerCase().indexOf("zh") !== 0;
  const T = (zh, en) => (LANG_EN ? en : zh);
  const sec = (title, inner) => (inner ? `<div class="tool-more-sec"><strong>${escapeHtml(title)}</strong>${inner}</div>` : "");

  const kegg = detail.kegg || [];
  const homo = detail.homology || [];
  const refs = detail.references || [];
  const aliases = ((detail.aliases || {}).items || []);
  const expr = (detail.expression || []).slice()
    .sort((x, y) => (Number(y.expression_level) || 0) - (Number(x.expression_level) || 0));

  const baseBits = [];
  if (b.interpro_domain) baseBits.push(`InterPro ${escapeHtml(b.interpro_domain)}`);
  if (b.uniprot_id) baseBits.push(`UniProt ${escapeHtml(b.uniprot_id)}`);
  if (b.expression_summary) baseBits.push(escapeHtml(b.expression_summary));

  const keggHtml = kegg.length ? `<div class="tool-mini-scroll"><ul class="tool-mini-list">${kegg.map((p) =>
    `<li><span class="mono">${escapeHtml(p.pathway_id || "")}</span> ${escapeHtml(p.pathway_name || "")}` +
    `${p.category ? ` <span class="muted">${escapeHtml(p.category)}</span>` : ""}</li>`).join("")}</ul></div>` : "";

  const homoHtml = homo.length ? `<p class="tool-mini-p">${homo.map((h) =>
    `${escapeHtml(h.species || "")}${T("：", ": ")}${escapeHtml(h.homo_gene_name || "")}` +
    `${h.homology_score != null ? ` (${Number(h.homology_score).toFixed(1)}%)` : ""}`).join(T("；", "; "))}</p>` : "";

  const refsHtml = refs.length
    ? `<div class="tool-mini-scroll"><ol class="tool-mini-list">${refs.map((r) => {
        const label = `[${escapeHtml(String(r.publish_year || ""))}] ${escapeHtml(r.title || "")}` +
          `${r.journal ? ` <span class="muted">${escapeHtml(r.journal)}</span>` : ""}`;
        const id = String(r.pubmed_id || "").trim();
        const linked = id && /^\d+$/.test(id) ? pubmedLink(id, `PMID ${id}`) : "";
        return `<li>${label}${linked ? ` <span class="muted">${linked}</span>` : ""}</li>`;
      }).join("")}</ol></div>`
    : "";

  const aliasHtml = aliases.length ? `<p class="tool-mini-p">${aliases.map((it) =>
    `<span class="gene-alias${it.is_primary ? " is-primary" : ""}" title="${escapeHtml(it.source_label || "")}">${escapeHtml(it.alias)}</span>`
  ).join("")}</p>` : "";

  const exprHtml = expr.length
    ? `<div class="tool-mini-scroll"><table class="tool-mini-table"><thead><tr>
         <th>${T("组织 / 时期", "Tissue / stage")}</th><th>${T("数值", "Value")}</th><th>${T("来源", "Source")}</th>
       </tr></thead><tbody>${expr.map((e) =>
        `<tr><td>${escapeHtml(e.tissue || "")}</td><td>${e.expression_level != null ? escapeHtml(String(e.expression_level)) : "-"}</td>` +
        `<td class="muted">${escapeHtml(e.data_source || "")}</td></tr>`).join("")}</tbody></table></div>`
    : "";

  const inner = (baseBits.length ? `<p class="tool-mini-p muted">${baseBits.join(" · ")}</p>` : "")
    + sec(T("别名", "Aliases"), aliasHtml)
    + sec("KEGG", keggHtml)
    + sec(T("同源", "Orthologs"), homoHtml)
    + sec(T("文献", "Literature"), refsHtml)
    + sec(T("表达", "Expression"), exprHtml);
  if (!inner) return "";
  return `<details class="tool-more" data-no-i18n="1">
      <summary>${T("更多注释：别名 · KEGG · 同源 · 文献 · 表达", "More: aliases · KEGG · orthologs · literature · expression")}</summary>
      ${inner}
    </details>`;
}

function renderToolGeneSummary(detail) {
  const b = detail.basic || {};
  /* 这张卡里带变量的文案没法走 i18n 词条表（词条只做整句精确匹配），
     所以统一用 T() 现场取词，避免英文界面漏中文。 */
  const LANG_EN = (document.documentElement.getAttribute("lang") || "en").toLowerCase().indexOf("zh") !== 0;
  const T = (zh, en) => (LANG_EN ? en : zh);
  const aliasNote = b.gene_symbol && b.gene_name !== b.gene_symbol
    ? `<div class="tool-gene-note">${T("查询已解析为官方基因", "Resolved to the official gene")} <strong>${escapeHtml(b.gene_name)}</strong>${T("（别名 ", " (alias ")}${escapeHtml(b.gene_symbol)}${T("）", ")")}</div>`
    : "";
  /* 坐标行（2026-10-28）：库表只有染色体号，起止来自注释目录（GRCz11 / Ensembl r116）。
     装配必须明写——GRCz11 的区间丢给默认 GRCz12tu 的取序列会取错位置。 */
  const loc = detail.locus;
  const hasLoc = loc && loc.start != null && loc.end != null;
  const locLine = hasLoc
    ? `<div class="meta">${T("位置", "Locus")} ${escapeHtml(loc.chromosome)}:${Number(loc.start).toLocaleString("en-US")}–${Number(loc.end).toLocaleString("en-US")} (${escapeHtml(loc.strand || "")}) · ${escapeHtml(loc.assembly)}</div>`
    : `<div class="meta muted">${T("位置：注释目录里没有该基因的坐标", "Locus: no coordinates for this gene in the catalog")}</div>`;
  /* 2026-10-31 用户要求：这一页只留「基因是谁、落在哪」。
     功能描述行、GO 折叠、别名跳转、更多注释折叠、View JSON 一律去掉（截图红框整块）。
     GO / 别名 / KEGG 等内容在各自的功能页里看，不在这里堆第二份。 */
  return `${aliasNote}
    <div class="tool-gene-summary">
      <div><strong>${escapeHtml(b.gene_name || "")}</strong>${b.gene_symbol && b.gene_symbol !== b.gene_name ? " " + escapeHtml(b.gene_symbol) : ""}</div>
      <div class="meta">Chr ${escapeHtml(b.chromosome || "-")} · ${escapeHtml(b.ensembl_id || "-")} · ${escapeHtml(b.zfin_id || "-")}</div>
      ${locLine}
    </div>`;
}

function renderGeneCard(detail) {
  const b = detail.basic || {};
  const LANG_EN = (document.documentElement.getAttribute("lang") || "en").toLowerCase().indexOf("zh") !== 0;
  const T = (zh, en) => (LANG_EN ? en : zh);
  const homo = (detail.homology || []).map((h) => `${h.species}:${h.homo_gene_name}(${h.homology_score}%)`).join("；");
  const refs = (detail.references || []).map((r) =>
    r.pubmed_id ? pubmedLink(r.pubmed_id, `[${r.publish_year || ""}] ${r.title || ""}`) : escapeHtml(r.title || "")
  ).join("<br>");
  const goAll = (detail.go || []).filter((r) => (r.go_term || "").trim());
  const goHtml = goListHtml(goAll);
  const ensUrl = b.ensembl_id ? `https://www.ensembl.org/Danio_rerio/Gene/Summary?g=${String(b.ensembl_id).split(".")[0]}` : null;
  const zfinUrl = b.zfin_id ? `https://zfin.org/${b.zfin_id}` : null;
  const std = detail.standard || {};
  const stdSame = std.gene_symbol && std.gene_symbol === b.gene_name;
  const stdLine = std.gene_symbol
    ? (stdSame
      ? ` · ${T("标准名版本", "standard build")} ${escapeHtml(std.assembly || "GRCz12tu")}`
      : ` · ${T("标准名", "standard")} ${escapeHtml(std.gene_symbol)}（${escapeHtml(std.assembly || "GRCz12tu")}）`)
    : "";

  // 任务①：别名（含历史名）
  const aliasItems = ((detail.aliases || {}).items || []);
  const aliasHtml = aliasItems.length
    ? aliasItems
        .map((it) =>
          `<span class="gene-alias${it.is_primary ? " is-primary" : ""}" title="${escapeHtml(it.source_label || "")}">${escapeHtml(it.alias)}</span>`
        )
        .join("")
    : escapeHtml(T("无记录", "none"));

  // 任务②：复制体 / 共直系同源
  const pg = detail.paralogs || {};
  const pgGroups = (pg.groups || []).filter((g) => (g.copies || []).length > 0);
  const pgHtml = pgGroups.length
    ? pgGroups
        .map((g) => {
          const copies = (g.copies || [])
            .map((c) => {
              const nm = escapeHtml(c.symbol || String(c.gene_id || ""));
              const sc = c.score != null ? `<small>${Number(c.score).toFixed(1)}</small>` : "";
              return `<span class="gene-copy${c.is_self ? " is-self" : ""}">${nm}${sc}</span>`;
            })
            .join("");
          const dup = g.duplicated
            ? `<em class="gene-dup-flag">${escapeHtml(T("一对多复制", "duplicated"))}</em>`
            : "";
          return `<div class="gene-ortho-group"><span class="gene-ortho-homo">${escapeHtml(g.homo_gene_name || "")}</span>` +
            `<small>${escapeHtml(g.species || "")}</small>${dup}<div class="gene-copies">${copies}</div></div>`;
        })
        .join("")
    : "";

  geneCard.innerHTML = `
    <div class="gene-title">${escapeHtml(b.gene_name || "")} <span>${escapeHtml(b.gene_symbol || "")}</span></div>
    <div class="gene-meta">Chr ${escapeHtml(b.chromosome || "-")} · ${ensUrl ? extLink(ensUrl, b.ensembl_id) : escapeHtml(b.ensembl_id || "-")} · ${zfinUrl ? extLink(zfinUrl, b.zfin_id) : escapeHtml(b.zfin_id || "-")}${stdLine}</div>
    <p class="gene-func">${escapeHtml(functionSummary(detail))}</p>
    ${b.expression_summary ? `<p class="gene-expr-sum">${escapeHtml(b.expression_summary)}</p>` : ""}
    <div class="gene-section"><strong>${T("别名（含历史名）", "Aliases (incl. historical)")}</strong><div class="gene-alias-wrap">${aliasHtml}</div></div>
    ${pgHtml ? `<div class="gene-section"><strong>${T("复制体 / 共直系同源", "Duplicates / co-orthologs")}</strong><div class="gene-ortho-wrap">${pgHtml}</div></div>` : ""}
    <div class="gene-section gene-expr-chart">${renderExpressionChart(detail.expression || [], undefined, (b.ensembl_id || "").split(".")[0])}</div>
    ${goHtml ? `<div class="gene-section"><strong>GO</strong> <span class="muted">共 ${goAll.length} 条</span><div class="gene-go">${goHtml}</div></div>` : ""}
    <div class="gene-section"><strong>${T("同源", "Orthologs")}</strong> ${escapeHtml(homo || T("无", "none"))}</div>
    <div class="gene-section"><strong>${T("文献", "Literature")}</strong><div class="gene-refs">${refs || T("无", "none")}</div></div>
  `;
}

function renderTable(rows) {
  const cols = Object.keys(rows[0] || {});
  const thead = `<tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr>`;
  const tbody = rows.map((row) => `<tr>${cols.map((c) => `<td>${escapeHtml(String(row[c] ?? ""))}</td>`).join("")}</tr>`).join("");
  dataTable.innerHTML = thead + tbody;
}

function setSending(v) {
  isSending = v;
  updateSendEnabled();
}

function updateSendEnabled() {
  // 有队列机制：即使正在回答/上一问 busy 也可输入并入队，按钮保持可用
  // DeepSeek 风格：发送按钮为 ↑ 图标，状态用 title/aria-label 提示
  const blocked = systemWarming || !lastHealthOk;
  sendBtn.disabled = blocked;
  let tip = "发送";
  if (systemWarming) {
    tip = "预热中请稍候";
  } else if (isSending || queueProcessing) {
    tip = "处理中…可继续排队";
  } else if (chatBusy) {
    tip = chatQueued > 0 ? `服务并行已满（${chatQueued} 人在排队），可直接发送` : "服务并行已满（可直接发送，自动排队）";
  }
  sendBtn.title = tip;
  sendBtn.setAttribute("aria-label", tip);
  sendBtn.classList.toggle("busy", blocked || isSending || queueProcessing || chatBusy);
}

function escapeHtml(str) {
  return String(str).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

/* ============ 轻量 Markdown 渲染（安全，仅用于 AI 回答气泡） ============ */
function mdEscape(s) {
  return String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function mdInline(s) {
  let t = mdEscape(s);
  // 行内代码
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  // 加粗 **x** 或 __x__
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // 斜体 *x*
  t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  /* 链接：先把 markdown 语法 [文字](url) 与裸 URL 都抽成占位符，
     最后统一还原成 <a>。这样裸 URL 也能点（用户反馈「参考文献的链接不能点」，
     根因就是以前只认 [文字](url)，正文里直接写的 https://pubmed... 是死文本）。
     占位符用 \u0000 包住，不会和正文冲突。 */
  const links = [];
  const stash = (label, url) => {
    links.push({ label: label, url: url });
    return "\u0000L" + (links.length - 1) + "\u0000";
  };
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|#\/[^)\s]+)\)/g, (m, label, url) => stash(label, url));
  // 裸 URL：止于空白、引号、尖括号或中文标点，避免把句末的 。）吃进去
  t = t.replace(/https?:\/\/[^\s<>"'\u3000-\u303f\uff00-\uffef]+/g, (m) => stash(m, m));
  return t.replace(/\u0000L(\d+)\u0000/g, (m, i) => {
    const it = links[Number(i)];
    if (!it) return m;
    const external = String(it.url).startsWith("http");
    const rel = external ? ' target="_blank" rel="noopener"' : "";
    const cls = external ? "md-a md-a-ext" : "md-a md-a-local";
    // url 取自 mdEscape 之后的文本，已经是可放进属性的形态，不要再转义一次
    return `<a class="${cls}" href="${it.url}"${rel}>${it.label}</a>`;
  });
}

function mdToHtml(text) {
  // 展示层兜底：绝不显示「本地文献库 PubmedYes」
  let src = String(text || "").replace(/\r\n/g, "\n").replace(/本地文献库\s*PubmedYes/gi, "本地文献库");
  const lines = src.split("\n");
  const out = [];
  let i = 0;
  let inTable = false;
  let tableRows = [];
  let inCode = false;
  let codeBuf = [];

  const flushTable = () => {
    if (!tableRows.length) return;
    let html = '<table class="md-table">';
    tableRows.forEach((row, ri) => {
      html += "<tr>";
      row.forEach((cell) => {
        const tag = ri === 0 ? "th" : "td";
        html += `<${tag}>${mdInline(cell.trim())}</${tag}>`;
      });
      html += "</tr>";
    });
    html += "</table>";
    out.push(html);
    tableRows = [];
    inTable = false;
  };

  for (; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // 代码块
    if (trimmed.startsWith("```")) {
      if (inCode) {
        out.push(`<pre class="md-code"><code>${codeBuf.join("\n")}</code></pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        flushTable();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeBuf.push(mdEscape(line));
      continue;
    }

    // 表格分隔行（|---|）
    if (/^\|[\s:|-]+\|$/.test(trimmed) && tableRows.length) {
      continue; // 跳过分隔行
    }
    // 表格行
    if (trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.includes("|")) {
      if (!inTable) { inTable = true; tableRows = []; }
      tableRows.push(trimmed.slice(1, -1).split("|"));
      continue;
    } else {
      flushTable();
    }

    // 空行
    if (!trimmed) { out.push(""); continue; }

    // 参考文献块：独立展示；兼容「参考文献（由系统生成）：」及同义标题
    // 密集同行引用（标题后紧跟 [n]）也并入卡片；同一回答只保留一个 .md-refs
    {
      const refHeadRe = /^(#{1,4}\s*)?(?:\*\*)?(参考文献|参考资料|引用文献|文献列表|参考研究|References|Bibliography)(?:\*\*)?(?:\s*[（(][^）)\n]{0,40}[）)])?(?:\*\*)?[：:]?\s*(.*)$/i;
      const rm = refHeadRe.exec(trimmed);
      const restAfterHead = rm ? String(rm[3] || "").trim() : "";
      const isRefHead = !!(rm && (!restAfterHead || /^\[\d/.test(restAfterHead)));
      if (isRefHead) {
        flushTable();
        if (out.some((x) => typeof x === "string" && x.includes('class="md-refs"'))) {
          // 已有卡片：吞掉第二段标题及后续文献行，避免双区
          i += 1;
          for (; i < lines.length; i++) {
            const rl = lines[i];
            if (!rl.trim()) continue;
            if (/^(#{1,4}\s+)/.test(rl.trim()) && !/^\[/.test(rl.trim())) break;
          }
          i -= 1;
          continue;
        }
        const refLines = [];
        if (restAfterHead) refLines.push(restAfterHead);
        i += 1;
        for (; i < lines.length; i++) {
          const rl = lines[i];
          if (!rl.trim()) {
            if (refLines.length) refLines.push("");
            continue;
          }
          const t2 = rl.trim();
          const m2 = refHeadRe.exec(t2);
          if (m2) {
            const r2 = String(m2[3] || "").trim();
            if (!r2 || /^\[\d/.test(r2)) {
              if (r2) refLines.push(r2);
              continue;
            }
          }
          if (/^(#{1,4}\s+)/.test(t2) && !/^\[/.test(t2)) break;
          refLines.push(rl);
        }
        i -= 1;
        const items = refLines
          .filter((x) => x.trim())
          .map((x) => `<div class="md-ref-line">${mdInline(x.trim())}</div>`)
          .join("");
        out.push(`<section class="md-refs"><h3 class="md-h3" data-i18n-live="1">参考文献</h3>${items}</section>`);
        continue;
      }
    }
    // 标题
    const h = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (h) {
      const level = h[1].length;
      out.push(`<h${level} class="md-h${level}">${mdInline(h[2])}</h${level}>`);
      continue;
    }
    // 无序列表
    if (/^[-*]\s+/.test(trimmed)) {
      out.push(`<li class="md-li">${mdInline(trimmed.replace(/^[-*]\s+/, ""))}</li>`);
      continue;
    }
    // 有序列表
    if (/^\d+[.、]\s+/.test(trimmed)) {
      out.push(`<li class="md-li">${mdInline(trimmed.replace(/^\d+[.、]\s+/, ""))}</li>`);
      continue;
    }
    // 引用
    if (trimmed.startsWith(">")) {
      out.push(`<blockquote class="md-quote">${mdInline(trimmed.replace(/^>\s?/, ""))}</blockquote>`);
      continue;
    }
    // 分隔线
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
      out.push('<hr class="md-hr">');
      continue;
    }
    // 普通段落
    out.push(`<p class="md-p">${mdInline(line)}</p>`);
  }
  flushTable();
  if (inCode && codeBuf.length) {
    out.push(`<pre class="md-code"><code>${codeBuf.join("\n")}</code></pre>`);
  }
  // 列表合并成 ul/ol
  const final = [];
  let listBuf = [];
  const flushList = () => {
    if (!listBuf.length) return;
    const allOl = listBuf.every((x) => x[0]);
    const items = listBuf.map((x) => x[1]);
    final.push(allOl ? `<ol class="md-list">${items.join("")}</ol>` : `<ul class="md-list">${items.join("")}</ul>`);
    listBuf = [];
  };
  out.forEach((block) => {
    if (block.startsWith('<li class="md-li">')) {
      const ordered = /^\d+[.、]\s+/.test(block);
      listBuf.push([ordered, block]);
    } else {
      flushList();
      final.push(block);
    }
  });
  flushList();
  return final.join("\n");
}

async function loadHealth() {
  try {
    const res = await apiFetch("/api/health");
    const data = await parseJsonResponse(res, res.url || "");
    lastHealthOk = true;
    // 文献 FAISS 预热/失败不再锁死发送：基因卡、统计、界面说明等都不依赖 PubmedYes
    systemWarming = !!(data.models_warming || data.rag_warming || data.heavy_warming);
    chatBusy = !!data.chat_busy;
    chatQueued = Number(data.chat_queued || 0);
    chatRunning = Number(data.chat_running || 0);
    if (!chatBusy && pendingQueue.length && !isSending && !queueProcessing) {
      processQueue();
    }
    updateSendEnabled();

    /* 2026-10-31：这里原来只看 data.warmup_error 就一票否决，写了「加载失败」就 return。
       但实际上 warmup_error 只是【本地轻量模型】加载失败（transformers 的
       "Cannot copy out of meta tensor"，通常是内存打满时 meta 权重没实化）。
       本服务的推理有远程通道（reasoning_remote / multi_model_ready，实测走 deepseek-chat），
       本地模型挂了照样能回答问题 —— 线上 chat_completed 一直在涨，页面却永远停在
       「加载失败」，属于误报。现在改成：只有在「既没有可用的推理通道、核心库也不可用」
       时才算真失败；否则继续走下面的正常状态判断，把失败原因挂到 title 里备查。 */
    // 每次刷新先清掉上一轮挂的说明，避免本地模型恢复后 title 还留着旧错误
    statusBar.removeAttribute("title");
    if (data.warmup_error) {
      const canAnswer = !!(
        data.multi_model_ready || data.reasoning_remote ||
        data.light_model || data.light_file
      );
      const coreUsable = !!(data.mysql || data.rag);
      if (!canAnswer && !coreUsable) {
        statusBar.innerHTML = `<span class="badge warn" title="${escapeHtml(String(data.warmup_error)).slice(0, 300)}">加载失败</span>`;
        return;
      }
      const why = String(data.warmup_error).split(";")[0].slice(0, 120);
      statusBar.title = `本地模型未就绪（已自动改用远程推理）：${why}`;
    }

    const coreReady = !!(data.mysql && data.rag);
    const litReady = !!data.pubmed_ready;
    const litFailed = !!(data.pubmed_warmup_error && !data.pubmed_ready && !data.pubmed_warming);
    const reasonReady = !!(data.multi_model_ready || data.reasoning_remote || data.light_model || data.light_file);
    const warming = !!(data.models_warming || data.pubmed_warming || data.rag_warming || data.heavy_warming);
    const coreOk = coreReady && reasonReady && !data.models_warming && !data.rag_warming && !data.heavy_warming;

    if (data.chat_busy) {
      const sec = data.chat_busy_seconds || 0;
      const q = chatQueued > 0 ? ` · ${chatQueued} 位排队` : "";
      statusBar.innerHTML = `<span class="badge pending">处理中${sec ? ` ${sec}s` : ""}${q}</span>`;
    } else if (chatRunning > 0) {
      statusBar.innerHTML = `<span class="badge ok">服务在线 · ${chatRunning} 人问答中</span>`;
    } else if (coreOk && litReady) {
      statusBar.innerHTML = `<span class="badge ok">加载完毕</span>`;
    } else if (coreOk && litFailed) {
      statusBar.innerHTML = `<span class="badge warn">文献暂不可用·其它可问</span>`;
    } else if (warming || data.pubmed_warming) {
      // 核心（MySQL + RAG）已可用时，不必把“预热中”写成“仍在加载”吓人：
      // 状态栏给绿点，具体在预热哪一块放到 title 里。
      const parts = [];
      if (data.models_warming) parts.push("模型");
      if (data.rag_warming) parts.push("检索库");
      if (data.pubmed_warming) parts.push("文献库");
      if (data.heavy_warming) parts.push("其他重资源");
      const tip = (parts.length ? parts.join(" / ") + "预热中" : "资源预热中") + "，问答与工具均可正常使用";
      statusBar.innerHTML = coreReady
        ? `<span class="badge ok" title="${tip}">服务在线</span>`
        : `<span class="badge pending">仍在加载…</span>`;
    } else if (coreOk) {
      statusBar.innerHTML = `<span class="badge ok">可提问</span>`;
    } else {
      statusBar.innerHTML = `<span class="badge pending">仍在加载…</span>`;
    }
  } catch {
    lastHealthOk = false;
    statusBar.innerHTML = `<span class="badge pending">仍在加载…</span>`;
    updateSendEnabled();
  }
}

function modelBadge(label, loaded, fileExists) {
  if (loaded || fileExists) {
    return `<span class="badge ok">${label} 就绪</span>`;
  }
  return `<span class="badge warn">${label} 缺失</span>`;
}

async function loadDbStats() {
  // 状态栏已收敛为「加载完毕 / 仍在加载」，统计条不再堆砌细节
  if (dbStats) dbStats.innerHTML = "";
}

function badge(label, ok) {
  return `<span class="badge ${ok ? "ok" : "warn"}">${label} ${ok ? "就绪" : "未就绪"}</span>`;
}

const retrievalQuery = document.getElementById("retrievalQuery");
const retrievalBtn = document.getElementById("retrievalBtn");
const retrievalMaxChars = document.getElementById("retrievalMaxChars");
const retrievalTopK = document.getElementById("retrievalTopK");
const retrievalSummary = document.getElementById("retrievalSummary");
const retrievalRankWrap = document.getElementById("retrievalRankWrap");
const retrievalRankTable = document.getElementById("retrievalRankTable");
const retrievalPackWrap = document.getElementById("retrievalPackWrap");
const retrievalPackTable = document.getElementById("retrievalPackTable");
const retrievalCtxPrev = document.getElementById("retrievalCtxPrev");

function _escHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function _fmtScore(x) {
  if (x === null || x === undefined || x === "") return "-";
  return typeof x === "number" ? x.toFixed(4) : String(x);
}

function _hitId(r) {
  if (r.pmid) return `PMID:${r.pmid}`;
  if (r.entry_no) return `ENO:${r.entry_no}`;
  return r.doc_id || "-";
}

retrievalBtn?.addEventListener("click", async () => {
  const q = (retrievalQuery?.value || questionInput?.value || "").trim();
  if (!q || retrievalBtn.disabled) return;
  const prefs = snapshotAnswerPrefs();
  retrievalBtn.disabled = true;
  if (retrievalSummary) retrievalSummary.textContent = "检索中（含重排，可能需数十秒）…";
  retrievalRankWrap?.classList.add("hidden");
  retrievalPackWrap?.classList.add("hidden");
  retrievalCtxPrev?.classList.add("hidden");
  try {
    const maxChars =
      Number(retrievalMaxChars?.value) ||
      (prefs.depth === "partial" ? 24000 : 48000);
    const topK = Number(retrievalTopK?.value) || 100;
    const candidatePreview = prefs.depth === "partial" ? 20 : 40;

    // 当前芯片为检索调试：走与聊天同一路径，结果进聊天/回答详情
    if (prefs.scope === "retrieval_debug") {
      const chatData = await runScopedChatRequest(q, prefs);
      publishScopedResult(q, chatData, prefs);
      const dbg = chatData.retrieval_debug || {};
      if (retrievalSummary) {
        retrievalSummary.innerHTML =
          `【${prefs.depthLabel} · ${prefs.scopeLabel}】` +
          `候选 <b>${dbg.candidates_before_rerank ?? "-"}</b>` +
          ` · 方法 <b>${_escHtml(dbg.rerank_method || "-")}</b>` +
          ` · AI 预算 <b>${maxChars}</b>`;
      }
      if (retrievalCtxPrev && (chatData.context || chatData.answer)) {
        retrievalCtxPrev.textContent = String(chatData.context || chatData.answer).slice(0, 2000);
        retrievalCtxPrev.classList.remove("hidden");
      }
      return;
    }

    const res = await apiFetch("/api/retrieval/debug", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: q,
        max_chars: maxChars,
        top_k: topK,
        candidate_preview: candidatePreview,
        answer_depth: prefs.depth,
      }),
    });
    const data = await parseJsonResponse(res, "/api/retrieval/debug");
    if (!res.ok) throw new Error(data.detail || "检索调试失败");
    if (retrievalSummary) {
      // 位次是怎么来的必须写出来：最终排序 = 相关度 + 年份（+ 期刊 IF），
      // 所以「第 4 条的 rerank 分比第 1 条高」不是 bug，是年份权重把它压下去了。
      // 不解释，用户只会认定排序错了。
      const bw = (data.ranked && data.ranked[0] && data.ranked[0].blend_weights) || null;
      const blendTxt = bw
        ? ` · 位次 = 相关度 <b>${Math.round(bw.rel * 100)}%</b> + 年份 <b>${Math.round(bw.year * 100)}%</b>` +
          (bw.if ? ` + 期刊 <b>${Math.round(bw.if * 100)}%</b>` : "")
        : "";
      retrievalSummary.innerHTML =
        `【${prefs.depthLabel} · ${prefs.scopeLabel}】` +
        `候选 <b>${data.candidates_before_rerank ?? "-"}</b> → 重排 <b>${data.after_rerank_top_n ?? "-"}</b>` +
        ` · 方法 <b>${_escHtml(data.rerank_method)}</b> · ${data.elapsed_sec}s` +
        ` · AI 打包 <b>${data.hub_pack?.chunks_included ?? 0}</b> 块 / ` +
        `<b>${data.hub_pack?.total_chars ?? 0}</b> 字（预算 ${data.hub_pack?.max_chars ?? maxChars}）` +
        blendTxt;
    }
    if (retrievalRankTable && data.ranked) {
      retrievalRankTable.innerHTML =
        "<thead><tr><th>#</th><th>PMID/ENO</th><th>field</th><th>raw_L2</th><th>adj</th><th>rerank</th><th>preview</th></tr></thead><tbody>" +
        data.ranked.map((r) =>
          `<tr><td>${r.rank}</td><td>${_escHtml(_hitId(r))}</td><td>${_escHtml(r.field)}</td>` +
          `<td>${_fmtScore(r.raw_L2)}</td><td>${_fmtScore(r.adj_L2)}</td><td>${_fmtScore(r.rerank_score)}</td>` +
          `<td>${_escHtml(r.preview)}</td></tr>`
        ).join("") +
        "</tbody>";
      retrievalRankWrap?.classList.remove("hidden");
    }
    if (retrievalPackTable && data.hub_pack?.order) {
      retrievalPackTable.innerHTML =
        "<thead><tr><th>order</th><th>rank</th><th>tag</th><th>chars</th><th>preview</th></tr></thead><tbody>" +
        data.hub_pack.order.map((o) =>
          `<tr><td>${o.order}</td><td>${o.rank}</td><td>${_escHtml(o.tag)}</td>` +
          `<td>${o.block_chars}</td><td>${_escHtml(o.preview)}</td></tr>`
        ).join("") +
        "</tbody>";
      retrievalPackWrap?.classList.remove("hidden");
    }
    if (retrievalCtxPrev) {
      retrievalCtxPrev.textContent = data.hub_pack?.context_preview_head || "";
      retrievalCtxPrev.classList.remove("hidden");
    }
    detailEmpty.classList.add("hidden");
    detailContent.classList.remove("hidden");
    detailTaskType.textContent = `retrieval_debug · ${prefs.depthLabel} · ${prefs.scopeLabel}`;
    if (data.hub_pack?.context_preview_head) {
      contextBlock.classList.remove("hidden");
      detailContext.textContent = data.hub_pack.context_preview_head;
    }
  } catch (err) {
    if (retrievalSummary) retrievalSummary.textContent = String(err.message || err);
  } finally {
    retrievalBtn.disabled = false;
  }
});

retrievalMaxChars?.addEventListener("change", () => {
  if (retrievalMaxChars) retrievalMaxChars.dataset.userEdited = "1";
});
retrievalMaxChars?.addEventListener("input", () => {
  if (retrievalMaxChars) retrievalMaxChars.dataset.userEdited = "1";
});

retrievalQuery?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    retrievalBtn?.click();
  }
});

loadHealth();
loadDbStats();
initAuth();
loadWorkflowPresets();
refreshTenantHint();

// ===== Hub 页面升级：右侧面板折叠 / 展开 =====
(function initPanelCollapse() {
  const detailPanel = document.querySelector(".detail-panel");
  if (!detailPanel) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.id = "panelCollapseBtn";
  btn.className = "panel-collapse-btn";
  btn.setAttribute("aria-label", "折叠/展开右侧面板");
  btn.textContent = "▸";
  detailPanel.prepend(btn);
  const apply = (collapsed) => {
    document.body.classList.toggle("side-collapsed", collapsed);
    btn.textContent = collapsed ? "◂" : "▸";
    btn.title = collapsed ? "展开右侧面板" : "折叠右侧面板";
  };
  btn.addEventListener("click", () => apply(!document.body.classList.contains("side-collapsed")));
})();

async function refreshTenantHint() {
  if (!tenantHint) return;
  try {
    const res = await apiFetch("/api/tenants/current");
    if (!res.ok) return;
    const t = await parseJsonResponse(res, res.url || "");
    currentTenantSlug = t.slug || "default";
    sessionStorage.setItem(TENANT_KEY, currentTenantSlug);
    tenantHint.textContent = `租户：${t.name || t.slug} (${t.slug})`;
  } catch {
    tenantHint.textContent = "租户：默认实验室 (default)";
  }
}

async function loadWorkflowPresets() {
  if (!workflowPreset) return;
  _syncingAnswerPrefs = true;
  try {
    const res = await apiFetch("/api/workflows/presets");
    if (!res.ok) return;
    const data = await parseJsonResponse(res, res.url || "");
    if (data.preset_by_scope) {
      Object.assign(SCOPE_TO_PRESET, data.preset_by_scope);
    }
    if (data.scope_by_preset) {
      Object.assign(PRESET_TO_SCOPE, data.scope_by_preset);
    }
    workflowPreset.innerHTML = "";
    (data.presets || []).forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.slug;
      opt.textContent = `${p.name} — ${p.description || p.slug}`;
      workflowPreset.appendChild(opt);
    });
    const want = "full_hub";
    if (Array.from(workflowPreset.options).some((o) => o.value === want)) {
      workflowPreset.value = want;
    } else if (data.presets?.length) {
      const hub = (data.presets || []).find((p) => p.slug === "full_hub") || data.presets[0];
      workflowPreset.value = hub.slug;
    }
    currentScope = "hub";
    currentDepth = "full";
    try {
      sessionStorage.setItem(SCOPE_KEY, "hub");
      sessionStorage.setItem(DEPTH_KEY, "full");
    } catch {}
    applyScopeUI();
  } catch {
    workflowPreset.innerHTML = '<option value="full_hub">Hub 全链路</option><option value="literature_only">仅文献 RAG</option>';
    workflowPreset.value = "full_hub";
    currentScope = "hub";
    applyScopeUI();
  } finally {
    _syncingAnswerPrefs = false;
  }
}

function renderWorkflowSteps(steps) {
  if (!workflowSteps) return;
  workflowSteps.innerHTML = "";
  if (!steps?.length) {
    workflowSteps.classList.add("hidden");
    return;
  }
  workflowSteps.classList.remove("hidden");
  steps.forEach((s) => {
    const li = document.createElement("li");
    li.className = `agent-step ${s.status === "ok" ? "ok" : s.status === "error" ? "err" : "miss"}`;
    li.innerHTML = `<span class="agent-name">${s.label || s.node_type}</span><span class="agent-sum">${s.summary || ""}</span><span class="agent-ms">${s.elapsed_ms || 0}ms</span>`;
    workflowSteps.appendChild(li);
  });
}

workflowPreset?.addEventListener("change", () => {
  if (_syncingAnswerPrefs) return;
  // 只改工作流运行预置，绝不改聊天 answer_scope（否则会把 Hub 多Agent 绑成仅 ID 映射）
  updateSyncHint();
});

workflowRunBtn?.addEventListener("click", async () => {
  const text = workflowInput?.value?.trim();
  const slug = workflowPreset?.value || SCOPE_TO_PRESET[currentScope];
  if (!text || !slug || workflowRunBtn.disabled) return;
  const prefs = snapshotAnswerPrefs();
  // 下拉与芯片不一致时以芯片为准，并纠正下拉
  const alignedSlug = SCOPE_TO_PRESET[prefs.scope] || slug;
  if (workflowPreset && workflowPreset.value !== alignedSlug) {
    _syncingAnswerPrefs = true;
    try {
      if (Array.from(workflowPreset.options).some((o) => o.value === alignedSlug)) {
        workflowPreset.value = alignedSlug;
      }
    } finally {
      _syncingAnswerPrefs = false;
    }
  }
  workflowRunBtn.disabled = true;
  workflowOutput?.classList.add("hidden");
  renderWorkflowSteps([{
    label: "执行中",
    node_type: "run",
    status: "miss",
    summary: `${prefs.depthLabel} · ${prefs.scopeLabel}`,
    elapsed_ms: 0,
  }]);
  try {
    const res = await apiFetch("/api/workflows/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        preset_slug: alignedSlug,
        input_text: text,
        answer_scope: prefs.scope,
        answer_depth: prefs.depth,
        usage_strategy: prefs.depth,
      }),
    });
    const data = await parseJsonResponse(res, "/api/workflows/run");
    if (!res.ok) throw new Error(data.detail || "工作流失败");
    renderWorkflowSteps(data.run?.steps || []);
    const chat = data.chat || data.output || {};
    const answer = chat.answer || data.output?.answer || data.run?.output?.answer;
    if (workflowOutput && answer) {
      workflowOutput.textContent = answer;
      workflowOutput.classList.remove("hidden");
    }
    publishScopedResult(text, {
      ...chat,
      answer: answer || `[工作流 ${alignedSlug}] 已完成，见右侧步骤详情。`,
      answer_scope: data.answer_scope || chat.answer_scope || prefs.scope,
      answer_depth: data.answer_depth || chat.answer_depth || prefs.depth,
    }, prefs);
  } catch (err) {
    renderWorkflowSteps([{
      label: "错误",
      node_type: "error",
      status: "error",
      summary: String(err.message || err),
      elapsed_ms: 0,
    }]);
    appendMessage("bot", `工作流失败：${err.message || err}`);
  } finally {
    workflowRunBtn.disabled = false;
  }
});

// 启动后持续刷新，直到模型 + 文献 FAISS 预热完成
let _healthPolls = 0;
const _healthTimer = setInterval(async () => {
  await loadHealth();
  _healthPolls += 1;
  try {
    const res = await apiFetch("/api/health");
    const data = await parseJsonResponse(res, res.url || "");
    if (
      !data.models_warming
      && !data.rag_warming
      && !data.heavy_warming
      && !data.pubmed_warming
      && (data.light_model || data.light_file || data.pubmed_ready || _healthPolls >= 120)
    ) {
      clearInterval(_healthTimer);
      // 预热结束后仍低频刷新，便于显示 chat_busy
      setInterval(loadHealth, 8000);
    }
  } catch {
    if (_healthPolls >= 90) {
      clearInterval(_healthTimer);
      setInterval(loadHealth, 10000);
    }
  }
}, 2000);


/* ===== 一功能一界面：首页 / 问答 / 各工具独立页 ===== */
(function initHubShell() {
  /* 每页 KPI（hero 右侧大数字）：数据都是本库实测值；标签用「中文|English」表示 */
const PAGE_KPIS = {
  genomes: [["对比装配|Builds compared", "GRCz11 / GRCz12tu"], ["序列数|Sequences", "25 / 25"],
    ["总长度|Total length", "1.45 Gb"], ["平均 GC|Mean GC", "37.11%"],
    ["对照级别|Comparison", "长度级|Length only"]],
  expression: [["表达记录|Rows", "319,344"], ["基因数|Genes", "24,245"], ["组织|Tissues", "1,151"],
    ["Bgee 单位|Bgee unit", "score 0–100"], ["Atlas 单位|Atlas unit", "TPM"]],
  phenotype: [["注释条目|Annotations", "171,079"], ["覆盖基因|Genes", "8,029"],
    ["术语组合|Term pairs", "20,971"], ["索引|Index", "SQLite · ready"], ["数据源|Source", "ZFIN"]],
  gene: [["基因库|Genes", "32,564"], ["别名|Aliases", "145,439"], ["同源|Orthologs", "31,630"],
    ["坐标|Coordinates", "GRCz11 r116"], ["符号|Symbols", "GRCz12tu"]],
  genes: [["基因库|Genes", "32,564"], ["别名索引|Alias index", "145,439"],
    ["可检索 ID|Searchable IDs", "ZFIN / NCBI / UniProt"], ["染色体|Chromosomes", "1–25 + scaffolds"]],
  blast: [["引擎|Engine", "BLAST+ blastn"], ["批量|Batch", "≤20 条|≤20 queries"],
    ["数据库|Database", "GRCz12tu RefSeq"], ["跨装配回退|Cross-assembly", "关闭|Off"]],
  crispr: [["引擎|Engine", "local rules"], ["PAM", "NGG / NAG / TTTV / NNGRRT"],
    ["脱靶库|Off-target DBs", "cdna / genome"], ["打分维度|Scoring", "GC · poly-T · structure"]],
  enrich: [["统计|Statistics", "Fisher + BH"], ["背景集|Background", "annotated genes"],
    ["通路条目|Pathway rows", "36,425"]],
  kegg: [["通路编号|Pathway IDs", "KEGG dre / WikiPathways"], ["基因–通路|Gene–pathway", "36,425"],
    ["GO 条目|GO annotations", "113,410"]],
  idresolve: [["覆盖库|Databases", "Ensembl / ZFIN / NCBI / UniProt"],
    ["消歧|Resolution", "unique / 1-to-many / none"], ["映射表|xref rows", "22,721"]],
  annotate: [["汇总|Aggregates", "identity · expression · GO · pathways"], ["缺失值|Missing", "“no record”"]],
  sequence: [["序列来源|Source", "GRCz12tu (RefSeq)"], ["按坐标|By coordinates", "chr:start-end"],
    ["支持区段|Regions", "cDNA / promoter / genomic / region"]],
  retrieval: [["文献库|Library", "32,549 records"], ["全文金标|Full-text", "29,221"],
    ["粗排→精排|Coarse→rerank", "500 → 100"], ["打包|Pack", "48,000 chars"], ["分片|Shards", "170"]],
  upload: [["格式|Types", "PDF / TXT / MD"], ["上限|Limit", "5 MB"],
    ["切分|Chunking", "360 / 110 · overlap 48"]],
  ops: [["定时任务|Cron jobs", "literature · gallery · stats · guard"], ["隐私|Privacy", "无 IP 明细|no IP details"]],
  about: [["快照|Snapshot", "2026-10-15"], ["数据源|Sources", "Ensembl / NCBI / ZFIN / Bgee / KEGG"]]
};
PAGE_KPIS.literature = PAGE_KPIS.retrieval;
/* 2026-10-28 导航重组：一个面板可能对应多条路由（Go 复用 enrich、sgRNA/引物复用 crispr 等），
   KPI 按路由取，缺条目就回落到原面板的 KPI，hero 右侧不会空一块。 */
PAGE_KPIS.liftover = [];
PAGE_KPIS["sequence-region"] = PAGE_KPIS.sequence;
PAGE_KPIS.go = PAGE_KPIS.enrich;
PAGE_KPIS.sgrna = PAGE_KPIS.crispr;
PAGE_KPIS.primer = [
  ["引擎|Engine", "local rules + specificity"],
  ["产物长度|Amplicon", "80–300 bp（可调）"],
  ["特异性|Specificity", "cdna / genome"],
  ["打分维度|Scoring", "Tm · GC · dimer · 3′ end"]
];
PAGE_KPIS["phenotype-gene"] = PAGE_KPIS.phenotype;
PAGE_KPIS["phenotype-term"] = PAGE_KPIS.phenotype;
PAGE_KPIS.news = PAGE_KPIS.retrieval;

/* 当前功能页 key：语言切换时用它重渲染 hero 右侧 KPI（唯一来源，不再依赖 defer 脚本） */
let lastFeatureKey = null;

/* 功能页页脚：不再放各页自己的口径注释，统一指向「使用说明」。
   页脚只有一行链接，是「书面注释搬进说明页」的落点标记。 */
function renderFeatureFoot() {
  const foot = document.getElementById("featureFootNote");
  if (!foot) return;
  const lang = (document.documentElement.getAttribute("lang") || "en").toLowerCase();
  const zh = lang.indexOf("zh") === 0;
  foot.innerHTML = '<a class="feature-foot-link" href="#/guide" data-guide-open>' +
    (zh ? "每个数字怎么算 · 使用说明" : "How each number is computed · Guide") +
    ' <span aria-hidden="true">→</span></a>';
}

function renderFeatureKpis(feat) {
  const host = document.getElementById("featureKpis");
  if (!host) return;
  /* 2026-10-30 用户要求：功能页 hero 右侧那组 KPI 数字（「基因库 32,564 / 别名 145,439 / …」）
     全站下线（含 Liftover 页）。数据本身没有删除，PAGE_KPIS 仍留着；
     以后想恢复某页，把路由加进下面这份白名单即可。 */
  const KEEP = new Set();
  if (!KEEP.has(feat)) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  const rows = PAGE_KPIS[feat];
  if (!rows || !rows.length) { host.hidden = true; host.innerHTML = ""; return; }
  const lang = (document.documentElement.getAttribute("lang") || "en").toLowerCase();
  const zh = lang.indexOf("zh") === 0;
  host.innerHTML = rows.slice(0, 6).map((row) => {
    const parts = String(row[0]).split("|");
    const label = zh ? parts[0] : (parts[1] || parts[0]);
    // 取值同样支持「中文|English」，否则英文界面里会出现中文数字（如「不做（无链文件）」）
    const vparts = String(row[1]).split("|");
    const value = zh ? vparts[0] : (vparts[1] || vparts[0]);
    return '<div class="fk-item"><span class="fk-v">' + escapeHtml(value) +
      '</span><span class="fk-k">' + escapeHtml(label) + '</span></div>';
  }).join("");
  host.hidden = false;
}

const PAGE_META = {
    /* 字段用「中文|English」：界面语言由 openToolPage 现场拆，
       不再依赖 i18n 词条表——改文案时不会再出现「英文界面漏一句中文」。 */
    home: { kind: "home" },
    chat: { kind: "chat" },
    guide: { kind: "guide" },
    gene: {
      kind: "tool",
      tool: "gene",
      panel: "toolGene",
      title: "基因位置查找|Gene locus",
      // 2026-10-31 边界重划：本页只答「它在哪」——坐标、链、基因体序列。
      // 「这个名称是哪个基因」属于别名页，故不再提关键词检索。
      lead: "给一个基因名，看它在基因组上的落点：坐标、链、基因体序列。|Give a gene name and see where it sits: coordinates, strand and gene-body sequence.",
      pillA: "GRCz11 坐标|GRCz11 coords",
      pillB: "GRCz12tu cDNA|GRCz12tu cDNA",
      formHint: "基因名，如 tp53|Gene name, e.g. tp53",
      emptyTitle: "输入一个基因名|Enter a gene name",
      emptySub: "试 tp53、sox9b，或 ZDB-GENE-…|Try tp53, sox9b or ZDB-GENE-…",
    },
    genes: {
      kind: "tool",
      tool: "genes",
      panel: "toolGenes",
      title: "基因别名|Gene aliases",
      // 2026-10-31 边界重划：本页答「这个名字是哪个基因 / 它还有哪些名字」，
      // 所以是模糊检索 + 候选列表；坐标与基因体在「位置查找」。
      lead: "给一个名称（符号、别名、旧名或跨库 ID），确认它是哪个基因、还有哪些叫法。|Give a name (symbol, alias, former name or cross-DB ID) and see which gene it is and what else it is called.",
      pillA: "GRCz12tu 符号|GRCz12tu symbols",
      pillB: "Alias · Ortholog|Alias · Ortholog",
      formHint: "符号 / 别名 / 旧名 / 跨库 ID|Symbol / alias / former name / cross-DB ID",
      emptyTitle: "输入一个名称|Enter a name",
      emptySub: "试 p53、tp53，或粘一个 Ensembl / ZFIN ID|Try p53, tp53, or paste an Ensembl / ZFIN ID",
    },
    sgrna: {
      kind: "tool",
      tool: "crispr",
      panel: "toolCrispr",
      title: "sgRNA 设计|sgRNA design",
      lead: "给基因或靶序列，扫 PAM、打分、查脱靶。|Give a gene or target sequence: scan PAMs, score, check off-targets.",
      pillA: "本地引擎|Local engine",
      pillB: "可跳 CHOPCHOP|Jump to CHOPCHOP",
      // 2026-10-31：PAM 与「脱靶范围」原来在这里各挂一排药丸，和下面的下拉重复；
      // 现在 PAM 只留下拉（自绘、可滚轮），脱靶范围下拉整颗删掉 → hero 不画任何按钮。
      paneChips: {
        sgrna: [],
        primer: [
          { label: "特异性|Specificity", target: "primerScope",
            items: [
              { value: "transcriptome", label: "转录组|Transcriptome" },
              { value: "genome", label: "全基因组|Genome" },
            ] },
        ],
      },
      formHint: "基因名或粘贴序列|Gene name or pasted sequence",
      emptyTitle: "等待设计|Ready to design",
      emptySub: "填基因名；有靶序列时直接粘序列更准|Fill a gene name; pasting the target sequence is more accurate",
    },
    primer: {
      kind: "tool",
      tool: "crispr",
      panel: "toolCrispr",
      title: "引物设计|Primer design",
      lead: "给模板（基因或序列），按产物长度与特异性出引物对。|Give a template, get primer pairs by amplicon size and specificity.",
      pillA: "本地引擎|Local engine",
      pillB: "Tm · GC · 二聚体|Tm · GC · dimers",
      paneChips: {
        primer: [
          { label: "特异性|Specificity", target: "primerScope", focus: "primerInput",
            items: [
              { value: "transcriptome", label: "转录组|Transcriptome" },
              { value: "genome", label: "全基因组|Genome" },
            ] },
        ],
        sgrna: [
          // 2026-10-31：本页（引物设计）不该出现 sgRNA 的 PAM／脱靶范围按钮（串味），已整组撤掉。
        ],
      },
      formHint: "基因名或粘贴模板序列|Gene name or pasted template",
      emptyTitle: "等待设计|Ready to design",
      emptySub: "填基因名；模板 ≥120 bp 更准|Fill a gene name; templates ≥120 bp work better",
    },
    liftover: {
      kind: "tool",
      tool: "genomes",
      panel: "toolLiftover",
      colorKey: "genomes",
      title: "Liftover|Liftover",
      lead: "支持基于UCSC chain的liftover转换和把一批基因 ID 在符号 ⇄ Ensembl ⇄ ZFIN ⇄ NCBI 之间互相转换|Supports UCSC-chain liftover and batch gene-ID conversion across symbol ⇄ Ensembl ⇄ ZFIN ⇄ NCBI.",
      // 两种模式的可选项完全不同：按当前 tab 换一组（paneChips，2026-10-28）
      // ID 互转模式不需要选项 → 空数组，hero 那行就不画按钮（不会再出现"像按钮却点不动"）
      paneChips: {
        coord: [
          // 2026-10-31：源／目标版本原本在这里排了 11 + 13 个药丸，和左栏下拉完全重复，
          // 页头被撑成三四行、看着很乱。药丸整组撤掉，版本只在左栏下拉里选（该下拉已换成
          // 分组可滚动的自定义下拉，见 js/liftpick.js）。这里只留「链」——liftStrand 是
          // hidden input，没有这两个药丸就没有任何界面能改它。
          { label: "链|Strand", target: "liftStrand",
            items: [
              { value: "+", label: "正链 +|Plus +" },
              { value: "-", label: "负链 −|Minus −" },
            ] },
        ],
        idmap: [],
      },
      formHint: "来源 + 目标 + 染色体 + 起止；或一批基因 ID|Source + target + chromosome + start/end; or a batch of gene IDs",
      emptyTitle: "选自选版本与区间，或贴一批 ID|Pick assemblies and a region, or paste IDs",
      emptySub: "例如 GRCz11 chr5:24086227-24097805 → GRCz12tu，或 tp53 / ENSDARG00000035559|e.g. GRCz11 chr5:24086227-24097805 → GRCz12tu, or tp53 / ENSDARG00000035559",
    },
    expression: {
      kind: "tool",
      tool: "expression",
      panel: "toolExpression",
      title: "表达|Expression",
      lead: "按基因看组织，或按组织看基因。|Browse by gene, or by tissue.",
      pillA: "Bgee 表达分|Bgee score",
      pillB: "Atlas TPM（分期）|Atlas TPM (stages)",
      chips: [
        { label: "模式|Mode", target: "exprMode", focus: "exprQueryInput",
          items: [
            { value: "gene", label: "按基因|By gene" },
            { value: "tissue", label: "按组织|By tissue" },
            { value: "compare", label: "多基因对比|Compare" },
          ] },
      ],
      formHint: "基因名，或选一个组织|Gene name, or pick a tissue",
      emptyTitle: "等待查询|Ready",
      emptySub: "输入 tp53；多个基因用逗号隔开|Enter tp53; separate genes with commas",
    },
    "phenotype-gene": {
      kind: "tool",
      tool: "phenotype",
      panel: "toolPhenotype",
      title: "基因查表型|Gene → phenotype",
      lead: "给一个基因，看 ZFIN 里记录的相关表型。|Give a gene, see the phenotypes recorded in ZFIN.",
      pillA: "ZFIN|ZFIN",
      // 2026-10-28：本页方向是固定的（导航里另有「表型查基因 / 基因表型查询」一项），
      // hero 再放正反两组按钮属于重复入口，用户点名去掉。方向仍由面板里 #phenoMode 决定。
      formHint: "基因名，如 sox9b|Gene name, e.g. sox9b",
      emptyTitle: "输入一个基因|Enter a gene",
      emptySub: "试 sox9b、tbx5a|Try sox9b, tbx5a",
    },
    "phenotype-term": {
      kind: "tool",
      tool: "phenotype",
      panel: "toolPhenotype",
      title: "表型查基因|Phenotype → gene",
      lead: "给一个表型描述，找有该表型的基因。|Give a phenotype description, find genes with that phenotype.",
      pillA: "ZFIN|ZFIN",
      // 2026-10-28：本页方向是固定的（导航里另有「表型查基因 / 基因表型查询」一项），
      // hero 再放正反两组按钮属于重复入口，用户点名去掉。方向仍由面板里 #phenoMode 决定。
      formHint: "表型关键词，中英文都可|Phenotype keywords, zh or en",
      emptyTitle: "输入表型|Enter a phenotype",
      emptySub: "试「心脏畸形」或 increased curvature|Try “心脏畸形” or increased curvature",
    },
    about: {
      kind: "tool",
      tool: "about",
      panel: "toolAbout",
      title: "平台说明|About",
      lead: "这个平台是做什么的、数据从哪来、该怎么引用。|What this platform does, where the data comes from, and how to cite it.",
      pillA: "数据快照 2026-10-15|Snapshot 2026-10-15",
      pillB: "本库 / ZFIN / Ensembl / NCBI|Local / ZFIN / Ensembl / NCBI",
      formHint: "选择下方章节|Pick a section",
      emptyTitle: "选择下方章节|Pick a section",
      emptySub: "三章：定位与范围 / 数据来源 / 引用方式|Three sections: scope / sources / citation",
    },
    blast: {
      kind: "tool",
      tool: "blast",
      panel: "toolBlast",
      title: "BLAST|BLAST",
      lead: "把你的序列比对到斑马鱼核酸库。|Align your sequence against the zebrafish nucleotide library.",
      pillA: "blastn|blastn",
      pillB: "批量 ≤20 条|Batch ≤20",
      // 2026-10-31：删「每条保留（前 5/10/20 条）」这组按钮，配套的下拉也已从页面上撤掉，
      // 命中条数固定走后端默认值 10。
      chips: [],
      formHint: "一条裸序列，或 FASTA 多条|One raw sequence, or multi-FASTA",
      emptyTitle: "等待运行|Ready",
      emptySub: "粘贴 ≥20 bp 的序列，多条用 FASTA|Paste ≥20 bp; use FASTA for multiple",
    },
    go: {
      kind: "tool",
      tool: "enrich",
      panel: "toolEnrich",
      colorKey: "enrich",
      title: "GO 富集|GO enrichment",
      lead: "一串基因，看哪些 GO 条目显著富集。|A gene list in, enriched GO terms out.",
      pillA: "Fisher 精确检验|Fisher exact",
      pillB: "BH-FDR 校正|BH-FDR",
      formHint: "逗号分隔的基因列表|Comma-separated gene list",
      emptyTitle: "等待运行|Ready",
      emptySub: "例如 tp53,hsp70,myl7|e.g. tp53,hsp70,myl7",
    },
    kegg: {
      kind: "tool",
      tool: "kegg",
      panel: "toolKegg",
      title: "通路浏览器|Pathway browser",
      lead: "查斑马鱼通路里有哪些基因。|See which genes sit in a zebrafish pathway.",
      pillA: "dre pathways|dre pathways",
      pillB: "多来源|Multi-source",
      formHint: "通路关键词|Pathway keyword",
      emptyTitle: "等待检索|Ready",
      emptySub: "试 MAPK、p53、apoptosis|Try MAPK, p53, apoptosis",
    },
    id: {
      kind: "tool",
      tool: "idresolve",
      panel: "toolIdResolve",
      title: "跨库 ID 映射|ID mapping",
      lead: "符号、ZFIN、Entrez、Ensembl 之间互转。|Convert between symbol, ZFIN, Entrez and Ensembl.",
      pillA: "三态消歧|Three states",
      pillB: "支持批量|Batch",
      formHint: "单个或批量 ID|Single or batch IDs",
      emptyTitle: "等待解析|Ready",
      emptySub: "可批量：每行一个 ID|Batch: one ID per line",
    },
    "sequence-region": {
      kind: "tool",
      tool: "sequence",
      panel: "toolSequence",
      colorKey: "sequence",
      title: "位置查序列|Sequence by region",
      lead: "给一段基因组区间，取出这段序列（含反向互补）。|Give a genomic interval, get the sequence (reverse complement included).",
      pillA: "cDNA · 启动子 · 基因体|cDNA · promoter · gene body",
      pillB: "批量 ≤50|Batch ≤50",
      // 区间面板的装配版本是可选项：按钮组直连下拉框（2026-10-28）
      // 2026-10-31：删掉「链 / 正链 − 负链」这组 —— 它与面板里的 #seqRegionStrand 重复，
      // 且负链只是“取出反向互补”的一个勾选项，摆在 hero 上像必选，用户点名去掉。
      // 链方向仍在面板下拉里可改，功能没有减少。
      chips: [
        { label: "装配|Assembly", target: "seqRegionAsm", focus: "seqRegionChrom",
          items: ["GRCz12tu", "GRCz11"] },
      ],
      // 「按基因」面板没有装配下拉（cDNA 固定 GRCz12tu、启动子/基因体 GRCz11），
      // 所以切到这一栏就退回说明文字，不摆一排点不动的按钮。
      paneChips: { gene: null },
      formHint: "染色体 + 起止|Chromosome + start/end",
      emptyTitle: "填一段坐标|Enter an interval",
      emptySub: "例如 8 / 1000 / 2000，或 8:1000-2000 批量|e.g. 8 / 1000 / 2000, or batch 8:1000-2000",
    },
    annotate: {
      kind: "tool",
      tool: "annotate",
      panel: "toolAnnotate",
      title: "一键注释|One-click annotation",
      lead: "一个基因，GO、表达、同源、文献汇总成一页。|One gene, every layer on one page.",
      pillA: "一键汇总|One click",
      pillB: "多层整合|Multi-layer",
      formHint: "基因符号|Gene symbol",
      emptyTitle: "等待生成|Ready",
      emptySub: "输入基因符号即可|Just enter a gene symbol",
    },
    /* 2026-10-28：News 不再有独立功能页。原来这一页是「Library updates + 调试检索」
       （粗排/精排/AI 打包顺序的表），属于开发者工具，用户要求「点击 News 就是首页界面」。
       这里保留路由名但落到首页视图：导航里 News 仍然高亮，旧书签 #/news 也不会 404。 */
    news: { kind: "home" },
    upload: {
      kind: "tool",
      tool: "upload",
      panel: "toolUpload",
      title: "文献入库|Upload literature",
      lead: "上传自己的文献，进入本地知识库。|Add your own papers to the local library.",
      pillA: "RAG 入库|Into RAG",
      pillB: "≤5 MB|≤5 MB",
      formHint: "选择本地文件|Choose a local file",
      emptyTitle: "等待上传|Ready",
      emptySub: "单文件 ≤5 MB，UTF-8|One file ≤5 MB, UTF-8",
    },
    ops: {
      kind: "tool",
      tool: "ops",
      panel: "toolOps",
      title: "运营看板|Dashboard",
      lead: "定时任务状态与访问量。|Cron status and traffic.",
      pillA: "cron 任务|Cron jobs",
      pillB: "仅聚合统计|Aggregates only",
      formHint: "点右侧刷新|Click refresh",
      emptyTitle: "等待加载|Loading",
      emptySub: "点「刷新」拉取最新状态|Click “Refresh” for the latest state",
    },
  };

  const bank = () => document.getElementById("toolBank");
  const mount = () => document.getElementById("featureMount");
  const resultMount = () => document.getElementById("featureResultMount");

  function parseRoute() {
    const raw = (location.hash || "#/home").replace(/^#\/?/, "");
    const [name, ...rest] = raw.split("/");
    return { name: name || "home", arg: rest.join("/") };
  }

  function parkTools() {
    const b = bank();
    const m = mount();
    const rm = resultMount();
    if (!b) return;
    if (m) {
      Array.from(m.querySelectorAll(".tool-panel")).forEach((p) => {
        p.classList.add("hidden");
        b.appendChild(p);
      });
    }
    const tr = document.getElementById("toolResult");
    if (tr && rm && tr.parentElement === rm) b.appendChild(tr);
  }

  function placeChatToolResult() {
    const host = document.getElementById("chatToolResultHost");
    const tr = document.getElementById("toolResult");
    if (host && tr && tr.parentElement !== host) host.appendChild(tr);
  }

  /* 视图互斥：既切 class，也用内联 !important 锁死 display。
     为什么两样都做——历史事故：某层 CSS 写 `#view-chat{display:flex!important}`，
     其 ID 特异性压过了 `.app-view.hidden{display:none}`，于是切到功能页时
     聊天页仍留在屏幕上，两个视图内容互相重叠/覆盖。
     内联 !important 的优先级高于任何作者样式表 !important，
     所以这一步之后，无论哪一层 CSS 怎么写，都不可能再出现"两个视图同时在"。
     显示时清掉内联，把 display 交回样式表（聊天页需要 flex、其余是 block）。 */
  function showOnly(viewId) {
    ["view-home", "view-chat", "view-feature", "view-guide"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      const on = id === viewId;
      el.classList.toggle("hidden", !on);
      if (on) el.style.removeProperty("display");
      else el.style.setProperty("display", "none", "important");
    });
  }

  function setNavActive(route) {
    document.querySelectorAll(".top-nav-link, .rail-item").forEach((el) => {
      el.classList.toggle("active", el.getAttribute("data-route") === route);
    });
  }

  function syncFeatureEmpty() {
    const empty = document.getElementById("featureEmptyState");
    const tr = document.getElementById("toolResult");
    const geneHost = document.getElementById("featureGeneCardHost");
    const hasResult = tr && !tr.classList.contains("hidden") && (tr.textContent || "").trim().length > 0;
    const hasGene = geneHost && !geneHost.classList.contains("hidden") && geneHost.childElementCount > 0;
    if (empty) empty.classList.toggle("hidden", !!(hasResult || hasGene));
  }

  /* 「中文|English」取值：语言由 <html lang> 决定。
     页面文案只写一处数据，改文案不必再同步 i18n 词条表，
     也就不会出现「英文界面里漏一句中文」这种混排。 */
  function langIsZh() {
    return (document.documentElement.getAttribute("lang") || "en").toLowerCase().indexOf("zh") === 0;
  }
  function pickText(v, fb) {
    if (v == null || v === "") return fb || "";
    const parts = String(v).split("|");
    return langIsZh() ? parts[0] : (parts[1] || parts[0]);
  }
  function applyFeatureStatic(meta) {
    if (!meta) return;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    /* 2026-10-31：功能页标题上方那行小标签（「— 315k 条表达记录」「— GRCz12tu / GRCz11」
       「— 本地注释 · 可复核」之类）全站下线。用户的原话是：功能页不要把这些本机/服务器
       后台与数据库里的东西直接摆给用户看，每个功能能让人清楚用就行。PAGE_META.eyebrow
       里的文案一并删除，不留没人读的死数据。 */
    const _ebEl = document.getElementById("featureEyebrow");
    if (_ebEl) {
      _ebEl.textContent = "";
      _ebEl.hidden = true;
      /* CSS 里 .feature-eyebrow 是 display: inline-flex !important，
         普通 style.display 压不住，必须带 important 才真的不占位。 */
      _ebEl.style.setProperty("display", "none", "important");
    }
    set("featureTitle", pickText(meta.title, ""));
    /* 2026-10-31：各功能页标题下的那句副标题说明（例如「给一个基因，看它在基因组上的
       位置、基因体与序列。」）全站下线 —— 用户要求功能页只留标题，直接进输入区。
       文案没有删，PAGE_META.lead 仍留着；将来想恢复某页，把下面三行去掉即可。 */
    const _leadEl = document.getElementById("featureLead");
    if (_leadEl) {
      _leadEl.textContent = "";
      _leadEl.hidden = true;
      _leadEl.style.display = "none";
    }
    set("featureFormHint", pickText(meta.formHint, ""));
    set("featureEmptyTitle", pickText(meta.emptyTitle, ""));
    set("featureEmptySub", pickText(meta.emptySub, ""));

    /* hero 右侧那一行（2026-10-28 重做）
       ----------------------------------------------------------------------
       这一行以前是两颗只读药丸（展示用）。但其中很多本来就是**可选字段的当前值**
       —— Liftover 的源版本、取序列的装配/链方向、表达模式、PAM、脱靶范围、
       BLAST 每条保留数……写成点不动的文字，用户只会觉得「坏了」。
       规则改成硬性的两条：
         1) 页面上真有对应控件的，一律渲染成可点按钮组（点击写回 <select> 并派发
            change，页面自身联动照常触发；选中态永远以控件当前值为准）；
         2) 页面上没有可选项的，就不画成药丸，只留一行淡色说明文字 —— 不再出现
            「看着像按钮、点了没反应」。
       meta.chips 两种写法都支持：
         { target, focus, items: ["GRCz11", "GRCz10"] }                 单组
         [ {label:"装配", target:"…", items:[…]}, {label:"链", …} ]      多组
       items 元素可以是 "value"，也可以是 {value, label}（label 写「中文|English」）。
       meta.paneChips = { sgrna:[…], primer:[…] } 用于一个面板里有两个 tab 的页面：
       切 tab 时按钮组跟着换，指向当前可见的那一组控件。 */
    activeMeta = meta;
    activePane = "";
    renderFeatureChips(meta, "");
  }

  /* 当前页面的 meta 与当前 tab —— 切 tab / 切语言时要按新的那组重画 */
  let activeMeta = null;
  let activePane = "";

  function chipsOf(meta, paneKey) {
    if (!meta) return [];
    const pc = meta.paneChips;
    if (!pc) return meta.chips ? (Array.isArray(meta.chips) ? meta.chips : [meta.chips]) : [];
    // 一页两个 tab：没显式指定就跟着**本面板内**当前激活的那个走（例如直接打开
    // #/primer 时，crispr.js 已把「引物设计」设为 active，不必等它再点一次）。
    // 注意必须限定在 meta.panel 里找：页面里别的面板也有 .seq-tab.active，
    // 全局查找会拿到「按基因」那个，结果整组按钮都落到不存在的键上。
    let key = paneKey;
    if (!key) {
      const panel = meta.panel ? document.getElementById(meta.panel) : null;
      const active = (panel || document).querySelector(
        ".crispr-tab.active, .seq-tab.active, .lift-mode-tab.active"
      );
      key = active
        ? (active.getAttribute("data-crispr-tab") || active.getAttribute("data-seq-tab")
           || active.getAttribute("data-lift-mode"))
        : Object.keys(pc)[0];
    }
    let cfg = Object.prototype.hasOwnProperty.call(pc, key) ? pc[key] : meta.chips;
    if (!cfg) return [];
    return Array.isArray(cfg) ? cfg : [cfg];
  }

  function chipItem(it) {
    if (it && typeof it === "object") {
      const value = it.value != null ? String(it.value) : String(it.label == null ? "" : it.label);
      const label = pickText(it.label != null ? it.label : it.value, value);
      return { value, label };
    }
    return { value: String(it), label: pickText(String(it), String(it)) };
  }

  function renderFeatureChips(meta, paneKey) {
    const row = document.getElementById("featureMetaRow");
    if (!row) return;
    activePane = paneKey || "";
    row.innerHTML = "";
    const groups = chipsOf(meta, paneKey).filter(
      (g) => g && g.target && Array.isArray(g.items) && g.items.length
    );
    if (!groups.length) {
      // 没有任何可选项就**什么都不画**（2026-10-28）
      // 以前这里退化成一行淡字（“GRCz11 坐标 · GRCz12tu cDNA”），本意是当注释，
      // 但读起来像状态控件，用户点了没反应会以为坏了。页头的副标题与 eyebrow
      // 已经说清口径，这行纯属多余，直接留白。
      row.removeAttribute("role");
      return;
    }
    row.setAttribute("role", "group");
    groups.forEach((g) => {
      const box = document.createElement("span");
      box.className = "feature-chipset";
      const label = pickText(g.label, "");
      if (label) {
        const lab = document.createElement("span");
        lab.className = "feature-chipset-label";
        lab.textContent = label;
        box.appendChild(lab);
      }
      g.items.map(chipItem).forEach((it) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "feature-pill feature-chip";
        b.textContent = it.label;
        b.setAttribute("data-chip-target", g.target);
        b.setAttribute("data-chip-value", it.value);
        b.setAttribute("aria-pressed", "false");
        b.addEventListener("click", () => {
          const t = document.getElementById(g.target);
          if (!t) return;
          t.value = it.value;
          t.dispatchEvent(new Event("change", { bubbles: true }));
          syncChipState(g.target);
          // 选完这一项可把焦点递到下一步输入；preventScroll 避免顶栏点选时整页被滚走
          const next = document.getElementById(g.focus || "");
          if (next) {
            const pane = next.closest ? next.closest(".hidden") : null;
            if (!pane) {
              try { next.focus({ preventScroll: true }); }
              catch (_) { next.focus(); }
            }
          }
        });
        box.appendChild(b);
      });
      row.appendChild(box);
      syncChipState(g.target);
      // 控件被别处程序改了值（例如点组织下拉会自动切到 tissue 模式），
      // 也要反映到按钮上：只绑一次，避免每次切页重复叠加监听。
      const t = document.getElementById(g.target);
      if (t && !t.dataset.chipBound) {
        t.dataset.chipBound = "1";
        t.addEventListener("change", () => syncChipState(g.target));
      }
    });
  }

  function syncAllChips() {
    const seen = {};
    document.querySelectorAll("[data-chip-target]").forEach((b) => {
      const id = b.getAttribute("data-chip-target");
      if (!id || seen[id]) return;
      seen[id] = 1;
      syncChipState(id);
    });
  }

  /* 供其它脚本调用：一个面板两个 tab（sgRNA/引物、按基因/按区间）切换后，
     hero 里的按钮组要跟着指向当前可见的那组控件。 */
  window.ZFPageChips = {
    refresh: () => {
      if (!activeMeta) return;
      renderFeatureChips(activeMeta, activePane);
      syncAllChips();
    },
    onPaneChange: (paneKey) => {
      if (!activeMeta || !activeMeta.paneChips) return;
      activePane = paneKey || "";
      renderFeatureChips(activeMeta, activePane);
    },
    syncAll: syncAllChips,
  };

  /* 任何下拉框变化都把按钮组重新对齐一次：有的页面会「点了组织下拉，模式自动切到
     tissue」，这时下拉的 change 事件不会落在 exprMode 上，靠冒泡统一兜住。
     切语言同理，按钮文字是渲染时按当前语言取的，必须重画一遍。 */
  document.addEventListener("change", () => {
    if (window.ZFPageChips) window.ZFPageChips.syncAll();
  });
  document.addEventListener("hub-lang-change", () => {
    if (window.ZFPageChips) window.ZFPageChips.refresh();
  });

  // 药丸选中态：永远以目标控件（select/input）当前值为准，避免"点了却看着没选中"
  function syncChipState(targetId) {
    if (!targetId) return;
    const t = document.getElementById(targetId);
    if (!t) return;
    const cur = String(t.value || "");
    document.querySelectorAll(`[data-chip-target="${targetId}"]`).forEach((b) => {
      const on = b.getAttribute("data-chip-value") === cur;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function openToolPage(meta, routeName) {
    parkTools();
    /* 2026-10-31：有的路由会刻意把结果区整块藏起来（#/genes/<name> 只开一张卡）。
       进任何工具页先把这块恢复，否则切走再回来结果区就一直空着。 */
    resultMount()?.classList.remove("hidden");
    showOnly("view-feature");
    const view = document.getElementById("view-feature");
    const root = document.getElementById("featurePageRoot");
    // data-feature 只决定强调色（style.css 按 `#view-feature[data-feature="…"]` 上色）；
    // 一个面板被多条路由复用时（GO 复用 enrich、sgRNA/引物复用 crispr），用 colorKey 指回原色。
    const feat = meta.colorKey || routeName || meta.tool || "gene";
    if (view) view.setAttribute("data-feature", feat);
    if (root) {
      root.classList.remove("feature-enter");
      void root.offsetWidth;
      root.classList.add("feature-enter");
    }
    applyFeatureStatic(meta);
    const foot = document.getElementById("featureFootNote");
    renderFeatureFoot();
    const panel = document.getElementById(meta.panel);
    const m = mount();
    const rm = resultMount();
    if (panel && m) {
      panel.classList.remove("hidden");
      m.appendChild(panel);
    }
    const tr = document.getElementById("toolResult");
    if (tr && rm) {
      rm.appendChild(tr);
      tr.classList.add("hidden");
      tr.innerHTML = "";
    }
    const geneHost = document.getElementById("featureGeneCardHost");
    if (geneHost) {
      geneHost.classList.add("hidden");
      geneHost.innerHTML = "";
    }
    document.querySelectorAll(".tool-tab").forEach((t) => {
      t.classList.toggle("active", t.getAttribute("data-tool") === meta.tool);
    });
    syncFeatureEmpty();
    try {
      // 事件里传**路由名**（不是强调色键）：各页脚本要按路由判断「是不是我，该不该自动加载」。
      document.dispatchEvent(new CustomEvent("zf-feature-open", { detail: { route: routeName || meta.tool || feat } }));
    } catch (_) {}
    // hero 右侧 KPI：同步渲染，唯一来源就是 app.js 里的 PAGE_KPIS 数据表。
    // （原先由 page-meta.js 另画一套，导致同一页出现两份指标，已整体移除）
    // 2026-10-28：KPI 与语言切换重渲染都按**路由名**取，不能按强调色键，
    // 否则 Liftover 会显示基因组对比页那五个数字。
    const kpiKey = routeName || meta.tool || "gene";
    lastFeatureKey = kpiKey;
    try { renderFeatureKpis(kpiKey); } catch (_) {}
  }

  if (toolResult) {
    const mo = new MutationObserver(() => syncFeatureEmpty());
    mo.observe(toolResult, { attributes: true, childList: true, characterData: true, subtree: true });
  }
  /* 2026-10-31：别名页的卡片挂在 #featureGeneCardHost 上，和 #toolResult 是两个容器。
     只盯 toolResult 的话，`#/genes/<name>` 深链直接开卡片时空态不会让位，
     页面上就是「先一个大大的 Enter a name，下面再挂一张卡」。 */
  const geneCardHostEl = document.getElementById("featureGeneCardHost");
  if (geneCardHostEl) {
    new MutationObserver(() => syncFeatureEmpty())
      .observe(geneCardHostEl, { attributes: true, childList: true, subtree: true });
  }

  /* 旧入口兼容：2026-10-27 起「一键注释」并入基因工作台（它原本只是基因页的子集：
     只输出 summary + 前 8 个 GO 项）。老链接 / 书签仍然可用，落到基因工作台并自动查。 */
  /* 旧路由兼容（2026-10-28 导航重组）。只改名，不改能力：
     Genomics → Omics（Genome / Gene / Phenotype 三区）、sgRNA 与引物拆成两条、
     Literature → News。旧书签 / 旧外链仍然落到对应页面与对应模式。 */
  const ROUTE_ALIAS = {
    annotate: "gene",
    genomics: "liftover",
    genomes: "liftover",
    sequence: "sequence-region",
    enrich: "go",
    crispr: "sgrna",
    phenotype: "phenotype-term",
    literature: "news",
  };

  function applyRoute() {
    let { name, arg } = parseRoute();
    /* 每次路由变化都重算一次锁态：没入场时无论切到哪一页都还是门在台上，
       只有 guide（使用说明/协议/数据来源）允许临时在门外看。 */
    syncGateLock();
    const rawName = name;   // 旧路由名：用于判断「旧链接想表达哪种模式」
    if (ROUTE_ALIAS[name]) name = ROUTE_ALIAS[name];
    const meta = PAGE_META[name] || PAGE_META.home;
    const route = PAGE_META[name] ? name : "home";
    setNavActive(route);

    if (meta.kind === "home") {
      parkTools();
      showOnly("view-home");
      return;
    }
    if (meta.kind === "guide") {
      parkTools();
      showOnly("view-guide");
      if (window.ZFGuide && typeof window.ZFGuide.render === "function") window.ZFGuide.render();
      return;
    }
    if (meta.kind === "chat") {
      parkTools();
      placeChatToolResult();
      showOnly("view-chat");
      if (arg.startsWith("q=")) {
        const q = decodeURIComponent(arg.slice(2));
        const ta = document.getElementById("questionInput");
        if (ta && q) {
          ta.value = q;
          setTimeout(() => document.getElementById("chatForm")?.requestSubmit(), 80);
        }
      }
      return;
    }

    openToolPage(meta, route);
    if (route === "ops") renderOpsPanel();
    if (route === "gene" && arg) {
      const input = document.getElementById("toolGeneInput");
      if (input) input.value = decodeURIComponent(arg);
      setTimeout(() => document.getElementById("toolLookupBtn")?.click(), 60);
    }
    /* 2026-10-31 互跳对：位置查找结果里的「查它的别名与跨库 ID →」指向 #/genes/<name>，
       直接开别名卡；列表区不跟着跑一遍检索（那是 browse，会把页面拉回"瀑布"）。 */
    if (route === "genes" && arg) {
      const q = decodeURIComponent(arg);
      const input = document.getElementById("geneSearchInput");
      if (input) input.value = q;
      /* 列表区整块收起：这一页只有一张卡，留个 120px 的空挂载位会把卡片顶到下半屏 */
      document.getElementById("toolResult")?.classList.add("hidden");
      document.getElementById("featureResultMount")?.classList.add("hidden");
      setTimeout(() => {
        if (window.ZFGenes && window.ZFGenes.open) window.ZFGenes.open(q);
      }, 80);
    }
    /* 跨库 ID 映射（#/id）已从 Tools 菜单移除，但路由保留：旧书签仍可带参直达 */
    if (route === "id" && arg) {
      const input = document.getElementById("idResolveInput");
      if (input) input.value = decodeURIComponent(arg);
      setTimeout(() => document.getElementById("idResolveBtn")?.click(), 60);
    }
    /* 表达：三种模式同页切换，#/expression?mode=tissue&tp53 这类深链照旧 */
    if (route === "expression" && arg) {
      const q = decodeURIComponent(arg);
      const m = q.match(/(?:^|[?&])mode=(tissue|compare)/);
      const sel = document.getElementById("exprMode");
      if (m && sel) sel.value = m[1];
      const kw = q.replace(/(?:^|[?&])mode=\w+/, "").replace(/^[?&]+/, "");
      const input = document.getElementById("exprQueryInput");
      if (input && kw && !kw.includes("=")) input.value = kw;
      setTimeout(() => document.getElementById("exprRunBtn")?.click(), 80);
    }
    /* ── Omics / Genome：位置查序列 ────────────────────────────────────────
       默认落在「按染色体区间」；旧链接 #/sequence/<基因> 表达的是「按基因」，
       退回基因 tab 并回填，不让老链接跳到空表单。 */
    if (route === "sequence-region") {
      const asGene = rawName === "sequence" && arg && arg.trim();
      setSeqTab(asGene ? "gene" : "region");
      if (asGene) {
        const input = document.getElementById("seqGeneInput");
        if (input) input.value = decodeURIComponent(arg);
      } else if (!arg) {
        applyLiftRegionToSeq();   // 从 Liftover 结果卡「按这段坐标取序列」带过来的区间
      }
    }
    /* ── Tools / GO 富集：支持 #/go/tp53,hsp70 直接跑 ─────────────────── */
    if (route === "go" && arg) {
      const input = document.getElementById("enrichInput");
      if (input) input.value = decodeURIComponent(arg);
      setTimeout(() => document.getElementById("enrichBtn")?.click(), 80);
    }
    /* ── Tools / sgRNA 与引物：同一页两个面板，路由决定默认落在哪个 ───── */
    if (route === "sgrna" || route === "primer") {
      setCrisprTab(route === "primer" ? "primer" : "sgrna");
    }
    /* ── News：2026-10-28 起没有独立页面（路由直接落首页），
          旧 #/literature/<查询> 也不再回填调试检索框。 */
    /* ── Omics / Phenotype：两个页面方向写死，互不串门 ──────────────────
       2026-10-31：原来这里允许 `?mode=` 覆盖方向，两个页面于是长得一模一样
       （表型查基因页写着「基因名」，用户按提示输基因名当然查不到）。
       现在 #/phenotype-gene 只能是「基因 → 表型」、#/phenotype-term 只能是
       「表型 → 基因」，URL 里的 mode 参数一律忽略。 */
    if (route === "phenotype-gene" || route === "phenotype-term") {
      const q = arg ? decodeURIComponent(arg) : "";
      const want = route === "phenotype-gene" ? "gene" : "term";
      const sel = document.getElementById("phenoMode");
      if (sel) {
        sel.value = want;
        // 方向值仍由这个隐藏下拉承载，change 里同步 placeholder；程序改值时补一次事件。
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
      const kw = q.replace(/(?:^|[?&])mode=\w+/, "").replace(/^[?&]+/, "");
      const input = document.getElementById("phenoInput");
      if (input && kw && !kw.includes("=")) input.value = kw;
      if (kw) setTimeout(() => document.getElementById("phenoRunBtn")?.click(), 80);
    }
    /* ── Liftover：两种模式共用一页（坐标换算 / 批量 ID 互转）────────────
       #/liftover/idmap 直接落在 ID 模式（基因别名页的「批量 ID 互转 →」和
       其他脚本都走这个深链）；其余参数沿用旧行为：自动展开装配长度对比表。 */
    if (route === "liftover") {
      const want = (arg || "").toLowerCase();
      setTimeout(() => {
        if (window.ZFLift && window.ZFLift.setMode) {
          window.ZFLift.setMode(want === "idmap" ? "idmap" : "coord");
        }
        if (arg && want !== "idmap") document.getElementById("genomeLoadBtn")?.click();
      }, 60);
    }
    /* 路由里的深链会把模式下拉改成别的值（例如 #/phenotype-gene 强制 gene），
       上面的 openToolPage 已经画过一次按钮组，这里按最终值再对齐一次。 */
    if (window.ZFPageChips) window.ZFPageChips.syncAll();
  }

  window.addEventListener("hashchange", applyRoute);

  /* 「使用说明」入口：首页 CTA 下方、欢迎弹窗底部、各功能页页脚都用同一种标记。
     2026-10-28：这里原来直接给门加 .hidden，等于开了个后门 —— 在门上点一下
     「使用说明」就把锁解了，之后整站随便逛（用户看到的就是「点一下就进去了」）。
     现在只把「主动开门」这个临时状态撤掉，交给 syncGateLock 重新判定：
     文档页本身允许在门外看，离开文档页立刻重新上锁。 */
  document.addEventListener("click", (e) => {
    const el = e.target && e.target.closest ? e.target.closest("[data-guide-open]") : null;
    if (!el) return;
    e.preventDefault();
    gateForceOpen = false;
    const href = el.getAttribute("href") || "#/guide";
    if (location.hash === href) {
      syncGateLock();
      if (window.ZFGuide && typeof window.ZFGuide.render === "function") window.ZFGuide.render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } else {
      location.hash = href;
    }
  });
  /* 2026-10-28：品牌区不再是链接（只做 logo），这里原本的「点一下回首页」监听随之去掉。
     首页入口走顶部 News；直接访问 #/home 仍然有效。 */
  document.getElementById("themeToggleBtn")?.addEventListener("click", () => {
    document.body.classList.toggle("dark");
    localStorage.setItem("zfqa_theme_dark", document.body.classList.contains("dark") ? "1" : "0");
  });
  if (localStorage.getItem("zfqa_theme_dark") === "1") document.body.classList.add("dark");
  /* 2026-10-28：三道杠删除后不再有「收起侧栏」这个状态，历史上存下的偏好要清掉，
     否则老用户刷新后侧栏仍然是收起的样子、又找不到展开的入口。 */
  if (localStorage.getItem(SIDEBAR_HIDE_KEY) === "1") {
    document.body.classList.remove("chat-sidebar-hidden");
    try { localStorage.removeItem(SIDEBAR_HIDE_KEY); } catch { /* ignore */ }
  }

  /* 窄屏的历史抽屉从页眉下沿展开：把页眉实测高度写进 --zf-header-h，
     否则固定定位的面板会被页眉盖住顶部（「历史对话 / 新对话」两行直接被裁掉）。 */
  function syncHeaderHeightVar() {
    const h = document.querySelector(".header.site-header");
    if (h) document.documentElement.style.setProperty("--zf-header-h", h.offsetHeight + "px");
  }
  syncHeaderHeightVar();
  window.addEventListener("resize", syncHeaderHeightVar);
  setTimeout(syncHeaderHeightVar, 300);
  setTimeout(syncHeaderHeightVar, 1500);

  document.getElementById("homeSearchBtn")?.addEventListener("click", () => {
    const q = (document.getElementById("homeSearchInput")?.value || "").trim();
    const scope = document.getElementById("homeSearchScope")?.value || "chat";
    if (!q) return;
    if (scope === "gene") location.hash = "#/gene/" + encodeURIComponent(q);
    // 2026-10-28：文献范围原本跳 #/news/<查询>（调试检索页，已删）。改为直接问助手，
    // 助手内部就是「粗排 500 → 精排 100 → 打包」那条链路，结果比调试表更好读。
    else location.hash = "#/chat/q=" + encodeURIComponent(q);
  });
  document.getElementById("homeSearchInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("homeSearchBtn")?.click();
  });

  /* 2026-10-27：空状态的示例胶囊已整块删除（点了只是替用户打字，属于冗余控件），
     对应的 .quick-prompt 绑定一并去掉。 */
  /* 2026-10-27：输入框左侧的「＋」已删除。它原本只是个跳转到 #/gene 的假按钮
     （点一下直接进基因工作台），既不是附件也不是工具菜单，属于典型的失效控件。
     与其留一个行为莫名的图标，不如去掉——需要工具请走顶部 Tools。 */


  document.addEventListener("click", (e) => {
    const a = e.target.closest("a.md-a-local, a[href^='#/']");
    if (!a) return;
    const href = a.getAttribute("href") || "";
    if (href.startsWith("#/")) {
      e.preventDefault();
      location.hash = href;
    }
  });

  async function fillHomeStats() {
    const fmt = (n) => {
      const x = Number(n);
      if (!Number.isFinite(x)) return "—";
      return x.toLocaleString("en-US");
    };
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = fmt(val);
    };
    try {
      const res = await fetch("/api/hub/stats", { credentials: "same-origin" });
      if (!res.ok) throw new Error("stats " + res.status);
      const data = await parseJsonResponse(res, res.url || "");
      const db = data.database || {};
      const hub = data.hub || {};
      set("homeStatGenes", db.gene_basic);
      set("homeStatPapers", hub.literature_catalog_lines || db.gene_reference);
      set("homeStatGo", db.gene_go);
      set("homeStatKegg", hub.kegg_pathway_count);
      set("homeStatExpr", db.gene_expression);
      set("homeStatMap", hub.grcz12tu_mapped);
      set("homeStatXref", db.gene_xref || hub.gene_xref);
      const note = document.getElementById("homeStatsNote");
      if (note) {
        const asm = (data.reference && data.reference.user_facing_reference) || "GRCz12tu";
        const raf = data.refreshed_at ? String(data.refreshed_at).replace("T", " ").replace("Z", " UTC") : new Date().toLocaleTimeString();
        note.textContent = `实时库规模 · 用户锚 ${asm} · 本地注释 GRCz11 · 刷新 ${raf}`;
      }
    } catch (err) {
      // fallback: scrape chat dbStats if already loaded
      try {
        const ds = document.getElementById("dbStats");
        if (ds && /\d/.test(ds.textContent || "")) {
          const m = (ds.textContent || "").match(/(\d[\d,]*)/g) || [];
          if (m[0]) set("homeStatGenes", String(m[0]).replace(/,/g, ""));
          if (m[1]) set("homeStatPapers", String(m[1]).replace(/,/g, ""));
        }
      } catch (_) {}
    }
  }

  async function initZfGallery() {
    const cards = Array.from(document.querySelectorAll(".wave-card"));
    const hero = document.getElementById("heroWaveStrip");
    const track = document.getElementById("reefStreamTrack");
    const mosaic = document.getElementById("reefMosaic");
    const credit = document.getElementById("galleryCredit");
    let files = [];
    /** 各路由互斥影像子集（步长分桶，避免全站共用同一批） */
    const ROUTE_BUCKET = {
      home: 0, chat: 1, gene: 2, blast: 3, enrich: 4, kegg: 5,
      id: 6, sequence: 7, annotate: 8, literature: 9, upload: 10,
    };
    const ROUTE_KEYS = Object.keys(ROUTE_BUCKET);
    const routePools = {};
    let mode = "mix";
    let timer = null;
    let warmTimer = null;
    let poolEpoch = 0;

    const rebuildRoutePools = (epoch = 0) => {
      ROUTE_KEYS.forEach((k) => { routePools[k] = []; });
      if (!files.length) return;
      const n = ROUTE_KEYS.length;
      const rot = (epoch * 3) % Math.max(1, files.length);
      const rotated = files.slice(rot).concat(files.slice(0, rot));
      rotated.forEach((url, i) => {
        const key = ROUTE_KEYS[i % n];
        routePools[key].push(url);
      });
      // 每路由至少 6 张：不足则用本桶 + 邻桶补齐（仍与主桶偏移不同）
      ROUTE_KEYS.forEach((key, ki) => {
        const pool = routePools[key];
        let j = 0;
        while (pool.length < 6 && files.length) {
          const neighbor = ROUTE_KEYS[(ki + 1 + (j % (n - 1))) % n];
          const src = routePools[neighbor];
          if (src && src[j % Math.max(1, src.length)]) {
            const u = src[j % src.length];
            if (!pool.includes(u)) pool.push(u);
          } else {
            pool.push(files[(ki * 17 + j + rot) % files.length]);
          }
          j += 1;
          if (j > 40) break;
        }
      });
    };

    const poolFor = (route) => {
      const p = routePools[route] || routePools.gene || files;
      return p && p.length ? p : files;
    };

    /* 同一屏内不重复用图：优先取本池未用过的 URL，池太小时才退回轮转 */
    let roundUsed = new Set();
    const pickUnique = (pool, idx) => {
      const n = pool ? pool.length : 0;
      if (!n) return "";
      for (let k = 0; k < n; k++) {
        const u = pool[(idx + k) % n];
        if (u && !roundUsed.has(u)) {
          roundUsed.add(u);
          return u;
        }
      }
      return pool[idx % n] || "";
    };

    const paint = (el, pool, idx) => {
      if (!el || !pool || !pool.length) return;
      const a = pickUnique(pool, idx);
      const b = pickUnique(pool, idx + Math.max(2, Math.floor(pool.length / 3)));
      if (a) el.style.setProperty("--wave-img-a", `url("${a}")`);
      if (b) el.style.setProperty("--wave-img-b", `url("${b}")`);
    };

    const paintAll = (tick = 0) => {
      roundUsed = new Set();
      cards.forEach((card, i) => {
        const href = card.getAttribute("href") || "";
        const route = (href.match(/#\/([a-z]+)/) || [])[1] || "home";
        const thumb = card.querySelector(".wave-thumb");
        paint(thumb, poolFor(route), tick + i);
        card.dataset.waveIdx = String(i);
        card.dataset.waveRoute = route;
      });
      if (hero) paint(hero, poolFor("home"), 2 + tick);
      const featWave = document.getElementById("featureWaveStrip");
      if (featWave) {
        const feat = document.getElementById("view-feature")?.getAttribute("data-feature") || "gene";
        paint(featWave, poolFor(feat), tick);
      }
    };

    const tileHtml = (it) => {
      const u = it.url || "";
      const title = String(it.title || it.source || "zf").replace(/"/g, "");
      const badge =
        it.rank === "hot" ? "热" : it.rank === "latest" ? "新" : it.rank === "research" ? "研" : "库";
      return `<a class="reef-tile" href="${it.landing || u}" target="_blank" rel="noopener" title="${title}">
        <img src="${u}" alt="" loading="lazy" decoding="async" fetchpriority="low" onerror="this.closest('.reef-tile')?.classList.add('is-broken')" />
        <span class="reef-badge">${badge}</span>
      </a>`;
    };

    /** 内容指纹相同的图只留一张（服务端已去重，这里是前端傅底：老缓存也能正确显示） */
    const uniqItems = (items) => {
      const seenUrl = new Set();
      const seenKey = new Set();
      const out = [];
      (items || []).forEach((it) => {
        const u = (it && it.url) || "";
        if (!u || seenUrl.has(u)) return;
        const ck = (it && (it.content_key || it.sha1)) || "";
        if (ck && seenKey.has(ck)) return;
        seenUrl.add(u);
        if (ck) seenKey.add(ck);
        out.push(it);
      });
      return out;
    };

    // 影像流：限并发（60×2=120 个节点）+ 内容未变不重建，避免大量图片请求被反复取消
    let streamKey = "";
    const fillStream = (rawItems) => {
      if (!track) return;
      const items = uniqItems(rawItems);
      const slice = items.slice(0, 60);
      const key = slice.map((it) => it.url || "").join("|");
      if (key && key === streamKey) return;
      streamKey = key;
      const html = slice.map(tileHtml).join("");
      // 轨道复制一份用于无缝滚动（第二份在视口外）；马赛克另取**不重叠**的一段，避免同屏看到同一张图
      track.innerHTML = html + html;
      track.style.animationDuration = Math.max(48, slice.length * 1.35) + "s";
      if (mosaic) {
        const rest = items.slice(slice.length);
        const take = Math.min(48, rest.length);
        mosaic.innerHTML = take > 0 ? rest.slice(0, take).map(tileHtml).join("") : "";
        mosaic.classList.toggle("hidden", take === 0);
      }
    };

    const load = async (force = false) => {
      let payload = null;
      // 优先服务端维护的各页独立图池（3 日轮换）
      try {
        const bp = await fetch(`/api/hub/bg-pools?${force ? "force=1&" : ""}_=${Date.now()}`, {
          credentials: "same-origin",
          cache: "no-store",
        });
        if (bp.ok) {
          const poolsPayload = await bp.json();
          const pools = poolsPayload.pools || poolsPayload;
          if (pools && typeof pools === "object" && (pools.home || pools.gene || pools.chat)) {
            ROUTE_KEYS.forEach((k) => {
              const arr = pools[k] || pools.hub || [];
              routePools[k] = Array.isArray(arr) ? arr.slice() : [];
            });
            files = [];
            ROUTE_KEYS.forEach((k) => {
              (routePools[k] || []).forEach((u) => {
                if (u && !files.includes(u)) files.push(u);
              });
            });
            if (force && poolsPayload.count) {
              // force 会触发 gallery job，继续拉 feed 刷新 stream
            } else if (files.length) {
              fillStream((files || []).map((url) => ({ url, rank: "cached", title: "" })));
              paintAll(0);
              if (credit) {
                credit.textContent =
                  `影像池 ${files.length} 张 · 分页 ${ROUTE_KEYS.length} · ` +
                  (poolsPayload.refreshed_at ? `池刷新 ${poolsPayload.refreshed_at}` : "本地切分") +
                  (poolsPayload.ephemeral ? " · 瞬时" : " · 维护批次");
              }
              // 仍拉 gallery 更新马赛克/热度，但不覆盖 routePools
            }
          }
        }
      } catch (_) {}

      try {
        const q = `/api/hub/gallery?mode=${encodeURIComponent(mode)}&limit=240${force ? "&force=1" : ""}&_=${Date.now()}`;
        const res = await fetch(q, { credentials: "same-origin", cache: "no-store" });
        if (res.ok) payload = await parseJsonResponse(res, res.url || "");
      } catch (_) {}

      if (!payload || !(payload.items || []).length) {
        try {
          const res = await fetch("/static/assets/zf-gallery/manifest.json?v=20260925b", {
            credentials: "same-origin",
          });
          if (res.ok) {
            const man = await parseJsonResponse(res, res.url || "");
            payload = {
              count: man.count,
              local_count: man.count,
              live_count: 0,
              warming: false,
              items: (man.items || []).map((it) => ({
                url: "/static/assets/zf-gallery/" + it.file,
                landing: it.commons || "",
                rank: "cached",
                title: it.title || "",
              })),
            };
          }
        } catch (_) {}
      }
      if (!payload) return payload;

      const galleryFiles = Array.from(
        new Set((payload.items || []).map((it) => it.url).filter(Boolean))
      );
      if (galleryFiles.length) files = galleryFiles;
      // 若 bg-pools 未填满，回退本地分桶
      const filled = ROUTE_KEYS.some((k) => (routePools[k] || []).length >= 4);
      if (!filled) rebuildRoutePools(poolEpoch);
      fillStream(payload.items || []);
      paintAll(0);
      if (credit) {
        const live = payload.live_count || 0;
        const local = payload.local_count || 0;
        const tag = force ? "强制刷新" : payload.warming ? "后台拉取中…" : payload.cached ? "缓存" : "实时";
        const src = payload.sources || {};
        credit.textContent =
          `影像流 ${payload.count || files.length} 张 · 实时/镜像 ${live} · 本地 ${local}` +
          ` · ${tag} · 模式 ${mode} · 分页池` +
          (src.inat_hot != null
            ? ` · iNat热${src.inat_hot || 0}/新${src.inat_latest || 0} · Commons${(src.commons || 0) + (src.commons_sci || 0)} · OV${src.openverse || 0}`
            : "");
      }
      return payload;
    };

    const startWarmPoll = (forcePoll = false) => {
      if (warmTimer) clearInterval(warmTimer);
      let n = 0;
      warmTimer = setInterval(async () => {
        n += 1;
        const p = await load(false);
        const done = forcePoll ? n >= 16 : !p?.warming || n >= 24;
        if (done) {
          clearInterval(warmTimer);
          warmTimer = null;
          const btn = document.getElementById("galleryRefreshBtn");
          if (btn) {
            btn.disabled = false;
            btn.textContent = "刷新";
          }
        }
      }, 3500);
    };

    document.querySelectorAll(".reef-mode-btn[data-gallery-mode]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        mode = btn.getAttribute("data-gallery-mode") || "mix";
        document.querySelectorAll(".reef-mode-btn[data-gallery-mode]").forEach((b) => {
          b.classList.toggle("active", b === btn);
        });
        const refreshBtn = document.getElementById("galleryRefreshBtn");
        if (refreshBtn) {
          refreshBtn.disabled = true;
          refreshBtn.textContent = "切换中…";
        }
        if (credit) credit.textContent = `切换到「${mode}」并刷新…`;
        await load(true);
        startWarmPoll(true);
      });
    });
    document.getElementById("galleryRefreshBtn")?.addEventListener("click", async () => {
      const btn = document.getElementById("galleryRefreshBtn");
      if (btn?.disabled) return;
      if (btn) {
        btn.disabled = true;
        btn.textContent = "刷新中…";
      }
      if (credit) credit.textContent = "强制刷新影像流：清理缓存并拉取最新…";
      await load(true);
      startWarmPoll(true);
    });

    const first = await load(false);
    if (first?.warming) startWarmPoll();
    if (timer) clearInterval(timer);
    let tick = 0;
    timer = setInterval(() => {
      tick += 1;
      paintAll(tick);
    }, 2800);
    document.addEventListener("zf-feature-open", () => paintAll(tick));
    // KPI 标签的语言：i18n 切语言时会先改 <html lang> 再派发 hub-lang-change，
    // 而且首屏可能是「先画 KPI、后切语言」，所以两条路都挂上，并延后一拍再画，
    // 否则中文界面里会出现半中半英的标签（Assembly / 基因 混排）。
    const refreshKpisForLang = () => {
      if (lastFeatureKey) {
        setTimeout(() => { try { renderFeatureKpis(lastFeatureKey); } catch (_) {} }, 60);
      }
      try { renderFeatureFoot(); } catch (_) {}
      try { if (lastFeatureKey) applyFeatureStatic(PAGE_META[lastFeatureKey]); } catch (_) {}
      try { syncModeIndicator(); } catch (_) {}
    };
    document.addEventListener("hub-lang-change", refreshKpisForLang);
    try {
      new MutationObserver(refreshKpisForLang)
        .observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
    } catch (_) { /* 老浏览器忽略 */ }
    // 每 10 分钟软刷新图源；每 3 分钟旋转各路由互斥子集
    setInterval(() => {
      load(false).then((p) => {
        if (p?.warming) startWarmPoll();
      });
    }, 10 * 60 * 1000);
    setInterval(() => {
      poolEpoch += 1;
      rebuildRoutePools(poolEpoch);
      paintAll(tick);
    }, 3 * 60 * 1000);
  }

  /** 智能问答背景：
   *  2026-10-12 按设计稿（6.png）改为**全站静态浅蓝背景**（theme-agent.css），
   *  原来的真实照片轮播（每 5.5s 换一手）已停用；保留函数与上面的取池逻辑，
   *  若以后要恢复轮播，把下面这个 early return 去掉即可。
   */
  async function initChatReefFlow() {
    const a = document.getElementById("chatReefA");
    const b = document.getElementById("chatReefB");
    if (!a || !b) return;
    return;                       // ← 静态背景：不再轮播照片
    // eslint-disable-next-line no-unreachable
    let pool = [];

    const loadPool = async () => {
      let urls = [];
      try {
        const res = await fetch("/api/hub/bg-pools?_=" + Date.now(), { cache: "no-store" });
        if (res.ok) {
          const data = await parseJsonResponse(res, res.url || "");
          const pools = data.pools || {};
          urls = pools.chat || pools.hub || [];
        }
      } catch (_) {}
      if (urls.length < 6) {
        try {
          const res = await fetch("/api/hub/gallery?mode=mix&limit=120", { cache: "no-store" });
          if (res.ok) {
            const data = await parseJsonResponse(res, res.url || "");
            const all = (data.items || [])
              .map((it) => it.url)
              .filter((u) => u && String(u).startsWith("/static/"));
            urls = all.filter((_, i) => i % 11 === 1);
            if (urls.length < 6) urls = all.slice(0, 24);
          }
        } catch (_) {}
      }
      if (urls.length < 6) {
        try {
          const res = await fetch("/static/assets/zf-gallery/manifest.json?v=20260925b", {
            cache: "force-cache",
          });
          if (res.ok) {
            const man = await parseJsonResponse(res, res.url || "");
            urls = (man.items || []).map((it) => "/static/assets/zf-gallery/" + it.file);
          }
        } catch (_) {}
      }
      return (urls || []).filter(Boolean);
    };

    pool = await loadPool();
    if (!pool.length) return;

    let i = 0;
    let showA = true;
    const setBg = (el, url) => {
      el.style.backgroundImage = `url("${url}")`;
    };
    setBg(a, pool[0]);
    a.classList.add("is-active");
    setBg(b, pool[1 % pool.length]);
    setInterval(() => {
      if (!pool.length) return;
      i = (i + 1) % pool.length;
      const next = pool[i];
      if (showA) {
        setBg(b, next);
        b.classList.add("is-active");
        a.classList.remove("is-active");
      } else {
        setBg(a, next);
        a.classList.add("is-active");
        b.classList.remove("is-active");
      }
      showA = !showA;
    }, 5500);
    setInterval(async () => {
      const next = await loadPool();
      if (next.length) {
        pool = next;
        i = 0;
        setBg(a, pool[0]);
        setBg(b, pool[1 % pool.length]);
      }
    }, 3 * 60 * 1000);
  }

  if (!location.hash || location.hash === "#") location.hash = "#/home";
  applyRoute();
  fillHomeStats();
  setTimeout(fillHomeStats, 2500);
  setInterval(fillHomeStats, 60000);

  // 实时更新钩子（web/js/live-update.js 消费）：只暴露"会随内容变化"的两块刷新入口。
  // 注意：gallery 走既有刷新按钮的完整流程（含强制刷新 + 预热轮询），不要自己另写一套。
  window.ZFRefresh = {
    stats: () => {
      try { fillHomeStats(); } catch (e) { /* 静默：实时刷新失败不影响主流程 */ }
    },
    gallery: () => {
      const b = document.getElementById("galleryRefreshBtn");
      if (b && !b.disabled) b.click();
    },
    /* 文献检索：2026-10-28 调试检索页已删除，这里不再跳转，只提示用户直接提问。 */
    literature: () => {
      const box = document.getElementById("retrievalQuery");
      const q = box && box.value ? box.value.trim() : "";
      if (q) location.hash = "#/chat/q=" + encodeURIComponent(q);
    },
  };

  initZfGallery();
  initChatReefFlow();

  // 上文指示条：清空本轮上文（只清对话上下文，不动历史会话记录）
  document.getElementById("ctxClearBtn")?.addEventListener("click", () => {
    clearChatHistory();
    const cur = conversations.find((c) => c.id === activeConvId);
    if (cur) cur.apiHistory = [];
    saveConversationsToStorage();
    updateContextChip();
  });
  updateContextChip();
  bindChatExport();
  updateChatTools();

  document.getElementById("opsRefreshBtn")?.addEventListener("click", () => renderOpsPanel());
  document.getElementById("opsRunStatsBtn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "触发中…";
    try {
      const r = await apiFetch("/api/hub/maintenance/run?job=stats", { method: "POST" });
      btn.textContent = r.ok ? "已触发" : `失败 HTTP ${r.status}`;
      if (r.ok) setTimeout(() => renderOpsPanel(), 800);
    } catch (err) {
      btn.textContent = "失败";
    } finally {
      btn.disabled = false;
      setTimeout(() => { btn.textContent = old; }, 1800);
    }
  });

  // 回答溯源徽章兜底：观察消息区变化，任何渲染路径定稿后都补一次徽章
  if (messagesEl) {
    new MutationObserver(scheduleBadgeSweep).observe(messagesEl, { childList: true, subtree: true, characterData: true });
    scheduleBadgeSweep();
  }
  // 工具结果卡溯源：所有工具共用 #toolResult，统一在渲染后补一行“结果来自”
  if (toolResult) {
    new MutationObserver(scheduleToolSourceSweep).observe(toolResult, { childList: true, subtree: true, characterData: true });
    scheduleToolSourceSweep();
  }
})();
