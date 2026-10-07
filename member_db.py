import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB_FILE = Path(__file__).parent / "verified_users.json"
_lock = threading.RLock()


def _get_default_db() -> dict:
    return {
        "version": 1,
        "users": {},
        "pull_history": []
    }


def _save_db_unlocked(data: dict):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[DB ERROR] Save failed: {e}", flush=True)


def load_db() -> dict:
    with _lock:
        if not DB_FILE.exists():
            data = _get_default_db()
            _save_db_unlocked(data)
            return data
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return _get_default_db()


def save_db(data: dict):
    with _lock:
        _save_db_unlocked(data)


def add_or_update_user(user_info: dict, access_token: str, refresh_token: str = None, scope: str = None, ip: str = None, initial_guild_id: str = None) -> dict:
    """บันทึกหรืออัปเดตสมาชิกที่กดยืนยันตัวตน OAuth2"""
    with _lock:
        db = load_db()
        users = db.setdefault("users", {})
        uid = str(user_info.get("id"))
        
        avatar_hash = user_info.get("avatar")
        avatar_url = f"https://cdn.discordapp.com/avatars/{uid}/{avatar_hash}.png?size=128" if avatar_hash else "https://cdn.discordapp.com/embed/avatars/0.png"
        
        now_iso = datetime.now(timezone.utc).isoformat()
        existing = users.get(uid, {})
        
        guilds_joined = existing.get("guilds_joined", [])
        if initial_guild_id and initial_guild_id not in guilds_joined:
            guilds_joined.append(initial_guild_id)
            
        record = {
            "user_id": uid,
            "username": user_info.get("username", "Unknown"),
            "global_name": user_info.get("global_name") or user_info.get("username", "Unknown"),
            "avatar": avatar_url,
            "access_token": access_token,
            "refresh_token": refresh_token or existing.get("refresh_token"),
            "scope": scope or existing.get("scope", "identify guilds guilds.join"),
            "verified_at": existing.get("verified_at", now_iso),
            "last_updated": now_iso,
            "ip": ip or existing.get("ip", ""),
            "guilds_joined": guilds_joined,
            "status": "active"
        }
        
        users[uid] = record
        _save_db_unlocked(db)
        print(f"💾 [DB SAVED] Verified user saved: {record['global_name']} ({uid})", flush=True)
        return record


def mark_guild_joined(user_id: str, guild_id: str):
    with _lock:
        db = load_db()
        uid = str(user_id)
        gid = str(guild_id)
        if uid in db.get("users", {}):
            glist = db["users"][uid].setdefault("guilds_joined", [])
            if gid not in glist:
                glist.append(gid)
                _save_db_unlocked(db)


def mark_token_invalid(user_id: str, reason: str = "invalid_or_expired"):
    with _lock:
        db = load_db()
        uid = str(user_id)
        if uid in db.get("users", {}):
            db["users"][uid]["status"] = reason
            _save_db_unlocked(db)


def get_all_users() -> list[dict]:
    with _lock:
        db = load_db()
        return list(db.get("users", {}).values())


def get_stats() -> dict:
    with _lock:
        db = load_db()
        users = db.get("users", {})
        total = len(users)
        active = sum(1 for u in users.values() if u.get("status") == "active")
        history = db.get("pull_history", [])
        total_pulled = sum(h.get("success_count", 0) for h in history)
        
        return {
            "total_users": total,
            "active_tokens": active,
            "total_pulled": total_pulled,
            "total_pull_operations": len(history)
        }


def record_pull_history(entry: dict):
    with _lock:
        db = load_db()
        history = db.setdefault("pull_history", [])
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        history.insert(0, entry)
        # เก็บประวัติ 50 รายการล่าสุด
        if len(history) > 50:
            db["pull_history"] = history[:50]
        _save_db_unlocked(db)
