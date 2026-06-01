// =====================================================
// STATE
// =====================================================

const state = {
  ws: null,
  reconnectTimer: null,
  agents: {},
  selectedAgents: new Set(),
  chart: null
};

// =====================================================
// INIT
// =====================================================

window.addEventListener("load", () => {
  initChart();
  connectWS();
});

// =====================================================
// WEBSOCKET
// =====================================================

function connectWS() {
  setStatus("Connecting...", "yellow");

  state.ws = new WebSocket("ws://localhost:8000/ws/frontend");

  state.ws.onopen = () => setStatus("Connected", "green");

  state.ws.onclose = () => {
    setStatus("Disconnected", "red");
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(connectWS, 2000);
  };

  state.ws.onerror = () => setStatus("Error", "red");

  state.ws.onmessage = (e) => {
    let msg;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;
    }
    routeMessage(msg);
  };
}

// =====================================================
// SAFE ROUTER (NO MORE CRASHES)
// =====================================================

const handlers = {
  init: handleInit,
  agents: handleAgents,
  metrics: handleMetrics,
  system_info: handleSystemInfo,
  processes: handleProcesses,
  history: handleHistory,
  process_history: handleProcessHistory,
  logs: handleLogs,
  thresholds: handleThresholds,
  agent_status: handleAgentStatus
};


function handleAgentStatus(msg) {

  const id = msg.agent_id;

  if (!id) return;

  // =========================
  // OFFLINE
  // =========================
  if (msg.status === "offline") {

    delete state.agents[id];

    // close panel if active agent removed
    if (state.activeAgent === id) {
      closeAgentPanel();
    }

    showToast(`${id} disconnected`, "error");

  } else {

    // =========================
    // ONLINE
    // =========================
    if (!state.agents[id]) {
      state.agents[id] = createAgent(id);
    }

    state.agents[id].status = "online";

    showToast(`${id} connected`, "success");
  }

  // refresh UI
  renderAgents();

  updateAgentSelect(state.agents);
}

function routeMessage(msg) {
  const handler = handlers[msg.type];

  if (!handler) {
    console.warn("Unhandled message type:", msg.type);
    return;
  }

  handler(msg);
}

// =====================================================
// INIT
// =====================================================

function handleInit(msg) {
  if (msg.agents) handleAgents(msg);

  if (msg.thresholds) applyThresholds(msg.thresholds);

  state.agents = {};

  for (const id in msg.agents || {}) {
    state.agents[id] = createAgent(id, msg.agents[id]);
  }

  renderAgents();
}

// =====================================================
// AGENTS
// =====================================================

function handleAgents(msg) {
  updateAgentSelect(msg.agents || {});
}

function createAgent(id, data = {}) {
  return {
    id,
    ip: data.ip || "-",
    cpu: 0,
    ram: 0,
    disk: 0,
    net: 0
  };
}

function updateAgentSelect(agents) {
  const select = document.getElementById("agentSelect");
  const current = select.value;

  select.innerHTML = "";

  Object.entries(agents).forEach(([id, data]) => {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = `${id} (${data.status || "unknown"})`;
    select.appendChild(opt);
  });

  if (current && agents[current]) {
  select.value = current;
}
}

// =====================================================
// METRICS
// =====================================================

function handleMetrics(msg) {
  const d = msg.data || {};

  updateGlobalMetrics(d);
  updateAgentMetrics(msg.agent_id, d);

  if (msg.alert) handleAlert(msg);
}

function updateGlobalMetrics(d) {
  setText("cpuVal", `${(d.cpu ?? 0).toFixed(1)}%`);
  setText("ramVal", `${(d.ram?.percent ?? 0).toFixed(1)}%`);
  setText("diskVal", `${(d.disk?.percent ?? 0).toFixed(1)}%`);

  setText("netSent", formatBytes(d.net?.sent ?? 0));
  setText("netRecv", formatBytes(d.net?.recv ?? 0));
 

  updateChart(
    d.cpu ?? 0,
    d.ram?.percent ?? 0,
    d.disk?.percent ?? 0
  );
}

