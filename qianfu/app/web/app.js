const API = "/api/v1";

const state = {
  board: null,
  college: null,
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
  all.onclick = () => { showFeed(); state.board = null; state.college = null; loadBoards(); loadItems(); };
  nav.appendChild(all);

  for (const b of data) {
    const el = document.createElement("button");
    el.className = "board" + (state.board === b.key ? " active" : "");
    el.innerHTML = "<span>" + escapeHtml(b.name) + "</span>" +
      (b.unread ? '<span class="badge">' + b.unread + "</span>" : "");
    el.onclick = () => { showFeed(); state.board = b.key; state.college = null; loadBoards(); loadItems(); };
    nav.appendChild(el);
  }
  const cur = data.find((b) => b.key === state.board);
  $("#board-title").textContent = cur ? cur.name : "全部";
}

/* ---------------- 信息流 ---------------- */

function renderItemCard(it) {
    const el = document.createElement("article");
    el.className = "item" + (it.read ? " read" : "");
    const canSchedule = it.source === "second_classroom";
    const x = it.source === "second_classroom" ? parseExtra(it) : {};
    const badges = [];
    if (x.labor_counted) {
      badges.push('<span class="tag labor">劳动学时' + (x.labor_hours ? " " + x.labor_hours : "") + "</span>");
    }
    if (x.new_era) badges.push('<span class="tag newera">新时代讲座</span>');

    el.innerHTML =
      '<div class="item-head">' +
        (it.category ? '<span class="tag">' + escapeHtml(it.category) + "</span>" : "") +
        badges.join("") +
        '<span class="item-title">' + escapeHtml(it.title) + "</span>" +
        '<span class="spacer"></span>' +
        '<span class="item-actions">' +
          (canSchedule ? '<button data-act="schedule">加入日程</button>' : "") +
          '<button data-act="delete" class="danger">删除</button>' +
        "</span>" +
      "</div>" +
      (it.summary ? '<div class="item-summary">' + escapeHtml(it.summary) + "</div>" : "") +
      '<div class="item-foot">' +
        (it.source === "second_classroom"
          ? '<span>活动 ' + fmt(x.start || it.published) +
              (x.end ? "–" + String(x.end).slice(11, 16) : "") + "</span>" +
            '<span class="deadline">报名截止 ' + fmt(x.signup_end || it.deadline) + "</span>" +
            (x.signed_up != null
              ? "<span>已报名 " + x.signed_up + (x.capacity ? "/" + x.capacity : "") + "</span>"
              : "")
          : "<span>" + fmt(it.published || it.created_at) + "</span>" +
            (it.deadline ? '<span class="deadline">截止 ' + fmt(it.deadline) + "</span>" : "")) +
      "</div>";

    el.querySelectorAll(".item-actions button").forEach((btn) => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        if (btn.dataset.act === "delete") {
          await api("/items/" + it.id, { method: "DELETE" });
        } else if (btn.dataset.act === "schedule") {
          const r = await api("/items/" + it.id + "/schedule", { method: "POST" });
          if (r.ok) $("#status").textContent = "已加入 " + r.event.event_date + " 的日程";
          loadCalendar();
        }
        loadBoards();
        loadItems();
      };
    });

  el.onclick = async () => {
    if (!it.read) {
      await api("/items/" + it.id + "/read", { method: "POST" });
      loadBoards();
      loadItems();
    }
    if (it.source === "second_classroom") {
      openActivity(it);
    } else if (it.url) {
      window.open(it.url, "_blank");
    }
  };
  return el;
}

async function loadItems() {
  const params = new URLSearchParams({ limit: "200" });
  if (state.board) params.set("board", state.board);
  if (state.onlyUnread) params.set("unread", "true");
  if (state.college) params.set("college", state.college);

  const { items } = await api("/items?" + params);
  const box = $("#items");
  box.innerHTML = "";
  $("#count").textContent = items.length ? items.length + " 条" : "";

  if (!items.length) {
    box.innerHTML = '<div class="empty">还没有内容，点左下角「抓取全部」试试</div>';
    return;
  }

  // 学院公告：顶部一排学院，点哪个看哪个
  if (state.board === "college") {
    await renderCollegeBar();
    if (state.college) {
      for (const it of items) box.appendChild(renderItemCard(it));
    } else {
      renderCollegeGroups(box, items);
    }
    return;
  }

  $("#college-bar").hidden = true;

  for (const it of items) {
    box.appendChild(renderItemCard(it));
  }
}

