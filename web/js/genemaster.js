/* 基因主表：所有标识符统一映射到 GRCz12tu 后的非冗余功能基因。
 *
 * 与「别名」页的分工：
 *   别名页   答「这个名字是哪个基因、它还有哪些叫法」
 *   本页     答「这个基因在最新参考基因组上的唯一落点，以及这条映射的依据」
 *
 * 数据来自 gene_master_grcz12tu / gene_id_map_grcz12tu，
 * 由后端标识符统一映射管线产出（共线性投影 + BLAST 序列比对 + ID 直连）。
 */
(function () {
  "use strict";

  var API = "/api/gene/master";
  var resultEl = null;

  // 表格里的短标签：完整描述放在概览卡片，表格只留最短可辨识的说法，
  // 否则「NCBI Gene ID 直连」这类长标签会把列挤成两行，甚至把
  // protein_coding 折断成 protein_c / oding。
  var SHORT = {
    id_direct: "ID 直连",
    assembly_xref: "库内坐标",
    blast_100: "序列 100%",
    blast: "序列比对",
    synteny: "共线性",
    symbol_match: "符号匹配",
    legacy_synteny: "老版坐标",
  };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function box() {
    if (!resultEl) resultEl = document.getElementById("toolResult");
    return resultEl;
  }

  function put(html) {
    var b = box();
    if (!b) return;
    b.innerHTML = html;
    b.classList.remove("hidden");
  }

  function num(n) {
    return String(n == null ? 0 : n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  /* ---------- 概览 ---------- */
  function renderStats(d) {
    var el = document.getElementById("gmStats");
    if (!el || !d || !d.ok) return;
    // 值保持短：左面板只有 ~320px，长串会被折成三行反而更难读；
    // 完整信息放 title，悬停可见。
    var cards = [
      ["参考基因组", "GRCz12tu", d.reference || ""],
      ["非冗余功能基因", num(d.functional_total), ""],
      ["本地基因已映射", num(d.source_genes_mapped) + " / " + num(d.source_genes_total),
        "覆盖率 " + (d.coverage_pct || 0) + "%"],
      ["多对一合并", num(d.merged_multi_source), "多个本地基因收敛到同一 GRCz12tu 基因"],
    ];
    var html = '<div class="gm-cards">';
    for (var i = 0; i < cards.length; i++) {
      var tip = cards[i][2] ? ' title="' + esc(cards[i][2]) + '"' : "";
      html += '<div class="gm-card"' + tip + '><span class="gm-card-k">' + esc(cards[i][0]) +
        '</span><span class="gm-card-v">' + esc(cards[i][1]) + "</span></div>";
    }
    html += "</div>";

    if (d.methods && d.methods.length) {
      html += '<div class="gm-methods"><span class="gm-card-k">映射依据</span>';
      for (var j = 0; j < d.methods.length; j++) {
        var m = d.methods[j];
        html += '<span class="gm-method"><b>' + esc(m.label || m.method) + "</b> " +
          num(m.n) + "</span>";
      }
      html += "</div>";
    }
    el.innerHTML = html;
  }

  function loadStats() {
    fetch(API + "/stats")
      .then(function (r) { return r.json(); })
      .then(renderStats)
      .catch(function () { /* 概览失败不影响检索 */ });
  }

  /* ---------- 检索结果 ---------- */
  function renderRows(d) {
    var items = d.items || [];
    if (!items.length) {
      put('<p class="tool-hint">没有匹配的基因。</p>');
      return;
    }
    var html = '<div class="zf-kv">' +
      '<div class="zf-kv-item"><span class="zf-kv-k">命中</span><span class="zf-kv-v">' +
      num(d.total) + "</span></div>" +
      '<div class="zf-kv-item"><span class="zf-kv-k">显示</span><span class="zf-kv-v">' +
      items.length + "</span></div></div>";

    // 不展示 biotype 列：本页默认已筛「仅功能基因」，类型几乎全是 protein_coding，
    // 留着只会把「位置」和「映射依据」挤到换行/被裁。
    html += '<div class="table-wrap"><table class="gene-table"><thead><tr>' +
      "<th>GRCz12tu ID</th><th>符号</th><th>位置</th>" +
      "<th>GO</th><th>KEGG</th><th>依据</th></tr></thead><tbody>";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      html += "<tr>" +
        '<td class="zf-nowrap">' + esc(it.gene_id) + "</td>" +
        '<td class="zf-nowrap">' + esc(it.symbol || "—") + "</td>" +
        '<td class="zf-nowrap">' + esc((it.chrom || "?") + ":" + (it.start || "?") + "-" + (it.end || "?")) + "</td>" +
        "<td>" + esc(it.n_go) + "</td>" +
        "<td>" + esc(it.n_kegg) + "</td>" +
        '<td class="zf-nowrap" title="' + esc(it.method_label || it.method || "") + '">' +
        esc(SHORT[it.method] || it.method_label || it.method || "") + "</td>" +
        "</tr>";
    }
    html += "</tbody></table></div>";
    if (d.total > items.length) {
      html += '<p class="tool-hint">共 ' + num(d.total) + " 条，已显示前 " + items.length + " 条</p>";
    }
    put(html);
  }

  function search(q, offset) {
    var input = document.getElementById("gmSearchInput");
    var only = document.getElementById("gmFuncOnly");
    q = (q == null ? (input ? input.value : "") : q).trim();
    var url = API + "/search?q=" + encodeURIComponent(q) +
      "&functional_only=" + (only && only.checked ? 1 : 0) + "&limit=50&offset=" + (offset || 0);
    put('<p class="tool-hint">检索中…</p>');
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(renderRows)
      .catch(function (e) {
        put('<p class="tool-hint">检索失败：' + esc(e && e.message ? e.message : e) + "</p>");
      });
  }

  function init() {
    var btn = document.getElementById("gmSearchBtn");
    var browse = document.getElementById("gmBrowseBtn");
    var input = document.getElementById("gmSearchInput");
    // 注意：必须传 null 让 search 自己去读输入框；传 "" 会覆盖掉用户输入，
    // 变成「浏览全部」。
    if (btn) btn.addEventListener("click", function () { search(null, 0); });
    if (browse) {
      browse.addEventListener("click", function () {
        if (input) input.value = "";
        search(null, 0);
      });
    }
    if (input) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); search("", 0); }
      });
    }
    loadStats();
  }

  window.ZFGeneMaster = { init: init, search: search, loadStats: loadStats };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
