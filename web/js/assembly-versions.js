/* ==========================================================================
   assembly-versions.js —— 全站「装配版本」下拉的单一事实来源（2026-11-07）
   --------------------------------------------------------------------------
   背景：Liftover「坐标换算」的来源/目标版本有 18 个（GRCz 系列 / Zv 系列 /
   NCBI 品系组装三个分组），「批量 ID 互转」原先只有 2 个。2026-11-12 起
   **批量 ID 互转也开放全部 18 个装配**：后端 id_version_map 对没有独立基因
   注释表的 16 个装配，输出跨版本稳定标识符（符号 / Ensembl / ZFIN / NCBI /
   UniProt）+ 由 UCSC chain 换算出的该版本坐标，因此每一版都是真能出结果的，
   不再需要按能力削减。

   本模块仍以 #liftSourceBuild 为唯一模板（新增装配只改 HTML 一处）：

     · idmap（原/目标）  → 全部 18 版，三个分组照搬模板。
     · seq-target（目标）→ GRCz11 / GRCz12tu。只有这两版有序列数据源
                           （Ensembl REST / NCBI RefSeq EFetch）。
     · seq-gene（按基因）→ 自动 + 上述两版。
     · seq-source（来源）→ 保留**秒级能出结果**的 12 版。来源版本只决定输入
       坐标属于哪套装配，只要能经 UCSC chain 换算到 GRCz11 / GRCz12tu 就能取
       序列；线上 36 条 chain 全部就位，但其中 6 个装配的 chain 首次加载要
       60–140 s，HTTP 预算内必定超时（见 SLOW_SOURCE_BUILDS），同样属于
       「无真实产出结果」，按用户要求不列出。

   后端仍做严格白名单校验：不认识的装配一律 400，绝不静默当成 GRCz12tu 取出
   坐标对不上、却看起来正常的碱基。
   ========================================================================== */
