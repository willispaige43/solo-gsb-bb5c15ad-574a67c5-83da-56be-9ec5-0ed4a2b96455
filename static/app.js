/* 邮件线程重建台前端（原生 JS，无框架） */
(function () {
  "use strict";

  const container = document.getElementById("thread-container");
  const statsEl = document.getElementById("stats");
  const modal = document.getElementById("import-modal");
  const fileInput = document.getElementById("file-input");
  const dropzone = document.getElementById("dropzone");
  const fileNameEl = document.getElementById("file-name");
  const rawInput = document.getElementById("raw-input");
  const importError = document.getElementById("import-error");

  let selectedFile = null;
  // 记录默认展开的线程根；切换筛选时保持展开状态
  const expanded = new Set();

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fmtDate(node) {
    if (node.date_ts != null) {
      const d = new Date(node.date_ts * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
             `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
    return node.date_raw ? esc(node.date_raw) : "时间缺失";
  }

  function currentFilter() {
    return document.querySelector('input[name="filter"]:checked').value;
  }

  function countNodes(node) {
    return 1 + node.children.reduce((n, c) => n + countNodes(c), 0);
  }

  function hasRealChild(node) {
    return node.children.some((c) => !c.placeholder || c.children.length || hasRealChild(c));
  }

  function badges(node) {
    const out = [];
    if (node.placeholder) out.push('<span class="badge placeholder">缺失占位</span>');
    if (node.is_cycle) {
      out.push('<span class="badge cycle">循环断开</span>');
    }
    return out.join(" ");
  }

  function flags(node) {
    return [
      node.is_cycle ? "flag-cycle" : "",
      node.placeholder ? "flag-placeholder" : "",
    ].join(" ").trim();
  }

  function detailHtml(node) {
    const refs = node.references && node.references.length
      ? node.references.map(esc).join("<br>") : "（无）";
    const broken = node.broken_edge
      ? `<div><span class="label">断开的父引用：</span><code>${esc(node.broken_edge)}</code></div>` : "";
    return `
      <div class="detail" data-detail="${esc(node.message_id)}">
        <div><span class="label">Message-ID：</span><code>${esc(node.message_id)}</code></div>
        <div><span class="label">From：</span>${esc(node.from || "（未知）")}</div>
        <div><span class="label">In-Reply-To：</span><code>${esc(node.in_reply_to || "（无）")}</code></div>
        <div><span class="label">References：</span><br>${refs}</div>
        ${broken}
      </div>`;
  }

  function renderNode(node, isRoot) {
    const subject = node.placeholder && !node.subject
      ? "（缺失的父邮件）"
      : (node.subject || "（无主题）");
    const leaf = node.children.length === 0;
    const twisty = `<span class="twisty ${leaf ? "leaf" : ""}">▶</span>`;
    const id = `msg-${node.message_id.replace(/[^a-zA-Z0-9]/g, "_")}`;

    if (isRoot) {
      const badge = badges(node) ||
        (leaf && !node.placeholder ? '<span class="badge orphan">孤立</span>' : "");
      return `
        <div class="thread" data-root="${esc(node.message_id)}">
          <button class="thread-root ${flags(node)}" data-toggle="${esc(node.message_id)}">
            ${twisty}
            <span class="node-main">
              <span class="node-subject">${esc(subject)} ${badge}</span>
              <span class="node-meta">
                ${fmtDate(node)} · ${esc(node.from || "未知发件人")} ·
                <code>${esc(node.message_id)}</code>
              </span>
            </span>
            <span class="count-badge">${countNodes(node)} 封</span>
          </button>
          <div class="children" data-children="${esc(node.message_id)}">
            ${node.children.map((c) => renderNode(c, false)).join("")}
          </div>
        </div>`;
    }

    return `
      <div id="${id}" class="message ${flags(node)}" data-mid="${esc(node.message_id)}">
        <div class="node-row" data-detail-toggle="${esc(node.message_id)}">
          ${twisty}
          <span class="node-main">
            <span class="node-subject">${esc(subject)} ${badges(node)}</span>
            <span class="node-meta">
              ${fmtDate(node)} · ${esc(node.from || "未知发件人")} ·
              <code>${esc(node.message_id)}</code>
            </span>
          </span>
        </div>
        ${detailHtml(node)}
        ${node.children.length
          ? `<div class="children" data-children="${esc(node.message_id)}">
               ${node.children.map((c) => renderNode(c, false)).join("")}
             </div>`
          : ""}
      </div>`;
  }

  function render(data) {
    const s = data.stats || {};
    statsEl.innerHTML = `
      <span>邮件 <b>${s.total ?? 0}</b></span>
      <span>线程 <b>${s.thread_roots ?? 0}</b></span>
      <span>孤立 <b>${s.orphans ?? 0}</b></span>
      <span>缺失占位 <b>${s.placeholders ?? 0}</b></span>
      <span>断开循环 <b>${s.cycles ?? 0}</b></span>`;

    if (!data.threads || data.threads.length === 0) {
      container.innerHTML = '<p class="empty">没有符合条件的线程。</p>';
      return;
    }
    container.innerHTML = data.threads.map((t) => renderNode(t, true)).join("");

    // 默认折叠；含占位或断环标记的线程根自动展开以便发现问题
    document.querySelectorAll(".thread").forEach((th) => {
      const mid = th.getAttribute("data-root");
      if (th.querySelector(".badge.placeholder, .badge.cycle")) {
        expanded.add(mid);
      }
    });
    syncTreeState();
  }

  function syncTreeState() {
    document.querySelectorAll("[data-children]").forEach((childEl) => {
      const mid = childEl.getAttribute("data-children");
      const open = expanded.has(mid);
      childEl.classList.toggle("collapsed", !open);
      const twisty = document.querySelector(
        `[data-toggle="${CSS.escape(mid)}"] .twisty, ` +
        `[data-mid="${CSS.escape(mid)}"] > .node-row .twisty`
      );
      if (twisty && !twisty.classList.contains("leaf")) {
        twisty.classList.toggle("open", open);
      }
    });
  }

  async function load() {
    const f = currentFilter();
    try {
      const res = await fetch(`/api/threads?filter=${encodeURIComponent(f)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      render(await res.json());
    } catch (e) {
      container.innerHTML = `<p class="empty">加载失败：${esc(e.message)}</p>`;
    }
  }

  // 展开 / 折叠 / 查看详情
  container.addEventListener("click", (ev) => {
    const twistyHit = ev.target.classList.contains("twisty");

    const rootToggle = ev.target.closest("[data-toggle]");
    if (rootToggle) {
      const mid = rootToggle.getAttribute("data-toggle");
      if (expanded.has(mid)) expanded.delete(mid); else expanded.add(mid);
      syncTreeState();
      return;
    }

    const row = ev.target.closest("[data-detail-toggle]");
    if (row) {
      const mid = row.getAttribute("data-detail-toggle");
      const msg = row.closest(".message");
      const childEl = msg.querySelector(":scope > .children");
      if (twistyHit && childEl) {
        if (expanded.has(mid)) expanded.delete(mid); else expanded.add(mid);
        syncTreeState();
      } else {
        msg.querySelector(":scope > .detail")?.classList.toggle("open");
      }
    }
  });

  // 筛选
  document.getElementById("filters").addEventListener("change", load);
  document.getElementById("btn-refresh").addEventListener("click", load);

  // 清空
  document.getElementById("btn-clear").addEventListener("click", async () => {
    if (!confirm("确定清空全部邮件与线程数据吗？")) return;
    await fetch("/api/messages", { method: "DELETE" });
    expanded.clear();
    load();
  });

  // 导入弹窗
  document.getElementById("btn-import").addEventListener("click", () => {
    modal.classList.remove("hidden");
  });
  document.getElementById("btn-import-cancel").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

  function closeModal() {
    modal.classList.add("hidden");
    importError.textContent = "";
  }

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    selectedFile = fileInput.files[0] || null;
    fileNameEl.textContent = selectedFile ? selectedFile.name : "";
  });
  ["dragover", "dragenter"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); }));
  dropzone.addEventListener("drop", (e) => {
    selectedFile = e.dataTransfer.files[0] || null;
    fileNameEl.textContent = selectedFile ? selectedFile.name : "";
  });

  document.getElementById("btn-import-submit").addEventListener("click", async () => {
    importError.textContent = "";
    const fd = new FormData();
    if (selectedFile) fd.append("file", selectedFile);
    if (rawInput.value.trim()) fd.append("raw", rawInput.value);
    if (!selectedFile && !rawInput.value.trim()) {
      importError.textContent = "请选择文件或粘贴邮件文本。";
      return;
    }
    try {
      const res = await fetch("/api/import", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) {
        importError.textContent = data.detail || `导入失败（HTTP ${res.status}）`;
        return;
      }
      selectedFile = null;
      fileInput.value = "";
      fileNameEl.textContent = "";
      rawInput.value = "";
      closeModal();
      expanded.clear();
      render(data);
    } catch (e) {
      importError.textContent = "请求失败：" + e.message;
    }
  });

  load();
})();