async function renderCollegeBar() {
  const bar = $("#college-bar");
  bar.hidden = false;

  const { colleges } = await api("/colleges");
  const list = colleges.filter((c) => c.count > 0 || c.enabled);

  bar.innerHTML = "";

  const all = document.createElement("button");
  all.className = "college-chip" + (state.college ? "" : " on");
  all.textContent = "全部";
  all.onclick = () => {
    state.college = null;
    loadItems();
  };
  bar.appendChild(all);

  for (const c of list) {
    const chip = document.createElement("button");
    chip.className = "college-chip" + (state.college === c.name ? " on" : "");
    chip.innerHTML = escapeHtml(c.name) + '<span class="n">' + c.count + "</span>";
    chip.onclick = () => {
      state.college = state.college === c.name ? null : c.name;
      loadItems();
    };
    bar.appendChild(chip);
  }
}

function renderCollegeGroups(box, items) {
  const groups = new Map();
  for (const it of items) {
    const key = it.category || "未分组";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(it);
  }

  for (const [name, list] of groups) {
    const section = document.createElement("section");
    section.className = "college-group";

    const head = document.createElement("div");
    head.className = "college-head";
    head.innerHTML = "<span>" + escapeHtml(name) + "</span><span>" + list.length + "</span>";
    section.appendChild(head);

    for (const it of list) section.appendChild(renderItemCard(it));
    box.appendChild(section);
  }
}

/* ---------------- 邮箱（主区） ---------------- */

async function loadMailView() {
  const box = $("#mail-list");
  box.innerHTML = '<div class="empty">读取中…</div>';
  try {
    const d = await api("/mail/list?limit=60");
    box.innerHTML = "";
    $("#mail-count").textContent = "未读 " + d.unread + " / 共 " + d.total;
    $("#mail-badge").textContent = d.unread || "";
    $("#mail-badge").hidden = !d.unread;

    if (!d.messages.length) {
      box.innerHTML = '<div class="empty">收件箱是空的</div>';
      return;
    }

    for (const m of d.messages) {
      const el = document.createElement("article");
      el.className = "mail-item" + (m.seen ? "" : " unread");
      el.innerHTML =
        '<div class="subject">' + escapeHtml(m.subject) + "</div>" +
        '<div class="meta">' +
          '<span class="who">' + escapeHtml(m.from_name || m.from_addr) + "</span>" +
          "<span>" + escapeHtml(m.date || "") + "</span>" +
        "</div>";
      el.onclick = () => window.open("https://mail.sjtu.edu.cn/zimbra/mail#1", "_blank");
      box.appendChild(el);
    }
  } catch (e) {
    const msg = String(e.message || e);
    box.innerHTML = '<div class="empty">' +
      (msg.includes("400") ? "还没有配置邮箱，去「订阅」里填地址和密码" : "读取失败：" + escapeHtml(msg)) +
      "</div>";
  }
}

function showMail() {
  $("#feed-view").hidden = true;
  $("#calendar-view").hidden = true;
  $("#mail-view").hidden = false;
  $("#nav-mail").classList.add("active");
  document.querySelectorAll("#boards .board").forEach((b) => b.classList.remove("active"));
  loadMailView();
}

