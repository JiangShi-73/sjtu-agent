const API = "/api/v1";

const state = {
  board: null,
  onlyUnread: false,
  view: "feed",
  month: new Date(),
  selected: "",
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
const todayStr = () => ymd(new Date());
const fmt = (iso) => (iso ? String(iso).replace("T", " ").slice(5, 16) : "");

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 板块 ---------------- */

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
    el.innerHTML = "<span>" + escapeHtml(b.name) + "</span>" +
      (b.unread ? '<span class="badge">' + b.unread + "</span>" : "");
    el.onclick = () => { state.board = b.key; loadBoards(); loadItems(); };
    nav.appendChild(el);
  }
  const cur = data.find((b) => b.key === state.board);
  $("#board-title").textContent = cur ? cur.name : "全部";
}

/* ---------------- 信息流 ---------------- */

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

/* ---------------- 日历 ---------------- */

async function loadCalendar() {
  if (!state.selected) state.selected = todayStr();
  const y = state.month.getFullYear();
  const m = state.month.getMonth();
  const start = ymd(new Date(y, m - 1, 20));
  const end = ymd(new Date(y, m + 2, 10));
  const data = await api("/events?start=" + start + "&end=" + end);
  state.events = data.events || [];
  state.derived = data.derived || [];
  renderRail();
  if (state.view === "calendar") renderBig();
  renderDayList();
}

function dotsFor(ds) {
  const blue = state.events.filter((e) => e.event_date === ds).length;
  const gray = state.derived.filter((x) => String(x.deadline).slice(0, 10) === ds).length;
  return { blue, gray };
}

function monthGrid() {
  const y = state.month.getFullYear();
  const m = state.month.getMonth();
  const first = new Date(y, m, 1);
  const offset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const rows = Math.ceil((offset + daysInMonth) / 7);
  const gridStart = new Date(y, m, 1 - offset);
  return { y, m, rows, gridStart, daysInMonth };
}

function renderRail() {
  const { y, m, rows, gridStart } = monthGrid();
  $("#rail-label").textContent = y + " 年 " + (m + 1) + " 月";

  const grid = $("#rail-grid");
  grid.innerHTML = "";
  const t = todayStr();

  for (let i = 0; i < rows * 7; i++) {
    const d = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
    const ds = ymd(d);
    const { blue, gray } = dotsFor(ds);

    const cell = document.createElement("div");
    cell.className = "rail-cell"
      + (d.getMonth() === m ? "" : " dim")
      + (ds === t ? " today" : "")
      + (ds === state.selected ? " sel" : "");

    const day = document.createElement("div");
    day.className = "rail-day";
    day.textContent = d.getDate();
    cell.appendChild(day);

    if (blue || gray) {
      const dots = document.createElement("div");
      dots.className = "dots";
      for (let k = 0; k < Math.min(blue, 3); k++) {
        const s = document.createElement("span");
        s.className = "dot-b";
        dots.appendChild(s);
      }
      if (gray) {
        const s = document.createElement("span");
        s.className = "dot-g";
        dots.appendChild(s);
      }
      cell.appendChild(dots);
    }

    cell.onclick = () => selectDay(ds, d, m);
    grid.appendChild(cell);
  }
}

function selectDay(ds, d, currentMonth) {
  state.selected = ds;
  if (d && currentMonth !== undefined && d.getMonth() !== currentMonth) {
    state.month = new Date(d.getFullYear(), d.getMonth(), 1);
  }
  renderRail();
  if (state.view === "calendar") renderBig();
  renderDayList();
}

