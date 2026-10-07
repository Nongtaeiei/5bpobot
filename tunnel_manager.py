# language: Python, file: tunnel_manager.py, target: Windows 11 / Linux
import os
import re
import sys
import json
import asyncio
import subprocess
import urllib.parse
import base64
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).parent
TUNNEL_FILE = BASE_DIR / "tunnel_url.txt"
CLOUDFLARED_EXE = BASE_DIR / "cloudflared.exe"
CONFIG_FILE = BASE_DIR / "button_role_config.json"

_cloudflared_proc = None
_current_url = None


def get_tunnel_url() -> str:
    global _current_url
    if _current_url:
        return _current_url
    env_url = os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or os.getenv("HOSTING_URL")
    if env_url and env_url.strip().startswith("http"):
        _current_url = env_url.strip().rstrip("/")
        return _current_url
    if TUNNEL_FILE.exists():
        try:
            val = TUNNEL_FILE.read_text(encoding="utf-8").strip()
            if val.startswith("http"):
                _current_url = val.rstrip("/")
                return _current_url
        except Exception:
            pass
    return "https://bot5bpo.onrender.com"


def set_tunnel_url(new_url: str) -> str:
    global _current_url
    clean_url = new_url.strip().rstrip("/")
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = f"https://{clean_url}"
    _current_url = clean_url
    try:
        TUNNEL_FILE.write_text(clean_url, encoding="utf-8")
    except Exception as e:
        print(f"[TUNNEL] Failed to write tunnel_url.txt: {e}", flush=True)

    # อัปเดตใน button_role_config.json ด้วยเพื่อให้ปุ่มในดิสคอร์ดเป็นลิงก์ใหม่ทันที
    update_button_config_oauth(clean_url)
    return clean_url


def update_button_config_oauth(tunnel_url: str):
    if not CONFIG_FILE.exists():
        return
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        changed = False
        client_id = "1554090963499089960"
        quoted_cb = urllib.parse.quote(f"{tunnel_url}/callback")

        for gid, data in cfg.items():
            if data.get("type") == "verify":
                role_id = data.get("role_id", "")
                state_encoded = base64.b64encode(f"{gid}:{role_id}".encode()).decode() if role_id else ""
                state_param = f"&state={state_encoded}" if state_encoded else ""
                data["oauth_url"] = f"https://discord.com/oauth2/authorize?client_id={client_id}&response_type=token&redirect_uri={quoted_cb}&scope=identify%20guilds%20guilds.join{state_param}"
                changed = True
        
        if changed:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print(f"🔄 [TUNNEL SYNC] Updated button_role_config.json with per-guild OAuth links", flush=True)
    except Exception as e:
        print(f"[TUNNEL SYNC ERROR] {e}", flush=True)


NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "3KN9mNjIO4TkFAr5alfnzIPOttz_4QhPC8xhEPXGqieiPHrT4")
NGROK_DOMAIN = os.getenv("NGROK_DOMAIN", "avert-starch-ragweed.ngrok-free.dev")

_ngrok_tunnel = None


def start_ngrok_tunnel(port: int = 5000) -> tuple[bool, str]:
    """เริ่มรัน ngrok Tunnel ด้วย Static Domain ถาวร"""
    global _ngrok_tunnel
    try:
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = NGROK_AUTHTOKEN
        ngrok.set_auth_token(NGROK_AUTHTOKEN)
        
        # ปิด tunnel เก่าก่อนถ้ามี
        try:
            ngrok.disconnect(f"https://{NGROK_DOMAIN}")
        except Exception:
            pass

        _ngrok_tunnel = ngrok.connect(port, domain=NGROK_DOMAIN)
        url = _ngrok_tunnel.public_url
        set_tunnel_url(url)
        print(f"🎉 [NGROK READY] Permanent Static URL: {url}", flush=True)
        return True, url
    except Exception as e:
        print(f"❌ [NGROK ERROR] {e}", flush=True)
        # fallback เป็น URL เดิม
        fallback_url = f"https://{NGROK_DOMAIN}"
        set_tunnel_url(fallback_url)
        return False, str(e)


async def start_cloudflared_async(port: int = 5000, timeout: int = 25) -> tuple[bool, str]:
    """เริ่มรัน Tunnel (ใช้ ngrok Static Domain ถาวรเป็นหลักเพื่อไม่ให้ URL เปลี่ยน)"""
    # ใช้วิธี ngrok ก่อนเสมอ
    ok, url = start_ngrok_tunnel(port)
    if ok:
        return True, url

    # ถ้า ngrok ไม่ผ่าน ค่อย fallback ไป cloudflared
    global _cloudflared_proc
    if not CLOUDFLARED_EXE.exists():
        return False, "ไม่พบไฟล์ cloudflared.exe ในโฟลเดอร์"

    stop_cloudflared()
    cmd = [str(CLOUDFLARED_EXE), "tunnel", "--url", f"http://127.0.0.1:{port}"]
    print(f"🌐 [CLOUDFLARED] Launching: {' '.join(cmd)}", flush=True)

    try:
        _cloudflared_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        extracted_url = None
        start_time = asyncio.get_event_loop().time()

        while True:
            if asyncio.get_event_loop().time() - start_time > timeout:
                break
            line_bytes = await _cloudflared_proc.stderr.readline()
            if not line_bytes:
                await asyncio.sleep(0.5)
                continue
            line = line_bytes.decode("utf-8", errors="ignore")
            matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if matches:
                extracted_url = matches[0]
                break

        if extracted_url:
            set_tunnel_url(extracted_url)
            print(f"🎉 [CLOUDFLARED READY] Public URL: {extracted_url}", flush=True)
            return True, extracted_url
        else:
            return False, "Cloudflare Tunnel เริ่มทำงานแล้วแต่ยังไม่พบ URL"
    except Exception as e:
        return False, f"เกิดข้อผิดพลาดในการรัน cloudflared: {e}"


def stop_cloudflared():
    global _cloudflared_proc, _ngrok_tunnel
    if _ngrok_tunnel:
        try:
            from pyngrok import ngrok
            ngrok.disconnect(_ngrok_tunnel.public_url)
        except Exception:
            pass
        _ngrok_tunnel = None

    if _cloudflared_proc:
        try:
            _cloudflared_proc.terminate()
        except Exception:
            pass
        _cloudflared_proc = None
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def is_cloudflared_running() -> bool:
    global _cloudflared_proc, _ngrok_tunnel
    if _ngrok_tunnel:
        return True
    if _cloudflared_proc and _cloudflared_proc.returncode is None:
        return True
    if sys.platform == "win32":
        try:
            out = subprocess.check_output('tasklist /FI "IMAGENAME eq ngrok.exe"', shell=True, text=True)
            if "ngrok.exe" in out:
                return True
            out_cf = subprocess.check_output('tasklist /FI "IMAGENAME eq cloudflared.exe"', shell=True, text=True)
            return "cloudflared.exe" in out_cf
        except Exception:
            return False
    return False
