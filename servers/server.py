from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import msgpack
import time
import json
import asyncio
from collections import deque
from contextlib import asynccontextmanager
import uuid
import aiomysql

app = FastAPI()

# =========================================================
# STORAGE
# =========================================================


MYSQL_POOL = None

async def init_mysql_pool():
    global MYSQL_POOL
    MYSQL_POOL = await aiomysql.create_pool(
        host="localhost",
        port=3306,
        user="root",
        password="root123",
        db="agent_monitoring",
        autocommit=True,
        minsize=1,
        maxsize=10
    )

async def db_execute(query, params=None):
    if not MYSQL_POOL:
        return

    async with MYSQL_POOL.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params or ())
#========================================================
AGENTS = {}
AGENT_SOCKETS = {}  # agent_id -> websocket

HISTORY = {}
PROCESS_HISTORY = {}
SYSTEM_INFO = {}
LOGS = {}

FRONTENDS = {}  # ws_id -> websocket
FRONTEND_LAST_PING = {}  # ws_id -> timestamp

# =========================================================
# THRESHOLDS
# =========================================================

CRITICAL_THRESHOLDS = {
    "cpu": 90,
    "ram": 90,
    "disk": 90,
}

# =========================================================
# INIT HELPERS
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
# SAFE FLOAT
# =========================================================

def safe_float(v, default=0.0):
    try:
        return float(v)
    except:
        return default

# =========================================================
# SAFE SEND
# =========================================================

async def safe_send(ws, msg):
    try:
        await ws.send_json(msg)
        return True
    except:
        return False

# =========================================================
# BROADCAST (NON-BLOCKING)
# =========================================================

async def broadcast(msg):
    if not FRONTENDS:
        return

    dead = []

    results = await asyncio.gather(
        *[safe_send(ws, msg) for ws in FRONTENDS.values()],
        return_exceptions=True
    )

    for (ws_id, _), result in zip(list(FRONTENDS.items()), results):
        if result is not True:
            dead.append(ws_id)
        else:
            FRONTEND_LAST_PING[ws_id] = time.time()

    for ws_id in dead:
        FRONTENDS.pop(ws_id, None)
        FRONTEND_LAST_PING.pop(ws_id, None)

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
# HISTORY FETCH
# =========================================================
async def fetch_process_history(agent_id, limit=20):
    if not MYSQL_POOL:
        return list(PROCESS_HISTORY.get(agent_id, []))

    async with MYSQL_POOL.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("""
                SELECT timestamp, data
                FROM process_history
                WHERE agent_id=%s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (agent_id, limit))
            return await cur.fetchall()

async def fetch_metrics_history(agent_id, limit=50):
    if not MYSQL_POOL:
        return list(HISTORY.get(agent_id, []))

    async with MYSQL_POOL.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("""
                SELECT timestamp, cpu, ram, disk, net
                FROM metrics_history
                WHERE agent_id=%s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (agent_id, limit))
            return await cur.fetchall()



# =========================================================
# HANDLE AGENT DATA
# =========================================================

