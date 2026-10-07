# language: Python, file: run_all.py, target: Windows 11
import os
import sys
import re
import time
import signal
import subprocess
import threading
from pathlib import Path

# บังคับใช้ UTF-8 บน Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).parent.resolve()
CLOUDFLARED_EXE = BASE_DIR / "cloudflared.exe"
TUNNEL_FILE = BASE_DIR / "tunnel_url.txt"
CONFIG_FILE = BASE_DIR / "button_role_config.json"
PHP_EXE = Path(r"C:\xampp\php\php.exe")

processes = []


def cleanup():
    """หยุดการทำงานของทุกโพรเซสเมื่อปิดหน้าต่าง"""
    print("\n🛑 กำลังปิดระบบทั้งหมด...", flush=True)
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def kill_existing():
    """เคลียร์โพรเซสเก่าที่อาจค้างอยู่ เพื่อไม่ให้บอทรันซ้อนกัน 2 ตัวเด็ดขาด"""
    if sys.platform == "win32":
        curr_pid = os.getpid()
        try:
            out = subprocess.check_output('wmic process where "name=\'python.exe\'" get processid,commandline', shell=True, text=True)
            for line in out.splitlines():
                if "main.py" in line or "run_all.py" in line:
                    parts = line.strip().split()
                    if parts:
                        try:
                            pid = int(parts[-1])
                            if pid != curr_pid:
                                subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except ValueError:
                            pass
        except Exception:
            pass


def update_configs(tunnel_url: str):
    """บันทึก URL ใหม่ลงใน tunnel_url.txt และ button_role_config.json"""
    try:
        TUNNEL_FILE.write_text(tunnel_url, encoding="utf-8")
    except Exception as e:
        print(f"[ERROR] Failed to save tunnel_url.txt: {e}", flush=True)

    if CONFIG_FILE.exists():
        try:
            import json
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            
            import urllib.parse, base64
            client_id = "1554090963499089960"
            quoted_cb = urllib.parse.quote(f"{tunnel_url}/callback")
            for gid, data in cfg.items():
                if data.get("type") == "verify":
                    role_id = data.get("role_id", "")
                    state_encoded = base64.b64encode(f"{gid}:{role_id}".encode()).decode() if role_id else ""
                    state_param = f"&state={state_encoded}" if state_encoded else ""
                    data["oauth_url"] = f"https://discord.com/oauth2/authorize?client_id={client_id}&response_type=token&redirect_uri={quoted_cb}&scope=identify%20guilds%20guilds.join{state_param}"
            
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print(f"🔄 [SYNC] อัปเดต button_role_config.json ด้วย {tunnel_url} เรียบร้อย", flush=True)
        except Exception as e:
            print(f"[ERROR] Sync config error: {e}", flush=True)


def is_tunnel_healthy(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    if sys.platform == "win32":
        try:
            res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cloudflared.exe"], capture_output=True, text=True)
            if "cloudflared.exe" in res.stdout:
                return True
        except Exception:
            pass
    try:
        import urllib.request
        req = urllib.request.Request(f"{url}/verify", headers={"User-Agent": "Health/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status in (200, 502)  # 502 means tunnel is connected to Cloudflare edge, even if backend is starting
    except Exception:
        return False


def main():
    print("=" * 65)
    print("  🔥 DISCORD BOT & VERIFICATION SYSTEM - UNIFIED LAUNCHER 🔥")
    print("  พัฒนาโดย VANTA & dj | รันครบทุกระบบในคลิกเดียว")
    print("=" * 65)

    # ฆ่า bot หรือ launcher ตัวเก่าก่อนเสมอ ป้องกันบอทรันซ้อน 2 ตัว
    kill_existing()

    current_tunnel = None
    if TUNNEL_FILE.exists():
        try:
            current_tunnel = TUNNEL_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    import tunnel_manager
    print("🌐 [1/3] กำลังเชื่อมต่อ ngrok Permanent Static Domain...", flush=True)
    ok, ng_url = tunnel_manager.start_ngrok_tunnel(5000)
    if ok and ng_url:
        current_tunnel = ng_url
        print(f"✅ [PERMANENT DOMAIN ONLINE] {current_tunnel}", flush=True)
        update_configs(current_tunnel)
    else:
        current_tunnel = "https://avert-starch-ragweed.ngrok-free.dev"
        update_configs(current_tunnel)

    tunnel_url = current_tunnel

    # 2. เริ่มต้น PHP Dashboard (ถ้ามี XAMPP)
    if PHP_EXE.exists() and (BASE_DIR / "web_php").exists():
        print("🐘 [2/3] กำลังเปิด PHP Dashboard (Port 8000)...", flush=True)
        php_cmd = [str(PHP_EXE), "-S", "0.0.0.0:8000", "-t", "web_php"]
        php_proc = subprocess.Popen(php_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(php_proc)

    # 3. แสดงแดชบอร์ดสรุปข้อมูล
    print("\n" + "=" * 65)
    print("  🚀 ทุกระบบพร้อมทำงานแล้ว:")
    print(f"  • Web Dashboard (Python) : http://127.0.0.1:5000/dashboard")
    if PHP_EXE.exists():
        print(f"  • Web Dashboard (PHP)    : http://localhost:8000")
    if tunnel_url:
        print(f"  • Public Verify Portal   : {tunnel_url}/verify")
        print(f"  • Discord Redirect URI   : {tunnel_url}/callback")
    print("=" * 65)
    print("  💡 อย่าลืมเช็คว่าใส่ Redirect URI ใน Discord Developer Portal ตรงกัน")
    print("  💡 กด Ctrl+C เพื่อหยุดการทำงานของระบบทั้งหมดพร้อมกัน")
    print("=" * 65 + "\n")

    # 4. รัน Discord Bot (main.py)
    print("🤖 [3/3] กำลังเริ่มต้นรัน Discord Bot...", flush=True)
    try:
        bot_proc = subprocess.Popen([sys.executable, "main.py"], cwd=str(BASE_DIR))
        processes.append(bot_proc)
        bot_proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        cleanup()
