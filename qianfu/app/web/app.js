const API = "/api/v1";
const WD = ["一", "二", "三", "四", "五", "六", "日"];

const state = {
  view: "feed",
  board: null,
  onlyUnread: false,
  month: new Date(),
  events: [],
  derived: [],
};

const $ = (sel) => document.querySelector(sel);

async function api(path, options) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(res.status + " " + res.statusText);
  return res.json();
}

const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());

function fmt(iso) {
  if (!iso) return "";
  return String(iso).replace("T", " ").slice(5, 16);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 视图切换 ---------------- */

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".view-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));

  const isCal = view === "calendar";
  $("#calendar").hidden = !isCal;
  $("#items").hidden = isCal;
  $("#boards").style.display = isCal ? "none" : "";
  $("#unread-wrap").style.display = isCal ? "none" : "";
  $("#cal-nav").hidden = !isCal;
  $("#board-title").textContent = isCal ? "日程" : "全部";

  if (isCal) loadCalendar();
  else { loadBoards(); loadItems(); }
}

/* ---------------- 信息流 ---------------- */

async function loadBoards() {
  const data = await api("/boards");
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
    el.innerHTML = "<span>" + b.name + "</span>" +
      (b.unread ? '<span class="badge">' + b.unread + "</span>" : "");
    el.onclick = () => { state.board = b.key; loadBoards(); loadItems(); };
    nav.appendChild(el);
  }

  const cur = data.find((b) => b.key === state.board);
  $("#board-title").textContent = cur ? cur.name : "全部";
}

async function loadItems() {
  const params = new URLSearchParams({ limit: "200" });
  if (state.board) params.set("board", state.board);
  if (state.onlyUnread) params.set("unread", "true");

  const { items } = await api("/items?" + params);
  const box = $("#items");
  box.innerHTML = "";
  $("#count").textContent = items.length ? items.length + " 条" : "";

  if (!items.length) {
    box.innerHTML = '<div class="empty">还没有内容，点左下角「抓取全部」试试</div>';
    return;
  }

  for (const it of items) {
    const el = document.createElement("article");
    el.className = "item" + (it.read ? " read" : "");
    el.innerHTML =
      '<div class="item-head">' +
        (it.category ? '<span class="tag">' + escapeHtml(it.category) + "</span>" : "") +
        '<span class="item-title">' + escapeHtml(it.title) + "</span>" +
      "</div>" +
      (it.summary ? '<div class="item-summary">' + escapeHtml(it.summary) + "</div>" : "") +
      '<div class="item-foot">' +
        "<span>" + fmt(it.published || it.created_at) + "</span>" +
        (it.deadline ? '<span class="deadline">截止 ' + fmt(it.deadline) + "</span>" : "") +
      "</div>";

    el.onclick = async () => {
      if (!it.read) {
        await api("/items/" + it.id + "/read", { method: "POST" });
        loadBoards();
        loadItems();
      }
      if (it.url) window.open(it.url, "_blank");
    };
    box.appendChild(el);
  }
}

/* ---------------- 日程 ---------------- */

async function loadCalendar() {
  const y = state.month.getFullYear();
  const m = state.month.getMonth();
  const start = ymd(new Date(y, m - 1, 20));
  const end = ymd(new Date(y, m + 2, 10));
  const data = await api("/events?start=" + start + "&end=" + end);
  state.events = data.events || [];
  state.derived = data.derived || [];
  renderCalendar();
}

function renderCalendar() {
  const y = state.month.getFullYear();
  const m = state.month.getMonth();
  $("#cal-label").textContent = y + " 年 " + (m + 1) + " 月";

  const first = new Date(y, m, 1);
  const offset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const rows = Math.ceil((offset + daysInMonth) / 7);
  const gridStart = new Date(y, m, 1 - offset);

  const todayStr = ymd(new Date());
  const grid = $("#cal-grid");
  grid.innerHTML = "";
  grid.style.gridTemplateRows = "repeat(" + rows + ", minmax(96px, 1fr))";

  for (let i = 0; i < rows * 7; i++) {
    const d = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
    const ds = ymd(d);
    const inMonth = d.getMonth() === m;

    const cell = document.createElement("div");
    cell.className = "cal-cell" + (inMonth ? "" : " dim") + (ds === todayStr ? " today" : "");

    const head = document.createElement("div");
    head.className = "cal-day";
    head.textContent = d.getDate();
    cell.appendChild(head);

    const list = document.createElement("div");
    list.className = "cal-events";

    for (const ev of state.events.filter((e) => e.event_date === ds)) {
      const chip = document.createElement("div");
      chip.className = "chip manual" + (ev.done ? " done" : "");
      chip.textContent = (ev.start_time ? ev.start_time + " " : "") + ev.title;
      chip.title = [ev.start_time, ev.end_time, ev.location].filter(Boolean).join(" ");
      chip.onclick = (e) => { e.stopPropagation(); openDialog(ev); };
      list.appendChild(chip);
    }

    for (const it of state.derived.filter((x) => String(x.deadline).slice(0, 10) === ds)) {
      const chip = document.createElement("div");
      chip.className = "chip derived";
      chip.textContent = String(it.deadline).slice(11, 16) + " 截止 · " + it.title;
      chip.title = "来自信息流，点击查看原文";
      chip.onclick = (e) => {
        e.stopPropagation();
        if (it.url) window.open(it.url, "_blank");
      };
      list.appendChild(chip);
    }

    cell.appendChild(list);
    cell.onclick = () => openDialog(null, ds);
    grid.appendChild(cell);
  }

  $("#count").textContent = state.events.length ? state.events.length + " 个日程" : "";
}

