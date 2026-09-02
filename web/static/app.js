(() => {
  "use strict";

  const AI_WORKSPACE_VERSION = "workspace-20260601-telegram-ops";
  console.log("AI_MANAGER_WORKSPACE_RENDERER", AI_WORKSPACE_VERSION);
  window.AI_MANAGER_WORKSPACE_VERSION = AI_WORKSPACE_VERSION;

  const app = document.getElementById("app");
  let roomEngine = null;
  const state = {
    token: localStorage.getItem("am_token") || "",
    chat: [],
    tasks: {
      active: [],
      delegated: [],
      in_progress: [],
      pending: [],
      completed: [],
      archived: [],
      active_user: [],
      active_agent: [],
      active_system: [],
      counts: { active: 0, delegated: 0, in_progress: 0, active_user: 0, active_agent: 0, active_system: 0, pending: 0, completed: 0, archived: 0 },
    },
    stats: null,
    selectedTask: null,
    loading: false,
    taskCreating: false,
    mockApi: false,
    agentActivity: [],
    agents: [
      { id: "chief", name: "Chief", role: "Lead AI Strategist", online: true, status: "Idle" },
      { id: "business", name: "Business", role: "Business Strategist", online: false, status: "Idle" },
      { id: "smm", name: "SMM", role: "Content and Growth", online: false, status: "Idle" },
    ],
  };

  const escapeHtml = (value = "") =>
    String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    })[char]);

  function inlineRichText(text = "") {
    return escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(/(https?:\/\/[^\s<]+)/g, "<a href=\"$1\" target=\"_blank\" rel=\"noreferrer\">$1</a>");
  }

  function markdown(text = "") {
    const lines = String(text).replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [];
    let list = [];
    let code = [];
    let inCode = false;

    const flushParagraph = () => {
      if (!paragraph.length) return;
      blocks.push(`<p>${paragraph.map(inlineRichText).join("<br>")}</p>`);
      paragraph = [];
    };
    const flushList = () => {
      if (!list.length) return;
      blocks.push(`<ul>${list.map((item) => `<li>${inlineRichText(item)}</li>`).join("")}</ul>`);
      list = [];
    };
    const flushCode = () => {
      if (!code.length) return;
      blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      code = [];
    };

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (line.trim().startsWith("```")) {
        if (inCode) {
          flushCode();
          inCode = false;
        } else {
          flushParagraph();
          flushList();
          inCode = true;
        }
        continue;
      }
      if (inCode) {
        code.push(rawLine);
        continue;
      }
      if (!line.trim()) {
        flushParagraph();
        flushList();
        continue;
      }
      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        flushList();
        blocks.push(`<h4>${inlineRichText(heading[2])}</h4>`);
        continue;
      }
      const bullet = line.match(/^[-*•]\s+(.+)$/);
      if (bullet) {
        flushParagraph();
        list.push(bullet[1]);
        continue;
      }
      const numbered = line.match(/^\d+[.)]\s+(.+)$/);
      if (numbered) {
        flushParagraph();
        list.push(numbered[1]);
        continue;
      }
      flushList();
      paragraph.push(line.trim());
    }

    flushCode();
    flushParagraph();
    flushList();
    return `<div class="rich-text">${blocks.join("")}</div>`;
  }

  function nowLabel(value) {
    if (!value) return "Just now";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Just now";
    return date.toLocaleString([], { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function formatUptime(seconds = 0) {
    const total = Math.max(0, Number(seconds) || 0);
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }

  function activeAgentStatus() {
    if (state.loading) return "Thinking";
    if ((state.tasks.counts.active_agent || 0) > 0) return "Working";
    return "Idle";
  }

  function setAgentStatus() {
    if (!state.loading) return;
    state.agents = state.agents.map((agent) => (
      agent.id === "chief" ? { ...agent, status: "Thinking" } : agent
    ));
  }

  function mockResponse(path, opts = {}) {
    state.mockApi = true;
    const method = (opts.method || "GET").toUpperCase();
    if (path === "/api/chat/history") return [];
    if (path === "/api/tasks/queue") return { active: [], delegated: [], in_progress: [], pending: [], completed: [], archived: [], active_user: [], active_agent: [], active_system: [], counts: { active: 0, delegated: 0, in_progress: 0, active_user: 0, active_agent: 0, active_system: 0, pending: 0, completed: 0, archived: 0 } };
    if (path === "/api/agents") return state.agents;
    if (path === "/api/agents/activity") return [];
    if (path === "/api/system/stats") {
      return { cpu_percent: 0, memory_used_mb: null, memory_total_mb: null, disk_used_gb: 0, disk_total_gb: 0, uptime_seconds: 0, tasks_running: 0 };
    }
    if (path === "/api/chat" && method === "POST") return { reply: "Backend is unavailable. Emergency UI mode is active." };
    if (method === "POST" || method === "DELETE") return { ok: true };
    return {};
  }

  async function api(path, opts = {}) {
    const headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${state.token}`,
      ...(opts.headers || {}),
    };

    let response;
    try {
      response = await fetch(path, { ...opts, headers });
    } catch (error) {
      console.warn("API unavailable", path, error);
      return mockResponse(path, opts);
    }

    if (response.status === 401) {
      localStorage.removeItem("am_token");
      state.token = "";
      throw new Error("auth");
    }
    if (response.status === 404) return mockResponse(path, opts);
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(`API ${response.status}: ${path}\n${body}`);
    }

    state.mockApi = false;
    return response.json();
  }

  function normalizeQueue(queue) {
    return {
      active: queue.active || [],
      delegated: queue.delegated || [],
      in_progress: queue.in_progress || [],
      pending: queue.pending || [],
      completed: queue.completed || [],
      archived: queue.archived || [],
      active_user: queue.active_user || (queue.active || []).filter((task) => task.type === "user_task"),
      active_agent: queue.active_agent || (queue.active || []).filter((task) => task.type === "agent_task"),
      active_system: queue.active_system || (queue.active || []).filter((task) => task.type === "system_task"),
      counts: {
        active: queue.counts?.active ?? (queue.active || []).length,
        delegated: queue.counts?.delegated ?? (queue.delegated || []).length,
        in_progress: queue.counts?.in_progress ?? (queue.in_progress || []).length,
        active_user: queue.counts?.active_user ?? (queue.active_user || []).length,
        active_agent: queue.counts?.active_agent ?? (queue.active_agent || []).length,
        active_system: queue.counts?.active_system ?? (queue.active_system || []).length,
        pending: queue.counts?.pending ?? (queue.pending || []).length,
        completed: queue.counts?.completed ?? (queue.completed || []).length,
        archived: queue.counts?.archived ?? (queue.archived || []).length,
      },
    };
  }

  function normalizeAgents(agents = []) {
    const fallback = [
      { id: "chief", name: "Chief", role: "Lead AI Strategist", online: true, status: "Idle" },
      { id: "business", name: "Business", role: "Business Strategist", online: false, status: "Idle" },
      { id: "smm", name: "SMM", role: "Content and Growth", online: false, status: "Idle" },
    ];
    const incoming = Array.isArray(agents) && agents.length ? agents : fallback;
    return incoming.map((agent) => ({
      id: agent.id || agent.name?.toLowerCase() || "agent",
      name: agent.name || "Agent",
      role: agent.role || "Agent",
      online: Boolean(agent.online),
      status: agent.status || "Idle",
      activeTasks: agent.active_tasks || 0,
      currentTask: agent.current_task || "",
      currentTaskId: agent.current_task_id || null,
    }));
  }

  async function hydrate() {
    if (!state.token) {
      render();
      return;
    }

    const [history, tasks, stats, agents, activity] = await Promise.all([
      api("/api/chat/history"),
      api("/api/tasks/queue"),
      api("/api/system/stats"),
      api("/api/agents"),
      api("/api/agents/activity"),
    ]);
    state.chat = history.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      createdAt: message.created_at,
    }));
    state.tasks = normalizeQueue(tasks);
    state.stats = stats;
    state.agents = normalizeAgents(agents);
    state.agentActivity = activity || [];
    render({ scrollChatBottom: true });
  }

  async function refreshOperationalData({ soft = true } = {}) {
    if (!state.token) return;
    try {
      const [tasks, stats, agents, activity] = await Promise.all([
        api("/api/tasks/queue"),
        api("/api/system/stats"),
        api("/api/agents"),
        api("/api/agents/activity"),
      ]);
      state.tasks = normalizeQueue(tasks);
      state.stats = stats;
      state.agents = normalizeAgents(agents);
      state.agentActivity = activity || [];
      if (soft) updateOperationalDom();
    } catch (error) {
      console.warn("Operational refresh failed", error);
    }
  }

  function disableLegacyServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.getRegistrations()
      .then((registrations) => Promise.all(registrations.map((registration) => registration.unregister())))
      .catch((error) => console.warn("Service worker unregister failed", error));
  }

  function getChatScrollSnapshot() {
    const stream = document.getElementById("chatStream");
    if (!stream) return null;
    const bottomOffset = stream.scrollHeight - stream.scrollTop - stream.clientHeight;
    return { nearBottom: bottomOffset < 72, bottomOffset };
  }

  function restoreChatScroll(snapshot, forceBottom = false) {
    const stream = document.getElementById("chatStream");
    if (!stream) return;
    if (forceBottom || !snapshot || snapshot.nearBottom) {
      stream.scrollTop = stream.scrollHeight;
      return;
    }
    stream.scrollTop = Math.max(0, stream.scrollHeight - stream.clientHeight - snapshot.bottomOffset);
  }

  function renderLogin() {
    app.innerHTML = `
      <main class="login-shell">
        <section class="login-card">
          <div class="brand-mark">AI</div>
          <h1>AI Manager</h1>
          <p>Personal AI operating workspace</p>
          <div class="login-row">
            <input id="tokenInput" type="password" placeholder="WEB_ACCESS_TOKEN" autocomplete="off">
            <button id="loginButton">Enter</button>
          </div>
        </section>
      </main>
    `;
    document.getElementById("loginButton").addEventListener("click", login);
    document.getElementById("tokenInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") login();
    });
  }

  function render(options = {}) {
    if (!app) return;
    if (!state.token) {
      renderLogin();
      return;
    }

    const scrollSnapshot = options.preserveChatScroll === false ? null : getChatScrollSnapshot();
    setAgentStatus();
    app.innerHTML = `
      <main class="os-shell ${state.loading ? "is-thinking" : ""}">
        <div class="global-workspace-title">AI Operating Workspace</div>
        <div class="version-stamp">AI Workspace ${AI_WORKSPACE_VERSION}</div>
        ${state.mockApi ? '<div class="system-banner">Emergency API fallback is active</div>' : ""}
        <section class="chat-panel">
          ${renderChatPanel()}
        </section>
        <section class="control-panel">
          ${renderTeamPanel()}
          ${renderTaskQueue()}
          ${renderStats()}
        </section>
        <section class="room-panel">
          ${renderRoom()}
        </section>
        ${state.selectedTask ? renderTaskModal(state.selectedTask) : ""}
      </main>
    `;

    bindEvents();
    restoreChatScroll(scrollSnapshot, Boolean(options.scrollChatBottom));
  }

  function renderChatPanel() {
    return `
      <header class="panel-header">
        <div>
          <span class="eyebrow">Local Console</span>
          <h2>Workspace Console</h2>
        </div>
        <button class="icon-button" id="clearChatButton" title="Clear local console">Clear</button>
      </header>
      <div id="chatStream" class="chat-stream">
        ${state.chat.length ? state.chat.map(renderMessage).join("") : `
          <div class="empty-state">
            <span>Telegram is primary</span>
            <p>This local console stays available for direct workspace checks.</p>
          </div>
        `}
        ${state.loading ? renderTyping() : ""}
      </div>
      <form id="chatForm" class="composer">
        <textarea id="chatInput" rows="1" placeholder="Local console message..."></textarea>
        <button type="submit" ${state.loading ? "disabled" : ""}>Send</button>
      </form>
    `;
  }

  function renderMessage(message) {
    const own = message.role === "user";
    return `
      <article class="message ${own ? "from-user" : "from-ai"}">
        <div class="message-meta">${own ? "You" : "Chief"} &middot; ${nowLabel(message.createdAt)}</div>
        <div class="message-bubble">${own ? escapeHtml(message.content) : markdown(message.content)}</div>
      </article>
    `;
  }

  function renderTyping() {
    return `
      <article id="typingMessage" class="message from-ai">
        <div class="message-meta">Chief &middot; thinking</div>
        <div class="message-bubble typing"><span></span><span></span><span></span></div>
      </article>
    `;
  }

  function renderTeamPanel() {
    return `
      <section class="panel team-panel">
        <header class="section-header">
          <span>Team</span>
          <button class="small-button" disabled>Agents</button>
        </header>
        <div id="agentList" class="agent-list">
          ${agentListHtml()}
        </div>
      </section>
    `;
  }

  function agentListHtml() {
    return state.agents.map((agent) => `
      <article class="agent-card">
        <div>
          <h3>${escapeHtml(agent.name)}</h3>
          <p>${escapeHtml(agent.role)}</p>
          <div class="agent-state ${agent.status.toLowerCase()}">${escapeHtml(agent.status)}</div>
        </div>
        <div class="online-state ${agent.online ? "online" : "offline"}"><span></span>${agent.online ? "Online" : "Offline"}</div>
      </article>
    `).join("");
  }

  function renderTaskQueue() {
    const { active, pending, completed, counts } = state.tasks;
    const visibleCompleted = completed.slice(0, 5);
    return `
      <section class="panel task-panel">
        <header class="section-header">
          <span>Task Queue</span>
          <div id="taskCounter" class="task-counter">${counts.active_user || 0} User · ${counts.active_agent || 0} Agent</div>
        </header>
        <form id="newTaskForm" class="new-task-form">
          <input id="newTaskInput" placeholder="Capture task">
          <button id="newTaskButton" type="submit" ${state.taskCreating ? "disabled" : ""}>${state.taskCreating ? "..." : "New"}</button>
        </form>
        <div id="taskList" class="task-list">
          ${taskListHtml(active, pending, visibleCompleted, counts)}
        </div>
        <div id="agentActivity" class="activity-list">
          ${agentActivityHtml()}
        </div>
      </section>
    `;
  }

  function taskListHtml(active = state.tasks.active, pending = state.tasks.pending, completed = state.tasks.completed.slice(0, 5), counts = state.tasks.counts) {
    const userTasks = active.filter((task) => task.type === "user_task");
    const agentTasks = active.filter((task) => task.type === "agent_task");
    const systemTasks = active.filter((task) => task.type === "system_task");
    return `
      <div class="queue-label">User Tasks</div>
      ${userTasks.length ? userTasks.map((task) => renderTaskCard(task, "active")).join("") : '<div class="empty-row compact">No active user tasks</div>'}
      <div class="queue-label">Agent Work</div>
      ${agentTasks.length ? agentTasks.map((task) => renderTaskCard(task, "agent")).join("") : '<div class="empty-row compact">Chief is idle</div>'}
      ${systemTasks.length ? `<div class="queue-label">System</div>${systemTasks.map((task) => renderTaskCard(task, "system")).join("")}` : ""}
      <details class="completed-group">
        <summary>Completed <span>${counts.completed}</span></summary>
        ${completed.length ? completed.map((task) => renderTaskCard(task, "completed")).join("") : '<div class="empty-row compact">Nothing completed yet</div>'}
      </details>
    `;
  }

  function agentActivityHtml() {
    const rows = (state.agentActivity || []).slice(-6);
    if (!rows.length) {
      return `
        <div class="queue-label">Orchestration</div>
        <div class="empty-row compact">No delegation activity yet</div>
      `;
    }
    return `
      <div class="queue-label">Orchestration</div>
      ${rows.map((item) => `
        <button class="activity-row" ${item.task_id ? `data-task-id="${item.task_id}"` : ""} type="button">
          <span>${escapeHtml(item.from_agent || "Agent")} → ${escapeHtml(item.to_agent || "Chief")}</span>
          <strong>${escapeHtml(item.task_title || item.channel || "coordination")}</strong>
        </button>
      `).join("")}
    `;
  }

  function renderTaskCard(task, tone) {
    return `
      <button class="task-card ${tone}" data-task-id="${task.id}" type="button">
        <span class="task-type">${escapeHtml(task.type || "user_task")}</span>
        <span class="task-title">${escapeHtml(task.title)}</span>
        <span class="task-meta">${escapeHtml(task.assigned_to || task.assigned_agent || "Chief")} &middot; ${escapeHtml(task.status || "active")} &middot; ${nowLabel(task.created_at)}</span>
      </button>
    `;
  }

  function renderStats() {
    const stats = state.stats || {};
    const memory = stats.memory_total_mb ? `${stats.memory_used_mb}/${stats.memory_total_mb}MB` : "n/a";
    const disk = stats.disk_total_gb ? `${stats.disk_used_gb}/${stats.disk_total_gb}GB` : "n/a";
    return `
      <section id="statsPanel" class="panel stats-panel">
        ${statsHtml(memory, disk)}
      </section>
    `;
  }

  function statsHtml(memory, disk) {
    const stats = state.stats || {};
    const memoryValue = memory ?? (stats.memory_total_mb ? `${stats.memory_used_mb}/${stats.memory_total_mb}MB` : "n/a");
    const diskValue = disk ?? (stats.disk_total_gb ? `${stats.disk_used_gb}/${stats.disk_total_gb}GB` : "n/a");
    return `
      <article><span>CPU</span><strong>${Math.round(stats.cpu_percent || 0)}%</strong></article>
      <article><span>RAM</span><strong>${escapeHtml(memoryValue)}</strong></article>
      <article><span>Disk</span><strong>${escapeHtml(diskValue)}</strong></article>
      <article><span>Uptime</span><strong>${formatUptime(stats.uptime_seconds)}</strong></article>
    `;
  }

  function renderRoom() {
    const active = state.tasks.counts.active || 0;
    return `
      <div class="room-shell" data-active-tasks="${active}">
        <div class="room-scene canvas-scene">
          <canvas id="roomCanvas" class="room-canvas" aria-label="AI operations office"></canvas>
        </div>
      </div>
    `;
  }

  function initRoomCanvas() {
    const canvas = document.getElementById("roomCanvas");
    if (!canvas) return;
    if (roomEngine?.canvas === canvas) {
      updateRoomCanvasState();
      return;
    }

    roomEngine = {
      canvas,
      ctx: canvas.getContext("2d"),
      dpr: Math.max(1, Math.min(window.devicePixelRatio || 1, 2)),
      activeTasks: state.tasks.counts.active || 0,
      userTasks: state.tasks.counts.active_user || 0,
      agentTasks: state.tasks.counts.active_agent || 0,
      systemTasks: state.tasks.counts.active_system || 0,
      mode: activeAgentStatus().toLowerCase(),
      startedAt: performance.now(),
      lastSize: "",
      running: true,
      resizeObserver: null,
    };

    resizeRoomCanvas();
    roomEngine.resizeObserver = new ResizeObserver(() => {
      roomEngine.lastSize = "";
      resizeRoomCanvas();
    });
    roomEngine.resizeObserver.observe(canvas.parentElement || canvas);
    window.addEventListener("resize", resizeRoomCanvas, { passive: true });
    requestAnimationFrame(drawRoomFrame);
  }

  function resizeRoomCanvas() {
    if (!roomEngine?.canvas || !roomEngine.ctx) return;
    const { canvas, ctx, dpr } = roomEngine;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(320, Math.floor(rect.width));
    const height = Math.max(320, Math.floor(rect.height));
    const nextSize = `${width}x${height}`;
    if (roomEngine.lastSize === nextSize) return;
    roomEngine.lastSize = nextSize;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function updateRoomCanvasState() {
    if (!roomEngine) return;
    roomEngine.activeTasks = state.tasks.counts.active || 0;
    roomEngine.userTasks = state.tasks.counts.active_user || 0;
    roomEngine.agentTasks = state.tasks.counts.active_agent || 0;
    roomEngine.systemTasks = state.tasks.counts.active_system || 0;
    roomEngine.mode = activeAgentStatus().toLowerCase();
  }

  function agentMode(agentId) {
    const agent = state.agents.find((item) => item.id === agentId);
    const status = String(agent?.status || "Idle").toLowerCase();
    if (status.includes("working") || status.includes("delegated") || status.includes("queued")) return "working";
    if (status.includes("thinking")) return "thinking";
    return "idle";
  }

  function drawRoomFrame(time) {
    if (!roomEngine?.running || !roomEngine.ctx || !roomEngine.canvas.isConnected) return;
    resizeRoomCanvas();
    updateRoomCanvasState();

    const { canvas, ctx } = roomEngine;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const t = (time - roomEngine.startedAt) / 1000;

    ctx.clearRect(0, 0, w, h);
    drawRoomBackground(ctx, w, h);
    drawWindow(ctx, w * 0.08, h * 0.06, w * 0.24, h * 0.3, t, 0);
    drawWindow(ctx, w * 0.38, h * 0.06, w * 0.24, h * 0.3, t, 1);
    drawWindow(ctx, w * 0.68, h * 0.06, w * 0.24, h * 0.3, t, 2);
    drawStringLights(ctx, w, h, t);

    const s = Math.max(0.78, Math.min(w / 1120, h / 700));
    const chiefMode = state.loading ? "thinking" : agentMode("chief");
    const businessMode = agentMode("business");
    const smmMode = agentMode("smm");
    const chiefActive = chiefMode === "thinking" || chiefMode === "working";
    const smmActive = smmMode === "thinking" || smmMode === "working" || (roomEngine.systemTasks || 0) > 0;
    const chiefDesk = { x: w * 0.18, y: h * 0.63 };
    const businessDesk = { x: w * 0.5, y: h * 0.74 };
    const systemDesk = { x: w * 0.82, y: h * 0.63 };
    drawDeskPod(ctx, chiefDesk.x, chiefDesk.y, s, "Chief", chiefMode, t, "dark", !chiefActive);
    drawDeskPod(ctx, businessDesk.x, businessDesk.y, s * 0.98, "Business", businessMode, t + 0.8, "brown");
    drawDeskPod(ctx, systemDesk.x, systemDesk.y, s * 0.98, "SMM", smmActive ? "working" : smmMode, t + 1.6, "light");

    if (!chiefActive) {
      drawWanderingChief(ctx, [chiefDesk, businessDesk, systemDesk], s, t);
    }

    drawTaskLamp(ctx, chiefDesk.x + 92 * s, chiefDesk.y - 84 * s, s, chiefActive ? "active" : "idle", t);
    drawTaskLamp(ctx, businessDesk.x + 88 * s, businessDesk.y - 84 * s, s, businessMode === "working" ? "active" : "idle", t + 0.8);
    drawTaskLamp(ctx, systemDesk.x + 88 * s, systemDesk.y - 84 * s, s, smmActive ? "active" : "idle", t + 1.6);
    drawServerRack(ctx, w * 0.94, h * 0.62, s, t, smmActive);
    drawSystemUnit(ctx, w * 0.3, h * 0.73, s, chiefActive, t);
    drawSystemUnit(ctx, w * 0.59, h * 0.84, s * 0.92, businessMode === "working", t + 1);
    drawSystemUnit(ctx, w * 0.87, h * 0.72, s * 0.92, smmActive, t + 2);
    drawPlant(ctx, w * 0.07, h * 0.82, s * 0.95);
    drawPlant(ctx, w * 0.95, h * 0.82, s * 0.9);
    drawAmbientDust(ctx, w, h, t);

    requestAnimationFrame(drawRoomFrame);
  }

  function roundedRect(ctx, x, y, w, h, r) {
    const radius = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  }

  function fillRound(ctx, x, y, w, h, r, fill, stroke = null) {
    roundedRect(ctx, x, y, w, h, r);
    ctx.fillStyle = fill;
    ctx.fill();
    if (stroke) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }

  function drawRoomBackground(ctx, w, h) {
    const wall = ctx.createLinearGradient(0, 0, 0, h);
    wall.addColorStop(0, "#efe6da");
    wall.addColorStop(0.52, "#f7f0e8");
    wall.addColorStop(1, "#dbcec0");
    ctx.fillStyle = wall;
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = "rgba(42, 37, 31, 0.055)";
    ctx.lineWidth = 1;
    for (let x = 0; x < w; x += 48) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    for (let y = 0; y < h; y += 48) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    ctx.fillStyle = "rgba(72, 61, 49, 0.08)";
    ctx.fillRect(0, h * 0.47, w, 1);
    ctx.fillRect(0, h * 0.78, w, 1);

    const vignette = ctx.createRadialGradient(w * 0.52, h * 0.52, Math.min(w, h) * 0.2, w * 0.52, h * 0.52, Math.max(w, h) * 0.82);
    vignette.addColorStop(0, "rgba(255,255,255,0)");
    vignette.addColorStop(1, "rgba(90,78,62,0.055)");
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, w, h);
  }

  function drawWindow(ctx, x, y, w, h, t, offset) {
    fillRound(ctx, x, y, w, h, 4, "#fff8ed", "rgba(44, 39, 32, 0.08)");
    const inset = Math.max(7, w * 0.04);
    const sky = ctx.createLinearGradient(0, y + inset, 0, y + h - inset);
    sky.addColorStop(0, "#77afd1");
    sky.addColorStop(0.68, "#c6dde3");
    sky.addColorStop(1, "#364147");
    fillRound(ctx, x + inset, y + inset, w - inset * 2, h - inset * 2, 2, sky);

    const baseY = y + h - inset * 1.2;
    const buildingColor = "#2d373b";
    for (let i = 0; i < 6; i += 1) {
      const bw = w * (0.11 + (i % 2) * 0.02);
      const bh = h * (0.2 + ((i + offset) % 3) * 0.08);
      const bx = x + inset + w * 0.08 + i * w * 0.12;
      ctx.fillStyle = buildingColor;
      ctx.fillRect(bx, baseY - bh, bw, bh);
      ctx.fillStyle = `rgba(236, 203, 107, ${0.35 + Math.sin(t + i + offset) * 0.18})`;
      ctx.fillRect(bx + bw * 0.28, baseY - bh + bh * 0.28, 3, 3);
      ctx.fillRect(bx + bw * 0.62, baseY - bh + bh * 0.52, 3, 3);
    }

    ctx.fillStyle = "#e3c864";
    ctx.globalAlpha = 0.82;
    ctx.fillRect(x + w * 0.68, y + h * 0.18, 12, 12);
    ctx.globalAlpha = 1;
  }

  function drawStringLights(ctx, w, h, t) {
    const y = h * 0.39;
    const start = w * 0.12;
    const end = w * 0.9;
    ctx.strokeStyle = "rgba(92, 79, 62, 0.18)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(start, y);
    for (let i = 1; i <= 5; i += 1) {
      const x = start + ((end - start) / 5) * i;
      const sag = Math.sin((i / 5) * Math.PI) * 18;
      ctx.lineTo(x, y + sag);
    }
    ctx.stroke();
    for (let i = 0; i < 13; i += 1) {
      const p = i / 12;
      const x = start + (end - start) * p;
      const sag = Math.sin(p * Math.PI) * 18;
      const glow = 0.12 + Math.abs(Math.sin(t * 0.7 + i)) * 0.08;
      const color = i % 5 === 0 ? "#d8c47b" : i % 5 === 3 ? "#9fb993" : "#ead7a1";
      const radial = ctx.createRadialGradient(x, y + sag, 1, x, y + sag, 28);
      radial.addColorStop(0, `rgba(232, 208, 144, ${glow})`);
      radial.addColorStop(1, "rgba(232, 208, 144, 0)");
      ctx.fillStyle = radial;
      ctx.fillRect(x - 30, y + sag - 30, 60, 60);
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.42 + Math.sin(t * 0.5 + i) * 0.08;
      ctx.beginPath();
      ctx.arc(x, y + sag + 2, 2.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  function drawCeilingLamp(ctx, x, y, t) {
    ctx.strokeStyle = "rgba(46, 41, 34, 0.35)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x, y + 55);
    ctx.stroke();
    fillRound(ctx, x - 18, y + 48, 36, 18, 8, "#d8c47b");
    const glow = ctx.createRadialGradient(x, y + 66, 4, x, y + 72, 72);
    glow.addColorStop(0, `rgba(219, 198, 123, ${0.18 + Math.sin(t) * 0.03})`);
    glow.addColorStop(1, "rgba(219, 198, 123, 0)");
    ctx.fillStyle = glow;
    ctx.fillRect(x - 90, y + 28, 180, 130);
  }

  function drawDeskPod(ctx, cx, cy, scale, label, mode, t, variant, hideAgent = false) {
    const s = Math.max(0.75, Math.min(scale, 1.25));
    const deskW = 190 * s;
    const deskH = 58 * s;
    const x = cx - deskW / 2;
    const y = cy - deskH / 2;

    ctx.fillStyle = "rgba(36, 31, 25, 0.09)";
    ctx.beginPath();
    ctx.ellipse(cx, y + deskH + 44 * s, deskW * 0.54, 18 * s, 0, 0, Math.PI * 2);
    ctx.fill();

    fillRound(ctx, x, y, deskW, deskH, 5 * s, "#d7cabb", "rgba(42, 36, 30, 0.12)");
    ctx.fillStyle = "#8c7d6e";
    ctx.fillRect(x + 12 * s, y + deskH - 6 * s, deskW - 24 * s, 6 * s);
    ctx.fillStyle = "#b6a694";
    ctx.fillRect(x + 18 * s, y + deskH, 7 * s, 46 * s);
    ctx.fillRect(x + deskW - 25 * s, y + deskH, 7 * s, 46 * s);

    const activity = mode === "working" || mode === "thinking";
    if (activity) {
      const pulse = ctx.createRadialGradient(cx, y + 6 * s, 8 * s, cx, y + 16 * s, 120 * s);
      pulse.addColorStop(0, "rgba(143, 176, 138, 0.16)");
      pulse.addColorStop(1, "rgba(143, 176, 138, 0)");
      ctx.fillStyle = pulse;
      ctx.fillRect(x - 44 * s, y - 88 * s, deskW + 88 * s, deskH + 150 * s);
    }
    drawMonitor(ctx, x + 30 * s, y - 50 * s, 60 * s, 44 * s, t, mode, "#9fb993");
    drawMonitor(ctx, x + 98 * s, y - 42 * s, 60 * s, 44 * s, t + 0.6, mode, "#d5ad66");
    if (label === "Business") drawMonitor(ctx, x + 66 * s, y - 64 * s, 76 * s, 48 * s, t + 1.2, mode, "#9fb993");

    if (!hideAgent) {
      drawAgent(ctx, cx + (activity ? Math.sin(t * 1.4) * 5 * s : Math.sin(t * 0.35) * 1.6 * s), y - 5 * s, s, mode, t, variant);
    }

    ctx.fillStyle = "rgba(39, 34, 29, 0.46)";
    ctx.font = `${10 * s}px Inter, Segoe UI, sans-serif`;
    ctx.fillText(label, x + 12 * s, y + deskH + 24 * s);
  }

  function drawMonitor(ctx, x, y, w, h, t, mode, accent) {
    const active = mode === "working" || mode === "thinking";
    fillRound(ctx, x, y, w, h, 4, "#181918");
    const screen = active ? "#202d24" : "#222923";
    fillRound(ctx, x + 5, y + 5, w - 10, h - 10, 2, screen);
    const glow = ctx.createRadialGradient(x + w / 2, y + h / 2, 2, x + w / 2, y + h / 2, w * 0.66);
    glow.addColorStop(0, active ? "rgba(159,185,147,0.2)" : "rgba(210,210,188,0.08)");
    glow.addColorStop(1, "rgba(159,185,147,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(x - w * 0.18, y - h * 0.18, w * 1.36, h * 1.36);
    ctx.fillStyle = active ? accent : "rgba(190, 190, 175, 0.42)";
    for (let i = 0; i < 5; i += 1) {
      const drift = active ? Math.sin(t * 1.7 + i) * 3.4 : Math.sin(t * 0.38 + i) * 1.4;
      const alpha = active ? 0.42 + Math.abs(Math.sin(t * 2.2 + i)) * 0.5 : 0.22 + Math.abs(Math.sin(t * 0.42 + i)) * 0.18;
      ctx.globalAlpha = alpha;
      ctx.fillRect(x + 12 + drift, y + 11 + i * 6, 16 + ((i + 1) % 3) * 11, 2.5);
    }
    ctx.globalAlpha = active ? 0.75 : 0.32 + Math.abs(Math.sin(t * 0.8)) * 0.16;
    ctx.fillStyle = active ? "#e7d182" : "#c8c0a6";
    ctx.fillRect(x + w - 13, y + 10 + Math.sin(t * (active ? 6 : 1.2)) * 1.5, 3, 6);
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#2e2f2c";
    ctx.fillRect(x + w * 0.45, y + h, w * 0.1, 10);
    ctx.fillRect(x + w * 0.32, y + h + 10, w * 0.36, 3);
  }

  function drawWanderingChief(ctx, desks, s, t) {
    const stops = [
      { x: desks[0].x, y: desks[0].y - 34 * s, hold: 9 },
      { x: desks[1].x - 34 * s, y: desks[1].y - 44 * s, hold: 6 },
      { x: desks[2].x - 38 * s, y: desks[2].y - 38 * s, hold: 6 },
      { x: desks[0].x + 18 * s, y: desks[0].y - 32 * s, hold: 8 },
    ];
    const moveDuration = 7;
    const total = stops.reduce((sum, stop) => sum + stop.hold + moveDuration, 0);
    let cursor = t % total;
    for (let i = 0; i < stops.length; i += 1) {
      const current = stops[i];
      const next = stops[(i + 1) % stops.length];
      if (cursor < current.hold) {
        drawAgent(ctx, current.x + Math.sin(t * 0.7) * 1.2 * s, current.y, s, "idle", t, "dark");
        drawTinyInteraction(ctx, current.x + 24 * s, current.y + 18 * s, s, t);
        return;
      }
      cursor -= current.hold;
      if (cursor < moveDuration) {
        const p = cursor / moveDuration;
        const eased = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
        const x = current.x + (next.x - current.x) * eased;
        const y = current.y + (next.y - current.y) * eased + Math.sin(p * Math.PI * 6) * 2 * s;
        drawAgent(ctx, x, y, s, "idle", t * 1.6, "dark");
        return;
      }
      cursor -= moveDuration;
    }
  }

  function drawTinyInteraction(ctx, x, y, s, t) {
    ctx.globalAlpha = 0.24 + Math.sin(t * 0.9) * 0.08;
    ctx.strokeStyle = "#8fb08a";
    ctx.lineWidth = 1.2 * s;
    ctx.beginPath();
    ctx.arc(x, y, 5 * s, 0, Math.PI * 1.4);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  function drawAgent(ctx, cx, y, s, mode, t, variant) {
    const bob = mode === "idle" ? Math.sin(t * 0.8) * 1.1 * s : Math.sin(t * 2.2) * 2.2 * s;
    const head = variant === "light" ? "#f0cfa2" : variant === "brown" ? "#d5ab80" : "#c79568";
    const suit = variant === "light" ? "#f7f4ec" : "#242321";
    const hair = variant === "light" ? "#d2a94e" : "#2a211b";

    fillRound(ctx, cx - 17 * s, y + 26 * s + bob, 34 * s, 44 * s, 5 * s, suit);
    fillRound(ctx, cx - 13 * s, y + 4 * s + bob, 26 * s, 25 * s, 6 * s, head);
    ctx.fillStyle = hair;
    ctx.fillRect(cx - 13 * s, y + 4 * s + bob, 26 * s, 8 * s);
    ctx.fillStyle = "rgba(25, 22, 19, 0.62)";
    ctx.fillRect(cx - 6 * s, y + 18 * s + bob, 3 * s, 3 * s);
    ctx.fillRect(cx + 5 * s, y + 18 * s + bob, 3 * s, 3 * s);

    if (mode === "thinking") {
      ctx.strokeStyle = "rgba(139, 108, 50, 0.48)";
      ctx.lineWidth = 1.5 * s;
      ctx.beginPath();
      ctx.arc(cx + 22 * s, y + 1 * s + Math.sin(t * 2) * 2 * s, 5 * s, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (mode === "working") {
      ctx.strokeStyle = "rgba(143, 176, 138, 0.5)";
      ctx.lineWidth = 1.2 * s;
      ctx.beginPath();
      ctx.arc(cx, y + 41 * s + bob, 27 * s + Math.sin(t * 2) * 2 * s, -0.2, Math.PI * 1.15);
      ctx.stroke();
    }

    ctx.strokeStyle = "rgba(30, 27, 23, 0.42)";
    ctx.lineWidth = 3 * s;
    ctx.beginPath();
    ctx.moveTo(cx - 14 * s, y + 42 * s + bob);
    ctx.lineTo(cx - 29 * s, y + 55 * s + bob);
    ctx.moveTo(cx + 14 * s, y + 42 * s + bob);
    ctx.lineTo(cx + 29 * s, y + 55 * s + bob);
    ctx.stroke();
  }

  function drawTaskLamp(ctx, cx, cy, s, state, t) {
    const active = state === "active";
    const idle = state === "idle";
    ctx.strokeStyle = "rgba(55, 48, 39, 0.46)";
    ctx.lineWidth = 3 * s;
    ctx.beginPath();
    ctx.moveTo(cx, cy + 56 * s);
    ctx.lineTo(cx, cy + 10 * s);
    ctx.lineTo(cx + 24 * s, cy - 2 * s);
    ctx.stroke();
    fillRound(ctx, cx + 14 * s, cy - 16 * s, 34 * s, 18 * s, 8 * s, active ? "#d8c47b" : "#c4b894");
    if (active || idle) {
      const strength = active ? 0.24 + Math.sin(t * 2) * 0.04 : 0.07 + Math.sin(t * 0.45) * 0.018;
      const glow = ctx.createRadialGradient(cx + 30 * s, cy + 4 * s, 4 * s, cx + 30 * s, cy + 16 * s, 95 * s);
      glow.addColorStop(0, `rgba(218, 196, 123, ${strength})`);
      glow.addColorStop(1, "rgba(218, 196, 123, 0)");
      ctx.fillStyle = glow;
      ctx.fillRect(cx - 70 * s, cy - 26 * s, 180 * s, 150 * s);
    }
  }

  function drawSystemUnit(ctx, cx, cy, s, active, t) {
    fillRound(ctx, cx - 14 * s, cy - 28 * s, 28 * s, 56 * s, 4 * s, "#2a2925", "rgba(20,18,15,0.16)");
    ctx.fillStyle = active ? "#8fb08a" : "rgba(245,241,232,0.22)";
    ctx.globalAlpha = active ? 0.45 + Math.abs(Math.sin(t * 2.2)) * 0.4 : 0.3;
    ctx.fillRect(cx - 7 * s, cy - 18 * s, 14 * s, 3 * s);
    ctx.fillRect(cx - 7 * s, cy - 7 * s, 14 * s, 3 * s);
    ctx.fillStyle = active ? "#d1b568" : "rgba(245,241,232,0.18)";
    ctx.fillRect(cx - 4 * s, cy + 12 * s, 8 * s, 8 * s);
    ctx.globalAlpha = 1;
  }

  function drawServerRack(ctx, cx, cy, s, t, active = false) {
    const w = 50 * s;
    const h = 92 * s;
    fillRound(ctx, cx - w / 2, cy - h / 2, w, h, 5 * s, "#262522", "rgba(20, 18, 15, 0.12)");
    for (let i = 0; i < 5; i += 1) {
      ctx.fillStyle = "rgba(245, 241, 232, 0.12)";
      ctx.fillRect(cx - w * 0.32, cy - h * 0.36 + i * 15 * s, w * 0.64, 1.5 * s);
      ctx.fillStyle = i % 2 ? "#d1b568" : "#8fb08a";
      ctx.globalAlpha = active ? 0.42 + Math.abs(Math.sin(t * 1.5 + i)) * 0.42 : 0.18;
      ctx.fillRect(cx - w * 0.25, cy - h * 0.31 + i * 15 * s, 18 * s, 3 * s);
      ctx.globalAlpha = 1;
    }
  }

  function drawConsole(ctx, cx, cy, s, t) {
    fillRound(ctx, cx - 42 * s, cy - 20 * s, 84 * s, 38 * s, 5 * s, "#2a2925");
    ctx.fillStyle = "#d7a85b";
    ctx.globalAlpha = 0.45 + Math.sin(t * 1.7) * 0.18;
    ctx.fillRect(cx - 25 * s, cy - 8 * s, 18 * s, 4 * s);
    ctx.fillStyle = "#8fb08a";
    ctx.fillRect(cx + 5 * s, cy - 8 * s, 26 * s, 4 * s);
    ctx.globalAlpha = 1;
  }

  function drawPlant(ctx, cx, cy, s) {
    fillRound(ctx, cx - 13 * s, cy + 10 * s, 26 * s, 22 * s, 2 * s, "#5c3d28");
    ctx.fillStyle = "#60966a";
    for (let i = 0; i < 5; i += 1) {
      ctx.save();
      ctx.translate(cx, cy + 14 * s);
      ctx.rotate((-0.7 + i * 0.35) * Math.PI);
      fillRound(ctx, -5 * s, -36 * s, 10 * s, 30 * s, 8 * s, "#60966a");
      ctx.restore();
    }
  }

  function drawAmbientDust(ctx, w, h, t) {
    ctx.fillStyle = "rgba(108, 96, 76, 0.12)";
    for (let i = 0; i < 18; i += 1) {
      const x = ((i * 97 + t * (3 + (i % 4))) % (w + 80)) - 40;
      const y = h * (0.16 + ((i * 37) % 70) / 100) + Math.sin(t * 0.22 + i) * 7;
      const r = 0.7 + (i % 3) * 0.25;
      ctx.globalAlpha = 0.18 + Math.sin(t * 0.35 + i) * 0.06;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  function updateOperationalDom() {
    setAgentStatus();

    const agentList = document.getElementById("agentList");
    if (agentList) agentList.innerHTML = agentListHtml();

    const taskCounter = document.getElementById("taskCounter");
    if (taskCounter) taskCounter.textContent = `${state.tasks.counts.active_user || 0} User · ${state.tasks.counts.active_agent || 0} Agent`;

    const taskList = document.getElementById("taskList");
    if (taskList) {
      const completedOpen = taskList.querySelector(".completed-group")?.open ?? false;
      taskList.innerHTML = taskListHtml();
      const completedGroup = taskList.querySelector(".completed-group");
      if (completedGroup) completedGroup.open = completedOpen;
      bindTaskCardEvents();
    }

    const activityList = document.getElementById("agentActivity");
    if (activityList) {
      activityList.innerHTML = agentActivityHtml();
      bindActivityEvents();
    }

    const statsPanel = document.getElementById("statsPanel");
    if (statsPanel) statsPanel.innerHTML = statsHtml();

    const roomShell = document.querySelector(".room-shell");
    if (roomShell) roomShell.dataset.activeTasks = String(state.tasks.counts.active || 0);

    updateRoomCanvasState();
  }

  function isChatNearBottom() {
    const stream = document.getElementById("chatStream");
    if (!stream) return true;
    return stream.scrollHeight - stream.scrollTop - stream.clientHeight < 72;
  }

  function appendChatMessage(message, { forceBottom = false } = {}) {
    const stream = document.getElementById("chatStream");
    if (!stream) return;

    const empty = stream.querySelector(".empty-state");
    if (empty) empty.remove();

    const shouldStick = forceBottom || isChatNearBottom();
    stream.insertAdjacentHTML("beforeend", renderMessage(message));
    if (shouldStick) stream.scrollTop = stream.scrollHeight;
  }

  function showTypingMessage() {
    const stream = document.getElementById("chatStream");
    if (!stream || document.getElementById("typingMessage")) return;
    const shouldStick = isChatNearBottom();
    stream.insertAdjacentHTML("beforeend", renderTyping());
    if (shouldStick) stream.scrollTop = stream.scrollHeight;
  }

  function removeTypingMessage() {
    document.getElementById("typingMessage")?.remove();
  }

  function setThinkingDom(isThinking) {
    const shell = document.querySelector(".os-shell");
    if (shell) shell.classList.toggle("is-thinking", isThinking);
    updateOperationalDom();
  }

  function renderTaskModal(task) {
    return `
      <div class="modal-backdrop" id="modalBackdrop">
        <section class="task-modal">
          <button class="modal-close" id="closeTaskModal" title="Close">&times;</button>
          <span class="eyebrow">Task Details</span>
          <h2>${escapeHtml(task.title)}</h2>
          <div class="detail-grid">
            <div><span>Type</span><strong>${escapeHtml(task.type || "user_task")}</strong></div>
            <div><span>Assigned</span><strong>${escapeHtml(task.assigned_to || task.assigned_agent || "Chief")}</strong></div>
            <div><span>Source</span><strong>${escapeHtml(task.source || "manual")}</strong></div>
            <div><span>Status</span><strong class="status-chip ${escapeHtml(task.status)}">${escapeHtml(task.status)}</strong></div>
            <div><span>Created</span><strong>${nowLabel(task.created_at)}</strong></div>
            ${task.completed_at ? `<div><span>Completed</span><strong>${nowLabel(task.completed_at)}</strong></div>` : ""}
            <div><span>ID</span><strong>${task.id}</strong></div>
          </div>
          ${task.current_step ? `
            <div class="detail-description">
              <span>Current Step</span>
              <p>${escapeHtml(task.current_step)}</p>
            </div>
          ` : ""}
          ${task.description ? `
            <div class="detail-description">
              <span>Description</span>
              <p>${escapeHtml(task.description)}</p>
            </div>
          ` : ""}
          ${task.action_log ? `
            <div class="detail-description">
              <span>Action Log</span>
              <p>${escapeHtml(task.action_log).replace(/\n/g, "<br>")}</p>
            </div>
          ` : ""}
          ${task.type === "user_task" && ["active", "delegated", "in_progress"].includes(task.status) ? `
            <div class="modal-actions">
              <button id="completeTaskButton" data-task-id="${task.id}">Mark complete</button>
            </div>
          ` : ""}
        </section>
      </div>
    `;
  }

  function bindEvents() {
    document.getElementById("chatForm")?.addEventListener("submit", sendChat);
    document.getElementById("clearChatButton")?.addEventListener("click", clearChat);
    document.getElementById("newTaskForm")?.addEventListener("submit", createTask);
    document.getElementById("chatInput")?.addEventListener("input", autoSizeComposer);
    document.getElementById("chatInput")?.addEventListener("keydown", handleChatKeydown);
    bindTaskCardEvents();
    bindActivityEvents();
    bindModalEvents();
    initRoomCanvas();
  }

  function bindTaskCardEvents() {
    document.querySelectorAll(".task-card").forEach((card) => {
      card.addEventListener("click", () => openTask(Number(card.dataset.taskId)));
    });
  }

  function bindActivityEvents() {
    document.querySelectorAll(".activity-row[data-task-id]").forEach((row) => {
      row.addEventListener("click", () => openTask(Number(row.dataset.taskId)));
    });
  }

  function bindModalEvents() {
    document.getElementById("modalBackdrop")?.addEventListener("click", (event) => {
      if (event.target.id === "modalBackdrop") closeTask();
    });
    document.getElementById("closeTaskModal")?.addEventListener("click", closeTask);
    document.getElementById("completeTaskButton")?.addEventListener("click", (event) => {
      completeTask(Number(event.currentTarget.dataset.taskId));
    });
  }

  function autoSizeComposer(event) {
    const textarea = event.currentTarget;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 118)}px`;
  }

  function handleChatKeydown(event) {
    if (event.key !== "Enter" || event.shiftKey) return;
    const input = event.currentTarget;
    if (!input.value.trim()) return;
    event.preventDefault();
    document.getElementById("chatForm")?.requestSubmit();
  }

  function login() {
    const input = document.getElementById("tokenInput");
    state.token = (input?.value || "").trim();
    localStorage.setItem("am_token", state.token);
    hydrate().catch(handleError);
  }

  async function clearChat() {
    await api("/api/chat/history", { method: "DELETE" });
    state.chat = [];
    render({ scrollChatBottom: true });
  }

  async function sendChat(event) {
    event.preventDefault();
    const input = document.getElementById("chatInput");
    const content = (input?.value || "").trim();
    await submitChatText(content, { clearInput: true });
  }

  async function submitChatText(content, { clearInput = false } = {}) {
    const input = document.getElementById("chatInput");
    content = (content || "").trim();
    if (!content || state.loading) return;
    const userMessage = { role: "user", content, createdAt: new Date().toISOString() };
    state.chat.push(userMessage);
    state.loading = true;
    if (clearInput && input) {
      input.value = "";
      input.style.height = "";
    }
    appendChatMessage(userMessage, { forceBottom: true });
    showTypingMessage();
    setThinkingDom(true);

    try {
      const response = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: content }) });
      const assistantMessage = { role: "assistant", content: response.reply, createdAt: new Date().toISOString() };
      state.chat.push(assistantMessage);
      removeTypingMessage();
      appendChatMessage(assistantMessage, { forceBottom: true });
      await refreshOperationalData({ soft: false });
    } catch (error) {
      const errorMessage = { role: "assistant", content: "Request failed. Check backend logs and try again.", createdAt: new Date().toISOString() };
      state.chat.push(errorMessage);
      removeTypingMessage();
      appendChatMessage(errorMessage, { forceBottom: true });
      console.error(error);
    } finally {
      state.loading = false;
      removeTypingMessage();
      setThinkingDom(false);
    }
  }

  async function createTask(event) {
    event.preventDefault();
    const input = document.getElementById("newTaskInput");
    const title = (input?.value || "").trim();
    if (!title || state.taskCreating) return;

    state.taskCreating = true;
    const button = document.getElementById("newTaskButton");
    if (button) {
      button.disabled = true;
      button.textContent = "...";
    }
    try {
      await api("/api/tasks", { method: "POST", body: JSON.stringify({ title }) });
      if (input) input.value = "";
      await refreshOperationalData({ soft: false });
      updateOperationalDom();
    } catch (error) {
      console.error("Task creation failed", error);
      const errorMessage = {
        role: "assistant",
        content: "Task creation failed. The backend is reachable, but the task endpoint returned an error.",
        createdAt: new Date().toISOString(),
      };
      state.chat.push(errorMessage);
      appendChatMessage(errorMessage);
    } finally {
      state.taskCreating = false;
      const nextButton = document.getElementById("newTaskButton");
      if (nextButton) {
        nextButton.disabled = false;
        nextButton.textContent = "New";
      }
    }
  }

  async function openTask(taskId) {
    const pools = [state.tasks.active, state.tasks.delegated, state.tasks.in_progress, state.tasks.pending, state.tasks.completed, state.tasks.archived];
    const local = pools.flat().find((task) => task.id === taskId);
    state.selectedTask = local || await api(`/api/tasks/${taskId}`);
    showTaskModal();
  }

  function closeTask() {
    state.selectedTask = null;
    document.getElementById("modalBackdrop")?.remove();
  }

  async function completeTask(taskId) {
    await api(`/api/tasks/${taskId}/done`, { method: "POST" });
    state.selectedTask = null;
    await refreshOperationalData({ soft: false });
    document.getElementById("modalBackdrop")?.remove();
    updateOperationalDom();
  }

  function showTaskModal() {
    document.getElementById("modalBackdrop")?.remove();
    if (!state.selectedTask) return;
    document.querySelector(".os-shell")?.insertAdjacentHTML("beforeend", renderTaskModal(state.selectedTask));
    bindModalEvents();
  }

  function handleError(error) {
    console.error("AI Manager UI failed", error);
    if (String(error.message) === "auth") {
      renderLogin();
      return;
    }
    app.innerHTML = `
      <main class="login-shell">
        <section class="login-card error-card">
          <h1>Workspace failed to start</h1>
          <pre>${escapeHtml(error.stack || error.message || String(error))}</pre>
          <button id="resetButton">Reset token</button>
        </section>
      </main>
    `;
    document.getElementById("resetButton").addEventListener("click", () => {
      localStorage.removeItem("am_token");
      state.token = "";
      render();
    });
  }

  window.addEventListener("error", (event) => handleError(event.error || event.message));
  window.addEventListener("unhandledrejection", (event) => handleError(event.reason));

  disableLegacyServiceWorker();
  hydrate().catch(handleError);
  setInterval(() => refreshOperationalData().catch(console.warn), 5000);
})();