function renderBig() {
  const { y, m, rows, gridStart } = monthGrid();
  $("#cal-label").textContent = y + " 年 " + (m + 1) + " 月";

  const grid = $("#big-grid");
  grid.innerHTML = "";
  const t = todayStr();

  for (let i = 0; i < rows * 7; i++) {
    const d = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i);
    const ds = ymd(d);

    const cell = document.createElement("div");
    cell.className = "big-cell"
      + (d.getMonth() === m ? "" : " dim")
      + (ds === t ? " today" : "")
      + (ds === state.selected ? " sel" : "");

    const day = document.createElement("div");
    day.className = "big-day";
    day.textContent = d.getDate();
    cell.appendChild(day);

    const list = document.createElement("div");
    list.className = "big-events";

    for (const ev of state.events.filter((e) => e.event_date === ds)) {
      const chip = document.createElement("div");
      chip.className = "chip manual";
      chip.textContent = (ev.start_time ? ev.start_time + " " : "") + ev.title;
      chip.title = [ev.start_time, ev.end_time, ev.location].filter(Boolean).join(" ");
      chip.onclick = (e) => { e.stopPropagation(); openDialog(ev); };
      list.appendChild(chip);
    }

    for (const it of state.derived.filter((x) => String(x.deadline).slice(0, 10) === ds)) {
      const chip = document.createElement("div");
      chip.className = "chip derived";
      chip.textContent = String(it.deadline).slice(11, 16) + " 截止 · " + it.title;
      chip.onclick = (e) => { e.stopPropagation(); if (it.url) window.open(it.url, "_blank"); };
      list.appendChild(chip);
    }

    cell.appendChild(list);
    cell.onclick = () => selectDay(ds, d, m);
    grid.appendChild(cell);
  }
}

function renderDayList() {
  const ds = state.selected;
  const d = new Date(ds + "T00:00:00");
  $("#day-label").textContent =
    (d.getMonth() + 1) + " 月 " + d.getDate() + " 日 · 周" + "日一二三四五六"[d.getDay()];

  const box = $("#day-list");
  box.innerHTML = "";

  const mine = state.events.filter((e) => e.event_date === ds);
  const due = state.derived.filter((x) => String(x.deadline).slice(0, 10) === ds);

  if (!mine.length && !due.length) {
    box.innerHTML = '<div class="day-empty">这天没有安排</div>';
    return;
  }

  for (const e of mine) {
    const el = document.createElement("div");
    el.className = "day-item";
    el.innerHTML = '<div class="t">' + escapeHtml(e.title) + "</div>" +
      '<div class="m">' +
        (e.start_time ? e.start_time + (e.end_time ? "–" + e.end_time : "") : "全天") +
        (e.location ? " · " + escapeHtml(e.location) : "") +
      "</div>";
    el.onclick = () => openDialog(e);
    box.appendChild(el);
  }

  for (const x of due) {
    const el = document.createElement("div");
    el.className = "day-item derived";
    el.innerHTML = '<div class="t">' + escapeHtml(x.title) + "</div>" +
      '<div class="m">' + String(x.deadline).slice(11, 16) + " 截止 · 来自信息流</div>";
    el.onclick = () => { if (x.url) window.open(x.url, "_blank"); };
    box.appendChild(el);
  }
}

/* ---------------- 视图切换 ---------------- */

function switchView(view) {
  state.view = view;
  const isCal = view === "calendar";
  $("#feed-view").hidden = isCal;
  $("#calendar-view").hidden = !isCal;
  $("#rail").hidden = isCal;
  if (isCal) renderBig();
}

/* ---------------- 事件对话框 ---------------- */

let editing = null;

function openDialog(ev, dateStr) {
  editing = ev || null;
  $("#dlg-title").textContent = ev ? "编辑事件" : "新建事件";
  $("#f-title").value = ev ? ev.title : "";
  $("#f-date").value = ev ? ev.event_date : (dateStr || state.selected || todayStr());
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

  state.selected = body.event_date;
  $("#event-dlg").close();
  loadCalendar();
}

async function deleteDialog() {
  if (!editing) return;
  await api("/events/" + editing.id, { method: "DELETE" });
  $("#event-dlg").close();
  loadCalendar();
}