async def handle_agent(agent_id, data):
    ensure_agent(agent_id)
    AGENTS[agent_id]["last_seen"] = time.time()

    msg_type = data.get("type")

    # ---------------- METRICS ----------------
    if msg_type == "metrics":
        cpu = safe_float(data.get("cpu"))
        ram = safe_float(data.get("ram", {}).get("percent"))
        disk = safe_float(data.get("disk", {}).get("percent"))

        net = data.get("net", {})
        net_total = safe_float(net.get("sent")) + safe_float(net.get("recv"))

        HISTORY[agent_id].append({
            "timestamp": time.time(),
            "cpu": cpu,
            "ram": ram,
            "disk": disk,
            "net": net_total
        })

        alert, reasons = compute_critical_alert(cpu, ram, disk)

        if alert:
            asyncio.create_task(db_execute("""
                INSERT INTO agent_events (agent_id, timestamp, event_type, cpu, ram, disk)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                agent_id,
                time.time(),
                ",".join(reasons),
                cpu,
                ram,
                disk
            )))
            # STORE METRICS IN DB (non-blocking)
            asyncio.create_task(db_execute("""
                INSERT INTO metrics_history (agent_id, timestamp, cpu, ram, disk, net)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                agent_id,
                time.time(),
                cpu,
                ram,
                disk,
                net_total
            )))

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
        asyncio.create_task(db_execute("""
            UPDATE agents
            SET sys_info=%s
            WHERE agent_id=%s
            ORDER BY Aid DESC
            LIMIT 1
        """, (
            json.dumps(
                SYSTEM_INFO[agent_id],
                ensure_ascii=False
            ),
            agent_id
        )))
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

        PROCESS_HISTORY[agent_id].append(payload)

        asyncio.create_task(db_execute("""
            INSERT INTO process_history (agent_id, timestamp, data)
            VALUES (%s, %s, %s)
        """, (
            agent_id,
            time.time(),
            json.dumps(payload)
        )))

        await broadcast({
            "type": "processes",
            "agent_id": agent_id,
            "data": payload
        })

    # ---------------- LOGS ----------------
    elif msg_type == "logs":
        LOGS[agent_id].extend(data.get("logs", []))

        asyncio.create_task(db_execute("""
            INSERT INTO log_history (agent_id, timestamp, data)
            VALUES (%s, %s, %s)
        """, (
            agent_id,
            time.time(),
            json.dumps(data.get("logs", []))
        )))

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

    # replace old connection
    old = AGENT_SOCKETS.get(agent_id)
    if old:
        try:
            await old.close()
        except:
            pass

    AGENT_SOCKETS[agent_id] = websocket

    init_agent(agent_id)
    ensure_agent(agent_id)

    AGENTS[agent_id] = {
        "status": "online",
        "last_seen": time.time()
    }

    asyncio.create_task(db_execute("""
        INSERT INTO agents (
            agent_id,
            status,
            last_seen
        )
        VALUES (%s, %s, %s)
    """, (
        agent_id,
        "online",
        time.time()
    )))

    await broadcast({
        "type": "agent_status",
        "agent_id": agent_id,
        "status": "online",
        "timestamp": time.time()
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
                    action = cmd.get("action")

                    # forward command from frontend if needed
                    if action:
                        print("[AGENT CMD]", action)

                except Exception as e:
                    print("[TEXT ERROR]", e)

    except WebSocketDisconnect:
        print(f"[AGENT DISCONNECTED] {agent_id}")

    finally:
        AGENTS[agent_id] = {
            "status": "offline",
            "last_seen": time.time()
        }
        asyncio.create_task(db_execute("""
            UPDATE agents
            SET
                status=%s,
                last_seen=%s
            WHERE agent_id=%s
            ORDER BY Aid DESC
            LIMIT 1
        """, (
            "offline",
            time.time(),
            agent_id
        )))

        AGENT_SOCKETS.pop(agent_id, None)

        await broadcast({
            "type": "agent_status",
            "agent_id": agent_id,
            "status": "offline",
            "timestamp": time.time()
        })
    

# =========================================================
# FRONTEND SOCKET
# =========================================================

@app.websocket("/ws/frontend")
async def frontend_ws(websocket: WebSocket):
    await websocket.accept()

    ws_id = str(uuid.uuid4())
    FRONTENDS[ws_id] = websocket
    FRONTEND_LAST_PING[ws_id] = time.time()

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

            FRONTEND_LAST_PING[ws_id] = time.time()

            # ---------------- THRESHOLDS ----------------
            if action == "get_thresholds":
                await websocket.send_json({
                    "type": "thresholds",
                    "data": CRITICAL_THRESHOLDS
                })

            elif action == "set_thresholds":
                for k in CRITICAL_THRESHOLDS:
                    if k in data.get("data", {}):
                        CRITICAL_THRESHOLDS[k] = data["data"][k]

                await broadcast({
                    "type": "thresholds",
                    "data": CRITICAL_THRESHOLDS
                })

            # ---------------- HISTORY ----------------
            elif action == "get_history":

                data = await fetch_metrics_history(agent_id)
                await websocket.send_json({
                    "type": "history",
                    "agent_id": agent_id,
                    "data": data
                })

            elif action == "get_process_history":
                data = await fetch_process_history(agent_id)
                await websocket.send_json({
                    "type": "process_history",
                    "agent_id": agent_id,
                    "data": data
                })

            # ---------------- COMMANDS TO AGENT ----------------
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
        FRONTENDS.pop(ws_id, None)
        FRONTEND_LAST_PING.pop(ws_id, None)

# =========================================================
# CLEANUP TASK
# =========================================================

async def cleanup():
    while True:
        await asyncio.sleep(30)

        now = time.time()
        dead = []

        for ws_id, last in FRONTEND_LAST_PING.items():
            ws = FRONTENDS.get(ws_id)

            if not ws:
                dead.append(ws_id)
                continue

            if now - last > 60:
                dead.append(ws_id)

        for ws_id in dead:
            FRONTENDS.pop(ws_id, None)
            FRONTEND_LAST_PING.pop(ws_id, None)

# =========================================================
# LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_mysql_pool()
    task = asyncio.create_task(cleanup())
    yield
    task.cancel()

app.router.lifespan_context = lifespan
