from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import msgpack
import time
import json
import asyncio
from collections import deque
from contextlib import asynccontextmanager

app = FastAPI()

# =========================================================
# STORAGE
# =========================================================

AGENTS = {}
AGENT_SOCKETS = {}

HISTORY = {}
PROCESS_HISTORY = {}
SYSTEM_INFO = {}
LOGS = {}

FRONTENDS = set()
FRONTEND_LAST_PING = {}

# =========================================================
# CRITICAL ONLY THRESHOLDS
# =========================================================

CRITICAL_THRESHOLDS = {
    "cpu": 90,
    "ram": 90,
    "disk": 90,

}

# =========================================================
# INIT
# =========================================================

def init_agent(agent_id):
    HISTORY.setdefault(agent_id, deque(maxlen=50))
    PROCESS_HISTORY.setdefault(agent_id, deque(maxlen=20))
    LOGS.setdefault(agent_id, deque(maxlen=50))

def ensure_agent(agent_id):
    AGENTS.setdefault(agent_id, {
        "status": "online",
        "last_seen": time.time()
    })

# =========================================================
# SAFE TYPE CONVERSION (FIX)
# =========================================================

def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except:
        return default

# =========================================================
# SAFE BROADCAST
# =========================================================

async def broadcast(msg):
    if not FRONTENDS:
        return

    results = await asyncio.gather(
        *[ws.send_json(msg) for ws in list(FRONTENDS)],
        return_exceptions=True
    )

    dead = set()
    for ws, result in zip(list(FRONTENDS), results):
        if isinstance(result, Exception):
            dead.add(ws)
        else:
            FRONTEND_LAST_PING[ws] = time.time()

    for ws in dead:
        FRONTENDS.discard(ws)
        FRONTEND_LAST_PING.pop(ws, None)

# =========================================================
# ALERT ENGINE
# =========================================================

def compute_critical_alert(cpu, ram, disk):
    reasons = []

    if cpu >= CRITICAL_THRESHOLDS["cpu"]:
        reasons.append("cpu_critical")

    if ram >= CRITICAL_THRESHOLDS["ram"]:
        reasons.append("ram_critical")

    if disk >= CRITICAL_THRESHOLDS["disk"]:
        reasons.append("disk_critical")


    return ("critical" if reasons else None), reasons

# =========================================================
# HANDLE AGENT DATA
# =========================================================

async def handle_agent(agent_id, data):

    ensure_agent(agent_id)
    AGENTS[agent_id]["last_seen"] = time.time()

    msg_type = data.get("type")

    # ---------------- METRICS ----------------
    if msg_type == "metrics":

        # FIX APPLIED HERE (ONLY CHANGE)
        cpu = safe_float(data.get("cpu", 0))
        ram = safe_float(data.get("ram", {}).get("percent", 0))
        disk = safe_float(data.get("disk", {}).get("percent", 0))

        net = data.get("net", {})
        net_sent = safe_float(net.get("sent", 0))
        net_recv = safe_float(net.get("recv", 0))
        net_total = net_sent + net_recv

        HISTORY.setdefault(agent_id, deque(maxlen=50)).append({
            "timestamp": time.time(),
            "cpu": cpu,
            "ram": ram,
            "disk": disk,
            "net": net_total
        })

        alert, reasons = compute_critical_alert(cpu, ram, disk)

        await broadcast({
            "type": "metrics",
            "agent_id": agent_id,
            "data": data,
            "alert": alert,
            "reasons": reasons,
            "thresholds": CRITICAL_THRESHOLDS
        })

    # ---------------- SYSTEM INFO ----------------
    elif msg_type == "system_info":

        SYSTEM_INFO[agent_id] = data.get("system", {})

        await broadcast({
            "type": "system_info",
            "agent_id": agent_id,
            "data": SYSTEM_INFO[agent_id]
        })

    # ---------------- PROCESSES ----------------
    elif msg_type == "processes":

        payload = {
            "timestamp": time.time(),
            "processes": data.get("processes", [])
        }

        PROCESS_HISTORY.setdefault(agent_id, deque(maxlen=20)).append(payload)

        await broadcast({
            "type": "processes",
            "agent_id": agent_id,
            "data": payload
        })

    # ---------------- LOGS ----------------
    elif msg_type == "logs":

        LOGS.setdefault(agent_id, deque(maxlen=50)).extend(data.get("logs", []))

        await broadcast({
            "type": "logs",
            "agent_id": agent_id,
            "data": list(LOGS[agent_id])
        })