/* ---------------- 对话 ---------------- */

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
    if (r.ok && r.event) {
      state.selected = r.event.event_date;
      state.month = new Date(r.event.event_date + "T00:00:00");
      loadCalendar();
    }
  } catch (e) {
    logChat("出错了：" + e.message, false);
  }
}

/* ---------------- 订阅 ---------------- */

async function openFeeds() {
  await renderFeeds();
  $("#feeds-dlg").showModal();
}

async function renderFeeds() {
  const { feeds } = await api("/feeds");
  const box = $("#feed-list");
  box.innerHTML = "";

  if (!feeds.length) {
    box.innerHTML = '<div class="feed-empty">还没有订阅。在下面填一个 RSS 地址试试。</div>';
    return;
  }

  for (const f of feeds) {
    const row = document.createElement("div");
    row.className = "feed-row";
    row.innerHTML = '<div class="info"><div class="name">' + escapeHtml(f.name || f.url) +
      '</div><div class="url">' + escapeHtml(f.url) + "</div></div>";
    const del = document.createElement("button");
    del.className = "mini danger";
    del.textContent = "删除";
    del.onclick = async () => {
      await api("/feeds?url=" + encodeURIComponent(f.url), { method: "DELETE" });
      renderFeeds();
    };
    row.appendChild(del);
    box.appendChild(row);
  }
}

async function addFeed() {
  const name = $("#feed-name").value.trim();
  const url = $("#feed-url").value.trim();
  const board = $("#feed-board").value;
  if (!url) return;

  const r = await api("/feeds", {
    method: "POST",
    body: JSON.stringify({ name: name || url, url, board }),
  });
  if (!r.ok) { alert(r.error || "添加失败"); return; }

  $("#feed-name").value = "";
  $("#feed-url").value = "";
  await renderFeeds();
  await api("/sources/rss/fetch", { method: "POST" });
  loadBoards();
  loadItems();
}

/* ---------------- 全局 ---------------- */

async function fetchAll() {
  const btn = $("#btn-fetch");
  btn.disabled = true;
  $("#status").textContent = "抓取中…";
  try {
    const { results } = await api("/sources/fetch-all", { method: "POST" });
    const ok = results.filter((r) => r.ok).length;
    const added = results.reduce((s, r) => s + (r.added || 0), 0);
    $("#status").textContent = ok + "/" + results.length + " 成功，新增 " + added + " 条";
    loadBoards();
    loadItems();
    loadCalendar();
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
    if (msg.type === "items.added") { loadBoards(); loadItems(); loadCalendar(); }
    if (msg.type === "source.error") $("#status").textContent = msg.key + " 出错：" + msg.error;
  };
  ws.onclose = () => setTimeout(connectEvents, 3000);
}

/* ---------------- 绑定 ---------------- */

$("#btn-fetch").onclick = fetchAll;
$("#btn-diag").onclick = diagnose;
$("#btn-feeds").onclick = openFeeds;
$("#feed-add-btn").onclick = addFeed;
$("#only-unread").onchange = (e) => { state.onlyUnread = e.target.checked; loadItems(); };
$("#event-form").addEventListener("submit", saveDialog);
$("#f-cancel").onclick = () => $("#event-dlg").close();
$("#f-delete").onclick = deleteDialog;
$("#day-add").onclick = () => openDialog(null, state.selected);
$("#chat-send").onclick = sendChat;
$("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

$("#rail-expand").onclick = () => switchView("calendar");
$("#cal-close").onclick = () => switchView("feed");

$("#rail-prev").onclick = () => { state.month = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1); loadCalendar(); };
$("#rail-next").onclick = () => { state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1); loadCalendar(); };
$("#cal-prev").onclick = $("#rail-prev").onclick;
$("#cal-next").onclick = $("#rail-next").onclick;
$("#cal-today").onclick = () => { state.month = new Date(); state.selected = todayStr(); loadCalendar(); };

loadBoards();
loadItems();
loadCalendar();
connectEvents();