(function () {
  "use strict";

  var SOURCE_ID = "liftSourceBuild";

   /* 有本地数据、能真正产出结果的装配 */
  var DATA_BUILDS = { GRCz11: true, GRCz12tu: true };

  /* 2026-11-07 线上实测（18 个来源 × 5:1000000-1000100 → GRCz12tu）：
     下列 6 个装配的 chain 首次加载就要 60–140 s（实测 GRCz13ab 138.8 s、
     ASM2018471v1 141.6 s、fDanRer4.1 100.8 s、ASM1840007v1 59.6 s；
     另两个品系组装在 HTTP 90 s 预算内同样超时），
     虽然 lift_region 最终 ok=True，但 HTTP 90 s 预算内用户只会等到超时 ——
     按「无真实产出结果就去掉」的口径，不列入序列页的来源下拉。
     坐标换算页（#liftSourceBuild）仍保留这 18 项：那里不取碱基、语义不同，
     且本模块以它为唯一模板，删了就少了新增装配的单一入口。
     若以后给 chain 做进程级缓存 + 启动预热、或放宽接口超时，把它们从这里
     移除即可恢复，无需改别处。 */
  var SLOW_SOURCE_BUILDS = {
    GRCz13ab: true,
    ASM1840007v1: true,
    ASM2018471v1: true,
    "fDanRer4.1": true,
    "fDreABH1.1": true,
    "fDreTuH1.2": true
  };

  var I18N = {
    group: {
      "GRCz 系列": "GRCz series",
      "Zv 系列": "Zv series",
      "NCBI 品系组装": "NCBI strain assemblies",
      "Ensembl 版本": "Ensembl releases"
    },
    /* 2026-11-07：用户要求清掉下拉选项里所有括号说明/口径注释，
       标签一律只留纯名称。这里只保留「自动」的双语，其余原样。 */
    label: { "自动": "Auto" }
  };

  /* 2026-11-13：Ensembl release 基因集版本只属于「批量 ID 互转」，
     不能塞进模板 #liftSourceBuild —— 坐标换算页要的是参考组装，
     resolve_route 不认 ens:*。所以由 idmap profile 单独追加这一组，
     其余页面（坐标换算 / 序列提取）完全不受影响。
     值必须与后端 ensembl_version_map.build_catalog() 一致。 */
  var ENSEMBL_GROUP = {
    label: "Ensembl 版本",
    labelEn: "Ensembl releases",
    options: [
      { value: "ens:54", label: "Ensembl r54" },
      { value: "ens:77", label: "Ensembl r77" },
      { value: "ens:79", label: "Ensembl r79" },
      { value: "ens:80", label: "Ensembl r80" },
      { value: "ens:116", label: "Ensembl r116" }
    ]
  };

  /* 四类下拉的能力配置：
       allow = 白名单对象：只保留这些版本（不生成点不动的置灰项）；
       allow = null：全部保留（来源版本每一版都有 chain 可达序列版本）。 */
  var PROFILES = {
    idmap: {
      /* 2026-11-12：18 版全部可转换 —— 没有本地注释表的装配由后端给出
         稳定标识符 + UCSC chain 换算坐标（见 id_version_map._convert_generic）。
         这里不再按 DATA_BUILDS 削减，allow:null = 照搬模板的全部选项。
         2026-11-13：另追加 Ensembl release 组，见 ENSEMBL_GROUP。 */
      allow: null,
      labels: {},
      extraGroups: [ENSEMBL_GROUP]
    },
    "seq-source": {
      allow: null,
      deny: SLOW_SOURCE_BUILDS,
      labels: {}
    },
    "seq-target": {
      allow: DATA_BUILDS,
      labels: {}
    },
    "seq-gene": {
      allow: DATA_BUILDS,
      labels: {},
      extraFirst: {
        value: "auto",
        label: "自动"
      }
    }
  };

  function t(zh, lang) {
    if (lang !== "en") return zh;
    return (I18N.label && I18N.label[zh]) || (I18N.group && I18N.group[zh]) || zh;
  }

  function currentLang() {
    try {
      /* i18n.js 暴露的是 window.HubI18n.lang（getter，字符串）；defer 脚本执行时
         i18n 的 DOMContentLoaded 初始化可能还没跑，所以再兜一层 localStorage。 */
      if (window.HubI18n && window.HubI18n.lang) return window.HubI18n.lang;
      return localStorage.getItem("hub_lang_v2") || "en";
    } catch (e) {
      return "en";
    }
  }

  /* 2026-11-07 用户要求：下拉选项里不准再出现「· 12994 基因」「无 ID 对照表」
     这类说明/统计尾巴，也不准出现括号口径注释。这里是**结构性防线** ——
     即使以后有人往模板 #liftSourceBuild 的 option 里手写了后缀，克隆到各页时
     也会被剥掉，只留纯装配名。新增装配只需写 value + 名称，不用记得守规矩。 */
  function cleanLabel(text) {
    return String(text || "")
      .replace(/\s+/g, " ")
      .replace(/\s*[·|]\s*.*$/, "")        // 「 · T5D」「 · 12994 基因」
      .replace(/[（(][^）)]*[）)]\s*$/, "")  // 行尾括号注释
      .replace(/^版本\s*[:：]\s*/, "")      // 「版本：xxx」前缀
      .trim();
  }

  function tweakOption(opt, cfg, lang) {
    var v = String(opt.value || "");
    var plain = cleanLabel(opt.textContent);
    if (!v) return false;                       // 「请选择…」占位项不克隆
    /* 无真实产出结果的版本直接不生成 —— 用户明确要求不要置灰占位。
       只保留白名单内的版本，选项本身永远可点、点了就一定有结果。 */
    if (cfg.allow && !cfg.allow[v]) return false;
    if (cfg.deny && cfg.deny[v]) return false;
    opt.textContent = (cfg.labels && cfg.labels[v]) ? t(cfg.labels[v], lang) : plain;
    return true;
  }

  function fill(sel, src, cfg, lang, keepValue) {
    var frag = document.createDocumentFragment();
    if (cfg.extraFirst) {
      var o = document.createElement("option");
      o.value = cfg.extraFirst.value;
      o.textContent = t(cfg.extraFirst.label, lang);
      frag.appendChild(o);
    }
    Array.prototype.forEach.call(src.children || [], function (node) {
      if (!node || node.tagName === "OPTION") {
        if (!node) return;
        var one = node.cloneNode(true);
        if (tweakOption(one, cfg, lang)) frag.appendChild(one);
        return;
      }
      if (node.tagName !== "OPTGROUP") return;
      var group = document.createElement("optgroup");
      var raw = node.getAttribute("label") || "";
      group.setAttribute("label", t(raw, lang));
      Array.prototype.forEach.call(node.children || [], function (child) {
        if (!child || child.tagName !== "OPTION") return;
        var oc = child.cloneNode(true);
        if (tweakOption(oc, cfg, lang)) group.appendChild(oc);
      });
      if (group.children.length) frag.appendChild(group);
    });

    /* 2026-11-13：只给 idmap 追加 Ensembl release 分组。
       这些选项不走 tweakOption（不套 DATA_BUILDS 白名单、不剥标签），
       文案由本模块按语言直接生成。 */
    (cfg.extraGroups || []).forEach(function (g) {
      var group = document.createElement("optgroup");
      group.setAttribute("label", lang === "en" ? (g.labelEn || g.label) : g.label);
      (g.options || []).forEach(function (o) {
        var opt = document.createElement("option");
        opt.value = o.value;
        opt.textContent = lang === "en" ? (o.labelEn || o.label) : o.label;
        group.appendChild(opt);
      });
      if (group.children.length) frag.appendChild(group);
    });

    /* 整组被过滤空的 optgroup 在 fill() 里就不会生成；这里不再需要
       data-zf-keep-disabled-groups（已无置灰项），select-ui 按常规逻辑画即可。 */
    /* option 文案由本模块自己按语言生成，禁止通用 i18n 再翻一遍（会重复命中）。 */
    sel.setAttribute("data-no-i18n", "1");
    sel.innerHTML = "";
    sel.appendChild(frag);

    var want = keepValue || sel.getAttribute("data-zf-default") || "";
    if (want) {
      for (var i = 0; i < sel.options.length; i += 1) {
        if (sel.options[i].value === want && !sel.options[i].disabled) {
          sel.selectedIndex = i;
          break;
        }
      }
    }
    /* 不派发 change：初始化 / 切语言只是重画选项，不是用户改了版本。
       select-ui.js 的 MutationObserver 会自己把按钮文字刷新。 */
  }

  function buildAll(keepValues) {
    var src = document.getElementById(SOURCE_ID);
    if (!src || !src.children || !src.children.length) return;
    var lang = String(currentLang()).toLowerCase().indexOf("en") === 0 ? "en" : "zh";
    Array.prototype.forEach.call(
      document.querySelectorAll("select[data-zf-builds]"),
      function (sel) {
        var cfg = PROFILES[sel.getAttribute("data-zf-builds")];
        if (!cfg) return;
        var keep = keepValues ? sel.value : null;
        fill(sel, src, cfg, lang, keep);
      }
    );
  }

  function boot() {
    buildAll(false);
    /* defer 脚本执行时 readyState 已是 interactive，而 i18n.js 的 init 绑在
       DOMContentLoaded 上、要等本轮事件跑完才建好 window.HubI18n。这里排一个
       macrotask 用真实语言重画一次（保留已选值），避免英文界面短暂挂中文选项。 */
    setTimeout(function () { buildAll(true); }, 0);
    /* i18n 初始化在 DOMContentLoaded，可能晚于本 defer 脚本；load 时再兜底一次。 */
    window.addEventListener("load", function () { buildAll(true); }, { once: true });
    document.addEventListener("hub-lang-change", function () { buildAll(true); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  window.ZFAssemblyVersions = { rebuild: buildAll };
})();