# =========================================================
# AGENT SOCKET
# =========================================================

@app.websocket("/ws/agent/{agent_id}")
async def agent_ws(websocket: WebSocket, agent_id: str):

    await websocket.accept()

    old = AGENT_SOCKETS.get(agent_id)
    if old:
        try:
            await old.close()
        except:
            pass

    AGENT_SOCKETS[agent_id] = websocket

    init_agent(agent_id)
    ensure_agent(agent_id)

    print(f"[AGENT CONNECTED] {agent_id}")

    AGENTS[agent_id] = {
    "status": "online",
    "last_seen": time.time()
    }

    await broadcast({
        "type": "agent_status",
        "agent_id": agent_id,
        "status": "online",
        "timestamp": time.time(),
        "message": f"{agent_id} connected"
    })

    try:
        while True:

            msg = await websocket.receive()

            if msg.get("bytes"):
                try:
                    data = msgpack.unpackb(msg["bytes"], raw=False)
                    await handle_agent(agent_id, data)
                except Exception as e:
                    print("[MSGPACK ERROR]", e)

            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                    print("[AGENT CMD]", cmd)
                except Exception as e:
                    print("[TEXT ERROR]", e)

    except WebSocketDisconnect:
        print(f"[AGENT DISCONNECTED] {agent_id}")

    finally:
        AGENTS[agent_id] = {
            "status": "offline",
            "last_seen": time.time()
        }
        AGENT_SOCKETS.pop(agent_id, None)

        await broadcast({
        "type": "agent_status",
        "agent_id": agent_id,
        "status": "offline",
        "timestamp": time.time(),
        "message": f"{agent_id} disconnected"
        })

# =========================================================
# FRONTEND SOCKET
# =========================================================

@app.websocket("/ws/frontend")
async def frontend_ws(websocket: WebSocket):

    await websocket.accept()

    FRONTENDS.add(websocket)
    FRONTEND_LAST_PING[websocket] = time.time()

    await websocket.send_json({
        "type": "init",
        "agents": AGENTS,
        "system_info": SYSTEM_INFO,
        "thresholds": CRITICAL_THRESHOLDS
    })

    try:
        while True:

            msg = await websocket.receive_text()

            try:
                data = json.loads(msg)
            except:
                await websocket.send_json({
                    "type": "error",
                    "message": "invalid_json"
                })
                continue

            action = data.get("action")
            agent_id = data.get("agent_id")

            FRONTEND_LAST_PING[websocket] = time.time()

            if action == "get_thresholds":
                await websocket.send_json({
                    "type": "thresholds",
                    "data": CRITICAL_THRESHOLDS
                })

            elif action == "set_thresholds":
                new_values = data.get("data", {})
                for k in CRITICAL_THRESHOLDS:
                    if k in new_values:
                        CRITICAL_THRESHOLDS[k] = new_values[k]

                await broadcast({
                    "type": "thresholds",
                    "data": CRITICAL_THRESHOLDS
                })

            elif action == "get_history":
                await websocket.send_json({
                    "type": "history",
                    "agent_id": agent_id,
                    "data": list(HISTORY.get(agent_id, []))
                })

            elif action == "get_process_history":
                await websocket.send_json({
                    "type": "process_history",
                    "agent_id": agent_id,
                    "data": list(PROCESS_HISTORY.get(agent_id, []))
                })

            elif action in ["get_processes", "get_system_info", "get_logs"]:

                agent = AGENT_SOCKETS.get(agent_id)

                if not agent:
                    await websocket.send_json({
                        "type": "error",
                        "message": "agent_not_connected"
                    })
                    continue

                await agent.send_text(json.dumps({"action": action}))

                await websocket.send_json({
                    "type": "ack",
                    "message": f"sent_to_agent:{action}"
                })

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": "unknown_action"
                })

    except WebSocketDisconnect:
        FRONTENDS.discard(websocket)
        FRONTEND_LAST_PING.pop(websocket, None)

# =========================================================
# CLEANUP TASK
# =========================================================

async def cleanup():
    while True:
        await asyncio.sleep(30)

        now = time.time()
        dead = [
            ws for ws, last in FRONTEND_LAST_PING.items()
            if now - last > 60
        ]

        for ws in dead:
            FRONTENDS.discard(ws)
            FRONTEND_LAST_PING.pop(ws, None)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(cleanup())
    yield
    task.cancel()

app.router.lifespan_context = lifespan