/* ---------------- 事件对话框 ---------------- */

let editing = null;

function openDialog(ev, dateStr) {
  editing = ev;
  $("#dlg-title").textContent = ev ? "编辑事件" : "新建事件";
  $("#f-title").value = ev ? ev.title : "";
  $("#f-date").value = ev ? ev.event_date : (dateStr || ymd(new Date()));
  $("#f-start").value = ev ? ev.start_time || "" : "";
  $("#f-end").value = ev ? ev.end_time || "" : "";
  $("#f-location").value = ev ? ev.location || "" : "";
  $("#f-note").value = ev ? ev.note || "" : "";
  $("#f-delete").hidden = !ev;
  $("#event-dlg").showModal();
  $("#f-title").focus();
}

async function saveDialog(e) {
  e.preventDefault();
  const body = {
    title: $("#f-title").value.trim(),
    event_date: $("#f-date").value,
    start_time: $("#f-start").value,
    end_time: $("#f-end").value,
    location: $("#f-location").value.trim(),
    note: $("#f-note").value.trim(),
  };
  if (!body.title || !body.event_date) return;

  if (editing) await api("/events/" + editing.id, { method: "PATCH", body: JSON.stringify(body) });
  else await api("/events", { method: "POST", body: JSON.stringify(body) });

  $("#event-dlg").close();
  loadCalendar();
}

async function deleteDialog() {
  if (!editing) return;
  await api("/events/" + editing.id, { method: "DELETE" });
  $("#event-dlg").close();
  loadCalendar();
}

/* ---------------- 对话建日程 ---------------- */

function logChat(text, mine) {
  const log = $("#chat-log");
  const row = document.createElement("div");
  row.className = "chat-msg" + (mine ? " mine" : "");
  row.textContent = text;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

async function sendChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  logChat(text, true);

  try {
    const r = await api("/chat", { method: "POST", body: JSON.stringify({ text }) });
    logChat(r.reply, false);
    if (r.ok) loadCalendar();
  } catch (e) {
    logChat("出错了：" + e.message, false);
  }
}

/* ---------------- 全局动作 ---------------- */

async function fetchAll() {
  const btn = $("#btn-fetch");
  btn.disabled = true;
  $("#status").textContent = "抓取中…";
  try {
    const { results } = await api("/sources/fetch-all", { method: "POST" });
    const ok = results.filter((r) => r.ok).length;
    const added = results.reduce((s, r) => s + (r.added || 0), 0);
    $("#status").textContent = ok + "/" + results.length + " 成功，新增 " + added + " 条";
    if (state.view === "calendar") loadCalendar();
    else { loadBoards(); loadItems(); }
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
    : "<pre>" + escapeHtml(JSON.stringify(d, null, 2)) + "</pre>";
  $("#diag").showModal();
}

function connectEvents() {
  const ws = new WebSocket("ws://" + location.host + "/ws");
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "items.added") {
      if (state.view === "calendar") loadCalendar();
      else { loadBoards(); loadItems(); }
    }
    if (msg.type === "source.error") {
      $("#status").textContent = msg.key + " 出错：" + msg.error;
    }
  };
  ws.onclose = () => setTimeout(connectEvents, 3000);
}

/* ---------------- 绑定 ---------------- */

document.querySelectorAll(".view-btn").forEach((b) =>
  b.addEventListener("click", () => switchView(b.dataset.view)));

$("#btn-fetch").onclick = fetchAll;
$("#btn-diag").onclick = diagnose;
$("#only-unread").onchange = (e) => { state.onlyUnread = e.target.checked; loadItems(); };
$("#event-form").addEventListener("submit", saveDialog);
$("#f-cancel").onclick = () => $("#event-dlg").close();
$("#f-delete").onclick = deleteDialog;
$("#chat-send").onclick = sendChat;
$("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
$("#cal-prev").onclick = () => { state.month = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1); loadCalendar(); };
$("#cal-next").onclick = () => { state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1); loadCalendar(); };
$("#cal-today").onclick = () => { state.month = new Date(); loadCalendar(); };

loadBoards();
loadItems();
connectEvents();