function updateAgentMetrics(id, d) {
  if (!id) return;

  if (!state.agents[id]) {
    state.agents[id] = createAgent(id);
  }

  const agent = state.agents[id];

  agent.cpu = d.cpu ?? 0;
  agent.ram = d.ram?.percent ?? 0;
  agent.disk = d.disk?.percent ?? 0;
  agent.net =
    (d.net?.sent ?? 0) +
    (d.net?.recv ?? 0);
  agent.ip = "123.654.88.96"

  // =========================
  // MAIN AGENT TABLE
  // =========================

  renderAgents();

  // =========================
  // LIVE PANEL UPDATE
  // =========================

  if (state.activeAgent === id) {

    const cpuEl = document.getElementById("panelCpu");
    const ramEl = document.getElementById("panelRam");
    const diskEl = document.getElementById("panelDisk");
    const netEl = document.getElementById("panelNet");

    if (cpuEl)
      cpuEl.innerText = `${agent.cpu.toFixed(1)}%`;

    if (ramEl)
      ramEl.innerText = `${agent.ram.toFixed(1)}%`;

    if (diskEl)
      diskEl.innerText = `${agent.disk.toFixed(1)}%`;

    if (netEl)
      netEl.innerText = formatBytes(agent.net);

    // OPTIONAL:
    // update panel header label too
    const label =
      document.getElementById("panelAgentLabel");

    if (label) {
      label.innerText =
        `${agent.id} • ${agent.ip}`;
    }
  }
}

// =====================================================
// SYSTEM INFO
// =====================================================

function handleSystemInfo(msg) {

  const el = document.getElementById("systemTable");

  el.innerHTML = Object.entries(msg.data || {})
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join("");
}

// =====================================================
// PROCESSES
// =====================================================

function handleProcesses(msg) {
  const el = document.getElementById("panelProcessTable"); // Updated to match your Agent Panel ID
  const list = msg.data?.processes || [];

  if (!el) return;

  el.innerHTML = list.map(p => {
    // Determine color intensity based on load
    const cpuColor = p.cpu > 50 ? "text-red-400" : p.cpu > 20 ? "text-yellow-400" : "text-gray-400";
    const memColor = p.mem > 50 ? "text-blue-400" : "text-gray-400";

    return `
      <tr class="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors">
        <td class="py-2 px-2 text-gray-500 font-mono text-[10px]">${p.pid ?? "-"}</td>
        <td class="py-2 px-2 text-white font-medium text-xs truncate max-w-[150px]">${p.name ?? "-"}</td>
        <td class="py-2 px-2 text-right font-mono text-xs ${cpuColor}">${(p.cpu ?? 0).toFixed(1)}%</td>
        <td class="py-2 px-2 text-right font-mono text-xs ${memColor}">${(p.mem ?? 0).toFixed(1)}%</td>
      </tr>
    `;
  }).join("");
}

// =====================================================
// HISTORY
// =====================================================

function handleHistory(msg) {

  const rows = (msg.data || []).map(h => `
    <tr>
      <td>${new Date(h.timestamp * 1000)
        .toLocaleTimeString()}</td>

      <td>${h.cpu}</td>
      <td>${h.ram}</td>
      <td>${h.disk}</td>
    </tr>
  `).join("");

  // MAIN TABLE
  const main = document.getElementById("historyTable");

  if (main)
    main.innerHTML = rows;

  // PANEL TABLE
  if (state.activeAgent === msg.agent_id) {

    const panel =
      document.getElementById("panelHistoryTable");

    if (panel)
      panel.innerHTML = rows;
  }
}

// =====================================================
// PROCESS HISTORY (FIXED MISSING ERROR)
// =====================================================

function handleProcessHistory(msg) {

  const block = msg.data || [];

  // flatten all processes from all snapshots
  const allProcesses = block.flatMap(p =>
    (p.processes || []).map(proc => ({
      ...proc,
      snapshotTime: p.timestamp
    }))
  );

  const rows = allProcesses.map(proc => `
    <tr>

      <td>
        ${new Date(proc.snapshotTime * 1000)
          .toLocaleTimeString()}
      </td>

      <td>${proc.pid}</td>

      <td>${proc.name}</td>

      <td>${proc.status}</td>

      <td>${proc.cpu_percent}%</td>

      <td>${proc.memory_percent.toFixed(2)}%</td>

    </tr>
  `).join("");

  const main = document.getElementById("processHistoryTable");

  if (main) {
    main.innerHTML = rows;
  }

  if (state.activeAgent === msg.agent_id) {

    const panel = document.getElementById("panelProcessHistoryTable");

    if (panel) {
      panel.innerHTML = rows;
    }
  }
}

