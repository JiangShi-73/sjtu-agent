const API = "/api/v1";

const state = {
  board: null,
  onlyUnread: false,
  boards: [],
};

const $ = (sel) => document.querySelector(sel);

async function api(path, options) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function fmt(iso) {
  if (!iso) return "";
  return iso.replace("T", " ").slice(5, 16);
}

/* ---------- 板块 ---------- */

async function loadBoards() {
  const data = await api("/boards");
  state.boards = data;
  const nav = $("#boards");
  nav.innerHTML = "";

  const all = document.createElement("button");
  all.className = "board" + (state.board === null ? " active" : "");
  all.textContent = "全部";
  all.onclick = () => { state.board = null; loadBoards(); loadItems(); };
  nav.appendChild(all);

  for (const b of data) {
    const el = document.createElement("button");
    el.className = "board" + (state.board === b.key ? " active" : "");
    el.innerHTML = `<span>${b.name}</span>` +
      (b.unread ? `<span class="badge">${b.unread}</span>` : "");
    el.onclick = () => { state.board = b.key; loadBoards(); loadItems(); };
    nav.appendChild(el);
  }

  const title = data.find((b) => b.key === state.board);
  $("#board-title").textContent = title ? title.name : "全部";
}

/* ---------- 条目 ---------- */

async function loadItems() {
  const params = new URLSearchParams({ limit: "200" });
  if (state.board) params.set("board", state.board);
  if (state.onlyUnread) params.set("unread", "true");

  const { items } = await api("/items?" + params);
  const box = $("#items");
  box.innerHTML = "";
  $("#count").textContent = items.length ? `${items.length} 条` : "";

  if (!items.length) {
    box.innerHTML = '<div class="empty">还没有内容，点左下角「抓取全部」试试</div>';
    return;
  }

  for (const it of items) {
    const el = document.createElement("article");
    el.className = "item" + (it.read ? " read" : "");
    el.innerHTML = `
      <div class="item-head">
        ${it.category ? `<span class="tag">${it.category}</span>` : ""}
        <span class="item-title">${escapeHtml(it.title)}</span>
      </div>
      ${it.summary ? `<div class="item-summary">${escapeHtml(it.summary)}</div>` : ""}
      <div class="item-foot">
        <span>${fmt(it.published || it.created_at)}</span>
        ${it.deadline ? `<span class="deadline">截止 ${fmt(it.deadline)}</span>` : ""}
      </div>`;

    el.onclick = async () => {
      if (!it.read) {
        await api(`/items/${it.id}/read`, { method: "POST" });
        loadBoards();
        loadItems();
      }
      if (it.url) window.open(it.url, "_blank");
    };
    box.appendChild(el);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- 动作 ---------- */

async function fetchAll() {
  const btn = $("#btn-fetch");
  btn.disabled = true;
  $("#status").textContent = "抓取中…";
  try {
    const { results } = await api("/sources/fetch-all", { method: "POST" });
    const ok = results.filter((r) => r.ok).length;
    const added = results.reduce((s, r) => s + (r.added || 0), 0);
    $("#status").textContent = `${ok}/${results.length} 成功，新增 ${added} 条`;
    await loadBoards();
    await loadItems();
  } catch (e) {
    $("#status").textContent = "失败：" + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function diagnose() {
  const d = await api("/diagnose");
  $("#diag-body").innerHTML = d.ok
    ? "<p>没有发现错误。</p>"
    : "<pre>" + JSON.stringify(d, null, 2) + "</pre>";
  $("#diag").showModal();
}

/* ---------- 事件流 ---------- */

function connectEvents() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "items.added") {
      loadBoards();
      loadItems();
    }
    if (msg.type === "source.error") {
      $("#status").textContent = `${msg.key} 出错：${msg.error}`;
    }
  };
  ws.onclose = () => setTimeout(connectEvents, 3000);
}

/* ---------- 启动 ---------- */

$("#btn-fetch").onclick = fetchAll;
$("#btn-diag").onclick = diagnose;
$("#only-unread").onchange = (e) => {
  state.onlyUnread = e.target.checked;
  loadItems();
};

loadBoards();
loadItems();
connectEvents();