function showFeed() {
  $("#mail-view").hidden = true;
  $("#feed-view").hidden = false;
  $("#nav-mail").classList.remove("active");
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

/* ---------------- Canvas 作业（右边栏） ---------------- */

async function loadHomework() {
  const { groups, total } = await api("/canvas/homework");
  const box = $("#rail-hw");
  box.innerHTML = "";

  if (!groups.length) {
    box.innerHTML = '<div class="hw-empty">没有待交作业</div>';
    return;
  }

  for (const g of groups) {
    const wrap = document.createElement("div");
    wrap.className = "hw-group";

    const head = document.createElement("div");
    head.className = "hw-course";
    head.innerHTML = "<span>" + escapeHtml(g.course) + "</span><span>" + g.count + "</span>";
    wrap.appendChild(head);

    for (const hw of g.items) {
      const el = document.createElement("div");
      el.className = "hw-item" + (hw.read ? " read" : "") + (hw.overdue ? " overdue" : "");
      const dl = hw.deadline ? String(hw.deadline).slice(5, 16) : "";
      el.innerHTML =
        '<div class="t">' + escapeHtml(hw.title) + "</div>" +
        (dl ? '<div class="d">' + (hw.overdue ? "已过期 " : "截止 ") + dl + "</div>" : "");
      el.onclick = async () => {
        if (!hw.read) {
          await api("/items/" + hw.id + "/read", { method: "POST" });
          loadHomework();
          loadBoards();
        }
        if (hw.url) window.open(hw.url, "_blank");
      };
      wrap.appendChild(el);
    }
    box.appendChild(wrap);
  }
}

async function loadZhitu() {
  const box = $("#rail-zhitu");
  box.innerHTML = '<div class="hw-empty">读取中…</div>';
  try {
    const { courses } = await api("/aihaoke/courses");
    box.innerHTML = "";
    if (!courses.length) {
      box.innerHTML = '<div class="hw-empty">还没有课程，或者登录已过期</div>';
      return;
    }
    for (const c of courses) {
      const el = document.createElement("div");
      el.className = "zt-card";
      el.innerHTML =
        '<div class="zt-name">' + escapeHtml(c.name) + "</div>" +
        '<div class="zt-meta">' +
          '<span class="zt-state ' + (c.finished ? "off" : "on") + '">' +
            (c.finished ? "已结课" : "进行中") + "</span>" +
          "<span>" + escapeHtml(c.teacher || "") + "</span>" +
        "</div>" +
        (c.intro ? '<div class="zt-intro">' + escapeHtml(c.intro) + "</div>" : "");
      el.onclick = () => window.open(c.url, "_blank");
      box.appendChild(el);
    }
  } catch (e) {
    box.innerHTML = '<div class="hw-empty">读取失败：' + escapeHtml(e.message) + "</div>";
  }
}

async function loadMail() {
  const box = $("#rail-mail");
  box.innerHTML = '<div class="hw-empty">读取中…</div>';
  try {
    const d = await api("/mail/list?limit=25");
    box.innerHTML = "";
    if (!d.messages || !d.messages.length) {
      box.innerHTML = '<div class="hw-empty">收件箱是空的</div>';
      return;
    }
    const head = document.createElement("div");
    head.className = "mail-head";
    head.innerHTML = "<span>收件箱</span><span>未读 " + d.unread + " / 共 " + d.total + "</span>";
    box.appendChild(head);

    for (const m of d.messages) {
      const el = document.createElement("div");
      el.className = "mail-row" + (m.seen ? "" : " unread");
      el.innerHTML =
        '<div class="mail-subject">' + escapeHtml(m.subject) + "</div>" +
        '<div class="mail-meta">' +
          '<span class="who">' + escapeHtml(m.from_name || m.from_addr) + "</span>" +
          "<span>" + escapeHtml((m.date || "").slice(5)) + "</span>" +
        "</div>";
      el.onclick = () => window.open("https://mail.sjtu.edu.cn/zimbra/mail#1", "_blank");
      box.appendChild(el);
    }
  } catch (e) {
    const msg = String(e.message || e);
    box.innerHTML = '<div class="hw-empty">' +
      (msg.includes("400") ? "还没有配置邮箱，去「订阅」里填地址和密码" : "读取失败：" + escapeHtml(msg)) +
      "</div>";
  }
}

function switchRail(tab) {
  for (const [key, btn, panel] of [
    ["hw", "#tab-hw", "#rail-hw"],
    ["zhitu", "#tab-zhitu", "#rail-zhitu"],
    ["mail", "#tab-mail", "#rail-mail"],
    ["day", "#tab-day", "#rail-day"],
  ]) {
    $(btn).classList.toggle("active", tab === key);
    $(panel).hidden = tab !== key;
  }
  if (tab === "zhitu") loadZhitu();
  if (tab === "mail") loadMail();
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

/* ---------------- 活动详情 ---------------- */

function parseExtra(it) {
  const raw = it.extra;
  if (!raw) return {};
  if (typeof raw === "object") return raw;
  try { return JSON.parse(raw); } catch (e) { return {}; }
}

function openActivity(it) {
  const x = parseExtra(it);

  $("#act-title").textContent = it.title;

  const rows = [
    ["类型", it.category || x.status || ""],
    ["劳动学时", x.labor_counted ? (x.labor_hours ? x.labor_hours + " 学时" : "计入") : "不计入"],
    ["新时代讲座", x.new_era ? "计入" : "不计入"],
    ["活动时间", [x.start, x.end].filter(Boolean).join("  →  ")],
    ["报名时间", [x.signup_start, x.signup_end].filter(Boolean).join("  →  ")],
    ["报名方式", x.signup],
    ["已报名", x.signed_up != null
      ? x.signed_up + (x.capacity ? " / " + x.capacity + " 人" : " 人")
      : ""],
    ["地点", x.place],
    ["主办", x.sponsor],
    ["人数", x.person_num],
    ["主讲", [x.presenter, x.presenter_bio].filter(Boolean).join(" · ")],
  ].filter((r) => r[1] && !(r[0] === "劳动学时" && r[1] === "不计入") && !(r[0] === "新时代讲座" && r[1] === "不计入"));

  const meta = $("#act-meta");
  meta.innerHTML = "";
  for (const [k, v] of rows) {
    const row = document.createElement("div");
    row.className = "act-row";
    const hot = k === "报名时间" && it.deadline;
    row.innerHTML = '<span class="k">' + k + "</span>" +
      '<span class="v' + (hot ? " hot" : "") + '">' + escapeHtml(String(v)) + "</span>";
    meta.appendChild(row);
  }

  $("#act-intro").textContent = x.introduction || it.summary || "";
  $("#act-intro").hidden = !(x.introduction || it.summary);

  const addToCalendar = async (kind) => {
    const r = await api("/items/" + it.id + "/schedule?kind=" + kind, { method: "POST" });
    if (r.ok) {
      $("#status").textContent = (kind === "signup" ? "报名截止" : "活动") +
        "已加入 " + r.event.event_date + " 的日程";
      loadCalendar();
      $("#act-dlg").close();
    }
  };
  $("#act-schedule-signup").onclick = () => addToCalendar("signup");
  $("#act-schedule-start").onclick = () => addToCalendar("start");
  $("#act-schedule-signup").disabled = !x.signup_end;
  const hasDetail = (it.url || "").includes("/activity/detail/");
  $("#act-open").textContent = hasDetail ? "打开活动详情" : "打开活动平台";
  $("#act-open").onclick = () => window.open(it.url || "https://activity.sjtu.edu.cn/dekt", "_blank");

  $("#act-dlg").showModal();
}

/* ---------------- 订阅 ---------------- */

async function openFeeds() {
  await renderFeeds();
  await renderColleges();
  await renderCourses();
  $("#feeds-dlg").showModal();
}

async function renderColleges() {
  const { colleges } = await api("/colleges");
  const box = $("#college-list");
  box.innerHTML = "";

  for (const c of colleges) {
    const row = document.createElement("div");
    row.className = "course-row" + (c.enabled ? "" : " off");

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = c.enabled;
    cb.disabled = c.mode === "todo";
    cb.onchange = async () => {
      await api("/colleges/" + c.key, {
        method: "PATCH",
        body: JSON.stringify({ enabled: cb.checked }),
      });
      renderColleges();
    };

    const name = document.createElement("div");
    name.className = "name";
    name.innerHTML = escapeHtml(c.name) +
      '<span class="id">' + (c.mode === "todo" ? "待适配" : c.count + " 条") + "</span>";

    row.append(cb, name);
    box.appendChild(row);
  }
}

async function renderCourses() {
  const { courses } = await api("/cnmooc/courses");
  const box = $("#course-list");
  box.innerHTML = "";

  if (!courses.length) {
    box.innerHTML = '<div class="feed-empty">还没有课程。加一门就能抓它的作业与测验。</div>';
    return;
  }

  for (const c of courses) {
    const on = c.enabled !== false;
    const row = document.createElement("div");
    row.className = "course-row" + (on ? "" : " off");

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = on;
    cb.onchange = async () => {
      await api("/cnmooc/courses/" + c.id, {
        method: "PATCH",
        body: JSON.stringify({ enabled: cb.checked }),
      });
      renderCourses();
    };

    const name = document.createElement("div");
    name.className = "name";
    name.innerHTML = escapeHtml(c.name || c.id) +
      '<span class="id">#' + escapeHtml(c.id) + "</span>";

    const del = document.createElement("button");
    del.className = "mini danger";
    del.textContent = "删除";
    del.onclick = async () => {
      await api("/cnmooc/courses/" + c.id, { method: "DELETE" });
      renderCourses();
    };

    row.append(cb, name, del);
    box.appendChild(row);
  }
}

async function addCourse() {
  const url = $("#course-url").value.trim();
  if (!url) return;
  const r = await fetch(API + "/cnmooc/courses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) { alert("添加失败：" + (await r.text()).slice(0, 120)); return; }
  $("#course-url").value = "";
  await renderCourses();
  await api("/sources/cnmooc/fetch", { method: "POST" });
  loadBoards();
  loadItems();
  loadCalendar();
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

/* ---------------- 登录 ---------------- */

let qrSession = null;
let qrVersion = -1;

function showLogin(show) {
  $("#login-overlay").hidden = !show;
  $("#btn-login").hidden = !show;
}

async function ensureLogin() {
  try {
    const st = await api("/auth/status");
    if (st.logged_in) { showLogin(false); return; }
  } catch (e) { /* 忽略 */ }
  showLogin(true);
  await startQR();
}

async function startQR() {
  $("#login-state").textContent = "正在获取二维码…";
  $("#login-state").className = "login-state";
  $("#login-retry").hidden = true;

  try {
    const r = await api("/auth/qr/start", { method: "POST" });
    if (r.already) { location.reload(); return; }
    qrSession = r.id;
    qrVersion = r.version;
    $("#login-qr").src = "data:image/png;base64," + r.png;
    $("#login-state").textContent = "等待扫码…";
    setTimeout(pollQR, 1200);
  } catch (e) {
    $("#login-state").textContent = "获取失败：" + e.message;
    $("#login-state").className = "login-state bad";
    $("#login-retry").hidden = false;
  }
}

async function pollQR() {
  if (!qrSession) return;
  try {
    const s = await api("/auth/qr/status/" + qrSession);
    if (s.version !== qrVersion && s.png) {
      qrVersion = s.version;
      $("#login-qr").src = "data:image/png;base64," + s.png;
    }
    if (s.state === "ok") {
      $("#login-state").textContent = "登录成功，正在加载…";
      $("#login-state").className = "login-state ok";
      setTimeout(() => location.reload(), 800);
      return;
    }
    if (s.state === "timeout" || s.state === "error" || s.state === "missing") {
      $("#login-state").textContent = s.state === "timeout" ? "二维码已过期" : "登录失败";
      $("#login-state").className = "login-state bad";
      $("#login-retry").hidden = false;
      return;
    }
  } catch (e) { /* 网络抖动，继续轮询 */ }
  setTimeout(pollQR, 1500);
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
    loadHomework();
  } catch (e) {
    $("#status").textContent = "失败：" + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function diagnose() {
  const [d, b] = await Promise.all([api("/diagnose"), api("/system/browser")]);

  let html = '<div class="diag-section"><h3>浏览器</h3>';
  if (b.launch_error) {
    html += '<p class="bad">✗ 用不了：' + escapeHtml(b.launch_error) + "</p>";
  } else if (b.version) {
    html += '<p class="ok">✓ ' + escapeHtml(b.version) + "</p>" +
      '<p class="muted">' + escapeHtml(b.executable || "Playwright 自带 Chromium") + "</p>";
  } else {
    html += '<p class="bad">✗ 没找到 Edge / Chrome，截图功能不可用</p>';
  }
  html += "</div>";

  html += '<div class="diag-section"><h3>抓取</h3>';
  html += d.ok ? "<p class='ok'>✓ 没有发现错误</p>"
    : "<pre>" + escapeHtml(JSON.stringify(d, null, 2)) + "</pre>";
  html += "</div>";

  $("#diag-body").innerHTML = html;
  $("#diag").showModal();
}

function connectEvents() {
  const ws = new WebSocket("ws://" + location.host + "/ws");
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "items.added") { loadBoards(); loadItems(); loadCalendar(); loadHomework(); }
    if (msg.type === "source.error") $("#status").textContent = msg.key + " 出错：" + msg.error;
  };
  ws.onclose = () => setTimeout(connectEvents, 3000);
}

/* ---------------- 绑定 ---------------- */

$("#btn-fetch").onclick = fetchAll;
$("#btn-diag").onclick = diagnose;
$("#btn-feeds").onclick = openFeeds;
$("#feed-add-btn").onclick = addFeed;
$("#course-add-btn").onclick = addCourse;

async function openBotDialog() {
  $("#bot-dlg").showModal();
  $("#bot-msg").textContent = "";
  await refreshBotStatus();
}

async function refreshBotStatus() {
  const box = $("#bot-status");
  box.innerHTML = "<div>读取中…</div>";

  let s;
  try {
    s = await api("/setup/status");
  } catch (e) {
    box.innerHTML = '<div class="no">读不到状态</div>';
    return;
  }

  const row = (label, ok, text) =>
    "<div>" + label + '</div><div class="' + (ok ? "ok" : "no") + '">' + text + "</div>";

  box.innerHTML = [
    row("AstrBot 安装", s.installed, s.installed ? "已安装" : "未安装"),
    row("AstrBot 运行", s.running, s.running ? "运行中" : "未运行"),
    row("模型", s.model_ready, s.model || "未配置"),
    row("QQ 机器人", s.qq_ready, s.qq_appid || "未填写"),
    row("信鸽插件", s.plugin_ready, s.plugin_ready ? "已就绪" : "缺失"),
  ].join("");

  if (s.qq_appid) $("#bot-appid").value = s.qq_appid;
}


/* ---------------- 问助手 ---------------- */

const agentHistory = [];

function addMsg(kind, text) {
  const el = document.createElement("div");
  el.className = "msg " + kind;
  el.textContent = text;
  $("#agent-log").appendChild(el);
  $("#agent-log").scrollTop = $("#agent-log").scrollHeight;
  return el;
}

$("#btn-agent").onclick = () => {
  $("#agent-dlg").showModal();
  if (!agentHistory.length) {
    addMsg("ai", "你好，我是信鸽助手。可以问我：\n· 这周有什么要交的？\n· 学院公告里有奖学金吗？\n· 第二课堂有什么活动？");
  }
  $("#agent-text").focus();
};

$("#agent-close").onclick = () => $("#agent-dlg").close();

async function sendAgent() {
  const input = $("#agent-text");
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  addMsg("user", text);
  agentHistory.push({ role: "user", content: text });

  const pending = addMsg("ai", "思考中…");
  $("#agent-send").disabled = true;

  try {
    const r = await api("/agent/chat", {
      method: "POST",
      body: JSON.stringify({ messages: agentHistory.slice(-12) }),
    });

    pending.remove();

    for (const step of r.steps || []) {
      const arg = Object.keys(step.args || {}).length
        ? " " + JSON.stringify(step.args)
        : "";
      addMsg("tool", "→ " + step.tool + arg);
    }

    addMsg("ai", r.reply || "（没有回复）");
    agentHistory.push({ role: "assistant", content: r.reply || "" });
  } catch (e) {
    pending.remove();
    addMsg("ai", "出错了：" + e);
  } finally {
    $("#agent-send").disabled = false;
    input.focus();
  }
}

$("#agent-send").onclick = sendAgent;
$("#agent-text").onkeydown = (e) => {
  if (e.key === "Enter") sendAgent();
};

$("#btn-bot").onclick = openBotDialog;
$("#bot-close").onclick = () => $("#bot-dlg").close();

$("#bot-save").onclick = async () => {
  const appid = $("#bot-appid").value.trim();
  const secret = $("#bot-secret").value.trim();
  if (!appid || !secret) {
    $("#bot-msg").textContent = "AppID 和 Secret 都要填";
    return;
  }

  const llmKey = $("#bot-llm-key").value.trim();
  if (llmKey) {
    const m = await api("/setup/model", {
      method: "POST",
      body: JSON.stringify({
        key: llmKey,
        base: $("#bot-llm-base").value.trim(),
        model: $("#bot-llm-model").value.trim(),
      }),
    });
    if (!m.ok) {
      $("#bot-msg").textContent = "模型配置失败：" + (m.error || "");
      return;
    }
  }

  $("#bot-msg").textContent = "保存中，AstrBot 正在重启，大约 30 秒…";
  try {
    const r = await api("/setup/qq", {
      method: "POST",
      body: JSON.stringify({ appid, secret }),
    });
    if (!r.ok) {
      $("#bot-msg").textContent = "失败：" + (r.error || "未知错误");
      return;
    }
    $("#bot-msg").textContent = "已保存，重启完成。去 QQ 里发 /信鸽 试试。";
    await refreshBotStatus();
  } catch (e) {
    $("#bot-msg").textContent = "失败：" + e;
  }
};

$("#act-close").onclick = () => $("#act-dlg").close();
$("#nav-mail").onclick = showMail;
$("#mail-refresh").onclick = loadMailView;
$("#login-retry").onclick = startQR;
$("#btn-login").onclick = () => { showLogin(true); startQR(); };
$("#course-discover").onclick = async () => {
  const btn = $("#course-discover");
  btn.disabled = true;
  btn.textContent = "检测中…";
  try {
    const r = await fetch(API + "/cnmooc/discover", { method: "POST" });
    const data = await r.json();
    if (!data.ok) {
      alert(data.error || "检测失败");
    } else {
      await renderCourses();
      await api("/sources/cnmooc/fetch", { method: "POST" });
      loadBoards();
      loadItems();
      loadCalendar();
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "自动检测课程";
  }
};
$("#only-unread").onchange = (e) => { state.onlyUnread = e.target.checked; loadItems(); };
$("#btn-clear-read").onclick = async () => {
  const r = await api("/items/read", { method: "DELETE" });
  $("#status").textContent = "已清理 " + r.deleted + " 条已读";
  loadBoards();
  loadItems();
  loadCalendar();
};
$("#event-form").addEventListener("submit", saveDialog);
$("#f-cancel").onclick = () => $("#event-dlg").close();
$("#f-delete").onclick = deleteDialog;
$("#day-add").onclick = () => openDialog(null, state.selected);
$("#chat-send").onclick = sendChat;
$("#chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

$("#tab-hw").onclick = () => switchRail("hw");
$("#tab-zhitu").onclick = () => switchRail("zhitu");
$("#tab-mail").onclick = () => switchRail("mail");
$("#mail-save").onclick = async () => {
  const address = $("#mail-address").value.trim();
  const password = $("#mail-password").value;
  const box = $("#mail-status");
  box.textContent = "保存中…";
  const r = await api("/mail/settings", {
    method: "PUT",
    body: JSON.stringify({ address, password }),
  });
  box.textContent = r.tested
    ? (r.ok ? "✓ 连接成功" : "✗ " + (r.error || "连接失败"))
    : "已保存";
  $("#mail-password").value = "";
};
$("#tab-day").onclick = () => switchRail("day");
$("#rail-expand").onclick = () => switchView("calendar");
$("#cal-close").onclick = () => switchView("feed");

$("#rail-prev").onclick = () => { state.month = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1); loadCalendar(); };
$("#rail-next").onclick = () => { state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1); loadCalendar(); };
$("#cal-prev").onclick = $("#rail-prev").onclick;
$("#cal-next").onclick = $("#rail-next").onclick;
$("#cal-today").onclick = () => { state.month = new Date(); state.selected = todayStr(); loadCalendar(); };

ensureLogin();
loadBoards();
loadItems();
loadCalendar();
loadHomework();
connectEvents();