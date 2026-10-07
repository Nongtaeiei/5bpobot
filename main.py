# language: Python, file: main.py, target: Windows 11 / Linux, discord.py >= 2.0, aiohttp >= 3.8
import os
import sys
import io
import json
import base64
import random
import asyncio
import re
import urllib.parse
import secrets
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
import member_db
import discord_puller
import tunnel_manager
import web_server

try:
    import win32crypt
except ImportError:
    win32crypt = None

try:
    from Cryptodome.Cipher import AES
except ImportError:
    try:
        from Crypto.Cipher import AES
    except ImportError:
        AES = None

def enforce_single_instance():
    """ป้องกันไม่ให้บอทรันซ้อนกัน 2 ตัวเด็ดขาด ถ้ามี main.py ตัวอื่นให้ปิดทิ้งทันที"""
    if sys.platform != "win32":
        return
    import subprocess
    curr_pid = os.getpid()
    try:
        out = subprocess.check_output('wmic process where "name=\'python.exe\'" get processid,commandline', shell=True, text=True)
        for line in out.splitlines():
            if "main.py" in line:
                parts = line.strip().split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        if pid != curr_pid:
                            print(f"⚠️ [SINGLE INSTANCE] ปิด bot ตัวเก่าที่รันค้างอยู่ (PID {pid})...", flush=True)
                            subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except ValueError:
                        pass
    except Exception:
        pass

enforce_single_instance()

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DEFAULT_IMAGE_URL = os.getenv(
    "EMBED_IMAGE_URL",
    "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExYmlzMHR0YXVuaTY5czdzODV4eTUxNWhkNG5ucWp1b3E5bWgxaDVmbiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/mlCb3AjEE6N4Q/giphy.gif"
)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# บังคับเลือกและจัดลำดับ: 🖥️ ภารกิจบนเดสก์ท็อป มาก่อนเสมอ!
DESKTOP_KEYS = ("PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2")
TASK_PRIORITY = [
    "PLAY_ON_DESKTOP",
    "PLAY_ON_DESKTOP_V2",
    "STREAM_ON_DESKTOP",
    "WATCH_VIDEO",
    "PLAY_ACTIVITY",
    "WATCH_VIDEO_ON_MOBILE",
]
SKIP_TASKS = {
    "ACHIEVEMENT_IN_ACTIVITY",
    "ACHIEVEMENT_IN_GAME",
    "PLAY_ON_XBOX",
    "PLAY_ON_PLAYSTATION",
    "progress"
}

API_BASE = "https://discord.com/api/v9"
QUEST_TOKENS_FILE = Path(__file__).parent / "user_quest_tokens.json"


def is_real_machine() -> bool:
    """ตรวจจับว่าบอทรันอยู่บนเครื่องจริง (Windows Desktop มี Discord) หรืออยู่บน Host/Cloud (Render, Linux, VPS)"""
    if os.getenv("RENDER") or os.getenv("PORT") or os.getenv("DYNO") or os.getenv("RAILWAY_ENVIRONMENT"):
        return False
    if sys.platform != "win32":
        return False
    appdata = os.getenv("APPDATA") or ""
    if not appdata:
        return False
    return (Path(appdata) / "discord").exists()


def _obfuscate_token(tok: str) -> str:
    return base64.b64encode(tok[::-1].encode("utf-8")).decode("utf-8")


def _deobfuscate_token(data: str) -> str:
    try:
        raw = base64.b64decode(data.encode("utf-8")).decode("utf-8")
        return raw[::-1]
    except Exception:
        return data