// =====================================================
// LOGS
// =====================================================
function handleLogs(msg) {

  // -----------------------------
  // TIME FORMAT (journald µs → JS time)
  // -----------------------------
  const formatTime = (ts) => {
    if (!ts) return "--:--:--";

    const ms = Number(ts) / 1000;

    return new Date(ms).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    });
  };

  // -----------------------------
  // LEVEL MAPPING
  // -----------------------------
  const levelMap = {
    0: { label: "EMERG", color: "text-red-500", bg: "bg-red-900/20" },
    1: { label: "ALERT", color: "text-red-500", bg: "bg-red-900/20" },
    2: { label: "CRIT", color: "text-red-400", bg: "bg-red-900/10" },
    3: { label: "ERROR", color: "text-red-400", bg: "bg-red-900/10" },
    4: { label: "WARN", color: "text-yellow-400", bg: "bg-yellow-900/10" },
    5: { label: "NOTICE", color: "text-blue-300", bg: "bg-blue-900/10" },
    6: { label: "INFO", color: "text-green-400", bg: "" },
    7: { label: "DEBUG", color: "text-gray-400", bg: "" }
  };

  // -----------------------------
  // RENDER LOG
  // -----------------------------
  const renderLogEntry = (l) => {

    const lvl = levelMap[l.level] || {
      label: String(l.level),
      color: "text-gray-400",
      bg: ""
    };

    const time = formatTime(l.timestamp);

    return `
      <div class="grid grid-cols-[90px_70px_140px_1fr] gap-2 px-2 py-0.5 font-mono text-[11px] hover:bg-gray-800/50 ${lvl.bg}">

        <span class="text-gray-500">${time}</span>

        <span class="font-bold ${lvl.color}">[${lvl.label}]</span>

        <span class="text-gray-500 truncate">${l.service || "-"}</span>

        <span class="text-gray-300 break-all">${l.message || ""}</span>

      </div>
    `;
  };

  // -----------------------------
  // HEADER (STATIC INSIDE FUNCTION)
  // -----------------------------
  const header = `
    <div class="grid grid-cols-[90px_70px_140px_1fr] gap-2 px-2 py-1 font-mono text-[11px] text-gray-500 bg-gray-900 border-b border-gray-700">
      <span>TIME</span>
      <span>LEVEL</span>
      <span>SERVICE</span>
      <span>MESSAGE</span>
    </div>
  `;

  // -----------------------------
  // BUILD LOGS
  // -----------------------------
  const logEntries = (msg.data || []).map(renderLogEntry).join("");

  // -----------------------------
  // RENDER MAIN BOX (HEADER + LOGS)
  // -----------------------------
  const el = document.getElementById("logBox");
  if (el) {
    el.innerHTML = header + logEntries;
  }

  // -----------------------------
  // PANEL
  // -----------------------------
  if (state.activeAgent === msg.agent_id) {
    const panel = document.getElementById("panelLogs");
    if (panel) {
      panel.innerHTML = header + logEntries;
    }
  }
}
// =====================================================
// THRESHOLDS
// =====================================================

function handleThresholds(msg) {
  applyThresholds(msg.data);
}

function applyThresholds(t) {
  if (!t) return;

  ["cpu", "ram", "disk", "net"].forEach(k => {
    const el = document.getElementById(k);
    const out = document.getElementById(k + "Out");

    if (el) el.value = t[k];
    if (out) out.innerText = t[k];
  });
}

// =====================================================
// CHART
// =====================================================

function initChart() {
  const ctx = document.getElementById("chart").getContext("2d");
  
  state.chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "CPU", data: [], borderColor: "#f87171", backgroundColor: "rgba(248, 113, 113, 0.1)", fill: true, tension: 0.4, pointRadius: 0 },
        { label: "RAM", data: [], borderColor: "#60a5fa", backgroundColor: "rgba(96, 165, 250, 0.1)", fill: true, tension: 0.4, pointRadius: 0 },
        { label: "DISK", data: [], borderColor: "#4ade80", backgroundColor: "rgba(74, 222, 128, 0.1)", fill: true, tension: 0.4, pointRadius: 0 }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { labels: { color: "#9ca3af", font: { size: 12 } } }
      },
      scales: {
        y: { 
          min: 0, max: 100,
          grid: { color: "#1f2937" },
          ticks: { color: "#9ca3af" }
        },
        x: { 
          grid: { display: false },
          ticks: { color: "#9ca3af" }
        }
      }
    }
  });
}

function updateChart(cpu, ram, disk) {
  const c = state.chart;

  c.data.labels.push(new Date().toLocaleTimeString());
  c.data.datasets[0].data.push(cpu);
  c.data.datasets[1].data.push(ram);
  c.data.datasets[2].data.push(disk);

  if (c.data.labels.length > 20) {
    c.data.labels.shift();
    c.data.datasets.forEach(d => d.data.shift());
  }

  c.update();
}

