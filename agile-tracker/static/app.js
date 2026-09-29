const $ = (s) => document.querySelector(s);
const STORY = { backlog: "Backlog", todo: "To do", in_progress: "In progress", done: "Done" };
const TASK = { todo: "To do", in_progress: "In progress", done: "Done" };
const PRIORITY = { low: "Low", medium: "Medium", high: "High" };
let token = localStorage.getItem("token"), me = null, users = [], projects = [], pid = null, tree = null, notesTimer = null;

// Escape everything that came from the server before it goes into innerHTML (XSS defence).
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const localToday = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); };

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401 && token) { signOut(); throw new Error("Session expired"); }
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    const msg = typeof e.detail === "string" ? e.detail : (e.detail || []).map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("\n") || "Request failed";
    alert(msg); throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

function signOut() { localStorage.removeItem("token"); token = null; location.reload(); }
async function auth(kind) {
  const body = { email: $("#a_email").value, password: $("#a_pw").value };
  if (kind === "register") body.name = $("#a_name").value;
  const r = await api("/auth/" + kind, { method: "POST", body });
  token = r.access_token; localStorage.setItem("token", token); start();
}

async function start() {
  if (!token) { $("#auth").hidden = false; $("#app").hidden = true; return; }
  try { me = await api("/me"); } catch { return; }
  $("#auth").hidden = true; $("#app").hidden = false; $("#who").textContent = me.name;
  users = await api("/users"); await loadProjects(); pollNotes();
  clearInterval(notesTimer); notesTimer = setInterval(pollNotes, 15000);
}

async function loadProjects() {
  projects = await api("/projects");
  $("#plist").innerHTML = projects.map((p) => `<li><button data-p="${p.id}" class="${p.id === pid ? "on" : ""}">${esc(p.name)}</button></li>`).join("");
  if (pid) await loadTree();
}
async function loadTree() { tree = await api("/projects/" + pid); render(); }

