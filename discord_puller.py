# language: Python, file: discord_puller.py, target: Windows 11 / Linux
import asyncio
import os
import aiohttp
from typing import Callable, Optional
import member_db

API_BASE = "https://discord.com/api/v10"

# สถานะของ Job การดึงคนปัจจุบัน
current_pull_job = {
    "is_running": False,
    "job_id": None,
    "guild_id": None,
    "guild_name": None,
    "total": 0,
    "processed": 0,
    "success": 0,
    "already_member": 0,
    "failed": 0,
    "rate_limited": 0,
    "logs": [],
    "status": "idle" # "idle", "running", "completed", "stopped"
}


def get_current_job_status() -> dict:
    return current_pull_job.copy()


def add_job_log(message: str, level: str = "info"):
    entry = {"time": asyncio.get_event_loop().time(), "message": message, "level": level}
    current_pull_job["logs"].append(entry)
    if len(current_pull_job["logs"]) > 200:
        current_pull_job["logs"].pop(0)
    print(f"[PULLER] {message}", flush=True)


async def add_member_to_guild(
    session: aiohttp.ClientSession,
    bot_token: str,
    guild_id: str,
    user_id: str,
    access_token: str,
    roles: list[str] = None
) -> dict:
    """
    เรียก Discord API: PUT /guilds/{guild.id}/members/{user.id}
    ส่ง user เข้าสู่ server ผ่าน OAuth2 token
    """
    url = f"{API_BASE}/guilds/{guild_id}/members/{user_id}"
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json"
    }
    payload = {"access_token": access_token}
    if roles:
        payload["roles"] = roles

    try:
        async with session.put(url, headers=headers, json=payload) as resp:
            if resp.status == 201:
                # เข้าใหม่สำเร็จ
                data = await resp.json()
                return {"status": "success", "code": 201, "data": data}
            elif resp.status == 204:
                # อยู่ในเซิร์ฟเวอร์อยู่แล้ว
                return {"status": "already_member", "code": 204}
            elif resp.status == 429:
                # โดน Rate Limit
                data = await resp.json()
                retry_after = data.get("retry_after", 2.0)
                return {"status": "rate_limited", "code": 429, "retry_after": retry_after}
            elif resp.status in (401, 403):
                text = await resp.text()
                return {"status": "forbidden", "code": resp.status, "error": text}
            else:
                text = await resp.text()
                return {"status": "failed", "code": resp.status, "error": text}
    except Exception as e:
        return {"status": "error", "code": 0, "error": str(e)}


async def refresh_user_token(session: aiohttp.ClientSession, client_id: str, client_secret: str, refresh_token: str) -> dict | None:
    """แลก Refresh Token เป็น Access Token ตัวใหม่ตาม Discord OAuth2 RFC"""
    url = f"{API_BASE}/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    try:
        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        print(f"[REFRESH ERROR] {e}", flush=True)
    return None


