import asyncio
import json
import logging
import msgpack
import platform
import psutil
import socket
import time
import websockets
from collections import deque

# =========================================================
# CONFIG
# =========================================================

AGENT_ID = "agent-156"
SERVER = f"ws://localhost:8000/ws/agent/{AGENT_ID}"


# =========================================================
# SYSTEM LOGS (LINUX JOURNALD)
# =========================================================

LOG_BUFFER = deque(maxlen=500)

async def journald_loop():


   # print("[JOURNALD] STARTED")

    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-f",
        "-n", "0",
        "-p", "warning",
        "-o", "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    while True:

        try:

            line = await proc.stdout.readline()

            if not line:
                continue

            decoded = line.decode(errors="ignore")

            #print("[JOURNALD RAW]", decoded)

            entry = json.loads(line.decode())

            log_entry = { "timestamp": entry.get("__REALTIME_TIMESTAMP"),
                          "level": entry.get("PRIORITY", "unknown"),
                          "service": entry.get("_SYSTEMD_UNIT") or entry.get("SYSLOG_IDENTIFIER"),
                          "pid": entry.get("_PID"), "host": entry.get("_HOSTNAME"),
                          "message": entry.get("MESSAGE") }

            

            LOG_BUFFER.append(log_entry)

          #  print("[JOURNALD PARSED]", log_entry)

        except Exception as e:
            print("[JOURNALD ERROR]", e)

        finally:
            proc.kill()
            await proc.wait()


# =========================================================
# LOGGER
# =========================================================

logger = logging.getLogger("agent")

if not logger.handlers:

    logger.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()

    stream_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s"
        )
    )

    logger.addHandler(stream_handler)


# =========================================================
# GET LOGS
# =========================================================

def get_logs():

    return list(LOG_BUFFER)














# =========================================================
# HELPERS
# =========================================================

def get_private_ip():

    try:

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        s.connect(("8.8.8.8", 80))

        ip = s.getsockname()[0]

        s.close()

        return ip

    except Exception:

        return "unknown"

# =========================================================
# SYSTEM INFO
# =========================================================

def get_system_info():

    return {
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_version": platform.version(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "cpu_cores": psutil.cpu_count(logical=True),
        "boot_time": psutil.boot_time(),
        "private_ip": get_private_ip(),
    }

# =========================================================
# METRICS
# =========================================================

psutil.cpu_percent(interval=None)

def get_metrics():

    return {
        "cpu": psutil.cpu_percent(interval=None),

        "ram": psutil.virtual_memory()._asdict(),

        "disk": psutil.disk_usage("/")._asdict(),

        "net": {
            "sent": psutil.net_io_counters().bytes_sent,
            "recv": psutil.net_io_counters().bytes_recv
        }
    }

# =========================================================
# PROCESSES
# =========================================================

async def get_processes():
    proc = await asyncio.create_subprocess_shell(
        "ps -eo pid,comm,%cpu,%mem,state --sort=-%cpu | head -50",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await proc.communicate()

    if stderr:
        return {"error": stderr.decode(errors="ignore")}

    lines = stdout.decode(errors="ignore").strip().split("\n")

    # first line is header
    header = lines[0]
    rows = lines[1:]

    processes = []
    for row in rows:
        parts = row.split(None, 4)
        if len(parts) < 5:
            continue

        processes.append({
            "pid": int(parts[0]),
            "name": parts[1],
            "cpu_percent": float(parts[2]),
            "memory_percent": float(parts[3]),
            "state": parts[4],
        })

    return processes 

# =========================================================
# SEND
# =========================================================

async def send(ws, payload):

    payload["agent_id"] = AGENT_ID

    await ws.send(
        msgpack.packb(payload, use_bin_type=True)
    )

# =========================================================
# TELEMETRY LOOP
# =========================================================

async def telemetry_loop(ws):

    system_sent = False

    while True:

        try:

            # -----------------------------
            # METRICS
            # -----------------------------

            await send(ws, {
                "type": "metrics",
                "timestamp": time.time(),
                **get_metrics()
            })

            # -----------------------------
            # SYSTEM INFO (ONCE)
            # -----------------------------

            if not system_sent:

                await send(ws, {
                    "type": "system_info",
                    "timestamp": time.time(),
                    "system": get_system_info()
                })

                logger.info("System info sent")

                system_sent = True

            await asyncio.sleep(2)

        except Exception as e:

            logger.error(f"Telemetry error: {e}")

            raise

# =========================================================
# COMMAND LOOP
# =========================================================

async def command_loop(ws):

    async for raw in ws:

        try:

            # -----------------------------
            # PARSE MESSAGE
            # -----------------------------

            if isinstance(raw, bytes):

                cmd = msgpack.unpackb(
                    raw,
                    raw=False
                )

            else:

                cmd = json.loads(raw)

            action = cmd.get("action")

            logger.info(f"Command received: {action}")

            # -----------------------------
            # GET PROCESSES
            # -----------------------------

            if action == "get_processes":

                await send(ws, {
                    "type": "processes",
                    "timestamp": time.time(),
                    "processes": get_processes()
                })

            # -----------------------------
            # GET LOGS
            # -----------------------------

            elif action == "get_logs":

                await send(ws, {
                    "type": "logs",
                    "timestamp": time.time(),
                    "logs": get_logs()
                })

            # -----------------------------
            # GET SYSTEM INFO
            # -----------------------------

            elif action == "get_system_info":

                await send(ws, {
                    "type": "system_info",
                    "timestamp": time.time(),
                    "system": get_system_info()
                })

            # -----------------------------
            # UNKNOWN COMMAND
            # -----------------------------

            else:

                await send(ws, {
                    "type": "error",
                    "timestamp": time.time(),
                    "message": f"unknown_action:{action}"
                })

        except Exception as e:

            logger.error(f"Command error: {e}")

            await send(ws, {
                "type": "error",
                "timestamp": time.time(),
                "message": str(e)
            })

# =========================================================
# MAIN LOOP
# =========================================================

async def agent():

    print(f"[STARTING] {AGENT_ID}")

    while True:

        try:

            logger.info(f"Connecting -> {SERVER}")

            async with websockets.connect(
                SERVER,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=10 * 1024 * 1024
            ) as ws:

                print("[CONNECTED TO SERVER]")

                logger.info("Connected")

                # -----------------------------
                # CONNECTION STATUS
                # -----------------------------

                await send(ws, {
                    "type": "status",
                    "timestamp": time.time(),
                    "status": "connected",
                    "message": f"{AGENT_ID} connected to server"
                })

                # -----------------------------
                # TASKS
                # -----------------------------

                telemetry_task = asyncio.create_task(
                    telemetry_loop(ws)
                )

                command_task = asyncio.create_task(
                    command_loop(ws)
                )

                journald_task = asyncio.create_task(journald_loop())

                done, pending = await asyncio.wait(
                    [telemetry_task, command_task,journald_task],
                    return_when=asyncio.FIRST_EXCEPTION
                )

                # -----------------------------
                # CLEANUP
                # -----------------------------

                for task in pending:

                    task.cancel()

                    try:
                        await task

                    except:
                        pass

        except Exception as e:

            print(f"[RECONNECTING] {e}")

            logger.error(f"Disconnected: {e}")

            await asyncio.sleep(5)

# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    asyncio.run(agent())