def get_saved_quest_token(user_id: int) -> str | None:
    if not QUEST_TOKENS_FILE.exists():
        return None
    try:
        with open(QUEST_TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            val = data.get(str(user_id))
            return _deobfuscate_token(val) if val else None
    except Exception:
        return None


def save_quest_token(user_id: int, token: str):
    data = {}
    if QUEST_TOKENS_FILE.exists():
        try:
            with open(QUEST_TOKENS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[str(user_id)] = _obfuscate_token(token.strip().strip('"').strip("'"))
    try:
        with open(QUEST_TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[QUEST TOKEN SAVE ERROR] {e}", flush=True)


def delete_quest_token(user_id: int):
    if not QUEST_TOKENS_FILE.exists():
        return
    try:
        with open(QUEST_TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if str(user_id) in data:
            del data[str(user_id)]
            with open(QUEST_TOKENS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_tokens_from_desktop() -> list[str]:
    """สแกนหา Discord Token จาก Discord Desktop และ Browser บนเครื่องโดยอัตโนมัติ (รองรับ DPAPI)"""
    if not is_real_machine():
        return []
    tokens = []
    appdata = os.getenv("APPDATA") or ""
    localappdata = os.getenv("LOCALAPPDATA") or ""

    paths = {
        "Discord": Path(appdata) / "discord",
        "Discord Canary": Path(appdata) / "discordcanary",
        "Discord PTB": Path(appdata) / "discordptb",
        "Chrome": Path(localappdata) / "Google" / "Chrome" / "User Data" / "Default",
    }

    for name, base_path in paths.items():
        if not base_path.exists():
            continue

        key_path = base_path / "Local State" if "Chrome" not in name else base_path.parent / "Local State"
        master_key = None

        if key_path.exists() and win32crypt and AES:
            try:
                with open(key_path, "r", encoding="utf-8") as f:
                    local_state = json.load(f)
                encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])[5:]
                master_key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
            except Exception:
                pass

        storage_path = base_path / "Local Storage" / "leveldb"
        if not storage_path.exists():
            continue

        for file_path in list(storage_path.glob("*.ldb")) + list(storage_path.glob("*.log")):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                if master_key and AES:
                    for enc_token in re.findall(r"dQw4w9WgXcQ:[^\"]*", content):
                        try:
                            token_bytes = base64.b64decode(enc_token.split("dQw4w9WgXcQ:")[1])
                            iv = token_bytes[3:15]
                            payload = token_bytes[15:]
                            cipher = AES.new(master_key, AES.MODE_GCM, iv)
                            decrypted = cipher.decrypt(payload)[:-16].decode("utf-8")
                            if decrypted not in tokens:
                                tokens.append(decrypted)
                        except Exception:
                            pass

                for token in re.findall(r"[\w-]{24,26}\.[\w-]{6}\.[\w-]{27,38}", content):
                    if token not in tokens:
                        tokens.append(token)
            except Exception:
                continue

    return tokens


def extract_user_id_from_token(token: str) -> int | None:
    """ถอดรหัส User ID จากท่อนแรกของ Discord Token (Base64)"""
    try:
        clean = token.strip().strip('"').strip("'")
        part = clean.split(".")[0]
        padded = part + "=" * (-len(part) % 4)
        uid_str = base64.b64decode(padded.encode()).decode("utf-8")
        if uid_str.isdigit():
            return int(uid_str)
    except Exception:
        pass
    return None


async def find_token_for_user(user_id: int, tokens: list[str]) -> str | None:
    """จับคู่ Token ให้ตรงกับบัญชี Discord ของคนที่กดปุ่ม 100%"""
    # 1. ถอดรหัส User ID ตรงๆ จาก Token (แม่นยำ เร็วที่สุด และไม่โดนบล็อก)
    for t in tokens:
        decoded_id = extract_user_id_from_token(t)
        if decoded_id and decoded_id == user_id:
            return t

    # 2. ถ้าไม่ได้ ให้ลองยิง API ตรวจสอบด้วย Header เต็ม
    async with aiohttp.ClientSession() as session:
        for t in tokens:
            try:
                headers = get_headers(t)
                async with session.get(f"{API_BASE}/users/@me", headers=headers) as res:
                    if res.status == 200:
                        u = await res.json()
                        if int(u.get("id")) == user_id:
                            return t
            except Exception:
                continue

    return None


def make_super_properties(build_number: int = 561532) -> str:
    obj = {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": "1.0.9175",
        "os_version": "10.0.26100",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "browser_user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        ),
        "browser_version": "32.2.7",
        "client_build_number": build_number,
        "native_build_number": 59498,
        "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()


def get_headers(user_token: str) -> dict:
    clean_token = user_token.strip().strip('"').strip("'")
    return {
        "Authorization": clean_token,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        ),
        "X-Super-Properties": make_super_properties(),
        "X-Discord-Locale": "en-US",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/channels/@me",
    }


def _dict_get(d: dict, *keys):
    if not d or not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None


def extract_quest_name(quest: dict) -> str:
    cfg = quest.get("config", {})
    msgs = cfg.get("messages", {})
    name = _dict_get(msgs, "questName", "quest_name")
    if name:
        return name.strip()
    game = _dict_get(msgs, "gameTitle", "game_title")
    if game:
        return game.strip()
    app = cfg.get("application", {})
    if app.get("name"):
        return app["name"]
    return f"Quest #{quest.get('id', '?')}"


def extract_task_info(quest: dict):
    cfg = quest.get("config", {})
    tc = _dict_get(cfg, "task_config_v2", "taskConfigV2", "task_config", "taskConfig")
    if not tc or "tasks" not in tc:
        return None, 0
    tasks = tc.get("tasks", {})

    # 👉 ถ้ามี "ภารกิจบนเดสก์ท็อป" บังคับเลือกอันนี้ 100%
    for desktop_key in DESKTOP_KEYS:
        if tasks.get(desktop_key) is not None:
            return desktop_key, tasks[desktop_key].get("target", 0)

    # ถ้าไม่มีเดสก์ท็อป ค่อยเลือกประเภทอื่นตามลำดับ
    for t in TASK_PRIORITY:
        if tasks.get(t) is not None:
            target = tasks[t].get("target", 0)
            return t, target

    for k, v in tasks.items():
        if k not in SKIP_TASKS and isinstance(v, dict):
            return k, v.get("target", 0)

    return None, 0


def get_current_progress(quest: dict, task_name: str) -> float:
    us = _dict_get(quest, "userStatus", "user_status")
    if not us or not isinstance(us, dict):
        return 0.0
    prog = us.get("progress", {})
    if not prog or not isinstance(prog, dict):
        return 0.0
    val = prog.get(task_name, {}).get("value")
    if val is not None:
        return float(val)
    if "PLAY_ON_DESKTOP" in prog:
        return float(prog["PLAY_ON_DESKTOP"].get("value", 0))
    return 0.0


def create_progress_bar(done: float, total: float, length: int = 10) -> str:
    if total <= 0:
        return "▰" * length
    percent = min(1.0, max(0.0, done / total))
    filled = int(round(length * percent))
    return "▰" * filled + "▱" * (length - filled)


def get_task_label(task_name: str) -> str:
    if task_name in DESKTOP_KEYS:
        return "🖥️ ภารกิจบนเดสก์ท็อป"
    labels = {
        "STREAM_ON_DESKTOP": "📡 สตรีมบนเดสท็อป",
        "WATCH_VIDEO": "🎬 ดูวิดีโอ (Watch Video)",
        "WATCH_VIDEO_ON_MOBILE": "📱 ดูวิดีโอบนมือถือ (เควสนี้ไม่มีเดสก์ท็อป)",
        "PLAY_ACTIVITY": "🕹️ กิจกรรม Discord Activity",
    }
    return labels.get(task_name, f"⚡ {task_name}")


async def safe_edit_interaction(interaction: discord.Interaction, embed: discord.Embed):
    """Safely edit interaction response without crashing if webhook token expires."""
    try:
        await interaction.edit_original_response(embed=embed)
    except Exception as e:
        print(f"[UI] Interaction edit ignored: {e}", flush=True)


async def execute_quest_automation(interaction: discord.Interaction, user_token: str):
    headers = get_headers(user_token)

    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. ตรวจสอบ User Token
        async with session.get(f"{API_BASE}/users/@me") as res:
            if res.status != 200:
                embed_err = discord.Embed(
                    title="❌ Token ไม่ถูกต้อง",
                    description="ไม่สามารถเข้าสู่ระบบด้วย Token ที่ระบุได้ กรุณาตรวจสอบ User Token ใหม่อีกครั้ง",
                    color=discord.Color.red()
                )
                await safe_edit_interaction(interaction, embed_err)
                return
            user_data = await res.json()
            username = f"{user_data.get('username', 'User')}#{user_data.get('discriminator', '0')}"
            print(f"[AUTH] User logged in: {username}", flush=True)

        embed = discord.Embed(
            title=f"⚡ AutoQuest: {username}",
            description="🔍 กำลังดึงข้อมูลเควสทั้งหมดจาก **Quest Home** (`https://discord.com/quest-home`)...",
            color=0x5865F2,
        )
        await safe_edit_interaction(interaction, embed)

        # 2. ดึงข้อมูลเควสทั้งหมดจาก @me
        async with session.get(f"{API_BASE}/quests/@me") as res:
            if res.status != 200:
                embed_err = discord.Embed(
                    title="⚠️ เกิดข้อผิดพลาด",
                    description=f"ไม่สามารถดึงข้อมูลเควสได้ (Status code: {res.status})",
                    color=discord.Color.red()
                )
                await safe_edit_interaction(interaction, embed_err)
                return
            data = await res.json()
            raw_quests = data.get("quests", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

        # 3. กดรับเควสทุกตัวที่ยังไม่ได้กดรับ (Auto-Enroll)
        enrolled_count = 0
        enrolled_names = []
        for q in raw_quests:
            us = _dict_get(q, "userStatus", "user_status") or {}
            is_enrolled = bool(_dict_get(us, "enrolledAt", "enrolled_at"))
            is_completed = bool(_dict_get(us, "completedAt", "completed_at"))

            expires = _dict_get(q.get("config", {}), "expiresAt", "expires_at")
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    if exp_dt <= datetime.now(timezone.utc):
                        continue
                except Exception:
                    pass

            if not is_enrolled and not is_completed:
                qid = q["id"]
                qname = extract_quest_name(q)
                enroll_payload = {
                    "location": 11,
                    "is_targeted": False,
                    "metadata_sealed": None,
                }
                traffic_sealed = _dict_get(q, "traffic_metadata_sealed")
                if traffic_sealed:
                    enroll_payload["traffic_metadata_sealed"] = traffic_sealed

                async with session.post(f"{API_BASE}/quests/{qid}/enroll", json=enroll_payload) as en_res:
                    res_body = await en_res.text()
                    print(f"[ENROLL] {qname} (ID: {qid}) -> Status {en_res.status}: {res_body[:120]}", flush=True)
                    if en_res.status in (200, 201, 204):
                        enrolled_count += 1
                        enrolled_names.append(qname)
                        embed.description = (
                            f"📥 **กำลังกดรับเควสทั้งหมดใน Quest Home...**\n\n"
                            f"✅ กดรับเควสแล้ว: `{qname}`\n"
                            f"*(กำลังจัดลำดับเควสเดสก์ท็อปขึ้นก่อน...)*"
                        )
                        await safe_edit_interaction(interaction, embed)
                        await asyncio.sleep(1)

        # 4. ดึงรายการเควสใหม่อีกครั้ง
        async with session.get(f"{API_BASE}/quests/@me") as res:
            if res.status == 200:
                data = await res.json()
                raw_quests = data.get("quests", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

        # กรองเควสที่ยังไม่เสร็จ
        target_quests = []
        for q in raw_quests:
            us = _dict_get(q, "userStatus", "user_status") or {}
            completed = bool(_dict_get(us, "completedAt", "completed_at"))
            if completed:
                continue

            expires = _dict_get(q.get("config", {}), "expiresAt", "expires_at")
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    if exp_dt <= datetime.now(timezone.utc):
                        continue
                except Exception:
                    pass

            task_name, target = extract_task_info(q)
            if task_name:
                target_quests.append((q, task_name, target))

        if not target_quests:
            embed_none = discord.Embed(
                title=f"🎉 AutoQuest: {username}",
                description="ไม่มีเควสที่ค้างอยู่ หรือเควสทั้งหมดถูกเคลียร์เรียบร้อยแล้ว!",
                color=discord.Color.green()
            )
            await safe_edit_interaction(interaction, embed_none)
            return

        # 🔥 สำคัญมาก: เรียงลำดับเอา "ภารกิจบนเดสก์ท็อป" ขึ้นมาทำอันดับแรกสุด!
        target_quests.sort(key=lambda item: 0 if item[1] in DESKTOP_KEYS else 1)

        total_q = len(target_quests)
        desktop_count = sum(1 for item in target_quests if item[1] in DESKTOP_KEYS)
        print(f"[QUEUE] Found {total_q} quests to complete ({desktop_count} Desktop tasks).", flush=True)

        embed.description = (
            f"🎯 พบเควสทั้งหมด **{total_q}** เควส (เป็นเควสเดสก์ท็อป {desktop_count} เควส)\n"
            f"🚀 **จัดลำดับนำเควสเดสก์ท็อป (เช่น Endfield, NTE) ขึ้นมาทำเป็นอันดับแรกทันที!**"
        )
        await safe_edit_interaction(interaction, embed)
        await asyncio.sleep(2)

        # 5. ลูปเคลียร์เควสตามลำดับ (เดสก์ท็อปนำหน้า)
        completed_quests_list = []

        for index, (q, task_name, target_secs) in enumerate(target_quests, start=1):
            qid = q["id"]
            qname = extract_quest_name(q)
            current_done = get_current_progress(q, task_name)
            task_title = get_task_label(task_name)
            is_desktop = task_name in DESKTOP_KEYS

            print(f"[START] ({index}/{total_q}) {qname} | Task: {task_name} | Progress: {current_done}/{target_secs}", flush=True)

            if task_name in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
                speed = 7
                while current_done < target_secs:
                    bar = create_progress_bar(current_done, target_secs)
                    pct = int(min(1.0, current_done / target_secs) * 100)
                    embed.description = (
                        f"⏳ **กำลังเคลียร์เควส ({index}/{total_q})**\n\n"
                        f"**เควส:** `{qname}`\n"
                        f"**ภารกิจที่เลือก:** {task_title}\n"
                        f"**ความคืบหน้า:** `{bar}` **{pct}%** ({int(current_done)}/{target_secs} วินาที)\n\n"
                        "💡 *เควสนี้เป็นวิดีโอ ระบบกำลังจำลองการดูอัตโนมัติ*"
                    )
                    await safe_edit_interaction(interaction, embed)

                    remaining = min(speed, target_secs - current_done)
                    await asyncio.sleep(remaining)

                    new_val = current_done + speed
                    payload = {"timestamp": min(target_secs, new_val + random.random())}
                    async with session.post(f"{API_BASE}/quests/{qid}/video-progress", json=payload) as vres:
                        if vres.status == 200:
                            vdata = await vres.json()
                            if vdata.get("completed_at"):
                                current_done = target_secs
                                break
                    current_done = min(target_secs, new_val)

                await session.post(f"{API_BASE}/quests/{qid}/video-progress", json={"timestamp": target_secs})

            elif is_desktop or task_name == "STREAM_ON_DESKTOP":
                # สำหรับภารกิจเดสก์ท็อป สตรีมคีย์ call:qid:1
                stream_key = f"call:{qid}:1"
                stalled_count = 0
                while current_done < target_secs:
                    bar = create_progress_bar(current_done, target_secs)
                    pct = int(min(1.0, current_done / target_secs) * 100)
                    remaining_mins = max(1, int((target_secs - current_done) / 60))
                    embed.description = (
                        f"⏳ **กำลังเคลียร์เควส ({index}/{total_q})**\n\n"
                        f"**เควส:** `{qname}`\n"
                        f"**ภารกิจที่เลือก:** **🖥️ ภารกิจบนเดสก์ท็อป**\n"
                        f"**ความคืบหน้า:** `{bar}` **{pct}%** ({int(current_done)}/{target_secs} วินาที)\n"
                        f"**เวลาที่เหลือโดยประมาณ:** ~{remaining_mins} นาที\n\n"
                        "💡 *ระบบล็อคเลือก **🖥️ ภารกิจบนเดสก์ท็อป** และส่งสัญญาณ Heartbeat เล่นเกมให้อัตโนมัติ*"
                    )
                    await safe_edit_interaction(interaction, embed)

                    payload = {
                        "stream_key": stream_key,
                        "terminal": False,
                    }
                    async with session.post(f"{API_BASE}/quests/{qid}/heartbeat", json=payload) as hres:
                        res_json = await hres.json() if hres.status == 200 else {}
                        print(f"[BEAT] {qname} -> Status {hres.status} | Data: {str(res_json)[:100]}", flush=True)

                        if hres.status == 200:
                            prog_data = res_json.get("progress", {})
                            new_val = None
                            if prog_data and task_name in prog_data:
                                new_val = prog_data[task_name].get("value")
                            elif prog_data and "PLAY_ON_DESKTOP" in prog_data:
                                new_val = prog_data["PLAY_ON_DESKTOP"].get("value")

                            if new_val is not None:
                                current_done = float(new_val)
                            else:
                                current_done += 20  # Progress fallback

                            if res_json.get("completed_at") or current_done >= target_secs:
                                break
                        else:
                            stalled_count += 1
                            if stalled_count >= 5:
                                print(f"[WARN] Too many heartbeat failures on {qname}, skipping...", flush=True)
                                break

                    await asyncio.sleep(20)

                await session.post(
                    f"{API_BASE}/quests/{qid}/heartbeat",
                    json={"stream_key": stream_key, "terminal": True}
                )

            elif task_name == "PLAY_ACTIVITY":
                stream_key = "call:0:1"
                while current_done < target_secs:
                    bar = create_progress_bar(current_done, target_secs)
                    pct = int(min(1.0, current_done / target_secs) * 100)
                    remaining_mins = max(1, int((target_secs - current_done) / 60))
                    embed.description = (
                        f"⏳ **กำลังเคลียร์เควส ({index}/{total_q})**\n\n"
                        f"**เควส:** `{qname}`\n"
                        f"**ภารกิจที่เลือก:** {task_title}\n"
                        f"**ความคืบหน้า:** `{bar}` **{pct}%** ({int(current_done)}/{target_secs} วินาที)\n"
                        f"**เวลาที่เหลือโดยประมาณ:** ~{remaining_mins} นาที\n\n"
                        "💡 *ระบบกำลังส่ง Activity Heartbeat ให้อัตโนมัติ*"
                    )
                    await safe_edit_interaction(interaction, embed)

                    payload = {
                        "stream_key": stream_key,
                        "terminal": False,
                    }
                    async with session.post(f"{API_BASE}/quests/{qid}/heartbeat", json=payload) as hres:
                        if hres.status == 200:
                            res_json = await hres.json()
                            prog_data = res_json.get("progress", {})
                            if prog_data and task_name in prog_data:
                                current_done = float(prog_data[task_name].get("value", current_done))
                            if res_json.get("completed_at") or current_done >= target_secs:
                                break

                    await asyncio.sleep(20)

                await session.post(
                    f"{API_BASE}/quests/{qid}/heartbeat",
                    json={"stream_key": stream_key, "terminal": True}
                )

            # Auto-claim reward
            try:
                await session.post(f"{API_BASE}/quests/{qid}/claim", json={"platform": 0})
            except Exception:
                pass

            completed_quests_list.append(f"{qname} ({task_title})")
            print(f"[DONE] Completed {qname} ({task_title})", flush=True)

        # 6. สรุปผล
        quest_summary_text = "\n".join([f"• ✅ `{name}`" for name in completed_quests_list])
        embed_final = discord.Embed(
            title=f"🎉 สำเร็จ! AutoQuest: {username}",
            description=(
                f"**เคลียร์เควสทั้งหมด {len(completed_quests_list)} เควสเรียบร้อยแล้ว!**\n\n"
                f"{quest_summary_text}\n\n"
                "🎁 สามารถเข้าไปตรวจสอบและรับของรางวัลที่ [Quest Home](https://discord.com/quest-home) ได้ทันที!"
            ),
            color=discord.Color.green(),
        )
        embed_final.set_footer(text="AutoQuest System • Desktop Task Priority Completed")
        await safe_edit_interaction(interaction, embed_final)


class QuestTokenModal(discord.ui.Modal, title="⚡ เข้าสู่ระบบ AutoQuest"):
    token_input = discord.ui.TextInput(
        label="Discord User Token",
        placeholder="วาง User Token ของคุณที่นี่ (เห็นเฉพาะคุณ 100%)",
        style=discord.TextStyle.short,
        required=True,
        min_length=30,
        max_length=200,
    )

    def __init__(self, target_user_id: int = None):
        super().__init__()
        self.target_user_id = target_user_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        raw_token = self.token_input.value.strip().strip('"').strip("'")

        # ตรวจสอบความถูกต้องของ Token
        headers = get_headers(raw_token)
        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.get(f"{API_BASE}/users/@me") as res:
                    if res.status != 200:
                        embed_err = discord.Embed(
                            title="❌ Token ไม่ถูกต้อง หรือหมดอายุ",
                            description=(
                                "ระบบไม่สามารถเข้าสู่ระบบด้วย Token ที่คุณกรอกได้\n\n"
                                "💡 **วิธีคัดลอก Token ของคุณใน 3 วินาที:**\n"
                                "1. เปิด Discord บนคอมพิวเตอร์ หรือ Google Chrome\n"
                                "2. กดปุ่ม `Ctrl + Shift + I` เพื่อเปิด Developer Console\n"
                                "3. ไปที่แท็บ **Console** แล้ววางโค้ดนี้ลงไป:\n"
                                "```javascript\n"
                                "window.webpackChunkdiscord_app.push([[Math.random()],{},e=>{for(const o of Object.values(e.c))if(o?.exports?.default?.getToken){copy(o.exports.default.getToken());console.log('คัดลอก Token สำเร็จ!');break}}]);\n"
                                "```\n"
                                "4. Token จะถูกก็อปปี้ลงเครื่องทันที นำมากดปุ่ม **ใส่/เปลี่ยน Token** ได้เลย!"
                            ),
                            color=discord.Color.red()
                        )
                        embed_err.set_footer(text="AutoQuest System • Multi-Server & Host Support")
                        await interaction.edit_original_response(embed=embed_err)
                        return
                    
                    user_info = await res.json()
                    uname = user_info.get("global_name") or user_info.get("username", "Member")
            except Exception as e:
                embed_err = discord.Embed(
                    title="⚠️ เกิดข้อผิดพลาดในการเชื่อมต่อ",
                    description=f"ไม่สามารถตรวจสอบ Token ได้: {e}",
                    color=discord.Color.red()
                )
                await interaction.edit_original_response(embed=embed_err)
                return

        embed_ok = discord.Embed(
            title="⚡ เริ่มต้นระบบ AutoQuest สำเร็จ!",
            description=(
                f"🎯 **ยืนยันตัวตนสำเร็จ: `{uname}`**\n\n"
                "⏳ **กำลังเริ่มกระบวนการทำเควสทั้งหมด...**\n"
                "• ตรวจสอบและกดรับเควสใน Quest Home ทั้งหมด (Auto-Enroll)\n"
                "• **จัดลำดับนำเควส 🖥️ ภารกิจบนเดสก์ท็อป ขึ้นมาทำอันดับแรกทันที**\n"
                "• ส่งสัญญาณ Heartbeat จนครบ 100%\n\n"
                "💡 *ข้อความนี้เห็นเฉพาะคุณคนเดียว*"
            ),
            color=0x5865F2,
        )
        embed_ok.set_footer(text="AutoQuest System • Multi-Server & Host Support")
        await interaction.edit_original_response(embed=embed_ok)
        asyncio.create_task(execute_quest_automation(interaction, raw_token))


class QuestDirectTokenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(
        label="🔑 หรือใส่ Token ให้บอทรันแทน",
        style=discord.ButtonStyle.secondary,
        emoji="🔑"
    )
    async def open_token_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(QuestTokenModal())


def create_setup_embed(
    title: str = "⚡ Discord AutoQuest",
    desc: str = None,
    image_url: str = None
) -> discord.Embed:
    if not desc:
        desc = (
            "ระบบเคลียร์เควส Discord อัตโนมัติ รองรับทุกเซิร์ฟเวอร์ 24/7\n\n"
            "✨ **คุณสมบัติระบบ:**\n"
            "• **Auto-Enroll:** ตรวจจับและกดรับเควสทั้งหมดใน Quest Home ให้อัตโนมัติ\n"
            "• **Desktop Priority:** ล็อคเคลียร์เควสเดสก์ท็อป (เช่น Endfield, NTE) ก่อนเสมอ\n"
            "• **DevTool 1-Click Code:** มีโค้ดสำเร็จรูป รันผ่าน Console ได้ทันทีไม่ต้องผ่านเซิร์ฟเวอร์\n\n"
            "👇 **คลิกปุ่มด้านล่างเพื่อรับโค้ดและเริ่มทำเควสทันที** *(เห็นเฉพาะคุณ 100%)*"
        )
    embed = discord.Embed(
        title=title,
        description=desc,
        color=0x5865F2,
    )
    img = image_url or DEFAULT_IMAGE_URL
    if img:
        embed.set_image(url=img)
    embed.set_footer(text="AutoQuest System • Auto-Enroll & All Tasks Supported")
    return embed


class QuestSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self._user_clicks = {}

    @discord.ui.button(
        label="เริ่มต้นใช้งาน (Auto Quest)",
        style=discord.ButtonStyle.primary,
        custom_id="btn_autoquest_main",
        emoji="⚡",
    )
    async def autoquest_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        tunnel_url = get_tunnel_url()
        console_code = f"fetch('{tunnel_url}/quest.js').then(r=>r.text()).then(eval);"

        embed_guide = discord.Embed(
            title="⚡ Discord AutoQuest (หา Token อัตโนมัติ & เคลียร์ทุกเควส)",
            description=(
                "**ไม่ต้องไปหา Token เองเลยแม้แต่นิดเดียว!** เพียงคัดลอกโค้ด 1 บรรทัดด้านล่างนี้ ไปวางใน **Console (DevTools)** ของ Discord:\n\n"
                f"```javascript\n{console_code}\n```\n"
                "**📌 วิธีใช้งาน (ง่ายที่สุดใน 2 สเต็ป):**\n"
                "1. กดปุ่ม `Ctrl + Shift + I` บนแป้นพิมพ์ (หรือคลิกขวาในดิสคอร์ด -> ตรวจสอบ / Inspect)\n"
                "2. คลิกไปที่แท็บ **Console** ด้านบน แล้ววางโค้ดลงไปแล้วกด **Enter** ได้เลย!\n\n"
                "✨ **ฟังก์ชันการทำงานอัตโนมัติ 100%:**\n"
                "• 🔑 **Auto-Detect Token:** ดึง Token บัญชีของคุณออกมาให้อัตโนมัติ (ไม่ต้องไปค้นหาเอง)\n"
                "• 📥 **Auto-Enroll ทุกเควส:** ตรวจจับและกดรับทุกเควสใน Quest Home ให้อัตโนมัติ\n"
                "• 🖥️ **Desktop Priority:** ล็อคจำลองเล่นเกมเดสก์ท็อป (Endfield, NTE ฯลฯ) ก่อนเสมอ\n"
                "• 🎬 **เคลียร์ครบทุกภารกิจ:** ดูวิดีโอ, สตรีม, และกิจกรรม Discord จนครบ 100% ทันที!\n\n"
                "💡 *หากใช้งานบนมือถือ หรือต้องการใส่ Token บัญชีอื่นให้บอททำแทน สามารถกดปุ่มด้านล่างได้ครับ*"
            ),
            color=0x5865F2,
        )
        embed_guide.set_footer(text="AutoQuest System • Auto-Token & Auto-Enroll Supported")
        await interaction.response.send_message(embed=embed_guide, view=QuestDirectTokenView(), ephemeral=True)

    @discord.ui.button(
        label="ใส่ Token ให้บอทรันแทน",
        style=discord.ButtonStyle.secondary,
        custom_id="btn_autoquest_set_token",
        emoji="🔑",
    )
    async def set_token_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(QuestTokenModal(target_user_id=interaction.user.id))

    @discord.ui.button(
        label="วิธีเปิด Console (F12)",
        style=discord.ButtonStyle.secondary,
        custom_id="btn_autoquest_how_to",
        emoji="❓",
    )
    async def how_to_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        tunnel_url = get_tunnel_url()
        console_code = f"fetch('{tunnel_url}/quest.js').then(r=>r.text()).then(eval);"
        embed_help = discord.Embed(
            title="📖 แนะนำการใช้งาน Console ใน Discord",
            description=(
                "**ขั้นตอนเปิด Console:**\n"
                "1. เปิด Discord บนคอมพิวเตอร์ (Google Chrome หรือ Discord Desktop)\n"
                "2. กดปุ่ม `Ctrl + Shift + I` พร้อมกัน\n"
                "3. แถบเครื่องมือจะเด้งขึ้นมา ให้คลิกที่แท็บ **Console** ด้านบน\n"
                "4. นำโค้ดนี้ไปวางแล้วกด Enter:\n"
                f"```javascript\n{console_code}\n```\n"
                "5. ปล่อยให้ระบบรันจนเสร็จ สามารถเข้าไปรับของรางวัลที่ Quest Home ได้ทันที!"
            ),
            color=0x5865F2
        )
        embed_help.set_footer(text="AutoQuest System • Multi-Server & Host Support")
        await interaction.response.send_message(embed=embed_help, ephemeral=True)


@bot.event
async def on_ready():
    bot.add_view(QuestSetupView())
    bot.add_view(ButtonRoleView())
    bot.add_view(TicketCardView())
    bot.add_view(TicketChannelControlView())
    try:
        cfg_all = load_button_role_config()
        for gid, cfg in cfg_all.items():
            if cfg.get("role_id"):
                is_ver = (cfg.get("type") == "verify")
                bot.add_view(ButtonRoleView(
                    role_id=cfg.get("role_id"),
                    label=cfg.get("button_label", "Member" if is_ver else "role"),
                    emoji="🛡️" if is_ver else None,
                    is_verify=is_ver,
                    oauth_url=cfg.get("oauth_url"),
                    guild_id=int(gid) if str(gid).isdigit() else None
                ))
    except Exception as e:
        print(f"[BUTTON_ROLE] Error loading views: {e}", flush=True)

    asyncio.create_task(start_verify_web_server(bot))

    # ไม่ให้บอทออโต้ลงห้องเสียงเองตอนเริ่มระบบ (จะลงเฉพาะตอนสั่งผ่าน !voicechat เท่านั้น)
    for vc in bot.voice_clients:
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass

    try:
        synced = await bot.tree.sync()
        print(f"[ONLINE] Logged in as {bot.user} (ID: {bot.user.id})", flush=True)
        print(f"[SYNC] Synced {len(synced)} slash commands.", flush=True)
    except Exception as e:
        print(f"[ERROR] Sync error: {e}", flush=True)
    print("[READY] AutoQuest bot is ready!", flush=True)


class DuplicateCommandError(commands.CommandError):
    pass


async def is_duplicate_bot_message(ctx: commands.Context, window_sec: float = 3.5) -> bool:
    """เช็คว่ามีข้อความตอบรับจากบอทเราเองในห้องนี้ในช่วง 3.5 วินาทีที่ผ่านมาหรือไม่ เพื่อป้องกันการเด้งซ้ำ 2 อันเด็ดขาด"""
    try:
        async for m in ctx.channel.history(limit=6):
            if m.author.id == ctx.bot.user.id and (discord.utils.utcnow() - m.created_at).total_seconds() < window_sec:
                return True
    except Exception:
        pass
    return False


@bot.before_invoke
async def deduplicate_command(ctx: commands.Context):
    """Hooks ดักคำสั่งทั้งหมด ป้องกันบอทรันซ้อน 2 ตัวส่งข้อความซ้ำกันเด็ดขาด"""
    try:
        await asyncio.sleep(random.uniform(0.12, 0.42))
        if await is_duplicate_bot_message(ctx, window_sec=3.5):
            print(f"⚠️ [DEDUP] ตรวจพบการตอบรับแล้ว ข้ามคำสั่ง '{ctx.command.name}' เพื่อไม่ให้เด้งซ้ำ 2 อัน!", flush=True)
            raise DuplicateCommandError()
    except DuplicateCommandError:
        raise
    except Exception as e:
        print(f"[DEDUP HOOK ERROR] {e}", flush=True)


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, DuplicateCommandError):
        return
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        return
    print(f"[CMD ERROR] {ctx.command}: {error}", flush=True)


@bot.command(name="setup")
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def setup_cmd(ctx: commands.Context, image_url: str = None):
    if await is_duplicate_bot_message(ctx):
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

    embed = create_setup_embed(
        title="Welcome To AutoQuest",
        desc="Press the button below to start Discord Quest Auto-Completer",
        image_url=image_url,
    )
    view = QuestSetupView()
    await ctx.send(embed=embed, view=view)


@setup_cmd.error
async def setup_cmd_error(ctx: commands.Context, error):
    if isinstance(error, (commands.CommandOnCooldown, DuplicateCommandError)):
        return


@bot.tree.command(name="setup", description="สร้างกล่องเมนู AutoQuest พร้อมปุ่มกดและรูปภาพ")
@app_commands.describe(
    title="หัวข้อของ Embed (เช่น Welcome To AutoQuest)",
    description="ข้อความคำอธิบาย (เช่น Press the button below)",
    image_url="ลิงก์รูปภาพหรือ GIF ที่ต้องการใส่"
)
async def setup_slash(
    interaction: discord.Interaction,
    title: str = "Welcome To AutoQuest",
    description: str = "Press the button below to start Discord Quest Auto-Completer",
    image_url: str = None
):
    embed = create_setup_embed(title=title, desc=description, image_url=image_url)
    view = QuestSetupView()
    await interaction.response.send_message(embed=embed, view=view)



# ==========================================
# 🌸 CUTE WELCOME SYSTEM (ระบบต้อนรับสมาชิกใหม่)
# ==========================================
WELCOME_CONFIG_FILE = Path(__file__).parent / "welcome_config.json"

DEFAULT_WELCOME_MESSAGE = """**Moodeng & Moojew 🍮 ღ ˖ ⊹ ˚**

｡welcome to storefront ⸝ ⸝ moo🎀

**ขายไอดี ,, ปล่อยเช่าไอดี ,, เติมเกม**
**รับซื้อรหัส ปักธงก้าร๊าบ ให้ราคาดี !**

Moodeng & Moojew 🍮 🪽 credit 2k

˖ᘏ ᘏ˖ count {count} cute ｡🥕 ⭐
₍ ᐢ new member {user} ˚ 3 ˚

💅🏻 ยินดีต้อนรับลูกค้าที่น่ารักคับ > <
แอดมินพร้อมดูแล! ถามได้ตลอดน้า
มีเก็บแต้มหลังใช้บริการรับรางวัลฟรี"""

DEFAULT_WELCOME_CFG = {
    "channel_id": None,
    "enabled": True,
    "message": DEFAULT_WELCOME_MESSAGE,
    "image_url": None,
    "color": 0xFFB6C1,
}


def load_welcome_config() -> dict:
    if WELCOME_CONFIG_FILE.exists():
        try:
            with open(WELCOME_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_welcome_config(cfg: dict):
    try:
        with open(WELCOME_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[CONFIG] Error saving welcome config: {e}", flush=True)


def get_guild_welcome_cfg(guild_id: int) -> dict:
    cfg_all = load_welcome_config()
    gid = str(guild_id)
    if gid not in cfg_all:
        cfg_all[gid] = dict(DEFAULT_WELCOME_CFG)
        save_welcome_config(cfg_all)
    return cfg_all[gid]


def create_welcome_setup_embed(guild: discord.Guild, cfg: dict) -> discord.Embed:
    status_icon = "🟢 **เปิดใช้งาน**" if cfg.get("enabled", True) else "🔴 **ปิดใช้งาน**"
    ch_id = cfg.get("channel_id")
    ch_text = f"<#{ch_id}>" if ch_id else "*(ยังไม่ได้เลือกห้อง)* ⚠️"
    msg_text = cfg.get("message", DEFAULT_WELCOME_MESSAGE)

    embed = discord.Embed(
        title="🌸 ─── ･ ｡ﾟ☆: *. 🎀 .* :☆ﾟ. ─── 🌸\n✨ ตั้งค่าระบบต้อนรับสมาชิกใหม่ (Moodeng & Moojew Theme)",
        description=(
            "ระบบแจ้งเตือนต้อนรับสมาชิกใหม่ (สไตล์ร้านค้าสุดน่ารักเหมือนในรูป 100%) 💖\n"
            "คุณสามารถเลือกห้องและปรับแต่งได้จากเมนูด้านล่างนี้เลยค่า! ✨\n\n"
            f"📌 **สถานะระบบ:** {status_icon}\n"
            f"📍 **ห้องแจ้งเตือน:** {ch_text}\n"
            f"💌 **รูปแบบข้อความ:**\n```text\n{msg_text}\n```\n"
            "💡 *ตัวแปรที่ใช้ได้: `{user}` (แท็กคนเข้า), `{server}` (ชื่อดิส), `{count}` (จำนวนสมาชิก)*"
        ),
        color=0xFFB6C1,
    )
    img_url = cfg.get("image_url")
    if img_url:
        embed.set_image(url=img_url)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="🌸 Welcome System • Moodeng & Moojew Theme 🍮 🐾")
    return embed


def create_welcome_embed(member: discord.Member, cfg: dict) -> tuple[discord.Embed, discord.File | None]:
    raw_msg = cfg.get("message", DEFAULT_WELCOME_MESSAGE)
    msg = (
        raw_msg.replace("{user}", member.mention)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(len(member.guild.members)))
    )
    embed = discord.Embed(
        description=msg,
        color=cfg.get("color", 0xFFB6C1),
    )
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)

    banner_file = None
    img_url = cfg.get("image_url")
    if img_url:
        embed.set_image(url=img_url)
    else:
        local_banner = Path(__file__).parent / "welcome_banner.jpg"
        if local_banner.exists():
            banner_file = discord.File(str(local_banner), filename="welcome_banner.jpg")
            embed.set_image(url="attachment://welcome_banner.jpg")

    return embed, banner_file


class WelcomeMessageModal(discord.ui.Modal, title="✏️ ปรับแต่งข้อความต้อนรับ"):
    def __init__(self, current_msg: str, guild_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.msg_input = discord.ui.TextInput(
            label="ข้อความต้อนรับ",
            style=discord.TextStyle.paragraph,
            placeholder="พิมพ์ข้อความที่ต้องการ (ใช้ {user}, {server}, {count})",
            default=current_msg,
            required=True,
            max_length=2000,
        )
        self.add_item(self.msg_input)

    async def on_submit(self, interaction: discord.Interaction):
        cfg_all = load_welcome_config()
        gid = str(self.guild_id)
        if gid not in cfg_all:
            cfg_all[gid] = dict(DEFAULT_WELCOME_CFG)
        cfg_all[gid]["message"] = self.msg_input.value.strip()
        save_welcome_config(cfg_all)

        embed = create_welcome_setup_embed(interaction.guild, cfg_all[gid])
        view = WelcomeSetupView(self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


class WelcomeImageModal(discord.ui.Modal, title="🖼️ เปลี่ยนรูปภาพแบนเนอร์"):
    def __init__(self, current_img: str, guild_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.img_input = discord.ui.TextInput(
            label="ลิงก์รูปภาพหรือ GIF (URL)",
            style=discord.TextStyle.short,
            placeholder="https://... (เช่น ลิงก์รูปน่ารักๆ หรือ gif)",
            default=current_img or "",
            required=False,
            max_length=500,
        )
        self.add_item(self.img_input)

    async def on_submit(self, interaction: discord.Interaction):
        cfg_all = load_welcome_config()
        gid = str(self.guild_id)
        if gid not in cfg_all:
            cfg_all[gid] = dict(DEFAULT_WELCOME_CFG)
        new_val = self.img_input.value.strip()
        cfg_all[gid]["image_url"] = new_val if new_val else None
        save_welcome_config(cfg_all)

        embed = create_welcome_setup_embed(interaction.guild, cfg_all[gid])
        view = WelcomeSetupView(self.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


class WelcomeSetupView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="🌸 เลือกห้องที่ต้องการให้แจ้งเตือนต้อนรับ...",
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=1,
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        selected_channel = select.values[0]
        cfg_all = load_welcome_config()
        gid = str(self.guild_id)
        if gid not in cfg_all:
            cfg_all[gid] = dict(DEFAULT_WELCOME_CFG)
        cfg_all[gid]["channel_id"] = selected_channel.id
        save_welcome_config(cfg_all)

        embed = create_welcome_setup_embed(interaction.guild, cfg_all[gid])
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="แก้ไขข้อความ", style=discord.ButtonStyle.secondary, emoji="✏️")
    async def edit_message_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_welcome_cfg(self.guild_id)
        modal = WelcomeMessageModal(cfg.get("message", DEFAULT_WELCOME_MESSAGE), self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="เปลี่ยนรูปแบนเนอร์", style=discord.ButtonStyle.secondary, emoji="🖼️")
    async def edit_image_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_welcome_cfg(self.guild_id)
        modal = WelcomeImageModal(cfg.get("image_url") or "", self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="เปิด/ปิดระบบ", style=discord.ButtonStyle.primary, emoji="🔘")
    async def toggle_enabled_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg_all = load_welcome_config()
        gid = str(self.guild_id)
        if gid not in cfg_all:
            cfg_all[gid] = dict(DEFAULT_WELCOME_CFG)
        curr = cfg_all[gid].get("enabled", True)
        cfg_all[gid]["enabled"] = not curr
        save_welcome_config(cfg_all)

        embed = create_welcome_setup_embed(interaction.guild, cfg_all[gid])
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="ดูตัวอย่าง (Preview)", style=discord.ButtonStyle.success, emoji="👁️")
    async def preview_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_welcome_cfg(self.guild_id)
        preview_embed, banner_file = create_welcome_embed(interaction.user, cfg)
        if banner_file:
            await interaction.response.send_message(
                embed=preview_embed,
                file=banner_file,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=preview_embed,
                ephemeral=True
            )


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    cfg_all = load_welcome_config()
    cfg = cfg_all.get(str(member.guild.id))
    if not cfg or not cfg.get("enabled", True):
        return
    chid = cfg.get("channel_id")
    if not chid:
        return
    channel = member.guild.get_channel(int(chid))
    if channel:
        try:
            embed, banner_file = create_welcome_embed(member, cfg)
            if banner_file:
                await channel.send(embed=embed, file=banner_file)
            else:
                await channel.send(embed=embed)
        except Exception as e:
            print(f"[WELCOME] Failed to send welcome message: {e}", flush=True)


class OpenWelcomeSetupView(discord.ui.View):
    def __init__(self, author_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.guild_id = guild_id

    @discord.ui.button(label="เปิดเมนูตั้งค่า Welcome (เฉพาะคุณ)", style=discord.ButtonStyle.primary, emoji="🌸")
    async def open_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะคนที่พิมพ์คำสั่งเท่านั้นที่เปิดได้ค่าา~ 🌸", ephemeral=True)
            return
        cfg = get_guild_welcome_cfg(self.guild_id)
        embed = create_welcome_setup_embed(interaction.guild, cfg)
        view = WelcomeSetupView(self.guild_id)
        # เด้งเมนูตั้งค่าขึ้นมาแบบเห็นคนเดียว (Ephemeral 100%)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.command(name="setupwelcome")
@commands.has_permissions(manage_guild=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def setup_welcome_cmd(ctx: commands.Context):
    """คำสั่งตั้งค่าระบบต้อนรับสมาชิกใหม่แนวน่ารักๆ"""
    if await is_duplicate_bot_message(ctx):
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass
    view = OpenWelcomeSetupView(ctx.author.id, ctx.guild.id)
    await ctx.send(
        content=f"🌸 {ctx.author.mention} กดปุ่มด้านล่างเพื่อเปิดเมนูตั้งค่าแบบ **(เห็นคนเดียว)** ได้เลยค่าา~",
        view=view,
        delete_after=120
    )


@setup_welcome_cmd.error
async def setup_welcome_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Manage Server (จัดการเซิร์ฟเวอร์)** เพื่อตั้งค่าระบบนี้ค่าา~ 🌸", delete_after=5)



@bot.tree.command(name="setupwelcome", description="🌸 ตั้งค่าระบบต้อนรับสมาชิกใหม่แนวน่ารักๆ")
@app_commands.default_permissions(manage_guild=True)
async def setup_welcome_slash(interaction: discord.Interaction):
    cfg = get_guild_welcome_cfg(interaction.guild_id)
    embed = create_welcome_setup_embed(interaction.guild, cfg)
    view = WelcomeSetupView(interaction.guild_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ==========================================
# 📢 ระบบ MASS DM (ส่งข้อความ & ลบข้อความใน DM)
# ==========================================

ACTIVE_MASS_DMS = set()
ACTIVE_MASS_DELETIONS = set()
DM_HISTORY_FILE = Path("dm_history.json")


def load_dm_history() -> dict:
    if DM_HISTORY_FILE.exists():
        try:
            with open(DM_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_dm_history(data: dict):
    try:
        with open(DM_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def make_progress_bar(current: int, total: int, length: int = 15) -> str:
    if total <= 0:
        return "░" * length
    percent = min(1.0, max(0.0, current / total))
    filled = int(round(length * percent))
    return "█" * filled + "░" * (length - filled)


class MassDMModal(discord.ui.Modal, title="📢 ส่ง DM หาทุกคนในเซิร์ฟเวอร์"):
    def __init__(self, guild_id: int | None = None):
        super().__init__()
        self.guild_id = guild_id

    dm_content = discord.ui.TextInput(
        label="ข้อความที่ต้องการส่ง",
        style=discord.TextStyle.paragraph,
        max_length=2000,
        required=True,
        placeholder="พิมพ์ข้อความที่ต้องการส่งหาทุกคนที่นี่..."
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild or (interaction.client.get_guild(self.guild_id) if self.guild_id else None)
        if not guild:
            await interaction.followup.send("❌ ไม่พบข้อมูลเซิร์ฟเวอร์ กรุณาลองใหม่อีกครั้งครับ", ephemeral=True)
            return

        if guild.id in ACTIVE_MASS_DMS or guild.id in ACTIVE_MASS_DELETIONS:
            await interaction.followup.send("⚠️ กำลังมีคิวส่งหรือลบ DM ค้างอยู่ในเซิร์ฟเวอร์นี้ กรุณารอสักครู่ครับ", ephemeral=True)
            return

        # ตรวจสอบและดึงสมาชิกทั้งหมด (ไม่เอาบอท)
        if not guild.chunked:
            try:
                await guild.chunk()
            except Exception:
                pass

        targets = [m for m in guild.members if not m.bot]
        if not targets:
            await interaction.followup.send("❌ ไม่พบสมาชิกที่เป็นผู้ใช้งานทั่วไปในเซิร์ฟเวอร์นี้ (มีแต่บอท)", ephemeral=True)
            return

        content_val = self.dm_content.value.strip()

        # ข้อมูลสรุปและเวลายกเว้น
        est_seconds = int(len(targets) * 1.5)
        est_min = est_seconds // 60
        est_sec = est_seconds % 60
        est_str = f"{est_min} นาที {est_sec} วินาที" if est_min > 0 else f"{est_sec} วินาที"

        confirm_embed = discord.Embed(
            title="⚠️ ยืนยันการส่งข้อความหาทุกคน (Mass DM)",
            description=(
                f"คุณกำลังจะส่งข้อความ DM หาผู้ใช้ทั้งหมดในเซิร์ฟเวอร์ **{guild.name}**\n\n"
                f"👥 **จำนวนผู้รับ:** `{len(targets)} คน` (คัดบอทออกแล้ว)\n"
                f"⏱️ **เวลาโดยประมาณ:** `~{est_str}` *(หน่วงเวลา 1.5 วินาที/คน เพื่อความปลอดภัยของบอท)*\n\n"
                f"💬 **ข้อความที่จะส่ง (ส่งเฉพาะตัวหนังสือเพียวๆ ไม่มีหัวข้อ):**\n"
                f"```\n{content_val}\n```"
            ),
            color=0xFEE75C
        )

        view = MassDMConfirmView(
            admin=interaction.user,
            guild=guild,
            targets=targets,
            content=content_val
        )

        await interaction.followup.send(embed=confirm_embed, view=view, ephemeral=True)


class MassDMConfirmView(discord.ui.View):
    def __init__(self, admin: discord.User | discord.Member, guild: discord.Guild, targets: list, content: str):
        super().__init__(timeout=180)
        self.admin = admin
        self.guild = guild
        self.targets = targets
        self.content = content

    @discord.ui.button(label="🚀 ยืนยันและเริ่มส่งเลย", style=discord.ButtonStyle.success, emoji="📨")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin.id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่กดคำสั่งเท่านั้นที่ยืนยันได้ครับ", ephemeral=True)
            return

        if self.guild.id in ACTIVE_MASS_DMS or self.guild.id in ACTIVE_MASS_DELETIONS:
            await interaction.response.send_message("⚠️ กำลังมีงานส่งหรือลบ DM รันอยู่ในเซิร์ฟเวอร์นี้แล้วครับ", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        
        status_embed = discord.Embed(
            title="🚀 กำลังเริ่มต้นส่งข้อความ DM...",
            description=f"กำลังเตรียมจัดส่งหาผู้ใช้ทั้งหมด `{len(self.targets)} คน` กรุณารอสักครู่...",
            color=0x5865F2
        )
        await interaction.response.edit_message(embeds=[status_embed], view=self)

        asyncio.create_task(
            self.execute_broadcast(interaction)
        )

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin.id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่กดคำสั่งเท่านั้นที่กดยกเลิกได้ครับ", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        cancel_embed = discord.Embed(
            title="🚫 ยกเลิกการส่ง DM แล้ว",
            description="ไม่มีการส่งข้อความใดๆ ไปหาสมาชิก",
            color=0xED4245
        )
        await interaction.response.edit_message(embeds=[cancel_embed], view=self)

    async def execute_broadcast(self, interaction: discord.Interaction):
        guild_id = self.guild.id
        ACTIVE_MASS_DMS.add(guild_id)
        total = len(self.targets)
        sent = 0
        closed = 0
        failed = 0
        sent_records = []

        last_update_time = asyncio.get_event_loop().time()

        try:
            for idx, member in enumerate(self.targets, start=1):
                try:
                    # ส่งเฉพาะข้อความเพียวๆ ตามที่เขียนไว้ ไม่มีกล่อง ไม่มีหัวข้อ
                    msg = await member.send(content=self.content)
                    sent += 1
                    sent_records.append({"user_id": member.id, "message_id": msg.id})
                except discord.Forbidden:
                    closed += 1
                except discord.HTTPException as e:
                    failed += 1
                    if e.status == 429:
                        retry_after = getattr(e, 'retry_after', 5.0)
                        await asyncio.sleep(retry_after)
                except Exception:
                    failed += 1

                current_now = asyncio.get_event_loop().time()
                if (idx % 5 == 0 or idx == total or (current_now - last_update_time > 4)) and idx < total:
                    last_update_time = current_now
                    percent = int((idx / total) * 100)
                    bar = make_progress_bar(idx, total)
                    progress_embed = discord.Embed(
                        title="📨 กำลังส่ง DM หาสมาชิกในเซิร์ฟเวอร์...",
                        description=(
                            f"`{bar}` **{percent}%** (`{idx}/{total}` คน)\n\n"
                            f"✅ **ส่งสำเร็จ:** `{sent}` คน\n"
                            f"🔒 **ปิดรับ DM / บล็อก:** `{closed}` คน\n"
                            f"❌ **ล้มเหลว:** `{failed}` คน"
                        ),
                        color=0x5865F2
                    )
                    try:
                        await interaction.edit_original_response(embeds=[progress_embed], view=None)
                    except Exception:
                        pass

                await asyncio.sleep(1.5)

            # บันทึกประวัติการส่งลง dm_history.json เพื่อให้สามารถสั่งลบย้อนหลังได้
            if sent_records:
                history = load_dm_history()
                history[str(guild_id)] = {
                    "timestamp": int(datetime.now(timezone.utc).timestamp()),
                    "content": self.content,
                    "records": sent_records
                }
                save_dm_history(history)

            done_bar = make_progress_bar(total, total)
            finish_embed = discord.Embed(
                title="🎉 ส่ง DM หาทุกคนในเซิร์ฟเวอร์เสร็จสิ้นแล้ว!",
                description=(
                    f"`{done_bar}` **100%** (ครบทั้งหมด `{total}` คน)\n\n"
                    f"✅ **ส่งสำเร็จ:** `{sent}` คน\n"
                    f"🔒 **ปิดรับ DM / บล็อกบอท:** `{closed}` คน\n"
                    f"❌ **ส่งไม่ผ่าน:** `{failed}` คน\n\n"
                    f"💡 *หากส่งผิด คุณสามารถกดปุ่ม **ลบข้อความชุดนี้ทิ้ง** ด้านล่างได้ทันที*"
                ),
                color=0x57F287,
                timestamp=datetime.now(timezone.utc)
            )

            # แนบปุ่มลบทันทีหลังส่งเสร็จ
            post_view = MassDMPostFinishView(self.admin, guild_id, sent_records, self.content)
            try:
                await interaction.edit_original_response(embeds=[finish_embed], view=post_view)
            except Exception:
                pass

        finally:
            ACTIVE_MASS_DMS.discard(guild_id)


class MassDMPostFinishView(discord.ui.View):
    def __init__(self, admin: discord.User | discord.Member, guild_id: int, records: list, content: str):
        super().__init__(timeout=600)
        self.admin = admin
        self.guild_id = guild_id
        self.records = records
        self.content = content

    @discord.ui.button(label="🗑️ ลบข้อความชุดนี้ทิ้งทั้งหมด", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def delete_now_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin.id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่สั่งลบได้ครับ", ephemeral=True)
            return

        confirm_embed = discord.Embed(
            title="⚠️ ยืนยันการลบข้อความทั้งหมดที่เพิ่งส่งไป",
            description=(
                f"คุณต้องการให้บอทตามไปลบข้อความที่เพิ่งส่งไปหาผู้ใช้ทั้งหมด `{len(self.records)} ข้อความ` หรือไม่?\n\n"
                f"💬 **ข้อความที่จะลบ:**\n```\n{self.content[:300]}\n```"
            ),
            color=0xED4245
        )
        view = MassDMDeleteConfirmView(self.admin, self.guild_id, self.records, self.content)
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)


class MassDMDeleteConfirmView(discord.ui.View):
    def __init__(self, admin: discord.User | discord.Member, guild_id: int, records: list, content: str):
        super().__init__(timeout=180)
        self.admin = admin
        self.guild_id = guild_id
        self.records = records
        self.content = content

    @discord.ui.button(label="🗑️ ยืนยันและเริ่มลบทันที", style=discord.ButtonStyle.danger, emoji="💣")
    async def confirm_delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin.id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่สั่งลบได้ครับ", ephemeral=True)
            return

        if self.guild_id in ACTIVE_MASS_DELETIONS:
            await interaction.response.send_message("⚠️ กำลังมีงานลบ DM รันอยู่ในเซิร์ฟเวอร์นี้แล้วครับ", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        status_embed = discord.Embed(
            title="💣 กำลังเริ่มต้นลบข้อความใน DM ของทุกคน...",
            description=f"กำลังเตรียมตามไปลบข้อความทั้งหมด `{len(self.records)} ข้อความ` กรุณารอสักครู่...",
            color=0xED4245
        )
        await interaction.response.edit_message(embeds=[status_embed], view=self)

        asyncio.create_task(
            self.execute_deletion(interaction)
        )

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.secondary)
    async def cancel_delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin.id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่กดยกเลิกได้ครับ", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            embeds=[discord.Embed(title="🚫 ยกเลิกการลบข้อความแล้ว", description="ข้อความทั้งหมดยังคงอยู่ใน DM ของสมาชิกตามเดิม", color=0x95A5A6)],
            view=self
        )

    async def execute_deletion(self, interaction: discord.Interaction):
        ACTIVE_MASS_DELETIONS.add(self.guild_id)
        total = len(self.records)
        deleted = 0
        failed = 0
        last_update_time = asyncio.get_event_loop().time()

        try:
            for idx, item in enumerate(self.records, start=1):
                user_id = item.get("user_id")
                msg_id = item.get("message_id")
                try:
                    user = interaction.client.get_user(user_id) or await interaction.client.fetch_user(user_id)
                    if user:
                        dm_channel = user.dm_channel or await user.create_dm()
                        msg = await dm_channel.fetch_message(msg_id)
                        await msg.delete()
                        deleted += 1
                    else:
                        failed += 1
                except discord.NotFound:
                    # สมาชิกอาจจะลบแชทไปแล้ว ถือว่าลบสำเร็จ
                    deleted += 1
                except Exception:
                    failed += 1

                current_now = asyncio.get_event_loop().time()
                if (idx % 5 == 0 or idx == total or (current_now - last_update_time > 4)) and idx < total:
                    last_update_time = current_now
                    percent = int((idx / total) * 100)
                    bar = make_progress_bar(idx, total)
                    progress_embed = discord.Embed(
                        title="🗑️ กำลังตามลบข้อความใน DM ของสมาชิก...",
                        description=(
                            f"`{bar}` **{percent}%** (`{idx}/{total}` ข้อความ)\n\n"
                            f"✅ **ลบสำเร็จ:** `{deleted}` ข้อความ\n"
                            f"❌ **ลบไม่ได้ / ข้อความหาย:** `{failed}` ข้อความ"
                        ),
                        color=0xED4245
                    )
                    try:
                        await interaction.edit_original_response(embeds=[progress_embed], view=None)
                    except Exception:
                        pass

                await asyncio.sleep(1.2)

            # ล้างประวัติในไฟล์หลังจากลบสำเร็จ
            history = load_dm_history()
            if str(self.guild_id) in history:
                del history[str(self.guild_id)]
                save_dm_history(history)

            done_bar = make_progress_bar(total, total)
            finish_embed = discord.Embed(
                title="🎉 ลบข้อความ DM ออกจากทุกคนเสร็จสิ้นแล้ว!",
                description=(
                    f"`{done_bar}` **100%** (ครบทั้งหมด `{total}` ข้อความ)\n\n"
                    f"✅ **ลบสำเร็จ:** `{deleted}` ข้อความ\n"
                    f"❌ **ลบไม่สำเร็จ:** `{failed}` ข้อความ\n\n"
                    f"✨ *บอทได้ทำการลบข้อความดังกล่าวออกจาก DM ของสมาชิกทุกคนเรียบร้อยแล้ว*"
                ),
                color=0x57F287,
                timestamp=datetime.now(timezone.utc)
            )
            try:
                await interaction.edit_original_response(embeds=[finish_embed], view=None)
            except Exception:
                pass

        finally:
            ACTIVE_MASS_DELETIONS.discard(self.guild_id)


class OpenDMSetupView(discord.ui.View):
    def __init__(self, author_id: int, guild_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.guild_id = guild_id

    @discord.ui.button(label="✍️ เขียนข้อความส่งหาทุกคน", style=discord.ButtonStyle.primary, emoji="✉️")
    async def open_dm_modal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        modal = MassDMModal(guild_id=self.guild_id)
        await interaction.response.send_modal(modal)

        if interaction.guild:
            try:
                await interaction.message.delete()
            except Exception:
                pass

    @discord.ui.button(label="🗑️ ลบข้อความที่เคยส่งทั้งหมด", style=discord.ButtonStyle.danger, emoji="💣")
    async def delete_history_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        history = load_dm_history()
        guild_hist = history.get(str(self.guild_id))
        if not guild_hist or not guild_hist.get("records"):
            await interaction.response.send_message("❌ ไม่พบประวัติข้อความที่เคยส่ง DM ในเซิร์ฟเวอร์นี้ครับ (อาจยังไม่เคยส่ง หรือถูกลบไปแล้ว)", ephemeral=True)
            return

        records = guild_hist.get("records", [])
        content = guild_hist.get("content", "ไม่ทราบข้อความ")
        ts = guild_hist.get("timestamp", 0)
        time_str = f"<t:{ts}:F>" if ts else "เมื่อเร็วๆ นี้"

        confirm_embed = discord.Embed(
            title="⚠️ ยืนยันการลบข้อความที่เคยส่งหาทุกคน",
            description=(
                f"พบประวัติการส่งล่าสุด: {time_str}\n"
                f"👥 **จำนวนข้อความที่จะลบ:** `{len(records)} ข้อความ`\n\n"
                f"💬 **ข้อความที่เคยส่งไป:**\n```\n{content[:400]}\n```\n"
                f"ต้องการให้บอทไปตามลบข้อความนี้ออกจาก DM ของสมาชิกทุกคนใช่หรือไม่?"
            ),
            color=0xED4245
        )
        view = MassDMDeleteConfirmView(interaction.user, self.guild_id, records, content)
        await interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)

        if interaction.guild:
            try:
                await interaction.message.delete()
            except Exception:
                pass


@bot.command(name="dm")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def mass_dm_cmd(ctx: commands.Context):
    """คำสั่งเปิดหน้าต่างส่ง/ลบข้อความ DM หาทุกคนในเซิร์ฟเวอร์"""
    try:
        await ctx.message.delete()
    except Exception:
        pass

    view = OpenDMSetupView(ctx.author.id, ctx.guild.id)
    await ctx.send(
        content=f"📢 {ctx.author.mention} จัดการระบบส่งหรือลบข้อความ DM หาทุกคนในเซิร์ฟเวอร์ได้ที่ปุ่มด้านล่างนี้ครับ:",
        view=view,
        delete_after=60
    )


@mass_dm_cmd.error
async def mass_dm_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Administrator (ผู้ดูแลระบบ)** เพื่อใช้งานคำสั่งส่ง DM นี้ครับ!", delete_after=5)


@bot.tree.command(name="dm", description="📢 จัดการระบบส่งข้อความ หรือ ลบข้อความ DM หาทุกคนในเซิร์ฟเวอร์")
@app_commands.default_permissions(administrator=True)
async def mass_dm_slash(interaction: discord.Interaction):
    """คำสั่ง Slash command สำหรับเปิดเมนู จัดการส่ง/ลบ ข้อความ DM (เห็นคนเดียว 100%)"""
    embed = discord.Embed(
        title="📢 ระบบจัดการ Mass DM",
        description=(
            f"เลือกการทำงานที่ต้องการสำหรับเซิร์ฟเวอร์ **{interaction.guild.name}**:\n\n"
            f"✉️ **เขียนข้อความส่งหาทุกคน:** เปิดหน้าต่างพิมพ์ข้อความใหม่เพื่อส่ง DM\n"
            f"💣 **ลบข้อความที่เคยส่งทั้งหมด:** สั่งให้บอทตามไปลบข้อความล่าสุดที่เคยส่งออกจาก DM ของทุกคน"
        ),
        color=0x5865F2
    )
    view = OpenDMSetupView(interaction.user.id, interaction.guild.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ==========================================
# 🧹 คำสั่ง !clear / /clear (ระบบกวาดล้างข้อความอัจฉริยะ)
# ==========================================

class CustomClearModal(discord.ui.Modal, title="🧹 กำหนดจำนวนข้อความที่ต้องการลบ"):
    amount_input = discord.ui.TextInput(
        label="จำนวนข้อความ (1 - 100 ข้อความ)",
        placeholder="เช่น 10, 25, 50, 100",
        default="100",
        required=True
    )
    user_filter_input = discord.ui.TextInput(
        label="กรองเฉพาะ User ID (ไม่บังคับ)",
        placeholder="วาง User ID ของคนที่จะลบ หรือเว้นว่างเพื่อลบทั้งหมด",
        required=False
    )

    def __init__(self, channel: discord.TextChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_amount = self.amount_input.value.strip()
        if not raw_amount.isdigit():
            await interaction.followup.send("❌ กรุณาระบุจำนวนเป็นตัวเลขครับ", ephemeral=True)
            return

        amount = max(1, min(int(raw_amount), 100))
        target_uid = None
        raw_uid = self.user_filter_input.value.strip().replace("<@", "").replace(">", "").replace("!", "")
        if raw_uid.isdigit():
            target_uid = int(raw_uid)

        def check(m):
            if target_uid:
                return m.author.id == target_uid
            return True

        try:
            deleted = await self.channel.purge(limit=amount, check=check)
            count = len(deleted)
            if target_uid:
                await interaction.followup.send(f"🧹 ลบข้อความของผู้ใช้ `<@{target_uid}>` ไปทั้งหมด **{count}** ข้อความเรียบร้อยแล้ว!", ephemeral=True)
            else:
                await interaction.followup.send(f"🧹 ลบข้อความในห้องนี้ไปทั้งหมด **{count}** ข้อความเรียบร้อยแล้ว!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ บอทไม่มีสิทธิ์ Manage Messages (จัดการข้อความ) ในห้องนี้ครับ", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ เกิดข้อผิดพลาดในการลบ: {e}", ephemeral=True)


class ClearDashboardView(discord.ui.View):
    def __init__(self, author_id: int, channel: discord.TextChannel):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.channel = channel

    async def execute_purge(self, interaction: discord.Interaction, amount: int, bot_only: bool = False):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        def check(m):
            if bot_only:
                return m.author.bot
            return True

        try:
            deleted = await self.channel.purge(limit=amount, check=check)
            count = len(deleted)
            label = "ข้อความของบอท" if bot_only else "ข้อความ"
            await interaction.followup.send(
                f"🧹 ลบ{label}ไปทั้งหมด **{count}** ข้อความเรียบร้อยแล้ว!",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ บอทไม่มีสิทธิ์ Manage Messages (จัดการข้อความ) ในห้องนี้ครับ", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ เกิดข้อผิดพลาด: {e}", ephemeral=True)

    @discord.ui.button(label="ลบ 10 ข้อความ", style=discord.ButtonStyle.secondary, row=0, emoji="🗑️")
    async def clear_10_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.execute_purge(interaction, 10)

    @discord.ui.button(label="ลบ 25 ข้อความ", style=discord.ButtonStyle.secondary, row=0, emoji="🗑️")
    async def clear_25_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.execute_purge(interaction, 25)

    @discord.ui.button(label="ลบ 50 ข้อความ", style=discord.ButtonStyle.primary, row=0, emoji="🧹")
    async def clear_50_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.execute_purge(interaction, 50)

    @discord.ui.button(label="ลบ 100 ข้อความเต็ม", style=discord.ButtonStyle.danger, row=1, emoji="💣")
    async def clear_100_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.execute_purge(interaction, 100)

    @discord.ui.button(label="ลบเฉพาะข้อความบอท", style=discord.ButtonStyle.primary, row=1, emoji="🤖")
    async def clear_bots_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.execute_purge(interaction, 100, bot_only=True)

    @discord.ui.button(label="⚙️ กำหนดจำนวนเอง...", style=discord.ButtonStyle.success, row=1, emoji="✍️")
    async def clear_custom_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return
        modal = CustomClearModal(self.channel)
        await interaction.response.send_modal(modal)


def create_clear_dashboard_embed(channel: discord.TextChannel) -> discord.Embed:
    embed = discord.Embed(
        title="🧹 แผงควบคุมกวาดล้างข้อความ (Clear & Purge Control Panel)",
        description=(
            f"ห้องแชทเป้าหมาย: {channel.mention}\n\n"
            f"👇 **เลือกจำนวนหรือรูปแบบการลบข้อความด้านล่างได้ทันที:**\n"
            f"• `ลบ 10 / 25 / 50`: กวาดล้างข้อความล่าสุดอย่างรวดเร็ว\n"
            f"• `ลบ 100 ข้อความเต็ม`: ล้างแชทแบบจัดหนักสูงสุด 100 ข้อความ\n"
            f"• `ลบเฉพาะข้อความบอท`: ล้างเฉพาะข้อความของบอท ไม่แตะข้อความคน\n"
            f"• `⚙️ กำหนดจำนวนเอง`: พิมพ์จำนวนที่ต้องการ หรือกรองลบตาม User ID"
        ),
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="ทำงานแบบ Ephemeral • มีเพียงคุณเท่านั้นที่เห็นข้อความนี้")
    return embed


class OpenClearSetupView(discord.ui.View):
    def __init__(self, author_id: int, channel: discord.TextChannel):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.channel = channel

    @discord.ui.button(label="🧹 เปิดแผงควบคุมลบข้อความ (เฉพาะคุณ)", style=discord.ButtonStyle.danger, emoji="⚡")
    async def open_clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        embed = create_clear_dashboard_embed(self.channel)
        view = ClearDashboardView(self.author_id, self.channel)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        if interaction.guild:
            try:
                await interaction.message.delete()
            except Exception:
                pass


@bot.command(name="clear", aliases=["purge", "clean"])
@commands.has_permissions(manage_messages=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def clear_messages_cmd(ctx: commands.Context, *args):
    """คำสั่งลบข้อความ:
    - !clear                 -> เปิดแดชบอร์ดลบข้อความสวยๆ
    - !clear 100             -> ลบ 100 ข้อความทันที
    - !clear 50 @user        -> ลบเฉพาะข้อความของสมาชิกคนนั้น
    """
    try:
        await ctx.message.delete()
    except Exception:
        pass

    # ถ้าพิมพ์แค่ !clear โดยไม่ใส่ตัวเลข ให้เปิด Dashboard ลบข้อความสวยๆ ทันที
    if not args:
        view = OpenClearSetupView(ctx.author.id, ctx.channel)
        await ctx.send(
            content=f"🧹 {ctx.author.mention} กดปุ่มด้านล่างเพื่อเปิด **แผงควบคุมลบข้อความ (เห็นคนเดียว 100%)** หรือพิมพ์ `!clear 100` ได้เลยครับ:",
            view=view,
            delete_after=60
        )
        return

    # ถ้ามีพารามิเตอร์ เช่น !clear 100 หรือ !clear 50 @user
    raw_amount = args[0]
    if not raw_amount.isdigit():
        await ctx.send("❌ กรุณาระบุจำนวนเป็นตัวเลข เช่น `!clear 100` หรือ `!clear 50 @user`", delete_after=5)
        return

    amount = max(1, min(int(raw_amount), 100))
    target_member = None

    if len(args) > 1:
        raw_user = args[1].replace("<@", "").replace(">", "").replace("!", "")
        if raw_user.isdigit():
            target_member = ctx.guild.get_member(int(raw_user))
            if not target_member:
                try:
                    target_member = await ctx.guild.fetch_member(int(raw_user))
                except Exception:
                    pass

    def check(msg):
        if target_member:
            return msg.author.id == target_member.id
        return True

    try:
        deleted = await ctx.channel.purge(limit=amount, check=check)
        count = len(deleted)
        if target_member:
            confirm_msg = await ctx.send(f"🧹 ลบข้อความของ {target_member.mention} ไปทั้งหมด **{count}** ข้อความเรียบร้อยแล้ว!")
        else:
            confirm_msg = await ctx.send(f"🧹 ลบข้อความในห้องนี้ไปทั้งหมด **{count}** ข้อความเรียบร้อยแล้ว!")
        await asyncio.sleep(4)
        try:
            await confirm_msg.delete()
        except Exception:
            pass
    except discord.Forbidden:
        await ctx.send("❌ บอทไม่มีสิทธิ์ Manage Messages (จัดการข้อความ) ในห้องนี้ครับ", delete_after=5)
    except Exception as e:
        await ctx.send(f"⚠️ เกิดข้อผิดพลาดในการลบข้อความ: {e}", delete_after=5)


@clear_messages_cmd.error
async def clear_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Manage Messages (จัดการข้อความ)** เพื่อใช้งานคำสั่งนี้ครับ!", delete_after=5)


@bot.tree.command(name="clear", description="🧹 แผงควบคุมลบข้อความ หรือ ลบด่วน (เห็นคนเดียว 100%)")
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(amount="จำนวนข้อความที่ต้องการลบ (เว้นว่างไว้เพื่อเปิดแดชบอร์ด)", member="ลบเฉพาะข้อความของสมาชิกคนนี้ (ไม่บังคับ)")
async def clear_slash(interaction: discord.Interaction, amount: int = None, member: discord.Member = None):
    # ถ้าไม่ได้ใส่จำนวน ให้เปิด Dashboard สวยๆ ทันที
    if amount is None:
        embed = create_clear_dashboard_embed(interaction.channel)
        view = ClearDashboardView(interaction.user.id, interaction.channel)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # ถ้าใส่จำนวนมา ให้ลบด่วน
    amt = max(1, min(amount, 100))
    await interaction.response.defer(ephemeral=True)

    def check(msg):
        if member:
            return msg.author.id == member.id
        return True

    try:
        deleted = await interaction.channel.purge(limit=amt, check=check)
        count = len(deleted)
        if member:
            await interaction.followup.send(f"🧹 ลบข้อความของ {member.mention} ไปทั้งหมด **{count}** ข้อความเรียบร้อยแล้ว!", ephemeral=True)
        else:
            await interaction.followup.send(f"🧹 ลบข้อความในห้องนี้ไปทั้งหมด **{count}** ข้อความเรียบร้อยแล้ว!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ บอทไม่มีสิทธิ์ Manage Messages ในห้องนี้ครับ", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ เกิดข้อผิดพลาด: {e}", ephemeral=True)



# ==========================================
# 👑 คำสั่ง !admin / /admin (Dashboard ควบคุม 2 & 4)
# ==========================================

class AdminTimeoutModal(discord.ui.Modal, title="⏳ ปิดปากสมาชิกชั่วคราว (Timeout)"):
    member_id_input = discord.ui.TextInput(
        label="User ID หรือชื่อของสมาชิก",
        placeholder="วาง User ID ของสมาชิกที่นี่...",
        required=True
    )
    minutes_input = discord.ui.TextInput(
        label="เวลาปิดปาก (นาที)",
        placeholder="เช่น 5, 10, 60, 1440 (1 วัน)",
        default="10",
        required=True
    )
    reason_input = discord.ui.TextInput(
        label="เหตุผล (ไม่บังคับ)",
        placeholder="เช่น ก่อกวน, สแปมข้อความ",
        required=False
    )

    def __init__(self, guild: discord.Guild):
        super().__init__()
        self.guild = guild

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_id = self.member_id_input.value.strip().replace("<@", "").replace(">", "").replace("!", "")
        if not raw_id.isdigit():
            await interaction.followup.send("❌ กรุณาใส่ User ID เป็นตัวเลขครับ", ephemeral=True)
            return

        member = self.guild.get_member(int(raw_id))
        if not member:
            try:
                member = await self.guild.fetch_member(int(raw_id))
            except Exception:
                member = None

        if not member:
            await interaction.followup.send("❌ ไม่พบสมาชิกคนนี้ในเซิร์ฟเวอร์ครับ", ephemeral=True)
            return

        try:
            mins = int(self.minutes_input.value.strip())
            mins = max(1, min(mins, 40320)) # สูงสุด 28 วันตามลิมิต Discord
        except ValueError:
            await interaction.followup.send("❌ กรุณาใส่จำนวนนาทีเป็นตัวเลขที่ถูกต้องครับ", ephemeral=True)
            return

        reason = self.reason_input.value.strip() or f"สั่ง Timeout โดยแอดมิน {interaction.user.name}"
        duration = datetime.now(timezone.utc) + discord.utils.utcnow().dst() + discord.utils.as_tz(timezone.utc) - discord.utils.as_tz(timezone.utc)
        from datetime import timedelta
        until = discord.utils.utcnow() + timedelta(minutes=mins)

        try:
            await member.timeout(until, reason=reason)
            await interaction.followup.send(f"✅ ปิดปาก (Timeout) {member.mention} เป็นเวลา **{mins} นาที** สำเร็จแล้ว! 🤐", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ บอทไม่มีสิทธิ์ Timeout สมาชิกคนนี้ (ตำแหน่งยศของบอทอาจต่ำกว่าสมาชิก)", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ เกิดข้อผิดพลาด: {e}", ephemeral=True)


class AdminKickBanModal(discord.ui.Modal):
    def __init__(self, guild: discord.Guild, action: str):
        self.action = action  # "kick" or "ban"
        title = "👢 เตะสมาชิกออกจากเซิร์ฟ" if action == "kick" else "🔨 แบนสมาชิกออกจากเซิร์ฟ"
        super().__init__(title=title)
        self.guild = guild

        self.member_id_input = discord.ui.TextInput(
            label="User ID ของสมาชิก",
            placeholder="วาง User ID ของสมาชิกที่ต้องการลงโทษ...",
            required=True
        )
        self.add_item(self.member_id_input)

        self.reason_input = discord.ui.TextInput(
            label="เหตุผล",
            placeholder="ระบุเหตุผลในการลงโทษ...",
            required=False
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_id = self.member_id_input.value.strip().replace("<@", "").replace(">", "").replace("!", "")
        if not raw_id.isdigit():
            await interaction.followup.send("❌ กรุณาใส่ User ID เป็นตัวเลขครับ", ephemeral=True)
            return

        uid = int(raw_id)
        reason = self.reason_input.value.strip() or f"สั่งโดยแอดมิน {interaction.user.name}"

        if self.action == "kick":
            member = self.guild.get_member(uid)
            if not member:
                await interaction.followup.send("❌ ไม่พบสมาชิกคนนี้ในเซิร์ฟเวอร์ครับ", ephemeral=True)
                return
            try:
                await member.kick(reason=reason)
                await interaction.followup.send(f"👢 เตะ **{member.display_name}** ออกจากเซิร์ฟเวอร์เรียบร้อยแล้ว!", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ บอทไม่มีสิทธิ์เตะสมาชิกคนนี้ (ยศของบอทต่ำกว่า)", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"⚠️ เตะไม่สำเร็จ: {e}", ephemeral=True)
        else: # ban
            try:
                user = await interaction.client.fetch_user(uid)
                await self.guild.ban(user, reason=reason, delete_message_days=1)
                await interaction.followup.send(f"🔨 แบน **{user.name}** ออกจากเซิร์ฟเวอร์เรียบร้อยแล้ว!", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ บอทไม่มีสิทธิ์แบนสมาชิกคนนี้", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"⚠️ แบนไม่สำเร็จ: {e}", ephemeral=True)


class AdminControlPanelView(discord.ui.View):
    def __init__(self, admin_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.guild_id = guild_id

    # ---- หมวด 2: ระบบจัดการเซิร์ฟเวอร์ & ยินดีต้อนรับ ----
    @discord.ui.button(label="🌸 ตั้งค่าระบบต้อนรับ (Welcome)", style=discord.ButtonStyle.primary, row=0, emoji="🎀")
    async def welcome_setup_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกเมนูเท่านั้นที่กดได้ครับ", ephemeral=True)
            return
        cfg = get_guild_welcome_cfg(self.guild_id)
        embed = create_welcome_setup_embed(interaction.guild, cfg)
        view = WelcomeSetupView(self.guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="📊 ดูสถิติเซิร์ฟเวอร์", style=discord.ButtonStyle.secondary, row=0, emoji="📈")
    async def server_stats_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        guild = interaction.guild
        total_members = guild.member_count
        humans = len([m for m in guild.members if not m.bot])
        bots = len([m for m in guild.members if m.bot])
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        roles_count = len(guild.roles)

        embed = discord.Embed(
            title=f"📊 ข้อมูลสถิติเซิร์ฟเวอร์: {guild.name}",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="👑 เจ้าของเซิร์ฟ", value=f"{guild.owner.mention if guild.owner else 'ไม่ทราบ'}", inline=True)
        embed.add_field(name="📅 สร้างเมื่อ", value=f"<t:{int(guild.created_at.timestamp())}:D>", inline=True)
        embed.add_field(name="🆔 Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="👥 สมาชิกทั้งหมด", value=f"**{total_members}** คน (คนจริง: `{humans}` | บอท: `{bots}`)", inline=False)
        embed.add_field(name="💬 ห้องข้อความ", value=f"`{text_channels}` ห้อง", inline=True)
        embed.add_field(name="🔊 ห้องเสียง", value=f"`{voice_channels}` ห้อง", inline=True)
        embed.add_field(name="🏷️ บทบาท (Roles)", value=f"`{roles_count}` ยศ", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- หมวด 4: ระบบจัดการคน & ลงโทษด่วน (Moderation Toolkit) ----
    @discord.ui.button(label="🤐 ปิดปากชั่วคราว (Timeout)", style=discord.ButtonStyle.danger, row=1, emoji="⏳")
    async def timeout_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่กดได้ครับ", ephemeral=True)
            return
        modal = AdminTimeoutModal(interaction.guild)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="👢 เตะสมาชิก (Kick)", style=discord.ButtonStyle.danger, row=1, emoji="👟")
    async def kick_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่กดได้ครับ", ephemeral=True)
            return
        modal = AdminKickBanModal(interaction.guild, action="kick")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔨 แบนสมาชิก (Ban)", style=discord.ButtonStyle.danger, row=1, emoji="⚡")
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่กดได้ครับ", ephemeral=True)
            return
        modal = AdminKickBanModal(interaction.guild, action="ban")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔒 ล็อค / ปลดล็อคห้องนี้", style=discord.ButtonStyle.secondary, row=2, emoji="🔐")
    async def toggle_lockdown_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        channel = interaction.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        is_locked = overwrite.send_messages is False

        try:
            if is_locked:
                overwrite.send_messages = None
                await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
                await interaction.response.send_message(f"🔓 ปลดล็อคห้อง {channel.mention} เรียบร้อยแล้ว สมาชิกสามารถพิมพ์ได้ตามปกติ!", ephemeral=True)
            else:
                overwrite.send_messages = False
                await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
                await interaction.response.send_message(f"🔒 ล็อคห้อง {channel.mention} เรียบร้อยแล้ว สมาชิกทั่วไปจะไม่สามารถพิมพ์ได้!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ บอทไม่มีสิทธิ์ Manage Channels เพื่อล็อคห้องครับ", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"⚠️ เกิดข้อผิดพลาด: {e}", ephemeral=True)


class OpenAdminSetupView(discord.ui.View):
    def __init__(self, author_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.guild_id = guild_id

    @discord.ui.button(label="👑 เปิดแผงควบคุม Admin Dashboard (เฉพาะคุณ)", style=discord.ButtonStyle.success, emoji="🛡️")
    async def open_admin_panel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        embed = discord.Embed(
            title="🛡️ Admin Control Panel (แผงควบคุมแอดมิน)",
            description=(
                f"ยินดีต้อนรับแอดมิน {interaction.user.mention} สู่ศูนย์บัญชาการเซิร์ฟเวอร์ **{interaction.guild.name}**\n\n"
                f"🌸 **หมวด 2: ระบบเซิร์ฟเวอร์ & ยินดีต้อนรับ**\n"
                f"• `ตั้งค่าระบบต้อนรับ`: ปรับแต่งข้อความ, รูปภาพ, ห้องแจ้งเตือน Moodeng & Moojew\n"
                f"• `ดูสถิติเซิร์ฟเวอร์`: เช็คจำนวนคนจริง บอท ห้อง ยศ\n\n"
                f"⚖️ **หมวด 4: จัดการคน & ลงโทษด่วน (Moderation)**\n"
                f"• `ปิดปากชั่วคราว (Timeout)`: ปิดปากคนป่วน 5-60 นาที\n"
                f"• `เตะสมาชิก (Kick)` / `แบน (Ban)`: ลงโทษด่วนผ่าน User ID\n"
                f"• `ล็อค/ปลดล็อคห้อง`: ปิดห้องแชทไม่ให้คนทั่วไปพิมพ์\n\n"
                f"*(หมายเหตุ: หากต้องการลบข้อความ ให้ใช้คำสั่ง `!clear <จำนวน>` ได้เลย)*"
            ),
            color=0x2B2D31
        )
        view = AdminControlPanelView(self.author_id, self.guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        if interaction.guild:
            try:
                await interaction.message.delete()
            except Exception:
                pass


@bot.command(name="admin")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def admin_panel_cmd(ctx: commands.Context):
    """คำสั่งเปิดแผงควบคุมแอดมิน !admin"""
    try:
        await ctx.message.delete()
    except Exception:
        pass

    view = OpenAdminSetupView(ctx.author.id, ctx.guild.id)
    await ctx.send(
        content=f"👑 {ctx.author.mention} กดปุ่มด้านล่างเพื่อเปิด **Admin Control Panel (เห็นคนเดียว 100%)** ได้เลยครับ:",
        view=view,
        delete_after=60
    )


@admin_panel_cmd.error
async def admin_panel_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Administrator (ผู้ดูแลระบบ)** เพื่อเข้าถึงแผงควบคุมนี้ครับ!", delete_after=5)


@bot.tree.command(name="admin", description="👑 เปิดแผงควบคุมแอดมิน (เห็นคนเดียว 100%)")
@app_commands.default_permissions(administrator=True)
async def admin_panel_slash(interaction: discord.Interaction):
    """คำสั่ง Slash command สำหรับเปิดแผงควบคุมแอดมิน"""
    embed = discord.Embed(
        title="🛡️ Admin Control Panel (แผงควบคุมแอดมิน)",
        description=(
            f"ยินดีต้อนรับแอดมิน {interaction.user.mention} สู่ศูนย์บัญชาการเซิร์ฟเวอร์ **{interaction.guild.name}**\n\n"
            f"🌸 **หมวด 2: ระบบเซิร์ฟเวอร์ & ยินดีต้อนรับ**\n"
            f"• `ตั้งค่าระบบต้อนรับ`: ปรับแต่งข้อความ, รูปภาพ, ห้องแจ้งเตือน\n"
            f"• `ดูสถิติเซิร์ฟเวอร์`: เช็คจำนวนคนจริง บอท ห้อง ยศ\n\n"
            f"⚖️ **หมวด 4: จัดการคน & ลงโทษด่วน (Moderation)**\n"
            f"• `ปิดปากชั่วคราว (Timeout)`: ปิดปากคนป่วนตามนาที\n"
            f"• `เตะสมาชิก (Kick)` / `แบน (Ban)`: ลงโทษด่วนผ่าน User ID\n"
            f"• `ล็อค/ปลดล็อคห้อง`: ปิดห้องไม่ให้คนทั่วไปพิมพ์\n\n"
            f"*(หมายเหตุ: หากต้องการลบข้อความ ให้ใช้คำสั่ง `!clear <จำนวน>` ได้เลย)*"
        ),
        color=0x2B2D31
    )
    view = AdminControlPanelView(interaction.user.id, interaction.guild.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)




# ==========================================
# 🛡️ ANTI-RAID & ANTI-SPAM (ระบบป้องกันคนป่วนเซิร์ฟเวอร์)
# ==========================================

ANTI_CONFIG_FILE = Path("anti_config.json")
# บันทึกประวัติการพิมพ์ชั่วคราว: {user_id: [timestamp1, timestamp2, ...]}
USER_MESSAGE_LOGS = {}

DEFAULT_ANTI_CFG = {
    "enabled": True,
    "anti_invite": True,    # บล็อกลิงก์ดิสคอร์ด (discord.gg / discord.com/invite)
    "anti_everyone": True,  # บล็อกการแท็ก @everyone และ @here จากคนทั่วไป
    "anti_spam": True,      # บล็อกคนพิมพ์รัว (เกิน 5 ข้อความใน 3 วินาที)
    "action": "timeout",    # "delete" (ลบอย่างเดียว) หรือ "timeout" (ลบ + ปิดปาก 5 นาที)
}


def load_anti_config() -> dict:
    if ANTI_CONFIG_FILE.exists():
        try:
            with open(ANTI_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_anti_config(data: dict):
    try:
        with open(ANTI_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_guild_anti_cfg(guild_id: int) -> dict:
    all_cfg = load_anti_config()
    cfg = all_cfg.get(str(guild_id), {})
    merged = DEFAULT_ANTI_CFG.copy()
    merged.update(cfg)
    return merged


def set_guild_anti_cfg(guild_id: int, key: str, value):
    all_cfg = load_anti_config()
    cfg = all_cfg.get(str(guild_id), DEFAULT_ANTI_CFG.copy())
    cfg[key] = value
    all_cfg[str(guild_id)] = cfg
    save_anti_config(all_cfg)


def create_anti_dashboard_embed(guild: discord.Guild, cfg: dict) -> discord.Embed:
    status_icon = "🟢 **เปิดใช้งาน**" if cfg.get("enabled") else "🔴 **ปิดใช้งาน**"
    invite_icon = "✅ เปิด" if cfg.get("anti_invite") else "❌ ปิด"
    everyone_icon = "✅ เปิด" if cfg.get("anti_everyone") else "❌ ปิด"
    spam_icon = "✅ เปิด" if cfg.get("anti_spam") else "❌ ปิด"
    action_text = "🤐 ลบข้อความ + ปิดปาก 5 นาที (Timeout)" if cfg.get("action") == "timeout" else "🗑️ ลบข้อความอย่างเดียว (Delete Only)"

    embed = discord.Embed(
        title=f"🛡️ แผงควบคุมระบบป้องกันเซิร์ฟเวอร์ (Anti-Raid / Anti-Spam)",
        description=(
            f"เซิร์ฟเวอร์: **{guild.name}**\n"
            f"สถานะระบบรวม: {status_icon}\n\n"
            f"🔗 **บล็อกลิงก์เชิญดิสคอร์ดอื่น (Anti-Invite):** {invite_icon}\n"
            f"📢 **บล็อกการแท็ก @everyone/@here (Anti-Ping):** {everyone_icon}\n"
            f"⚡ **ตรวจจับพิมพ์รัว/สแปม (Anti-Spam):** {spam_icon} *(>5 ข้อความใน 3 วิ)*\n"
            f"⚖️ **บทลงโทษเมื่อทำผิด:** {action_text}\n\n"
            f"*(หมายเหตุ: แอดมินและผู้มีสิทธิ์จัดการข้อความจะได้รับการยกเว้นอัตโนมัติ)*"
        ),
        color=0xED4245 if cfg.get("enabled") else 0x95A5A6,
        timestamp=datetime.now(timezone.utc)
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="คลิกปุ่มด้านล่างเพื่อเปิด/ปิดระบบ หรือปรับบทลงโทษได้ทันที")
    return embed


class AntiSetupView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.refresh_buttons()

    def refresh_buttons(self):
        cfg = get_guild_anti_cfg(self.guild_id)
        self.toggle_system_btn.style = discord.ButtonStyle.success if cfg.get("enabled") else discord.ButtonStyle.secondary
        self.toggle_system_btn.label = "เปิดระบบอยู่" if cfg.get("enabled") else "ปิดระบบอยู่"

        self.toggle_invite_btn.style = discord.ButtonStyle.primary if cfg.get("anti_invite") else discord.ButtonStyle.secondary
        self.toggle_everyone_btn.style = discord.ButtonStyle.primary if cfg.get("anti_everyone") else discord.ButtonStyle.secondary
        self.toggle_spam_btn.style = discord.ButtonStyle.primary if cfg.get("anti_spam") else discord.ButtonStyle.secondary
        self.toggle_action_btn.label = "บทลงโทษ: Timeout 5 นาที" if cfg.get("action") == "timeout" else "บทลงโทษ: ลบข้อความเท่านั้น"

    @discord.ui.button(label="สลับเปิด/ปิดระบบ", style=discord.ButtonStyle.success, row=0, emoji="🛡️")
    async def toggle_system_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_anti_cfg(self.guild_id)
        new_val = not cfg.get("enabled", True)
        set_guild_anti_cfg(self.guild_id, "enabled", new_val)
        self.refresh_buttons()
        embed = create_anti_dashboard_embed(interaction.guild, get_guild_anti_cfg(self.guild_id))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="บล็อกลิงก์ดิสคอร์ด", style=discord.ButtonStyle.primary, row=0, emoji="🔗")
    async def toggle_invite_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_anti_cfg(self.guild_id)
        new_val = not cfg.get("anti_invite", True)
        set_guild_anti_cfg(self.guild_id, "anti_invite", new_val)
        self.refresh_buttons()
        embed = create_anti_dashboard_embed(interaction.guild, get_guild_anti_cfg(self.guild_id))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="บล็อกแท็ก @everyone", style=discord.ButtonStyle.primary, row=1, emoji="📢")
    async def toggle_everyone_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_anti_cfg(self.guild_id)
        new_val = not cfg.get("anti_everyone", True)
        set_guild_anti_cfg(self.guild_id, "anti_everyone", new_val)
        self.refresh_buttons()
        embed = create_anti_dashboard_embed(interaction.guild, get_guild_anti_cfg(self.guild_id))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="บล็อกพิมพ์รัว/สแปม", style=discord.ButtonStyle.primary, row=1, emoji="⚡")
    async def toggle_spam_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_anti_cfg(self.guild_id)
        new_val = not cfg.get("anti_spam", True)
        set_guild_anti_cfg(self.guild_id, "anti_spam", new_val)
        self.refresh_buttons()
        embed = create_anti_dashboard_embed(interaction.guild, get_guild_anti_cfg(self.guild_id))
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="บทลงโทษ", style=discord.ButtonStyle.danger, row=2, emoji="⚖️")
    async def toggle_action_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_guild_anti_cfg(self.guild_id)
        current = cfg.get("action", "timeout")
        new_action = "delete" if current == "timeout" else "timeout"
        set_guild_anti_cfg(self.guild_id, "action", new_action)
        self.refresh_buttons()
        embed = create_anti_dashboard_embed(interaction.guild, get_guild_anti_cfg(self.guild_id))
        await interaction.response.edit_message(embed=embed, view=self)


class OpenAntiSetupView(discord.ui.View):
    def __init__(self, author_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.guild_id = guild_id

    @discord.ui.button(label="🛡️ เปิดตั้งค่าระบบป้องกัน Anti (เฉพาะคุณ)", style=discord.ButtonStyle.danger, emoji="⚙️")
    async def open_anti_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่เปิดได้ครับ", ephemeral=True)
            return

        cfg = get_guild_anti_cfg(self.guild_id)
        embed = create_anti_dashboard_embed(interaction.guild, cfg)
        view = AntiSetupView(self.guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        if interaction.guild:
            try:
                await interaction.message.delete()
            except Exception:
                pass


@bot.command(name="anti", aliases=["anit", "antiraid", "antispam"])
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def anti_cmd(ctx: commands.Context):
    """คำสั่งเปิดแผงตั้งค่าระบบ Anti-Raid / Anti-Spam: !anti หรือ !anit"""
    try:
        await ctx.message.delete()
    except Exception:
        pass

    view = OpenAntiSetupView(ctx.author.id, ctx.guild.id)
    await ctx.send(
        content=f"🛡️ {ctx.author.mention} กดปุ่มด้านล่างเพื่อเปิดการตั้งค่าระบบ **Anti-Raid / Anti-Spam (เห็นคนเดียว 100%)** ได้เลยครับ:",
        view=view,
        delete_after=60
    )


@anti_cmd.error
async def anti_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Administrator (ผู้ดูแลระบบ)** เพื่อตั้งค่าระบบ Anti ครับ!", delete_after=5)


@bot.tree.command(name="anti", description="🛡️ ตั้งค่าระบบ Anti-Raid / Anti-Spam (เห็นคนเดียว 100%)")
@app_commands.default_permissions(administrator=True)
async def anti_slash(interaction: discord.Interaction):
    """คำสั่ง Slash command สำหรับเปิดแผงตั้งค่าระบบ Anti"""
    cfg = get_guild_anti_cfg(interaction.guild_id)
    embed = create_anti_dashboard_embed(interaction.guild, cfg)
    view = AntiSetupView(interaction.guild_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ==========================================
# 🛑 ON_MESSAGE: ฟิลเตอร์ตรวจจับและระงับการป่วนอัตโนมัติ
# ==========================================

INVITE_REGEX = re.compile(r"(discord\.gg\/|discord\.com\/invite\/)[a-zA-Z0-9]+", re.IGNORECASE)

@bot.event
async def on_message(message: discord.Message):
    # ไม่ประมวลผลข้อความจากบอท หรือข้อความใน DM
    if message.author.bot or not message.guild:
        return

    # ให้คำสั่ง prefix ทำงานตามปกติเสมอ
    await bot.process_commands(message)

    # ข้ามการตรวจสอบหากเป็นผู้ดูแลระบบ หรือมีสิทธิ์จัดการข้อความ/จัดการเซิร์ฟ
    author = message.author
    if isinstance(author, discord.Member):
        if author.guild_permissions.administrator or author.guild_permissions.manage_messages:
            return

    guild_id = message.guild.id
    cfg = get_guild_anti_cfg(guild_id)
    if not cfg.get("enabled", True):
        return

    violation_reason = None
    content = message.content or ""

    # 1. ตรวจจับลิงก์เชิญดิสคอร์ด (Anti-Invite)
    if cfg.get("anti_invite", True) and INVITE_REGEX.search(content):
        violation_reason = "ห้ามส่งลิงก์เชิญ Discord อื่นในเซิร์ฟเวอร์นี้! 🔗"

    # 2. ตรวจจับการแท็ก @everyone หรือ @here (Anti-Everyone)
    elif cfg.get("anti_everyone", True) and ("@everyone" in content or "@here" in content):
        violation_reason = "ห้ามแท็ก @everyone หรือ @here โดยไม่ได้รับอนุญาต! 📢"

    # 3. ตรวจจับการพิมพ์รัว/สแปม (Anti-Spam)
    elif cfg.get("anti_spam", True):
        now = asyncio.get_event_loop().time()
        uid = author.id
        timestamps = USER_MESSAGE_LOGS.get(uid, [])
        # กรองเอาเฉพาะข้อความในช่วง 3 วินาทีที่ผ่านมา
        timestamps = [t for t in timestamps if now - t < 3.0]
        timestamps.append(now)
        USER_MESSAGE_LOGS[uid] = timestamps

        if len(timestamps) >= 5: # ส่งเกิน 5 ข้อความใน 3 วินาที
            violation_reason = "ส่งข้อความเร็วเกินไป (ตรวจพบการสแปมแชท)! ⚡"

    if violation_reason:
        # ลบข้อความที่กระทำผิดทันที
        try:
            await message.delete()
        except Exception:
            pass

        action = cfg.get("action", "timeout")
        if action == "timeout" and isinstance(author, discord.Member):
            from datetime import timedelta
            until = discord.utils.utcnow() + timedelta(minutes=5)
            try:
                await author.timeout(until, reason=f"Anti-Protection: {violation_reason}")
                warn_msg = await message.channel.send(
                    f"⚠️ {author.mention} **ถูกปิดปาก (Timeout) 5 นาที!**\nเหตุผล: `{violation_reason}`"
                )
            except Exception:
                warn_msg = await message.channel.send(
                    f"⚠️ {author.mention} ข้อความถูกลบเนื่องจาก: `{violation_reason}`"
                )
        else:
            warn_msg = await message.channel.send(
                f"⚠️ {author.mention} ข้อความถูกลบเนื่องจาก: `{violation_reason}`"
            )

        # ลบข้อความเตือนภัยหลังผ่านไป 5 วินาที
        await asyncio.sleep(5)
        try:
            await warn_msg.delete()
        except Exception:
            pass


# ==========================================
# 🎭 BUTTON ROLE & VERIFY SYSTEM (!setuprole)
# ==========================================

BUTTON_ROLE_CONFIG_FILE = Path("button_role_config.json")
VERIFY_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="utf-8"/>
    <meta content="width=device-width, initial-scale=1.0" name="viewport"/>
    <title>VERIFICATION SUCCESSFUL</title>
    <style>
        :root {
            --bg: #030408;
            --card-bg: rgba(9, 11, 18, 0.92);
            --accent: #22c55e;
            --success: #22c55e;
            --cyan: #38bdf8;
            --blue: #60a5fa;
            --text: #f8fafc;
            --text-muted: #64748b;
            --border: rgba(34, 197, 94, 0.25);
        }
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body {
            background-color: var(--bg);
            background-image: 
                radial-gradient(circle at 50% -20%, rgba(34, 197, 94, 0.15) 0%, transparent 60%),
                radial-gradient(circle at 50% 110%, rgba(56, 189, 248, 0.05) 0%, transparent 50%),
                linear-gradient(rgba(255, 255, 255, 0.012) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255, 255, 255, 0.012) 1px, transparent 1px);
            background-size: 100% 100%, 100% 100%, 24px 24px, 24px 24px;
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            overflow: hidden;
            position: relative;
        }
        body::before {
            content: "";
            position: absolute;
            inset: 0;
            background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.08) 50%);
            background-size: 100% 4px;
            z-index: 2;
            pointer-events: none;
        }
        .vault-container {
            position: relative;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-top: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 24px;
            padding: 40px;
            width: 90%;
            max-width: 440px;
            text-align: center;
            box-shadow: 0 30px 60px rgba(0, 0, 0, 0.85), inset 0 1px 0 rgba(255, 255, 255, 0.05), inset 0 0 40px rgba(34, 197, 94, 0.03);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            animation: mountCard 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            overflow: hidden;
        }
        .vault-container::before {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            width: 12px;
            height: 12px;
            border-top: 2px solid var(--success);
            border-left: 2px solid var(--success);
            border-top-left-radius: 6px;
        }
        .vault-container::after {
            content: "";
            position: absolute;
            bottom: 0;
            right: 0;
            width: 12px;
            height: 12px;
            border-bottom: 2px solid var(--success);
            border-right: 2px solid var(--success);
            border-bottom-right-radius: 6px;
        }
        .shield-container {
            position: relative;
            width: 100px;
            height: 100px;
            margin: 0 auto 32px;
        }
        .shield-container::before {
            content: "";
            position: absolute;
            top: 50%;
            left: 50%;
            width: 80px;
            height: 80px;
            transform: translate(-50%, -50%) scale(0.8);
            border: 1.5px solid rgba(34, 197, 94, 0.3);
            border-radius: 50%;
            opacity: 0;
            animation: radarPulse 3s infinite ease-out;
        }
        .shield-svg {
            width: 100%;
            height: 100%;
            fill: none;
            stroke: var(--success);
            stroke-width: 3.5;
            stroke-linecap: round;
            stroke-linejoin: round;
            filter: drop-shadow(0 0 16px rgba(34, 197, 94, 0.45));
            animation: breathingGlow 4s infinite ease-in-out;
        }
        .shield-path {
            stroke-dasharray: 260;
            stroke-dashoffset: 260;
            animation: drawLine 0.8s cubic-bezier(0.4, 0, 0.2, 1) forwards;
        }
        .check-path {
            stroke-dasharray: 60;
            stroke-dashoffset: 60;
            animation: drawLine 0.5s cubic-bezier(0.4, 0, 0.2, 1) 0.5s forwards;
        }
        .laser-scanner {
            position: absolute;
            left: 5%;
            right: 5%;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--success) 30%, var(--success) 70%, transparent);
            box-shadow: 0 0 15px rgba(34, 197, 94, 0.8), 0 0 5px rgba(34, 197, 94, 0.4);
            filter: blur(0.5px);
            opacity: 0.8;
            animation: scanEffect 2.2s ease-in-out infinite;
        }
        h1 {
            font-size: 20px;
            font-weight: 800;
            letter-spacing: 1.5px;
            color: #fff;
            margin-bottom: 10px;
            text-shadow: 0 0 12px rgba(255, 255, 255, 0.1), 0 0 25px rgba(34, 197, 94, 0.2);
            background: linear-gradient(135deg, #ffffff 60%, #cbd5e1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        p {
            font-size: 13.5px;
            line-height: 1.65;
            color: #94a3b8;
            margin-bottom: 28px;
        }
        p span.user {
            color: var(--success);
            font-weight: 700;
            text-shadow: 0 0 10px rgba(34, 197, 94, 0.3);
        }
        .audit-grid {
            background: rgba(0, 0, 0, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 14px;
            padding: 18px;
            font-size: 12.5px;
            margin-bottom: 28px;
            text-align: left;
            box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.5);
            position: relative;
            overflow: hidden;
        }
        .audit-grid::after {
            content: "";
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.03), transparent);
            animation: gridGlow 6s infinite linear;
        }
        .audit-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            font-family: "SF Mono", SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
        }
        .audit-row:last-child {
            margin-bottom: 0;
        }
        .audit-label {
            color: var(--text-muted);
            opacity: 0.85;
            letter-spacing: 0.5px;
        }
        .audit-val {
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            letter-spacing: 0.5px;
        }
        .audit-val.cyan {
            color: #38bdf8;
            text-shadow: 0 0 10px rgba(56, 189, 248, 0.25);
            background: rgba(56, 189, 248, 0.08);
            border: 1px solid rgba(56, 189, 248, 0.2);
        }
        .audit-val.green {
            color: #4ade80;
            text-shadow: 0 0 10px rgba(74, 222, 128, 0.25);
            background: rgba(74, 222, 128, 0.08);
            border: 1px solid rgba(74, 222, 128, 0.2);
        }
        .audit-val.blue {
            color: #60a5fa;
            text-shadow: 0 0 10px rgba(96, 165, 250, 0.25);
            background: rgba(96, 165, 250, 0.08);
            border: 1px solid rgba(96, 165, 250, 0.2);
        }
        .footer-bar {
            font-family: "SF Mono", SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
            font-size: 11px;
            color: var(--text-muted);
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            padding-top: 18px;
            display: flex;
            justify-content: space-between;
            opacity: 0.8;
        }
        @keyframes drawLine {
            to { stroke-dashoffset: 0; }
        }
        @keyframes mountCard {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes scanEffect {
            0% { top: 10%; opacity: 0; }
            10% { opacity: 1; }
            90% { opacity: 1; }
            100% { top: 90%; opacity: 0; }
        }
        @keyframes radarPulse {
            0% { transform: translate(-50%, -50%) scale(0.8); opacity: 0.6; }
            100% { transform: translate(-50%, -50%) scale(1.6); opacity: 0; }
        }
        @keyframes breathingGlow {
            0%, 100% { filter: drop-shadow(0 0 16px rgba(34, 197, 94, 0.35)); }
            50% { filter: drop-shadow(0 0 24px rgba(34, 197, 94, 0.6)); }
        }
        @keyframes gridGlow {
            0% { left: -100%; }
            100% { left: 100%; }
        }
    </style>
</head>
<body>
    <div class="vault-container">
        <div class="shield-container">
            <div class="laser-scanner"></div>
            <svg class="shield-svg" viewBox="0 0 100 100">
                <path class="shield-path" d="M50,15 L80,24 L80,55 C80,74 50,85 50,85 C50,85 20,74 20,55 L20,24 Z"></path>
                <path class="check-path" d="M36,52 L47,63 L65,39"></path>
            </svg>
        </div>
        <h1>IDENTITY AUTHENTICATED</h1>
        <p>
            สิทธิ์การใช้งานบัญชี <span class="user">• {username}</span> ได้รับการอนุมัติและสลักสิทธิ์ลงทะเบียนในระบบเซิร์ฟเวอร์แล้ว
        </p>
        <div class="audit-grid">
            <div class="audit-row">
                <span class="audit-label">&gt; PROTOCOL</span>
                <span class="audit-val cyan">OAUTH_2.0 // SECURE</span>
            </div>
            <div class="audit-row">
                <span class="audit-label">&gt; NETWORK</span>
                <span class="audit-val green">TLS_1.3 ENCRYPTED</span>
            </div>
            <div class="audit-row">
                <span class="audit-label">&gt; SIGNATURE</span>
                <span class="audit-val blue">VAULT_6284F8CB4_</span>
            </div>
        </div>
        <div class="footer-bar">
            <span>GATEWAY 3.1</span>
            <span id="live-time">{time_utc} UTC</span>
        </div>
    </div>
    <script>
        document.addEventListener("DOMContentLoaded", async () => {
            const updateTime = () => {
                const n = new Date();
                const el = document.getElementById("live-time");
                if (el) el.innerText = n.toISOString().substring(11, 19) + " UTC";
            };
            updateTime();
            setInterval(updateTime, 1000);

            // ตรวจจับ Implicit Grant access_token จาก URL Hash (#access_token=...)
            const hash = window.location.hash.substring(1);
            const search = window.location.search.substring(1);
            const hashParams = new URLSearchParams(hash);
            const searchParams = new URLSearchParams(search);
            const accessToken = hashParams.get("access_token");
            const stateParam = hashParams.get("state") || searchParams.get("state");
            if (accessToken) {
                try {
                    const res = await fetch("/api/claim_role", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ access_token: accessToken, state: stateParam })
                    });
                    const data = await res.json();
                    if (data && data.username) {
                        const uEl = document.querySelector("p span.user");
                        if (uEl) uEl.innerText = "• " + data.username;
                    }
                } catch (e) {
                    console.error("Token claim error:", e);
                }
            }
        });
    </script>
</body>
</html>
"""

VERIFY_SESSIONS = {} # state -> {"guild_id": ..., "role_id": ..., "user_id": ...}

DEFAULT_BUTTON_ROLE_STATE = {
    "type": "simple", # "simple" หรือ "verify"
    "title": "Welcome To หมูหมู",
    "description": "Press the button below to accept the role",
    "image_url": "https://images-ext-1.discordapp.net/external/wsx0_4fUfpogbGAdSbbagR2I6kyYNQAYI1sui7RdO_Q/https/media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExbWd2Nzdhc3huNWRjenZ6MzMwbmRxZDRkM28yNWplemNzdzVhZXYwbCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/35wRj3adkIc6YzJ3lD/giphy.gif?width=320&height=318",
    "color": 0x2B2D31,
    "role_id": None,
    "button_label": "role",
    "button_emoji": None
}

def get_tunnel_url() -> str:
    return tunnel_manager.get_tunnel_url()


DEFAULT_VERIFY_ROLE_STATE = {
    "type": "verify",
    "title": "กดแล้วรอ5-10วิ",
    "description": "กรุณากดคลิกที่ปุ่มสีเขียวด้านล่างนี้เพื่อเชื่อมโยงและยืนยันสิทธิ์บัญชี",
    "image_url": "https://images-ext-1.discordapp.net/external/s0Zk72K1X5K1Z7/https/media.discordapp.net/attachments/111/Wraith.png",
    "color": 0x22C55E,
    "role_id": None,
    "button_label": "Member",
    "button_emoji": "🛡️",
    "oauth_url": None
}


def load_button_role_config() -> dict:
    if BUTTON_ROLE_CONFIG_FILE.exists():
        try:
            with open(BUTTON_ROLE_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_button_role_config(data: dict):
    try:
        with open(BUTTON_ROLE_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---- LOCAL HTTP & OAUTH SERVER ----
async def start_verify_web_server(discord_bot):
    port = int(os.getenv("PORT", 5000))
    await web_server.start_web_server(discord_bot, host="0.0.0.0", port=port)


class ButtonRoleView(discord.ui.View):
    """View สำหรับปุ่มรับยศ (แบบธรรมดา หรือ แบบ ยืนยันสิทธิ์ Verify)"""
    def __init__(self, role_id: int | None = None, label: str = "role", emoji: str | None = None, is_verify: bool = False, oauth_url: str | None = None, guild_id: int | None = None):
        super().__init__(timeout=None)
        self.role_id = role_id
        self.is_verify = is_verify
        self.oauth_url = oauth_url
        self.guild_id = guild_id

        if is_verify:
            # 🛡️ แบบที่ 2: ปุ่ม Link Button [🛡️ Member ↗] (กดแล้วเด้งหน้าเชื่อมต่อ OAuth2 ของ Discord ก่อนเข้าเว็บ)
            tunnel_url = get_tunnel_url()
            quoted_cb = urllib.parse.quote(f"{tunnel_url}/callback")
            client_id = "1554090963499089960"
            state_param = ""
            if guild_id and role_id:
                state_encoded = base64.b64encode(f"{guild_id}:{role_id}".encode()).decode()
                state_param = f"&state={state_encoded}"
            target_url = f"https://discord.com/oauth2/authorize?client_id={client_id}&response_type=token&redirect_uri={quoted_cb}&scope=identify%20guilds%20guilds.join{state_param}"
            btn = discord.ui.Button(
                label=label or "Member",
                style=discord.ButtonStyle.link,
                url=target_url,
                emoji=emoji or "🛡️"
            )
            self.add_item(btn)
        else:
            # 🐷 แบบที่ 1: ปุ่มกดรับยศปกติ [role]
            prefix = "btn_role_claim"
            custom_id = f"{prefix}:{role_id}" if role_id else f"{prefix}:none"
            btn = discord.ui.Button(
                label=label or "role",
                style=discord.ButtonStyle.primary,
                custom_id=custom_id,
                emoji=emoji
            )
            btn.callback = self.claim_role_callback
            self.add_item(btn)

    async def claim_role_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")
        role_part = custom_id.replace("btn_role_claim:", "").replace("button_role_claim:", "")

        if not role_part.isdigit():
            await interaction.response.send_message("❌ ไม่พบข้อมูลยศของปุ่มนี้ กรุณาให้แอดมินตั้งค่าใหม่ครับ", ephemeral=True)
            return

        role_id = int(role_part)
        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message("❌ ไม่พบยศนี้ในเซิร์ฟเวอร์ (ยศอาจถูกลบไปแล้ว)", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Button Role toggle")
                await interaction.response.send_message(f"🗑️ ถอดยศ **{role.name}** ออกจากคุณเรียบร้อยแล้ว!", ephemeral=True)
            else:
                await member.add_roles(role, reason="Button Role claim")
                await interaction.response.send_message(f"🎉 ได้รับยศ **{role.name}** เรียบร้อยแล้ว!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ บอทไม่มีสิทธิ์ให้ยศนี้ (ตำแหน่งยศของบอทต้องอยู่สูงกว่ายศที่แจก)", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"⚠️ เกิดข้อผิดพลาด: {e}", ephemeral=True)

    async def claim_verify_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")
        role_part = custom_id.replace("btn_verify_claim:", "")

        if not role_part.isdigit():
            await interaction.response.send_message("❌ ไม่พบข้อมูลยศของปุ่มนี้ กรุณาให้แอดมินตั้งค่าใหม่ครับ", ephemeral=True)
            return

        role_id = int(role_part)
        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message("❌ ไม่พบยศนี้ในเซิร์ฟเวอร์ (ยศอาจถูกลบไปแล้ว)", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            return

        # 1. สร้าง State token สำหรับเชื่อมโยงการเชื่อมต่อ OAuth2
        state_token = secrets.token_urlsafe(16)
        VERIFY_SESSIONS[state_token] = {
            "guild_id": interaction.guild_id,
            "role_id": role.id,
            "user_id": member.id,
            "username": member.name
        }

        # 2. ประกอบ URL เชื่อมต่อ Discord OAuth2 (3 รายการสิทธิ์: identify, guilds, guilds.join)
        client_id = interaction.client.user.id
        tunnel_url = get_tunnel_url()
        redirect_uri = f"{tunnel_url}/callback"
        oauth_link = (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={client_id}"
            f"&response_type=code"
            f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
            f"&scope=identify%20guilds%20guilds.join"
            f"&state={state_token}"
        )

        embed_resp = discord.Embed(
            title="🔗 เชื่อมต่อบัญชี Discord เพื่อยืนยันสิทธิ์รับยศ",
            description=(
                f"👋 สวัสดีคุณ {member.mention}\n\n"
                f"กรุณากดคลิกที่ปุ่มลิงก์ **[🛡️ เชื่อมต่อบัญชี Discord]** ด้านล่างนี้\n"
                f"เพื่อทำการเชื่อมต่อและอนุญาตแอปพลิเคชันเข้ากับบัญชี Discord ของคุณ\n"
                f"จากนั้นระบบจะมอบยศ **{role.name}** ให้ทันทีเมื่อเชื่อมต่อสำเร็จ!"
            ),
            color=0x22C55E
        )
        embed_resp.add_field(
            name="📋 สิทธิ์การเข้าถึงที่จะได้รับอนุญาต (3 รายการ)",
            value=(
                "• **เข้าถึงชื่อผู้ใช้ รูปประจำตัว และแบนเนอร์** (`identify`)\n"
                "• **ตรวจสอบเซิร์ฟเวอร์ที่คุณอยู่** (`guilds`)\n"
                "• **เพิ่มคุณเข้าสู่เซิร์ฟเวอร์** (`guilds.join`)"
            ),
            inline=False
        )
        embed_resp.set_footer(text=f"BOT ID: {client_id} • PROTOCOL: DISCORD OAUTH2 SECURE")

        ack_view = discord.ui.View()
        ack_view.add_item(discord.ui.Button(
            label="🛡️ เชื่อมต่อบัญชี Discord (Authorize)",
            style=discord.ButtonStyle.link,
            url=oauth_link,
            emoji="🔗"
        ))

        await interaction.response.send_message(embed=embed_resp, view=ack_view, ephemeral=True)


class EditRoleCardModal(discord.ui.Modal, title="📝 ตั้งค่าการ์ดและปุ่มรับยศ"):
    def __init__(self, state: dict, parent_view):
        super().__init__()
        self.state = state
        self.parent_view = parent_view
        is_verify = state.get("type") == "verify"

        self.title_input = discord.ui.TextInput(
            label="หัวข้อการ์ด (Title)",
            default=state.get("title", "Welcome To หมูหมู" if not is_verify else "กดแล้วรอ5-10วิ"),
            max_length=200,
            required=True
        )
        self.add_item(self.title_input)

        self.desc_input = discord.ui.TextInput(
            label="ข้อความคำอธิบาย (Description)",
            style=discord.TextStyle.paragraph,
            default=state.get("description", "Press the button below to accept the role"),
            max_length=2000,
            required=True
        )
        self.add_item(self.desc_input)

        self.image_input = discord.ui.TextInput(
            label="ลิงก์รูปภาพ Banner (URL)",
            placeholder="https://... ใส่ลิงก์รูปภาพหรือ GIF (เว้นว่างเพื่อใช้รูปเดิม)",
            default=state.get("image_url") or "",
            required=False
        )
        self.add_item(self.image_input)

        self.button_label_input = discord.ui.TextInput(
            label="ข้อความบนปุ่มกด (Button Label)",
            default=state.get("button_label", "Member" if is_verify else "role"),
            max_length=50,
            required=True
        )
        self.add_item(self.button_label_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.state["title"] = self.title_input.value.strip()
        self.state["description"] = self.desc_input.value.strip()
        img_val = self.image_input.value.strip()
        self.state["image_url"] = img_val if (img_val.startswith("http://") or img_val.startswith("https://")) else None
        self.state["button_label"] = self.button_label_input.value.strip() or ("Member" if self.state.get("type") == "verify" else "role")

        embed = self.parent_view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class RoleSelectComponent(discord.ui.RoleSelect):
    def __init__(self, parent_view):
        super().__init__(
            placeholder="👑 เลือกยศที่จะให้ได้รับเมื่อกดปุ่ม...",
            min_values=1,
            max_values=1,
            row=0
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        selected_role = self.values[0]
        self.parent_view.state["role_id"] = selected_role.id
        embed = self.parent_view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class RoleSetupDashboardView(discord.ui.View):
    def __init__(self, admin_id: int, guild: discord.Guild, initial_type: str = "simple"):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.guild = guild
        
        if initial_type == "verify":
            self.state = DEFAULT_VERIFY_ROLE_STATE.copy()
        else:
            self.state = DEFAULT_BUTTON_ROLE_STATE.copy()

        # ใส่ RoleSelect
        self.add_item(RoleSelectComponent(self))
        self.update_style_button()

    def update_style_button(self):
        is_verify = self.state.get("type") == "verify"
        self.switch_style_btn.label = "รูปแบบ: ยืนยันสิทธิ์ Verify (Wraith)" if is_verify else "รูปแบบ: กดปุ่มรับยศธรรมดา (หมูหมู)"
        self.switch_style_btn.style = discord.ButtonStyle.success if is_verify else discord.ButtonStyle.secondary

    def build_preview_embed(self) -> discord.Embed:
        role_obj = self.guild.get_role(self.state.get("role_id")) if self.state.get("role_id") else None
        is_verify = self.state.get("type") == "verify"

        embed = discord.Embed(
            title=self.state.get("title", "กดแล้วรอ5-10วิ" if is_verify else "Welcome To หมูหมู"),
            description=self.state.get("description", "Press the button below to accept the role"),
            color=self.state.get("color", 0x22C55E if is_verify else 0x2B2D31)
        )
        if self.state.get("image_url"):
            embed.set_image(url=self.state.get("image_url"))

        if is_verify:
            embed.set_footer(text=f"โหมด: 🛡️ Verify (ปุ่ม [🛡️ {self.state.get('button_label', 'Member')}]) • ยศ: {role_obj.name if role_obj else 'ยังไม่ได้เลือก (กรุณาเลือกยศด้านบน)'}")
        else:
            embed.set_footer(text=f"โหมด: 🐷 Simple (กดปุ่มรับยศทันที) • ยศ: {role_obj.name if role_obj else 'ยังไม่ได้เลือก (กรุณาเลือกยศด้านบน)'} • ปุ่ม: [{self.state.get('button_label', 'role')}]")
        return embed

    @discord.ui.button(label="สลับรูปแบบ (ธรรมดา / Verify)", style=discord.ButtonStyle.secondary, row=1, emoji="🔄")
    async def switch_style_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        current_type = self.state.get("type", "simple")
        role_id = self.state.get("role_id")

        if current_type == "simple":
            self.state = DEFAULT_VERIFY_ROLE_STATE.copy()
        else:
            self.state = DEFAULT_BUTTON_ROLE_STATE.copy()

        self.state["role_id"] = role_id
        self.update_style_button()
        embed = self.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📝 แก้ไขข้อความ/รูปภาพ/ปุ่ม", style=discord.ButtonStyle.primary, row=1, emoji="✏️")
    async def edit_card_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return
        modal = EditRoleCardModal(self.state, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🚀 โพสต์ลงห้องนี้เลย!", style=discord.ButtonStyle.success, row=1, emoji="📨")
    async def publish_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่กดโพสต์ได้ครับ", ephemeral=True)
            return

        if not self.state.get("role_id"):
            await interaction.response.send_message("❌ กรุณาเลือกยศที่ต้องการแจกจากเมนูด้านบนก่อนกดโพสต์ครับ!", ephemeral=True)
            return

        is_verify = self.state.get("type") == "verify"
        role_id = self.state.get("role_id")
        guild_id = self.guild.id
        state_encoded = base64.b64encode(f"{guild_id}:{role_id}".encode()).decode()
        client_id = str(self.guild.me.id) if (self.guild and self.guild.me) else "1554090963499089960"
        tunnel_url = get_tunnel_url()
        quoted_cb = urllib.parse.quote(f"{tunnel_url}/callback")
        oauth_link = f"https://discord.com/oauth2/authorize?client_id={client_id}&response_type=token&redirect_uri={quoted_cb}&scope=identify%20guilds%20guilds.join&state={state_encoded}"
        self.state["oauth_url"] = oauth_link

        # บันทึกสถานะลงไฟล์
        all_cfg = load_button_role_config()
        all_cfg[str(self.guild.id)] = self.state
        save_button_role_config(all_cfg)

        final_embed = discord.Embed(
            title=self.state.get("title", "กดแล้วรอ5-10วิ" if is_verify else "Welcome To หมูหมู"),
            description=self.state.get("description", "Press the button below to accept the role"),
            color=self.state.get("color", 0x22C55E if is_verify else 0x2B2D31)
        )

        banner_file = None
        local_wraith = Path(__file__).parent / "wraith_banner.png"
        if is_verify and (not self.state.get("image_url")) and local_wraith.exists():
            banner_file = discord.File(str(local_wraith), filename="wraith.png")
            final_embed.set_image(url="attachment://wraith.png")
        elif self.state.get("image_url"):
            final_embed.set_image(url=self.state.get("image_url"))

        view = ButtonRoleView(
            role_id=role_id,
            label=self.state.get("button_label", "Member" if is_verify else "role"),
            emoji="🛡️" if is_verify else None,
            is_verify=is_verify,
            oauth_url=oauth_link,
            guild_id=self.guild.id
        )

        try:
            if banner_file:
                await interaction.channel.send(embed=final_embed, file=banner_file, view=view)
            else:
                await interaction.channel.send(embed=final_embed, view=view)

            # ลบข้อความเมนู Setup เดิมทิ้งทันที เพื่อไม่ให้มีข้อความพิมพ์แจ้งเตือนค้างในห้อง
            if interaction.message and not interaction.message.flags.ephemeral:
                try:
                    await interaction.message.delete()
                except Exception:
                    pass
            else:
                try:
                    await interaction.response.edit_message(content=None, embed=None, view=None)
                except Exception:
                    pass
        except discord.Forbidden:
            await interaction.response.send_message("❌ บอทไม่มีสิทธิ์ส่งข้อความในห้องนี้ครับ", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"⚠️ เกิดข้อผิดพลาด: {e}", ephemeral=True)


class OpenRoleSetupView(discord.ui.View):
    def __init__(self, author_id: int, target_role: discord.Role | None = None):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.target_role = target_role

    @discord.ui.button(label="🐷 แบบที่ 1: กดปุ่มรับยศปกติ (หมูหมู)", style=discord.ButtonStyle.primary, emoji="🐷")
    async def open_simple_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        dashboard_view = RoleSetupDashboardView(admin_id=self.author_id, guild=interaction.guild, initial_type="simple")
        if self.target_role:
            dashboard_view.state["role_id"] = self.target_role.id

        embed = dashboard_view.build_preview_embed()
        await interaction.response.edit_message(content=None, embed=embed, view=dashboard_view)

    @discord.ui.button(label="🛡️ แบบที่ 2: ระบบยืนยันสิทธิ์ Verify (Wraith)", style=discord.ButtonStyle.success, emoji="🔐")
    async def open_verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นที่กดได้ครับ", ephemeral=True)
            return

        dashboard_view = RoleSetupDashboardView(admin_id=self.author_id, guild=interaction.guild, initial_type="verify")
        if self.target_role:
            dashboard_view.state["role_id"] = self.target_role.id

        embed = dashboard_view.build_preview_embed()
        await interaction.response.edit_message(content=None, embed=embed, view=dashboard_view)


@bot.command(name="setlink")
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def setlink_cmd(ctx: commands.Context, *, url: str = None):
    """คำสั่งตั้งค่าลิงก์ของปุ่ม Verify: !setlink <url หรือ เว้นว่างเพื่อใช้ Tunnel อัตโนมัติ>"""
    try:
        await ctx.message.delete()
    except Exception:
        pass

    gid = str(ctx.guild.id)
    all_cfg = load_button_role_config()
    if gid not in all_cfg:
        all_cfg[gid] = {}

    if not url:
        tunnel_url = get_tunnel_url()
        role_id = all_cfg[gid].get("role_id")
        clean_url = f"{tunnel_url}/verify?guild_id={gid}" + (f"&role_id={role_id}" if role_id else "")
    else:
        clean_url = url.strip().strip("<>").strip('"').strip("'")

    all_cfg[gid]["oauth_url"] = clean_url
    save_button_role_config(all_cfg)

    # อัปเดตปุ่มบนการ์ดในห้องปัจจุบันทันที (ถ้ามีการ์ดที่บอทโพสต์ไว้อยู่แล้ว)
    updated_msg_count = 0
    try:
        async for m in ctx.channel.history(limit=20):
            if m.author.id == bot.user.id and m.components:
                new_view = discord.ui.View()
                for row in m.components:
                    for comp in row.children:
                        if comp.type == discord.ComponentType.button:
                            if comp.style == discord.ButtonStyle.link:
                                new_btn = discord.ui.Button(
                                    label=comp.label or "Member",
                                    style=discord.ButtonStyle.link,
                                    url=clean_url,
                                    emoji=comp.emoji
                                )
                                new_view.add_item(new_btn)
                            else:
                                new_btn = discord.ui.Button(
                                    label=comp.label,
                                    style=comp.style,
                                    custom_id=comp.custom_id,
                                    emoji=comp.emoji
                                )
                                new_view.add_item(new_btn)
                if new_view.children:
                    await m.edit(view=new_view)
                    updated_msg_count += 1
    except Exception as e:
        print(f"[SETLINK EDIT ERROR] {e}", flush=True)

    status_extra = f"\n*(อัปเดตปุ่มบนการ์ดเดิมในห้องนี้ให้ทันที {updated_msg_count} ข้อความ)*" if updated_msg_count > 0 else "\n*(คุณสามารถใช้ !setuprole เพื่อโพสต์การ์ดใหม่ได้ทันที)*"
    await ctx.send(f"✅ บันทึกลิงก์ Verify เรียบร้อยแล้ว:\n`{clean_url}`{status_extra}", delete_after=15)


@setlink_cmd.error
async def setlink_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Administrator** เพื่อเปลี่ยนลิงก์ครับ", delete_after=5)


@bot.command(name="setuprole", aliases=["rolemenu", "buttonrole"])
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def setuprole_cmd(ctx: commands.Context, role: discord.Role | None = None):
    """คำสั่งสร้างการ์ดรับยศ: มีให้เลือกระหว่างแบบหมูหมู หรือ แบบยืนยันสิทธิ์ Verify (Wraith)"""
    if await is_duplicate_bot_message(ctx):
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

    view = OpenRoleSetupView(ctx.author.id, target_role=role)
    await ctx.send(
        content=f"🎭 {ctx.author.mention} **เลือกรูปแบบการ์ดรับยศที่ต้องการสร้าง:**",
        view=view,
        delete_after=60
    )


@setuprole_cmd.error
async def setuprole_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return  # ป้องกันข้อความซ้ำจากการพิมพ์รัวๆ
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Administrator (ผู้ดูแลระบบ)** เพื่อสร้างการ์ดรับยศครับ!", delete_after=5)


@bot.tree.command(name="setuprole", description="🎭 สร้างการ์ดปุ่มกดรับยศ (เลือกแบบปกติ หรือ แบบ Verify ได้)")
@app_commands.default_permissions(administrator=True)
async def setuprole_slash(interaction: discord.Interaction):
    """คำสั่ง Slash command สำหรับเปิดหน้าต่างเลือกรูปแบบการ์ดรับยศ"""
    view = OpenRoleSetupView(interaction.user.id)
    await interaction.response.send_message(
        content=f"🎭 {interaction.user.mention} **เลือกรูปแบบการ์ดรับยศที่ต้องการสร้าง:**",
        view=view,
        ephemeral=True
    )



# ==========================================
# 📖 คำสั่ง !help / /help (ศูนย์ช่วยเหลือ & รวมคำสั่งทั้งหมด)
# ==========================================

def build_help_embed(bot_user: discord.ClientUser, guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="📚 คู่มือการใช้งาน & คำสั่งทั้งหมดของบอท",
        description=(
            f"สวัสดีครับ! บอท **{bot_user.name}** พร้อมให้บริการในเซิร์ฟเวอร์ **{guild.name}**\n"
            f"ด้านล่างนี้คือคำสั่งทั้งหมดที่บอทสามารถทำได้ แยกตามหมวดหมู่อย่างเป็นระเบียบครับ:\n"
            f"*(รองรับทั้งพิมพ์ `!<คำสั่ง>` และแบบ Slash Command `/<คำสั่ง>`)*"
        ),
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
    )
    if bot_user.avatar:
        embed.set_thumbnail(url=bot_user.avatar.url)

    # 1. หมวดเควสอัตโนมัติ
    embed.add_field(
        name="⚡ 1. หมวด AutoQuest (เคลียร์เควสอัตโนมัติ)",
        value=(
            "• `!setup` หรือ `/setup` — สร้างกล่องเมนู AutoQuest พร้อมปุ่มกดดึง Token อัตโนมัติในเครื่อง\n"
            "• `/quest token:<โทเคน>` — สั่งเคลียร์เควสผ่าน Slash Command เจาะจง Token"
        ),
        inline=False
    )

    # 2. หมวดต้อนรับสมาชิกใหม่
    embed.add_field(
        name="🌸 2. หมวดต้อนรับสมาชิก (Welcome System)",
        value=(
            "• `!setupwelcome` หรือ `/setupwelcome` — เปิดแผงตั้งค่าระบบต้อนรับ Moodeng & Moojew (ปรับแต่งข้อความ, รูปภาพ Banner, เลือกห้องแจ้งเตือน, เปิด/ปิดระบบ)"
        ),
        inline=False
    )

    # 3. หมวดกระจายข่าวสาร & Mass DM
    embed.add_field(
        name="📨 3. หมวดส่งข้อความหาทุกคน (Mass DM)",
        value=(
            "• `!dm` หรือ `/dm` — เปิดหน้าต่างเขียนข้อความส่ง DM หาคนทั้งเซิร์ฟเวอร์ (ส่งข้อความเพียวๆ ปลอดภัยต่อบอท พร้อมแถบโหลด Progress Bar) และมีปุ่มตามลบข้อความที่เคยส่งย้อนหลังได้"
        ),
        inline=False
    )

    # 4. หมวดกวาดล้างข้อความ
    embed.add_field(
        name="🧹 4. หมวดกวาดล้างข้อความ (Clear & Purge)",
        value=(
            "• `!clear` หรือ `/clear` — เปิดแผงควบคุมลบข้อความอัจฉริยะ (ลบ 10, 25, 50, 100 ข้อความ, ลบเฉพาะบอท)\n"
            "• `!clear <จำนวน>` — สั่งลบข้อความด่วน เช่น `!clear 100`\n"
            "• `!clear <จำนวน> @user` — ลบเฉพาะข้อความของสมาชิกคนนั้น เช่น `!clear 50 @user`"
        ),
        inline=False
    )

    # 5. หมวดป้องกันเซิร์ฟเวอร์
    embed.add_field(
        name="🛡️ 5. หมวดป้องกันเซิร์ฟเวอร์ (Anti-Raid / Anti-Spam)",
        value=(
            "• `!anti` หรือ `!anit` หรือ `/anti` — เปิดแผงควบคุมระบบป้องกันอัตโนมัติ (บล็อกลิงก์เชิญ Discord อื่น, บล็อกแท็ก @everyone/@here, บล็อกคนพิมพ์รัวสแปมแชท พร้อมเลือกลงโทษ Timeout 5 นาที)"
        ),
        inline=False
    )

    # 6. หมวดแจกยศ
    embed.add_field(
        name="🎭 6. หมวดปุ่มกดรับยศ (Button Role)",
        value=(
            "• `!setuprole` หรือ `/setuprole` — สร้างการ์ดปุ่มกดรับยศสวยๆ (แบบ SAKURA MODS) ปรับแต่งหัวข้อ คำอธิบาย รูปภาพ และเลือกยศที่ต้องการแจกได้อิสระ ปุ่มกดทนทานถาวร"
        ),
        inline=False
    )

    # 7. หมวดแดชบอร์ดแอดมิน
    embed.add_field(
        name="👑 7. หมวดศูนย์รวมอำนาจแอดมิน (Admin Control Panel)",
        value=(
            "• `!admin` หรือ `/admin` — เปิดแผงควบคุมรวม (ดูสถิติเซิร์ฟเวอร์แบบ Realtime, สั่ง Timeout ปิดปาก, สั่งเตะ Kick, สั่งแบน Ban, ล็อค/ปลดล็อคห้องแชทชั่วคราว)"
        ),
        inline=False
    )

    # 8. หมวดระบบตั๋วช่วยเหลือ
    embed.add_field(
        name="🎫 8. หมวดระบบตั๋วช่วยเหลือ (Ticket System)",
        value=(
            "• `!setupticket` หรือ `/setupticket` — ตั้งค่าและสร้างการ์ดตั๋วช่วยเหลือ (แบบ SAKURA MODS) ปรับแต่งหัวข้อ คำอธิบาย แบนเนอร์ และหมวดหมู่ห้องได้ตามใจชอบ"
        ),
        inline=False
    )

    # 9. หมวดห้องเสียง 24/7
    embed.add_field(
        name="🪽 9. หมวดห้องเสียง Voice 24/7 (Aria Bot)",
        value=(
            "• `!voicechat` หรือ `/voicechat` — เปิดเมนูตั้งค่าให้ออนห้องเสียง 24/7 ตลอดเวลา (เลือกห้องเสียง, ปิดระบบ, ออนค้างตลอด 24 ชั่วโมง พร้อมระบบต่อใหม่อัตโนมัติ)"
        ),
        inline=False
    )

    # 10. คำสั่งช่วยเหลือ
    embed.add_field(
        name="❓ 10. คำสั่งช่วยเหลือ",
        value="• `!help` หรือ `/help` — แสดงหน้ารวมคำสั่งทั้งหมดนี้",
        inline=False
    )

    embed.set_footer(text=f"พัฒนาโดย VANTA & dj • ใช้งานสำหรับ {guild.name} 🚀")
    return embed


# ==========================================
# 🎫 SAKURA MODS TICKET SYSTEM (ระบบตั๋วช่วยเหลือ)
# ==========================================

BASE_DIR = Path(__file__).resolve().parent
TICKET_CONFIG_FILE = BASE_DIR / "ticket_config.json"


def load_ticket_config() -> dict:
    if TICKET_CONFIG_FILE.exists():
        try:
            with open(TICKET_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_ticket_config(data: dict):
    try:
        with open(TICKET_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[TICKET SAVE ERROR] {e}", flush=True)


def get_guild_ticket_cfg(guild_id: int | str) -> dict:
    cfg = load_ticket_config()
    gid = str(guild_id)
    default_cfg = {
        "title": "SAKURA MODS TICKET",
        "description": "คลิกปุ่มด้านล่าง เพื่อสร้างตั๋ว ห้ามกดเล่นโดยเด็ดขาด\n\n> 💖 **สามารถติดต่อถามสอบถามได้ 24/7**",
        "image_url": "https://media.discordapp.net/attachments/1066030999580000300/1183049187131236402/ticket_banner.png",
        "button_label": "Open Ticket",
        "button_emoji": "📩",
        "button_style": "primary",
        "footer": "Powered by tickets.bot",
        "color": 0x2B2D31,
        "category_id": None,
        "support_role_id": None,
        "ticket_counter": 265,
        "active_tickets": {}
    }
    if gid not in cfg:
        cfg[gid] = default_cfg
        save_ticket_config(cfg)
    else:
        for k, v in default_cfg.items():
            if k not in cfg[gid]:
                cfg[gid][k] = v
    return cfg[gid]


class TicketCardView(discord.ui.View):
    """การ์ด Ticket สาธารณะที่โพสต์ลงห้องแชท (Persistent 100%)"""
    def __init__(self, label: str = "Open Ticket", emoji: str = "📩"):
        super().__init__(timeout=None)
        self.open_ticket_btn = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.primary,
            emoji=emoji,
            custom_id="btn_open_ticket_public"
        )
        self.open_ticket_btn.callback = self.handle_open_ticket
        self.add_item(self.open_ticket_btn)

    async def handle_open_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ ใช้งานได้เฉพาะในเซิร์ฟเวอร์ครับ", ephemeral=True)
            return

        cfg = get_guild_ticket_cfg(guild.id)

        # Defer interaction
        await interaction.response.defer(ephemeral=True, thinking=True)

        # เพิ่มเลขรันตั๋วเรื่อยๆ ไม่มีลิมิต 1 ถึง xxxxxxxxx
        counter = int(cfg.get("ticket_counter", 0)) + 1
        cfg["ticket_counter"] = counter
        all_cfg = load_ticket_config()
        all_cfg[str(guild.id)] = cfg
        save_ticket_config(all_cfg)

        channel_name = f"ticket-{counter}"

        # 4. หาหมวดหมู่ Category
        category = None
        if cfg.get("category_id"):
            category = guild.get_channel(int(cfg["category_id"]))
            if not isinstance(category, discord.CategoryChannel):
                category = None

        if not category:
            for cat in guild.categories:
                if "ticket" in cat.name.lower():
                    category = cat
                    break
            if not category:
                try:
                    category = await guild.create_category("🎫 TICKETS")
                    cfg["category_id"] = category.id
                except Exception:
                    category = None

        # 5. กำหนดสิทธิ์ Permission Overwrites
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_permissions=True
            )
        }

        if cfg.get("support_role_id"):
            support_role = guild.get_role(int(cfg["support_role_id"]))
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True
                )

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"ตั๋วช่วยเหลือของ {interaction.user} (ID: {interaction.user.id})"
            )
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ ไม่สามารถสร้างห้องตั๋วได้: {e}")
            return

        # ไม่จำกัดจำนวนตั๋ว สามารถสร้างต่อเนื่องได้เรื่อยๆ ticket-1, ticket-2, ...

        # 6. ส่งข้อความต้อนรับในการ์ดตั๋วห้องใหม่
        welcome_embed = discord.Embed(
            title=f"🎫 {cfg.get('title', 'SAKURA MODS TICKET')} — #{counter}",
            description=(
                f"ยินดีต้อนรับคุณ {interaction.user.mention} สู่ตั๋วช่วยเหลือ!\n\n"
                f"💬 **กรุณาแจ้งรายละเอียดปัญหา หรือเรื่องที่ต้องการสอบถามไว้ได้เลยครับ**\n"
                f"ทีมงานแอดมินจะรีบเข้ามาตรวจสอบและตอบกลับโดยเร็วที่สุด\n\n"
                f"> 🔒 หากเสร็จสิ้นธุระแล้ว สามารถกดปุ่ม **ปิดตั๋ว (Close Ticket)** ด้านล่างนี้ได้เลยครับ"
            ),
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )
        if cfg.get("image_url"):
            welcome_embed.set_thumbnail(url=interaction.user.display_avatar.url)
        welcome_embed.set_footer(text=f"Ticket ID: {counter} • สร้างโดย {interaction.user.name}")

        control_view = TicketChannelControlView()
        await ticket_channel.send(
            content=f"🔔 {interaction.user.mention} ยินดีต้อนรับ!",
            embed=welcome_embed,
            view=control_view
        )

        # 7. แจ้งเตือนผู้ใช้ใน Ephemeral
        await interaction.edit_original_response(
            content=f"🎉 สร้างห้องตั๋วของคุณเรียบร้อยแล้ว: {ticket_channel.mention}"
        )


class TicketCloseConfirmView(discord.ui.View):
    """ปุ่มยืนยันก่อนปิดตั๋ว ป้องกันคนกดเล่น"""
    def __init__(self, opener_user: discord.User | discord.Member):
        super().__init__(timeout=60)
        self.opener_user = opener_user

    @discord.ui.button(label="⚠️ ยืนยันปิดตั๋ว", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 กำลังบันทึกและลบห้องตั๋วนี้ใน 5 วินาที...", ephemeral=False)
        
        guild = interaction.guild
        if guild:
            cfg = get_guild_ticket_cfg(guild.id)
            active = cfg.get("active_tickets", {})
            for uid, ch_id in list(active.items()):
                if ch_id == interaction.channel_id:
                    del active[uid]
                    break
            cfg["active_tickets"] = active
            all_cfg = load_ticket_config()
            all_cfg[str(guild.id)] = cfg
            save_ticket_config(all_cfg)

        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception:
            pass

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.edit_message(content="ยกเลิกการปิดตั๋วแล้วครับ", view=None)


class TicketChannelControlView(discord.ui.View):
    """ปุ่มควบคุมในห้อง Ticket (Persistent 100%)"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ปิดตั๋ว (Close Ticket)",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="btn_close_ticket_channel"
    )
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TicketCloseConfirmView(interaction.user)
        await interaction.response.send_message(
            content="⚠️ **คุณแน่ใจหรือไม่ว่าต้องการปิดตั๋วนี้?**\n*(ห้องนี้จะถูกลบทันทีหลังจากยืนยัน)*",
            view=view,
            ephemeral=True
        )


class TicketTextModal(discord.ui.Modal, title="✏️ แก้ไขข้อความการ์ด Ticket"):
    def __init__(self, dashboard_view):
        super().__init__()
        self.dashboard_view = dashboard_view
        cfg = dashboard_view.cfg

        self.title_input = discord.ui.TextInput(
            label="หัวข้อการ์ด (Title)",
            default=cfg.get("title", "SAKURA MODS TICKET"),
            max_length=200,
            required=True
        )
        self.desc_input = discord.ui.TextInput(
            label="ข้อความอธิบาย (Description)",
            style=discord.TextStyle.paragraph,
            default=cfg.get("description", "คลิกปุ่มด้านล่าง เพื่อสร้างตั๋ว ห้ามกดเล่นโดยเด็ดขาด\n\n> 💖 **สามารถติดต่อถามสอบถามได้ 24/7**"),
            max_length=1500,
            required=True
        )
        self.footer_input = discord.ui.TextInput(
            label="ข้อความด้านล่าง (Footer)",
            default=cfg.get("footer", "Powered by tickets.bot"),
            max_length=150,
            required=False
        )

        self.add_item(self.title_input)
        self.add_item(self.desc_input)
        self.add_item(self.footer_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.dashboard_view.cfg["title"] = self.title_input.value.strip()
        self.dashboard_view.cfg["description"] = self.desc_input.value.strip()
        self.dashboard_view.cfg["footer"] = self.footer_input.value.strip()
        self.dashboard_view.save_cfg()

        embed = self.dashboard_view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.dashboard_view)


class TicketImageModal(discord.ui.Modal, title="🖼️ ตั้งค่ารูปภาพแบนเนอร์"):
    def __init__(self, dashboard_view):
        super().__init__()
        self.dashboard_view = dashboard_view
        self.img_input = discord.ui.TextInput(
            label="ลิงก์รูปภาพแบนเนอร์ (Banner URL)",
            placeholder="https://example.com/banner.png (เว้นว่างเพื่อไม่ใส่รูป)",
            default=self.dashboard_view.cfg.get("image_url") or "",
            required=False
        )
        self.add_item(self.img_input)

    async def on_submit(self, interaction: discord.Interaction):
        val = self.img_input.value.strip()
        self.dashboard_view.cfg["image_url"] = val if val else None
        self.dashboard_view.save_cfg()
        embed = self.dashboard_view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.dashboard_view)


class TicketButtonModal(discord.ui.Modal, title="🔘 ตั้งค่าปุ่มกด Ticket"):
    def __init__(self, dashboard_view):
        super().__init__()
        self.dashboard_view = dashboard_view
        self.label_input = discord.ui.TextInput(
            label="ข้อความบนปุ่ม (Label)",
            default=self.dashboard_view.cfg.get("button_label", "Open Ticket"),
            max_length=50,
            required=True
        )
        self.emoji_input = discord.ui.TextInput(
            label="อิโมจิบนปุ่ม (Emoji)",
            default=self.dashboard_view.cfg.get("button_emoji", "📩"),
            max_length=20,
            required=False
        )
        self.counter_input = discord.ui.TextInput(
            label="เลขเริ่มต้นของตั๋ว (Ticket Counter)",
            default=str(self.dashboard_view.cfg.get("ticket_counter", 265)),
            max_length=10,
            required=False
        )
        self.add_item(self.label_input)
        self.add_item(self.emoji_input)
        self.add_item(self.counter_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.dashboard_view.cfg["button_label"] = self.label_input.value.strip()
        self.dashboard_view.cfg["button_emoji"] = self.emoji_input.value.strip() or "📩"
        try:
            self.dashboard_view.cfg["ticket_counter"] = int(self.counter_input.value.strip())
        except ValueError:
            pass
        self.dashboard_view.save_cfg()
        embed = self.dashboard_view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.dashboard_view)


class TicketCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, dashboard_view):
        super().__init__(
            channel_types=[discord.ChannelType.category],
            placeholder="📁 เลือกหมวดหมู่ (Category) ที่จะสร้างห้องตั๋ว...",
            min_values=0,
            max_values=1,
            row=0
        )
        self.dashboard_view = dashboard_view

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            cat = self.values[0]
            self.dashboard_view.cfg["category_id"] = cat.id
        else:
            self.dashboard_view.cfg["category_id"] = None
        self.dashboard_view.save_cfg()
        embed = self.dashboard_view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.dashboard_view)


class TicketRoleSelect(discord.ui.RoleSelect):
    def __init__(self, dashboard_view):
        super().__init__(
            placeholder="🛡️ เลือกยศ Support / แอดมิน ที่ให้มองเห็นตั๋ว...",
            min_values=0,
            max_values=1,
            row=1
        )
        self.dashboard_view = dashboard_view

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            role = self.values[0]
            self.dashboard_view.cfg["support_role_id"] = role.id
        else:
            self.dashboard_view.cfg["support_role_id"] = None
        self.dashboard_view.save_cfg()
        embed = self.dashboard_view.build_preview_embed()
        await interaction.response.edit_message(embed=embed, view=self.dashboard_view)


class TicketSetupDashboardView(discord.ui.View):
    """แดชบอร์ดตั้งค่าตั๋ว Ticket สำหรับแอดมิน (เห็นคนเดียว 100%)"""
    def __init__(self, admin_id: int, guild: discord.Guild):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.guild = guild
        self.cfg = get_guild_ticket_cfg(guild.id)

        self.add_item(TicketCategorySelect(self))
        self.add_item(TicketRoleSelect(self))

    def save_cfg(self):
        all_cfg = load_ticket_config()
        all_cfg[str(self.guild.id)] = self.cfg
        save_ticket_config(all_cfg)

    def build_preview_embed(self) -> discord.Embed:
        cat_str = f"<#{self.cfg['category_id']}>" if self.cfg.get("category_id") else "*สร้าง Category อัตโนมัติ*"
        role_str = f"<@&{self.cfg['support_role_id']}>" if self.cfg.get("support_role_id") else "*เฉพาะแอดมิน*"

        embed = discord.Embed(
            title=self.cfg.get("title", "SAKURA MODS TICKET"),
            description=self.cfg.get("description", "คลิกปุ่มด้านล่าง เพื่อสร้างตั๋ว"),
            color=self.cfg.get("color", 0x2B2D31)
        )
        if self.cfg.get("image_url"):
            embed.set_image(url=self.cfg["image_url"])
        if self.cfg.get("footer"):
            embed.set_footer(text=self.cfg["footer"])

        embed.add_field(
            name="⚙️ ข้อมูลการตั้งค่าปัจจุบัน (เห็นเฉพาะคุณ)",
            value=(
                f"• หมวดหมู่ห้อง: {cat_str}\n"
                f"• ยศทีมงาน Support: {role_str}\n"
                f"• ปุ่ม: `{self.cfg.get('button_emoji', '📩')} {self.cfg.get('button_label', 'Open Ticket')}`\n"
                f"• เลขตั๋วปัจจุบัน: `ticket-{self.cfg.get('ticket_counter', 265)}`"
            ),
            inline=False
        )
        return embed

    @discord.ui.button(label="✏️ แก้ข้อความ", style=discord.ButtonStyle.primary, row=2)
    async def edit_text_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketTextModal(self))

    @discord.ui.button(label="🖼️ แบนเนอร์รูป", style=discord.ButtonStyle.primary, row=2)
    async def edit_img_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketImageModal(self))

    @discord.ui.button(label="🔘 ตั้งค่าปุ่ม/เลขตั๋ว", style=discord.ButtonStyle.primary, row=2)
    async def edit_btn_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketButtonModal(self))

    @discord.ui.button(label="🚀 โพสต์การ์ดลงห้องนี้เลย!", style=discord.ButtonStyle.success, emoji="🚀", row=3)
    async def publish_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        card_embed = discord.Embed(
            title=self.cfg.get("title", "SAKURA MODS TICKET"),
            description=self.cfg.get("description", "คลิกปุ่มด้านล่าง เพื่อสร้างตั๋ว ห้ามกดเล่นโดยเด็ดขาด\n\n> 💖 **สามารถติดต่อถามสอบถามได้ 24/7**"),
            color=self.cfg.get("color", 0x2B2D31)
        )
        if self.cfg.get("image_url"):
            card_embed.set_image(url=self.cfg["image_url"])
        if self.cfg.get("footer"):
            card_embed.set_footer(text=self.cfg["footer"])

        card_view = TicketCardView(
            label=self.cfg.get("button_label", "Open Ticket"),
            emoji=self.cfg.get("button_emoji", "📩")
        )

        try:
            await interaction.channel.send(embed=card_embed, view=card_view)
            await interaction.response.edit_message(
                content="🎉 **โพสต์การ์ด Ticket ลงในห้องนี้เรียบร้อยแล้ว! สมาชิกสามารถกดสร้างตั๋วได้ทันที**",
                embed=None,
                view=None
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ โพสต์การ์ดไม่สำเร็จ: {e}", ephemeral=True)


class OpenTicketSetupView(discord.ui.View):
    """ปุ่มสำหรับให้แอดมินคลิกเปิดแผงตั้งค่าตั๋วแบบเห็นคนเดียว"""
    def __init__(self, author_id: int, guild_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.guild_id = guild_id

    @discord.ui.button(label="⚙️ เปิดแผงตั้งค่าการ์ด Ticket (เห็นคนเดียว 100%)", style=discord.ButtonStyle.primary, emoji="🎫")
    async def open_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ เฉพาะแอดมินที่เรียกคำสั่งเท่านั้นครับ", ephemeral=True)
            return

        dashboard_view = TicketSetupDashboardView(admin_id=self.author_id, guild=interaction.guild)
        embed = dashboard_view.build_preview_embed()
        await interaction.response.send_message(embed=embed, view=dashboard_view, ephemeral=True)

        try:
            await interaction.message.delete()
        except Exception:
            pass


@bot.command(name="setupticket", aliases=["ticketsetup", "ticket"])
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def setupticket_cmd(ctx: commands.Context):
    """คำสั่งตั้งค่าและโพสต์การ์ดตั๋วช่วยเหลือ Ticket (แบบ SAKURA MODS)"""
    if await is_duplicate_bot_message(ctx):
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

    view = OpenTicketSetupView(ctx.author.id, ctx.guild.id)
    await ctx.send(
        content=f"🎫 {ctx.author.mention} กดปุ่มด้านล่างเพื่อเปิด **แผงควบคุมตั้งค่าตั๋ว Ticket (เห็นคนเดียว 100%)** ได้เลยครับ:",
        view=view,
        delete_after=60
    )


@setupticket_cmd.error
async def setupticket_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Administrator (ผู้ดูแลระบบ)** เพื่อตั้งค่าระบบ Ticket ครับ!", delete_after=5)


@bot.tree.command(name="setupticket", description="🎫 ตั้งค่าและสร้างการ์ดตั๋วช่วยเหลือ Ticket (แบบ SAKURA MODS)")
@app_commands.default_permissions(administrator=True)
async def setupticket_slash(interaction: discord.Interaction):
    """คำสั่ง Slash สำหรับเปิดแผงตั้งค่า Ticket (เห็นคนเดียว 100%)"""
    dashboard_view = TicketSetupDashboardView(admin_id=interaction.user.id, guild=interaction.guild)
    embed = dashboard_view.build_preview_embed()
    await interaction.response.send_message(embed=embed, view=dashboard_view, ephemeral=True)


# ==========================================
# 🪽 VOICE 24/7 SYSTEM (ระบบออนห้องเสียง 24/7 Aria Bot)
# ==========================================

VOICE_247_CONFIG_FILE = BASE_DIR / "voice_247_config.json"


def load_voice_247_config() -> dict:
    if VOICE_247_CONFIG_FILE.exists():
        try:
            with open(VOICE_247_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_voice_247_config(data: dict):
    try:
        with open(VOICE_247_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[VOICE 24/7 SAVE ERROR] {e}", flush=True)


def get_guild_voice_247_cfg(guild_id: int | str) -> dict:
    cfg = load_voice_247_config()
    gid = str(guild_id)
    default_cfg = {
        "channel_id": None,
        "channel_name": None,
        "is_active": False
    }
    if gid not in cfg:
        cfg[gid] = default_cfg
        save_voice_247_config(cfg)
    else:
        for k, v in default_cfg.items():
            if k not in cfg[gid]:
                cfg[gid][k] = v
    return cfg[gid]


def get_voice_avatar_url() -> str:
    tunnel_url = get_tunnel_url()
    if tunnel_url:
        return f"{tunnel_url}/voice_avatar.png"
    return "https://media.discordapp.net/attachments/1066030999580000300/1183049187131236402/alya.png"


def create_voice_247_main_embed(guild: discord.Guild, cfg: dict) -> discord.Embed:
    embed = discord.Embed(
        title="🪽 ระบบ Voice 24/7 | 5bpo Bot 🪽",
        description="สวัสดีค่ะ! โปรดเลือกการตั้งค่าห้องเสียง 24/7 จากเมนูด้านล่างเลยนะคะ ~",
        color=0xC084FC
    )
    avatar_url = get_voice_avatar_url()
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    channel_name = cfg.get("channel_name")
    ch_id = cfg.get("channel_id")
    if ch_id:
        ch_obj = guild.get_channel(int(ch_id))
        if ch_obj:
            channel_name = ch_obj.name

    channel_display = f"🔊 {channel_name}" if channel_name else "ยังไม่ได้ตั้งค่า"
    status_display = "🌸 เปิดใช้งาน" if cfg.get("is_active") else "🔴 ปิดใช้งาน"

    embed.add_field(name="📹 ห้องเสียง", value=channel_display, inline=True)
    embed.add_field(name="สถานะ", value=status_display, inline=True)
    return embed


def create_voice_247_select_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🪽 ตั้งค่า Voice 24/7 | 5bpo Bot 🪽",
        description="โปรดเลือกห้องเสียงที่ต้องการให้บอทอยู่ 24/7 แล้วกดยืนยันนะคะ ~",
        color=0xC084FC
    )
    avatar_url = get_voice_avatar_url()
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    return embed


class VoiceMainMenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="ตั้งค่าห้องเสียง (เลือกห้อง)",
                description="เลือกห้องเสียงที่บอทจะอยู่ 24/7",
                emoji="📹",
                value="set_channel"
            ),
            discord.SelectOption(
                label="ปิดระบบ",
                description="ปิดระบบ 24/7 บอทจะออกจากห้อง",
                emoji="🚨",
                value="disable"
            ),
            discord.SelectOption(
                label="ปิดหน้าต่างนี้",
                description="ลบการ์ดการตั้งค่านี้ออกจากแชท",
                emoji="🗑️",
                value="close"
            )
        ]
        super().__init__(placeholder="[ 🎀 ] เลือกการตั้งค่า...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ ใช้ได้เฉพาะในเซิร์ฟเวอร์ครับ", ephemeral=True)
            return

        choice = self.values[0]
        cfg = get_guild_voice_247_cfg(guild.id)

        if choice == "close":
            try:
                if interaction.message:
                    await interaction.message.delete()
                else:
                    await interaction.delete_original_response()
            except Exception:
                try:
                    await interaction.response.defer()
                    await interaction.delete_original_response()
                except Exception:
                    pass
            return

        if choice == "set_channel":
            embed = create_voice_247_select_embed(guild)
            view = Voice247SelectChannelView(guild.id, interaction.user.id)
            await interaction.response.edit_message(embed=embed, view=view)

        elif choice == "disable":
            cfg["is_active"] = False
            all_cfg = load_voice_247_config()
            all_cfg[str(guild.id)] = cfg
            save_voice_247_config(all_cfg)

            vc = guild.voice_client
            if vc:
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass

            # ลบเมนูตั้งค่าทิ้งทันทีตามที่ dj สั่ง
            try:
                if interaction.message:
                    await interaction.message.delete()
            except Exception:
                try:
                    await interaction.delete_original_response()
                except Exception:
                    pass

            await interaction.response.send_message("🚨 ปิดระบบ Voice 24/7 และบอทออกจากห้องเสียงเรียบร้อยแล้วค่ะ!", ephemeral=True)


class Voice247MainView(discord.ui.View):
    def __init__(self, guild_id: int, admin_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.admin_id = admin_id
        self.add_item(VoiceMainMenuSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่ตั้งค่าได้ครับ", ephemeral=True)
            return False
        return True


class VoiceChannelSelectDropdown(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="[ 🎀 ] เลือกห้องเสียง...",
            channel_types=[discord.ChannelType.voice, discord.ChannelType.stage_voice],
            min_values=1,
            max_values=1,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            self.view.selected_channel = self.values[0]
        await interaction.response.defer()


class Voice247SelectChannelView(discord.ui.View):
    def __init__(self, guild_id: int, admin_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.admin_id = admin_id
        self.selected_channel = None

        self.add_item(VoiceChannelSelectDropdown())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่ตั้งค่าได้ครับ", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="ยืนยันห้องเสียง", style=discord.ButtonStyle.success, emoji="🌸", row=1)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_channel:
            await interaction.response.send_message("⚠️ โปรดเลือกห้องเสียงจากเมนูด้านบนก่อนกดยืนยันนะคะ ~", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            return

        target_ch = self.selected_channel
        channel_id = target_ch.id if hasattr(target_ch, "id") else int(target_ch)
        real_ch = guild.get_channel(channel_id)
        if not real_ch or not isinstance(real_ch, (discord.VoiceChannel, discord.StageChannel)):
            try:
                real_ch = await guild.fetch_channel(channel_id)
            except Exception:
                real_ch = bot.get_channel(channel_id)

        if not real_ch or not isinstance(real_ch, (discord.VoiceChannel, discord.StageChannel)):
            await interaction.response.send_message("❌ ไม่พบห้องเสียง หรือประเภทห้องไม่ใช่ห้องเสียงค่ะ", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # บันทึกสถานะ 24/7 ทันที เพื่อไม่ให้ task reconnect ตีกลับ
        cfg = get_guild_voice_247_cfg(guild.id)
        cfg["channel_id"] = real_ch.id
        cfg["channel_name"] = real_ch.name
        cfg["is_active"] = True

        all_cfg = load_voice_247_config()
        all_cfg[str(guild.id)] = cfg
        save_voice_247_config(all_cfg)

        # ตรวจสอบและโหลด Opus
        if not discord.opus.is_loaded():
            try:
                discord.opus._load_default()
            except Exception:
                pass

        # เชื่อมต่อหรือย้ายเข้าห้องเสียง
        vc = guild.voice_client
        connected_ok = False
        try:
            if vc is not None:
                if vc.is_connected():
                    if vc.channel and vc.channel.id == real_ch.id:
                        connected_ok = True
                    else:
                        try:
                            await vc.move_to(real_ch, timeout=10.0)
                            connected_ok = True
                        except Exception as me:
                            print(f"[VOICE MOVE FAILED] {me}, attempting clean reconnect...", flush=True)
                            try:
                                await vc.disconnect(force=True)
                            except Exception:
                                pass
                            await asyncio.sleep(0.5)
                            await real_ch.connect(self_deaf=True, timeout=15.0)
                            connected_ok = True
                else:
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                    await real_ch.connect(self_deaf=True, timeout=15.0)
                    connected_ok = True
            else:
                await real_ch.connect(self_deaf=True, timeout=15.0)
                connected_ok = True
        except Exception as e:
            print(f"[VOICE CONNECT ERROR] {e}", flush=True)

        # 🔥 ลบการ์ดตั้งค่าออกไปจากแชททันทีตามที่ dj สั่ง ("ให้ไอ่ทั้ตั้งค่านั้นหายไปหลังจากเอาบอทลงห้อง")
        try:
            if interaction.message:
                await interaction.message.delete()
        except Exception:
            try:
                await interaction.delete_original_response()
            except Exception:
                pass

        # เริ่มระบบเฝ้าห้องเสียง 24/7 เฉพาะหลังจากกดยืนยันแล้วเท่านั้น
        start_voice_247_task()

        # แจ้งเตือนแบบเห็นคนเดียวให้แอดมินทราบ
        if connected_ok:
            await interaction.followup.send(f"✅ บอทได้เข้าห้องเสียง {real_ch.mention} และเปิดโหมด 24/7 เรียบร้อยแล้วค่ะ! 🌸", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ บันทึกห้องเสียง {real_ch.mention} เรียบร้อยแล้ว แต่กำลังรอเชื่อมต่อห้องเสียงอัตโนมัติสักครู่ค่ะ...", ephemeral=True)

    @discord.ui.button(label="ย้อนกลับ", style=discord.ButtonStyle.secondary, emoji="↗️", row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        cfg = get_guild_voice_247_cfg(guild.id) if guild else {}
        main_embed = create_voice_247_main_embed(guild, cfg)
        main_view = Voice247MainView(guild.id, interaction.user.id)
        await interaction.response.edit_message(embed=main_embed, view=main_view)

    @discord.ui.button(label="ปิดหน้าต่าง", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if interaction.message:
                await interaction.message.delete()
        except Exception:
            try:
                await interaction.delete_original_response()
            except Exception:
                pass


@tasks.loop(seconds=20)
async def voice_247_reconnect_loop():
    try:
        all_cfg = load_voice_247_config()
        for gid_s, cfg in all_cfg.items():
            if not cfg.get("is_active") or not cfg.get("channel_id"):
                continue
            guild = bot.get_guild(int(gid_s))
            if not guild:
                continue
            channel = guild.get_channel(int(cfg["channel_id"]))
            if not channel or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                try:
                    channel = await guild.fetch_channel(int(cfg["channel_id"]))
                except Exception:
                    channel = None
            if not channel or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                continue

            vc = guild.voice_client
            try:
                if not discord.opus.is_loaded():
                    try:
                        discord.opus._load_default()
                    except Exception:
                        pass

                if vc is None or not vc.is_connected():
                    if vc is not None:
                        try:
                            await vc.disconnect(force=True)
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
                    await channel.connect(self_deaf=True, timeout=15.0)
                elif vc.channel.id != channel.id:
                    try:
                        await vc.move_to(channel, timeout=10.0)
                    except Exception:
                        try:
                            await vc.disconnect(force=True)
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
                        await channel.connect(self_deaf=True, timeout=15.0)
            except Exception as e:
                print(f"[VOICE 247 RECONNECT LOOP ERROR] {e}", flush=True)
    except Exception as e:
        print(f"[VOICE 247 OUTER LOOP ERROR] {e}", flush=True)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id == bot.user.id:
        cfg = get_guild_voice_247_cfg(member.guild.id)
        if cfg.get("is_active") and cfg.get("channel_id"):
            target_id = int(cfg["channel_id"])
            if after.channel is None or after.channel.id != target_id:
                await asyncio.sleep(2)
                guild = member.guild
                ch = guild.get_channel(target_id)
                if not ch or not isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                    try:
                        ch = await guild.fetch_channel(target_id)
                    except Exception:
                        ch = None
                if ch and isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                    try:
                        if not discord.opus.is_loaded():
                            try:
                                discord.opus._load_default()
                            except Exception:
                                pass
                        vc = guild.voice_client
                        if vc is None or not vc.is_connected():
                            if vc is not None:
                                try:
                                    await vc.disconnect(force=True)
                                except Exception:
                                    pass
                                await asyncio.sleep(0.5)
                            await ch.connect(self_deaf=True, timeout=15.0)
                        elif vc.channel.id != ch.id:
                            try:
                                await vc.move_to(ch, timeout=10.0)
                            except Exception:
                                try:
                                    await vc.disconnect(force=True)
                                except Exception:
                                    pass
                                await asyncio.sleep(0.5)
                                await ch.connect(self_deaf=True, timeout=15.0)
                    except Exception as e:
                        print(f"[VOICE STATE UPDATE RECONNECT ERROR] {e}", flush=True)


def start_voice_247_task():
    if not voice_247_reconnect_loop.is_running():
        voice_247_reconnect_loop.start()


@bot.command(name="voicechat", aliases=["vc", "voice247", "247"])
@commands.has_permissions(administrator=True)
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def voicechat_cmd(ctx: commands.Context):
    """คำสั่งเปิดเมนูตั้งค่า Voice 24/7 (ออนห้องเสียงตลอดเวลา)"""
    if await is_duplicate_bot_message(ctx):
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

    cfg = get_guild_voice_247_cfg(ctx.guild.id)
    embed = create_voice_247_main_embed(ctx.guild, cfg)
    view = Voice247MainView(ctx.guild.id, ctx.author.id)
    await ctx.send(embed=embed, view=view, delete_after=300)


@voicechat_cmd.error
async def voicechat_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ คุณต้องมีสิทธิ์ **Administrator (ผู้ดูแลระบบ)** เพื่อตั้งค่า Voice 24/7 ครับ!", delete_after=5)


@bot.tree.command(name="voicechat", description="🪽 ตั้งค่าระบบ Voice 24/7 ให้ออนห้องเสียงตลอดเวลา (เห็นคนเดียว 100%)")
@app_commands.default_permissions(administrator=True)
async def voicechat_slash(interaction: discord.Interaction):
    """คำสั่ง Slash command สำหรับตั้งค่า Voice 24/7 (เห็นคนเดียว)"""
    cfg = get_guild_voice_247_cfg(interaction.guild.id)
    embed = create_voice_247_main_embed(interaction.guild, cfg)
    view = Voice247MainView(interaction.guild.id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.command(name="help", aliases=["commands", "cmd", "คำสั่ง"])
@commands.cooldown(1, 3.0, commands.BucketType.user)
async def help_cmd(ctx: commands.Context):
    """คำสั่งดูคู่มือและคำสั่งทั้งหมดของบอท: !help"""
    if await is_duplicate_bot_message(ctx):
        return
    try:
        await ctx.message.delete()
    except Exception:
        pass

    embed = build_help_embed(bot.user, ctx.guild)
    await ctx.send(embed=embed, delete_after=120)


@help_cmd.error
async def help_cmd_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandOnCooldown):
        return


@bot.tree.command(name="help", description="📚 ดูคู่มือและรายชื่อคำสั่งทั้งหมดของบอท (เห็นคนเดียว 100%)")
async def help_slash(interaction: discord.Interaction):
    """คำสั่ง Slash command สำหรับเปิดคู่มือ (เห็นคนเดียว)"""
    embed = build_help_embed(interaction.client.user, interaction.guild)
    await interaction.response.send_message(embed=embed, ephemeral=True)


def main():
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN is not set in .env", flush=True)
        return
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