async def start_pull_task(
    bot_token: str,
    guild_id: str,
    guild_name: str,
    user_ids: list[str] = None,
    delay_seconds: float = 2.0
):
    """รันกระบวนการดึงคนเข้าดิสคอร์ดแบบ Async Batch"""
    global current_pull_job

    if current_pull_job["is_running"]:
        return False, "มีกระบวนการดึงคนกำลังทำงานอยู่แล้ว"

    all_users = member_db.get_all_users()
    if user_ids:
        targets = [u for u in all_users if u["user_id"] in user_ids]
    else:
        # กรองเฉพาะคนที่ยังไม่ได้ระบุว่าอยู่ในกิลด์นี้ หรือดึงทุกคนที่มี Token
        targets = [u for u in all_users if u.get("status") == "active" and u.get("access_token")]

    if not targets:
        return False, "ไม่พบสมาชิกที่มี Token พร้อมใช้งานในระบบ"

    current_pull_job = {
        "is_running": True,
        "job_id": f"pull_{guild_id}_{int(asyncio.get_event_loop().time())}",
        "guild_id": guild_id,
        "guild_name": guild_name,
        "total": len(targets),
        "processed": 0,
        "success": 0,
        "already_member": 0,
        "failed": 0,
        "rate_limited": 0,
        "logs": [],
        "status": "running"
    }

    add_job_log(f"🚀 เริ่มต้นการดึงคนเข้าเซิร์ฟเวอร์ '{guild_name}' (เป้าหมาย: {len(targets)} คน)", "info")

    async def _runner():
        async with aiohttp.ClientSession() as session:
            for user in targets:
                if not current_pull_job["is_running"]:
                    add_job_log("⏹️ กระบวนการถูกสั่งหยุดโดยผู้ใช้", "warning")
                    break

                uid = user["user_id"]
                uname = user.get("global_name") or user.get("username", uid)
                token = user.get("access_token")

                add_job_log(f"⏳ กำลังดึง {uname} ({uid})...", "info")
                res = await add_member_to_guild(session, bot_token, guild_id, uid, token)

                # ถ้าเจอ Rate limit ให้รอตามคำสั่ง Discord
                while res["status"] == "rate_limited":
                    wait_time = res.get("retry_after", 3.0) + 0.5
                    current_pull_job["rate_limited"] += 1
                    add_job_log(f"⚠️ ติด Discord Rate Limit! กำลังพัก {wait_time:.1f} วินาที...", "warning")
                    await asyncio.sleep(wait_time)
                    res = await add_member_to_guild(session, bot_token, guild_id, uid, token)

                current_pull_job["processed"] += 1

                if res["status"] == "success":
                    current_pull_job["success"] += 1
                    member_db.mark_guild_joined(uid, guild_id)
                    add_job_log(f"✅ สำเร็จ! ดึง {uname} เข้าเซิร์ฟเวอร์เรียบร้อย", "success")
                elif res["status"] == "already_member":
                    current_pull_job["already_member"] += 1
                    member_db.mark_guild_joined(uid, guild_id)
                    add_job_log(f"ℹ️ {uname} อยู่ในเซิร์ฟเวอร์นี้อยู่แล้ว (ข้าม)", "info")
                elif res["status"] == "forbidden":
                    current_pull_job["failed"] += 1
                    err_msg = res.get("error", "")
                    if "10004" in err_msg or "Invalid" in err_msg or "token" in err_msg.lower():
                        member_db.mark_token_invalid(uid, "revoked_or_expired")
                        add_job_log(f"❌ {uname}: Token หมดอายุ หรือผู้ใช้ยกเลิกสิทธิ์", "error")
                    elif "50001" in err_msg or "Missing Access" in err_msg or "50013" in err_msg:
                        add_job_log(f"❌ บอทไม่มีสิทธิ์สร้างคำเชิญ (Create Invite) ในเซิร์ฟเวอร์ {guild_name}", "error")
                    else:
                        add_job_log(f"❌ ไม่สามารถดึง {uname} ได้: Code {res['code']}", "error")
                else:
                    current_pull_job["failed"] += 1
                    add_job_log(f"❌ ล้มเหลว {uname}: {res.get('error', 'Unknown error')}", "error")

                # ดีเลย์ป้องกัน rate limit ปกติ
                await asyncio.sleep(delay_seconds)

        # บันทึกประวัติ
        member_db.record_pull_history({
            "guild_id": guild_id,
            "guild_name": guild_name,
            "total_attempted": current_pull_job["total"],
            "success_count": current_pull_job["success"],
            "already_in_guild": current_pull_job["already_member"],
            "failed_count": current_pull_job["failed"]
        })

        current_pull_job["is_running"] = False
        current_pull_job["status"] = "completed"
        add_job_log(f"🎉 เสร็จสิ้นกระบวนการ! สำเร็จ: {current_pull_job['success']}, เดิมมีอยู่แล้ว: {current_pull_job['already_member']}, ล้มเหลว: {current_pull_job['failed']}", "success")

    asyncio.create_task(_runner())
    return True, "เริ่มกระบวนการดึงคนสำเร็จ"


def stop_pull_task():
    global current_pull_job
    if current_pull_job["is_running"]:
        current_pull_job["is_running"] = False
        current_pull_job["status"] = "stopped"
        add_job_log("🛑 ได้รับคำสั่งหยุดการดึงคนแล้ว", "warning")
        return True
    return False
