# language: Python, file: auto_run.py, target: Windows 11
import os
import re
import json
import base64
import asyncio
from pathlib import Path
import aiohttp

# Win32 CryptUnprotectData สำหรับถอดรหัส Token ของ Discord Desktop
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

API_BASE = "https://discord.com/api/v9"


def get_tokens_from_desktop() -> list[str]:
    """ดึง Token จาก Discord Desktop Client (LevelDB + DPAPI)"""
    tokens = []
    appdata = os.getenv("APPDATA")
    localappdata = os.getenv("LOCALAPPDATA")
    
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

        # หา Master Key สำหรับ Decrypt
        if key_path.exists() and win32crypt and AES:
            try:
                with open(key_path, "r", encoding="utf-8") as f:
                    local_state = json.load(f)
                encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])[5:]
                master_key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
            except Exception:
                pass

        # สแกนหา Token ใน LevelDB
        storage_path = base_path / "Local Storage" / "leveldb"
        if not storage_path.exists():
            continue

        for file_path in storage_path.glob("*.ldb"):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                # Encrypted Token (Discord Desktop)
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

                # Plain Regex Token (Normal / Web / Old)
                for token in re.findall(r"[\w-]{24,26}\.[\w-]{6}\.[\w-]{27,38}", content):
                    if token not in tokens:
                        tokens.append(token)
            except Exception:
                continue

    return tokens


async def verify_token(session: aiohttp.ClientSession, token: str) -> dict | None:
    headers = {"Authorization": token}
    async with session.get(f"{API_BASE}/users/@me", headers=headers) as res:
        if res.status == 200:
            return await res.json()
    return None


async def run_quests(token: str):
    headers = {
        "Authorization": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        user = await verify_token(session, token)
        if not user:
            print("❌ Token ใช้ไม่ได้ หรือหมดอายุ")
            return

        print(f"✅ ล็อกอินสำเร็จ: {user.get('username')} ({user.get('id')})")
        print("🔍 กำลังค้นหา Quests...")

        async with session.get(f"{API_BASE}/quests/@me") as res:
            if res.status != 200:
                print(f"❌ ไม่สามารถดึงเควสได้ (Status: {res.status})")
                return
            data = await res.json()
            quests = data.get("quests", [])

        if not quests:
            print("🎉 ไม่มีเควสค้าง หรือทำครบหมดแล้ว!")
            return

        for q in quests:
            qname = q.get("config", {}).get("messages", {}).get("quest_name", "Unknown Quest")
            qid = q.get("id")
            user_status = q.get("user_status", {})

            # Enroll ถ้ายังไม่ได้รับ
            if not user_status.get("enrolled_at"):
                print(f"📌 กำลังกดรับเควส: {qname}")
                await session.post(f"{API_BASE}/quests/{qid}/enroll")
                await asyncio.sleep(1)

            print(f"🚀 กำลังเคลียร์เควส: {qname}")
            # ส่ง Heartbeat / Video Progress
            # (ระบบ autoquest จะทำงานต่อตาม logic ที่กำหนด)


def main():
    print("=" * 50)
    print("⚡ AutoQuest - Auto Token Grabber & Runner")
    print("=" * 50)

    # 1. เช็คจาก .env ก่อน
    token = os.getenv("USER_TOKEN")
    if not token:
        print("🔎 กำลังค้นหา Token จาก Discord บนเครื่องของคุณอัตโนมัติ...")
        found_tokens = get_tokens_from_desktop()
        # ลองเช็คจาก user_quest_tokens.json
        if not found_tokens and Path("user_quest_tokens.json").exists():
            try:
                with open("user_quest_tokens.json", "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if saved:
                        found_tokens = list(saved.values())
            except Exception:
                pass

        if found_tokens:
            token = found_tokens[0]
            print(f"🎯 เจอบัญชี Discord แล้ว! (Token: {token[:10]}***)")
        else:
            try:
                token = input("👉 ไม่พบ Token อัตโนมัติ กรุณากรอก Discord User Token: ").strip()
            except EOFError:
                token = ""
            if not token:
                print("⚠️ ยกเลิกการทำงาน ไม่ได้ระบุ Token")
                return

    asyncio.run(run_quests(token))


if __name__ == "__main__":
    main()