const userOpts = (sel) => `<option value="">Unassigned</option>` + users.map((u) => `<option value="${u.id}" ${u.id === sel ? "selected" : ""}>${esc(u.name)}</option>`).join("");
const opts = (map, sel) => Object.entries(map).map(([k, v]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${v}</option>`).join("");

function taskHtml(t) {
  const late = t.due_date && t.due_date < localToday() && t.status !== "done";
  return `<div class="task ${t.status === "done" ? "isdone" : ""}">
    <select data-task="${t.id}" data-f="status" aria-label="Task status">${opts(TASK, t.status)}</select>
    <span class="t">${esc(t.title)}${late ? ` <span class="late">Overdue</span>` : ""}</span>
    <span class="tr">
      <input type="date" data-task="${t.id}" data-f="due_date" value="${esc(t.due_date || "")}" aria-label="Due date" title="Due date">
      <select data-task="${t.id}" data-f="assignee_id" aria-label="Task assignee">${userOpts(t.assignee_id)}</select>
      <button class="x" data-deltask="${t.id}" aria-label="Delete task" title="Delete task">✕</button>
    </span></div>`;
}
function storyHtml(s) {
  const pct = s.progress.total ? Math.round((100 * s.progress.done) / s.progress.total) : 0;
  return `<article class="story ${s.status} p-${s.priority}">
    <h4>${esc(s.title)}</h4>
    <div class="meta"><select data-story="${s.id}" data-f="status" aria-label="Story status">${opts(STORY, s.status)}</select>
      <select data-story="${s.id}" data-f="priority" aria-label="Priority">${opts(PRIORITY, s.priority)}</select>
      <select data-story="${s.id}" data-f="assignee_id" aria-label="Story assignee">${userOpts(s.assignee_id)}</select>
      ${s.points != null ? `<span>${s.points} pts</span>` : ""}<button class="x" data-delstory="${s.id}" aria-label="Delete story" title="Delete story">✕</button></div>
    ${s.description ? `<div class="muted">${esc(s.description)}</div>` : ""}
    <div class="bar" title="${s.progress.done} of ${s.progress.total} tasks done"><i style="width:${pct}%"></i></div>
    ${s.tasks.map(taskHtml).join("")}
    <div class="add">
      <input data-newtask="${s.id}" placeholder="New task title" maxlength="200" aria-label="New task title">
      <input type="date" data-newdue="${s.id}" aria-label="Due date for new task" title="Due date (optional)">
      <button class="primary" data-addtask="${s.id}">Add task</button>
    </div></article>`;
}

function render() {
  // keep half-typed task drafts when the board redraws
  const drafts = {};
  document.querySelectorAll("[data-newtask]").forEach((i) => {
    const d = document.querySelector(`[data-newdue="${i.dataset.newtask}"]`);
    if (i.value || d?.value) drafts[i.dataset.newtask] = [i.value, d?.value || ""];
  });
  const cols = Object.entries(STORY).map(([k, label]) => {
    const items = tree.stories.filter((s) => s.status === k);
    return `<div class="col"><h3>${label} (${items.length})</h3><div>${items.map(storyHtml).join("")}</div></div>`;
  }).join("");
  $("#board").innerHTML = `<div class="phead"><h2>${esc(tree.name)}</h2><span class="muted">${esc(tree.description)}</span>
    <span class="grow"></span><button data-delproj="1">Delete project</button></div>
    <div class="newstory"><input id="s_title" placeholder="New user story, e.g. As a user I can reset my password" maxlength="200">
    <input id="s_pts" type="number" min="0" max="100" placeholder="Points" style="width:80px"><button class="primary" id="btn_sadd">Add story</button></div>
    <div class="cols">${cols}</div>`;
  for (const [id, [title, due]] of Object.entries(drafts)) {
    const i = document.querySelector(`[data-newtask="${id}"]`), d = document.querySelector(`[data-newdue="${id}"]`);
    if (i) i.value = title; if (d) d.value = due;
  }
}

async function addTask(sid) {
  const input = document.querySelector(`[data-newtask="${sid}"]`);
  const title = input.value.trim();
  if (!title) { input.focus(); alert("Type a task title first."); return; }
  const due = document.querySelector(`[data-newdue="${sid}"]`).value || null;
  await api(`/stories/${sid}/tasks`, { method: "POST", body: { title, due_date: due } });
  await loadTree();
  document.querySelector(`[data-newtask="${sid}"]`)?.focus();   // ready for the next task
}

async function pollNotes() {
  try {
    const n = await api("/notifications"), unread = n.filter((x) => !x.read).length;
    $("#badge").hidden = !unread; $("#badge").textContent = unread;
    $("#notes").innerHTML = n.length ? n.map((x) => `<div>${esc(x.message)} <span class="muted">${esc(x.created_at.replace("T", " ").slice(0, 16))} UTC</span></div>`).join("") : `<span class="muted">No notifications yet.</span>`;
  } catch {}
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("button"); if (!t) return;
  if (t.id === "btn_login") return auth("login");
  if (t.id === "btn_reg") return auth("register");
  if (t.id === "btn_out") return signOut();
  if (t.dataset.addtask) return addTask(t.dataset.addtask);
  if (t.id === "bell") { const el = $("#notes"); el.hidden = !el.hidden; if (!el.hidden) { await api("/notifications/read", { method: "POST" }); $("#badge").hidden = true; } return; }
  if (t.id === "btn_padd") { const name = $("#p_name").value.trim(); if (!name) return; const p = await api("/projects", { method: "POST", body: { name } }); $("#p_name").value = ""; pid = p.id; return loadProjects(); }
  if (t.dataset.p) { pid = +t.dataset.p; return loadProjects(); }
  if (t.id === "btn_sadd") { const title = $("#s_title").value.trim(); if (!title) return; const pts = $("#s_pts").value; await api(`/projects/${pid}/stories`, { method: "POST", body: { title, points: pts === "" ? null : +pts } }); return loadTree(); }
  if (t.dataset.delstory && confirm("Delete this story and all its tasks?")) { await api("/stories/" + t.dataset.delstory, { method: "DELETE" }); return loadTree(); }
  if (t.dataset.deltask) { await api("/tasks/" + t.dataset.deltask, { method: "DELETE" }); return loadTree(); }
  if (t.dataset.delproj && confirm("Delete this project, its stories and tasks?")) { await api("/projects/" + pid, { method: "DELETE" }); pid = null; tree = null; $("#board").innerHTML = ""; return loadProjects(); }
});

// Editing an existing story/task (status, priority, assignee, due date) saves immediately.
document.addEventListener("change", async (e) => {
  const el = e.target, kind = el.dataset.task ? "tasks" : el.dataset.story ? "stories" : null; if (!kind) return;
  const id = el.dataset.task || el.dataset.story; let v = el.value;
  if (el.dataset.f === "assignee_id") v = v === "" ? null : +v;
  if (el.dataset.f === "due_date") v = v === "" ? null : v;
  await api(`/${kind}/${id}`, { method: "PATCH", body: { [el.dataset.f]: v } }); loadTree();
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const el = e.target, sid = el.dataset.newtask || el.dataset.newdue;
  if (sid) return addTask(sid);                 // Enter in title OR date field adds the task
  if (el.id === "s_title" || el.id === "s_pts") $("#btn_sadd").click();
  if (el.id === "p_name") $("#btn_padd").click();
  if (el.id === "a_pw") $("#btn_login").click();
});
start();