// =====================================================
// UI HELPERS
// =====================================================

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.innerText = val;
}

function setStatus(text, color) {
  const el = document.getElementById("status");
  el.innerText = text;
  el.className = `font-bold text-${color}-400`;
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";

  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return (bytes / Math.pow(k, i)).toFixed(1) + " " + sizes[i];
}

// =====================================================
// SEND ACTIONS
// =====================================================

function send(action) {
  const ws = state.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  const agent = document.getElementById("agentSelect")?.value;
  if (!agent) return;

  ws.send(JSON.stringify({ action, agent_id: agent }));
}

// =====================================================
// AGENT TABLE
// =====================================================



// =====================================================
// TOGGLES
// =====================================================

function toggleAgent(id) {
  if (state.selectedAgents.has(id)) {
    state.selectedAgents.delete(id);
  } else {
    state.selectedAgents.add(id);
  }
}


// =====================================================
// ALEARTS
// =====================================================



function handleAlert(msg) {
  const reasons = msg.reasons || [];

  const type =
    reasons.includes("cpu_critical") ? "warning" :
    reasons.includes("network_critical") ? "error" :
    "info";

  showToast(reasons.join(", ") || "Alert", type, 5000);
}


function setThresholds() {
  const ws = state.ws;

  if (!ws || ws.readyState !== WebSocket.OPEN) {
    alert("WebSocket not connected");
    return;
  }

  const cpuVal = Number(document.getElementById("cpu").value);
  const ramVal = Number(document.getElementById("ram").value);
  const diskVal = Number(document.getElementById("disk").value);


  ws.send(JSON.stringify({
    action: "set_thresholds",
    data: {
      cpu: cpuVal,
      ram: ramVal,
      disk: diskVal,
      net: 10000
    }
  }));
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    
    // Theme mapping
    const themes = {
        success: { border: 'border-green-500', icon: '✔', text: 'text-green-400' },
        error:   { border: 'border-red-500', icon: '✕', text: 'text-red-400' },
        info:    { border: 'border-cyan-500', icon: 'i', text: 'text-cyan-400' }
    };
    
    const theme = themes[type] || themes.info;

    toast.className = `w-80 bg-gray-900/90 backdrop-blur-md border border-gray-700 ${theme.border} border-l-4 rounded-r-lg shadow-2xl pointer-events-auto transform transition-all duration-300 ease-out translate-x-full opacity-0 overflow-hidden`;
    
    toast.innerHTML = `
        <div class="flex items-center p-4">
            <div class="w-6 h-6 flex items-center justify-center rounded-full bg-gray-800 ${theme.text} font-bold text-xs mr-3">${theme.icon}</div>
            <div class="text-sm font-medium text-white">${message}</div>
        </div>
        <div class="h-1 bg-gray-800 w-full">
            <div class="h-full ${theme.border.replace('border-', 'bg-')} animate-shrink"></div>
        </div>
    `;

    container.appendChild(toast);

    // Animation trigger
    requestAnimationFrame(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    });

    // Remove after 4s
    setTimeout(() => {
        toast.classList.add('opacity-0', 'translate-x-full');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
function renderAgents() {
  const el = document.getElementById("agentTable");

  el.innerHTML = Object.values(state.agents).map(a => {
    // Styling logic
    const active = state.activeAgent === a.id
        ? "bg-gray-800/50 border-l-4 border-cyan-400"
        : "hover:bg-gray-800/40 border-l-4 border-transparent";

    const cpu = a.cpu.toFixed(1);
    const ram = a.ram.toFixed(1);
    const disk = a.disk.toFixed(1);
    const net = formatBytes(a.net);

    // Color logic
    const cpuColor = a.cpu > 90 ? "text-red-400" : a.cpu > 70 ? "text-yellow-400" : "text-green-400";
    const ramColor = a.ram > 90 ? "text-red-400" : a.ram > 70 ? "text-yellow-400" : "text-blue-400";
    const diskColor = a.disk > 90 ? "text-red-400" : a.disk > 70 ? "text-yellow-400" : "text-cyan-400";

    return `
      <tr class="cursor-pointer transition-colors duration-200 border-b border-gray-800/50 ${active}"
          onclick="openAgentPanel('${a.id}')">
        <td class="p-3  font-mono tabular-nums font-semibold">${a.id}</td>
        <td class="p-3 text-right font-mono tabular-nums font-semibold">${a.ip}</td>
        <td class="p-3 text-right ${cpuColor} font-mono tabular-nums font-semibold">${cpu}%</td>
        <td class="p-3 text-right ${ramColor} font-mono tabular-nums font-semibold">${ram}%</td>
        <td class="p-3 text-right ${diskColor} font-mono tabular-nums font-semibold">${disk}%</td>
        
        <td class="p-3 text-right text-gray-400 font-mono tabular-nums">
          ${net}
        </td>
      </tr>
    `;
  }).join("");
}
function openAgentPanel(id) {
  const agent = state.agents[id];
  if (!agent) return;





  // slide panel in
  document.getElementById("agentPanel")
    .classList.remove("translate-x-full");

  // optionally mark selected
  state.activeAgent = id;
  
  
  // 🔥 auto load system info
  requestPanelSystemInfo();


}




function closeAgentPanel() {
  state.activeAgent = null;

  document.getElementById("agentPanel")
    .classList.add("translate-x-full");
}


document.getElementById("setThresholdBtn")
  .addEventListener("click", setThresholds);
  
  
function requestPanelSystemInfo() {
  const ws = state.ws;

  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!state.activeAgent) return;

  ws.send(JSON.stringify({
    action: "get_system_info",
    agent_id: state.activeAgent
  }));
}

function handleSystemInfo(msg) {
  const data = msg.data || {};

  // =========================
  // MAIN DASHBOARD TABLE
  // =========================
  const main = document.getElementById("systemTable");
  if (main) {
    main.innerHTML = Object.entries(data)
      .map(([k, v]) => `
        <tr class="border-b border-gray-800/50 hover:bg-gray-800/30 transition">
          <td class="py-3 px-4 text-xs font-bold text-gray-500 uppercase tracking-widest">${k.replace(/_/g, " ")}</td>
          <td class="py-3 px-4 text-sm font-mono text-gray-200">${v}</td>
        </tr>
      `)
      .join("");
  }

  // =========================
  // PANEL TABLE
  // =========================
  if (state.activeAgent === msg.agent_id) {
    const panel = document.getElementById("panelSystemInfoTable");
    if (panel) {
      panel.innerHTML = Object.entries(data)
        .map(([k, v]) => `
          <tr class="border-b border-gray-800/50 hover:bg-gray-900/50 transition">
            <td class="py-2 pr-4 text-[10px] font-bold text-cyan-500 uppercase tracking-wider whitespace-nowrap">
              ${k.replace(/_/g, " ")}
            </td>
            <td class="py-2 text-xs font-mono text-gray-300 break-all leading-relaxed">
              ${v}
            </td>
          </tr>
        `)
        .join("");
    }
  }
} 
state.activeAgent = null;

function openAgentPanel(id) {
  const a = state.agents[id];
  if (!a) return;

  state.activeAgent = id;

  document.getElementById("panelAgentLabel")
    .innerText = `${a.id} • ${a.ip}`;

  document.getElementById("panelCpu").innerText =
    `${a.cpu}%`;

  document.getElementById("panelRam").innerText =
    `${a.ram}%`;

  document.getElementById("panelDisk").innerText =
    `${a.disk}%`;

  document.getElementById("panelNet").innerText =
    formatBytes(a.net);

  document.getElementById("agentPanel")
    .classList.remove("translate-x-full");

  requestPanelSystemInfo();
    
}

function closeAgentPanel() {
  state.activeAgent = null;

  document.getElementById("agentPanel")
    .classList.add("translate-x-full");
}


function switchAgentTab(tab, btn) {

  // hide all tabs
  document.querySelectorAll(".agent-tab-content")
    .forEach(el => el.classList.add("hidden"));

  // remove active state
  document.querySelectorAll(".agent-tab")
    .forEach(el => el.classList.remove("active-tab"));

  // show selected tab
  document.getElementById(`tab-${tab}`)
    .classList.remove("hidden");

  // activate clicked button
  btn.classList.add("active-tab");
}
  
  
  function requestPanelSystemInfo() {
  const ws = state.ws;

  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!state.activeAgent) return;

  ws.send(JSON.stringify({
    action: "get_system_info",
    agent_id: state.activeAgent
  }));
}

function update_panal_metrics(a){
  document.getElementById("panelAgentLabel")
    .innerText = `${a.id} • ${a.ip}`;

  document.getElementById("panelCpu").innerText =
    `${a.cpu}%`;

  document.getElementById("panelRam").innerText =
    `${a.ram}%`;

  document.getElementById("panelDisk").innerText =
    `${a.disk}%`;

  document.getElementById("panelNet").innerText =
    formatBytes(a.net);

}
