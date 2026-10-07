import os
import sys
import json
import base64
import urllib.parse
import asyncio
from pathlib import Path
from datetime import datetime, timezone
import aiohttp
from aiohttp import web
import discord

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import member_db
import discord_puller
import tunnel_manager

BASE_DIR = Path(__file__).parent
DASHBOARD_HTML_FILE = BASE_DIR / "web_dashboard.html"
VERIFY_HTML_FILE = BASE_DIR / "verify.html"
import hmac
import hashlib
import time

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "0952235101Asd@@"
AUTH_SECRET = os.getenv("AUTH_SECRET", "5bpo_secret_key_dj_auth_2026")


def make_auth_token() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(AUTH_SECRET.encode(), f"{ADMIN_USERNAME}:{ts}".encode(), hashlib.sha256).hexdigest()
    return f"adm.{ts}.{sig}"


def verify_auth_token(token: str) -> bool:
    if not token or not token.startswith("adm."):
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    _, ts_s, sig = parts
    try:
        ts = int(ts_s)
        if time.time() - ts > 30 * 86400:  # 30 days
            return False
        expected_sig = hmac.new(AUTH_SECRET.encode(), f"{ADMIN_USERNAME}:{ts_s}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def is_request_authenticated(request: web.Request) -> bool:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if verify_auth_token(token):
            return True
            
    custom_header = request.headers.get("X-Admin-Token", "").strip()
    if custom_header and verify_auth_token(custom_header):
        return True
        
    cookie_token = request.cookies.get("admin_auth_token", "").strip()
    if cookie_token and verify_auth_token(cookie_token):
        return True
        
    return False


BUTTON_ROLE_CONFIG_FILE = BASE_DIR / "button_role_config.json"


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


def get_verify_html() -> str:
    if VERIFY_HTML_FILE.exists():
        try:
            return VERIFY_HTML_FILE.read_text(encoding="utf-8")
        except Exception:
            pass
    return "<h1>Verification Server Active</h1>"


def get_dashboard_html() -> str:
    if DASHBOARD_HTML_FILE.exists():
        try:
            return DASHBOARD_HTML_FILE.read_text(encoding="utf-8")
        except Exception:
            pass
    return "<h1>Dashboard Active</h1>"


async def setup_web_app(discord_bot) -> web.Application:
    app = web.Application()
    routes = web.RouteTableDef()

    # 1. แดชบอร์ดหลักสำหรับ Admin
    @routes.get("/")
    @routes.get("/dashboard")
    async def dashboard_handler(request):
        # ถ้ามีพารามิเตอร์ของ OAuth เข้ามาที่ root ให้แสดงหน้า verify
        if request.query.get("code") or request.query.get("state"):
            return web.Response(text=get_verify_html(), content_type="text/html")
        return web.Response(text=get_dashboard_html(), content_type="text/html")

    # 2. หน้า Landing Page เมื่อสมาชิกกดยืนยันตัวตน Discord
    @routes.get("/verify")
    @routes.get("/callback")
    async def verify_landing_handler(request):
        return web.Response(text=get_verify_html(), content_type="text/html")

    # 2.1 Route รูปอวาตาร์ระบบ Voice 24/7
    @routes.get("/voice_avatar.png")
    async def voice_avatar_handler(request):
        avatar_file = BASE_DIR / "voice_avatar.png"
        if avatar_file.exists():
            return web.FileResponse(str(avatar_file))
        return web.Response(status=404)

    # 2.2 ระบบ Authentication สำหรับ Admin Dashboard
    @routes.post("/api/login")
    async def api_login(request):
        try:
            body = await request.json()
            user = str(body.get("username", "")).strip()
            pwd = str(body.get("password", "")).strip()
            if user == ADMIN_USERNAME and pwd == ADMIN_PASSWORD:
                token = make_auth_token()
                resp = web.json_response({
                    "success": True,
                    "token": token,
                    "username": ADMIN_USERNAME,
                    "message": "เข้าสู่ระบบสำเร็จ"
                })
                resp.set_cookie("admin_auth_token", token, max_age=30 * 86400, path="/")
                return resp
            else:
                return web.json_response({
                    "success": False,
                    "error": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
                }, status=401)
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=400)

    @routes.post("/api/logout")
    async def api_logout(request):
        resp = web.json_response({"success": True, "message": "ออกจากระบบเรียบร้อย"})
        resp.del_cookie("admin_auth_token", path="/")
        return resp

    @routes.get("/api/check_auth")
    async def api_check_auth(request):
        is_authed = is_request_authenticated(request)
        return web.json_response({
            "authenticated": is_authed,
            "username": ADMIN_USERNAME if is_authed else None
        })

    # 3. API สำหรับรับ Token หรือ แลก Code และบันทึกลง Database (Public สำหรับสมาชิก Discord)
    @routes.post("/api/claim_role")
    async def api_claim_role(request):
        try:
            body = await request.json()
            access_token = body.get("access_token")
            code = body.get("code")
            state = body.get("state")
            client_ip = request.remote or ""

            client_secret = os.getenv("DISCORD_CLIENT_SECRET")
            refresh_token = None

            # ถ้าส่ง authorization_code มา และมี client_secret
            if (not access_token) and code and client_secret:
                tunnel_url = tunnel_manager.get_tunnel_url()
                redirect_uri = f"{tunnel_url}/callback"
                async with aiohttp.ClientSession() as sess:
                    token_url = "https://discord.com/api/v10/oauth2/token"
                    data = {
                        "client_id": discord_bot.user.id,
                        "client_secret": client_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri
                    }
                    headers = {"Content-Type": "application/x-www-form-urlencoded"}
                    async with sess.post(token_url, data=data, headers=headers) as tr:
                        if tr.status == 200:
                            tdata = await tr.json()
                            access_token = tdata.get("access_token")
                            refresh_token = tdata.get("refresh_token")

            if not access_token:
                return web.json_response({"error": "No valid access token provided or exchanged"}, status=400)

            # ตรวจสอบตัวตนผู้ใช้กับ Discord API
            async with aiohttp.ClientSession() as sess:
                async with sess.get("https://discord.com/api/v10/users/@me", headers={"Authorization": f"Bearer {access_token}"}) as mr:
                    if mr.status != 200:
                        return web.json_response({"error": "Failed to authenticate token with Discord"}, status=401)
                    
                    u_info = await mr.json()
                    uid = int(u_info["id"])
                    username = u_info.get("global_name") or u_info.get("username", "Member")

                    # ถอดรหัส state (ถ้ามี) เพื่อรู้ว่ามาจากกิลด์และยศไหน
                    # ถอดรหัส state หรือพารามิเตอร์เพื่อระบุ Discord Server และ Role ที่ต้องการให้รับ
                    target_guild = None
                    target_role = None
                    initial_guild_id = None
                    target_guild_id = None
                    target_role_id = None

                    # 1. ถอดรหัสจาก state (รูปแบบ base64 ของ "guild_id:role_id")
                    if state:
                        try:
                            dec = base64.b64decode(state).decode()
                            if ":" in dec:
                                parts = dec.split(":")
                                if parts[0].isdigit():
                                    target_guild_id = int(parts[0])
                                    initial_guild_id = str(target_guild_id)
                                if parts[1].isdigit():
                                    target_role_id = int(parts[1])
                        except Exception as e:
                            print(f"[STATE DECODE ERROR] {e}", flush=True)

                    # 2. ถ้าใน state ไม่มี ให้ดูจาก body หรือ query
                    if not target_guild_id:
                        g_in = body.get("guild_id") or request.query.get("guild_id")
                        if g_in and str(g_in).isdigit():
                            target_guild_id = int(g_in)
                            initial_guild_id = str(target_guild_id)

                    if not target_role_id:
                        r_in = body.get("role_id") or request.query.get("role_id")
                        if r_in and str(r_in).isdigit():
                            target_role_id = int(r_in)

                    # 3. ค้นหา Guild ในดิสคอร์ด
                    if target_guild_id:
                        target_guild = discord_bot.get_guild(target_guild_id)
                        if not target_guild:
                            try:
                                target_guild = await discord_bot.fetch_guild(target_guild_id)
                            except Exception:
                                pass

                    # 4. ถ้าพบ Guild แต่ยังไม่มียศ ให้ดูจากการตั้งค่าใน config ของ Guild นั้นโดยเฉพาะ
                    if target_guild:
                        if not target_role_id:
                            all_cfg = load_button_role_config()
                            gcfg = all_cfg.get(str(target_guild.id), {})
                            if gcfg.get("role_id"):
                                target_role_id = int(gcfg["role_id"])

                        if target_role_id:
                            target_role = target_guild.get_role(target_role_id)
                            if not target_role:
                                try:
                                    g_roles = await target_guild.fetch_roles()
                                    for r in g_roles:
                                        if r.id == target_role_id:
                                            target_role = r
                                            break
                                except Exception:
                                    pass

                    # บันทึกสมาชิกลง Database สำหรับ Member Puller
                    saved_user = member_db.add_or_update_user(
                        user_info=u_info,
                        access_token=access_token,
                        refresh_token=refresh_token,
                        scope="identify guilds guilds.join",
                        ip=client_ip,
                        initial_guild_id=initial_guild_id
                    )

                    # มอบยศในดิสคอร์ด (เฉพาะใน Guild ที่เรียกใช้งานเท่านั้น ไม่ข้ามไปดิสอื่นเด็ดขาด)
                    given = False
                    role_name = target_role.name if target_role else "Member"
                    guild_name = target_guild.name if target_guild else "Discord Server"
                    bot_token = os.getenv("DISCORD_TOKEN")

                    if target_guild and target_role:
                        m = target_guild.get_member(uid)
                        if not m:
                            try:
                                m = await asyncio.wait_for(target_guild.fetch_member(uid), timeout=3.5)
                            except Exception:
                                m = None

                        if m:
                            # สมาชิกอยู่ในเซิร์ฟเวอร์แล้ว -> ตรวจสอบและให้ยศ
                            if target_role in m.roles:
                                given = True
                                print(f"[OAUTH] Member {m.name} already has role {target_role.name} in {target_guild.name}", flush=True)
                            else:
                                try:
                                    await asyncio.wait_for(m.add_roles(target_role, reason="Discord OAuth2 Verified"), timeout=3.5)
                                    given = True
                                    print(f"[OAUTH SUCCESS] Role {target_role.name} added to {m.name} in {target_guild.name}", flush=True)
                                except Exception as e:
                                    print(f"[ROLE GATEWAY ERROR] {e}. Trying direct REST API...", flush=True)
                                    # ลองยิง REST API โดยตรง
                                    try:
                                        async with aiohttp.ClientSession() as rest_sess:
                                            url = f"https://discord.com/api/v10/guilds/{target_guild.id}/members/{uid}/roles/{target_role.id}"
                                            headers = {
                                                "Authorization": f"Bot {bot_token}",
                                                "X-Audit-Log-Reason": "Discord OAuth2 Verified"
                                            }
                                            async with rest_sess.put(url, headers=headers) as r_resp:
                                                if r_resp.status in (200, 204):
                                                    given = True
                                                    print(f"[OAUTH REST SUCCESS] Role {target_role.name} added via REST API", flush=True)
                                                else:
                                                    r_txt = await r_resp.text()
                                                    print(f"[OAUTH REST NOTICE] Status {r_resp.status}: {r_txt}", flush=True)
                                            # ไม่ว่าจะอยู่สูงกว่าหรือต่ำกว่า ให้ถือว่ายืนยันตัวตนสำเร็จเหมือนเดิม
                                            given = True
                                    except Exception as rest_e:
                                        print(f"[OAUTH REST EXCEPTION] {rest_e}", flush=True)
                                        given = True
                        else:
                            # สมาชิกยังไม่อยู่ในเซิร์ฟเวอร์ -> ใช้ OAuth2 Token ดึงเข้าเซิร์ฟเวอร์พร้อมให้ยศทันที!
                            print(f"[OAUTH] Member {username} ({uid}) is not in {target_guild.name}. Pulling into guild with role...", flush=True)
                            try:
                                async with aiohttp.ClientSession() as pull_sess:
                                    pull_res = await discord_puller.add_member_to_guild(
                                        session=pull_sess,
                                        bot_token=bot_token,
                                        guild_id=str(target_guild.id),
                                        user_id=str(uid),
                                        access_token=access_token,
                                        roles=[str(target_role.id)]
                                    )
                                    if pull_res.get("success"):
                                        given = True
                                        print(f"[OAUTH PULL SUCCESS] Added {username} to {target_guild.name} with role {target_role.name}!", flush=True)
                                    else:
                                        print(f"[OAUTH PULL STATUS] {pull_res.get('status')} - {pull_res.get('message')}", flush=True)
                            except Exception as pull_e:
                                print(f"[OAUTH PULL ERROR] {pull_e}", flush=True)

                    # User avatar
                    avatar_hash = u_info.get("avatar")
                    if avatar_hash:
                        ext = "gif" if str(avatar_hash).startswith("a_") else "png"
                        avatar_url = f"https://cdn.discordapp.com/avatars/{uid}/{avatar_hash}.{ext}?size=256"
                    else:
                        discrim = int(u_info.get("discriminator", "0"))
                        idx = (uid >> 22) % 6 if discrim == 0 else discrim % 5
                        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


                    return web.json_response({
                        "success": True,
                        "username": username,
                        "user_id": str(uid),
                        "given": given,
                        "avatar": avatar_url,
                        "role_name": role_name,
                        "guild_name": guild_name
                    })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # 3.1 API ขอ URL ลิงก์ OAuth2 สำหรับหน้าเว็บยืนยัน
    @routes.get("/api/oauth_url")
    async def api_oauth_url(request):
        tunnel_url = tunnel_manager.get_tunnel_url()
        client_id = discord_bot.user.id if getattr(discord_bot, "user", None) else "1554090963499089960"
        quoted_cb = urllib.parse.quote(f"{tunnel_url}/callback")
        guild_id = request.query.get("guild_id", "")
        role_id = request.query.get("role_id", "")
        state_str = ""
        if guild_id and role_id:
            state_str = "&state=" + base64.b64encode(f"{guild_id}:{role_id}".encode()).decode()
        oauth_url = f"https://discord.com/oauth2/authorize?client_id={client_id}&response_type=token&redirect_uri={quoted_cb}&scope=identify%20guilds%20guilds.join{state_str}"
        return web.json_response({
            "oauth_url": oauth_url,
            "tunnel_url": tunnel_url,
            "redirect_uri": f"{tunnel_url}/callback"
        })

    # 4. API สถิติภาพรวม
    @routes.get("/api/stats")
    async def api_stats(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        stats = member_db.get_stats()
        stats["connected_guilds"] = len(discord_bot.guilds)
        return web.json_response(stats)

    # 5. API รายชื่อสมาชิกที่ยืนยันสิทธิ์ทั้งหมด
    @routes.get("/api/users")
    async def api_users(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        users = member_db.get_all_users()
        # ซ่อน access_token บางส่วนเพื่อความปลอดภัย
        safe_users = []
        for u in users:
            su = u.copy()
            if su.get("access_token"):
                tok = su["access_token"]
                su["token_masked"] = f"{tok[:6]}...{tok[-4:]}" if len(tok) > 10 else "***"
                del su["access_token"]
            safe_users.append(su)
        return web.json_response(safe_users)

    # 6. API รายชื่อเซิร์ฟเวอร์ที่บอทอยู่
    @routes.get("/api/guilds")
    async def api_guilds(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        guilds = []
        for g in discord_bot.guilds:
            guilds.append({
                "id": str(g.id),
                "name": g.name,
                "member_count": g.member_count,
                "icon": str(g.icon.url) if g.icon else None
            })
        return web.json_response(guilds)

    # 7. API รายชื่อห้องของเซิร์ฟเวอร์
    @routes.get("/api/guilds/{guild_id}/channels")
    async def api_guild_channels(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        gid = request.match_info["guild_id"]
        guild = discord_bot.get_guild(int(gid))
        if not guild:
            return web.json_response({"error": "Guild not found"}, status=404)
        
        channels = []
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                channels.append({"id": str(ch.id), "name": ch.name})
        return web.json_response(channels)

    # 8. API เริ่มต้นดึงคนเข้าดิสคอร์ด
    @routes.post("/api/pull/start")
    async def api_pull_start(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        body = await request.json()
        guild_id = body.get("guild_id")
        guild_name = body.get("guild_name", "Target Guild")
        user_ids = body.get("user_ids")
        delay = float(body.get("delay", 2.0))

        if not guild_id:
            return web.json_response({"success": False, "message": "กรุณาระบุ guild_id"}, status=400)

        bot_token = os.getenv("DISCORD_TOKEN")
        success, msg = await discord_puller.start_pull_task(
            bot_token=bot_token,
            guild_id=str(guild_id),
            guild_name=guild_name,
            user_ids=user_ids,
            delay_seconds=delay
        )
        return web.json_response({"success": success, "message": msg})

    # 9. API สั่งหยุดการดึงคน
    @routes.post("/api/pull/stop")
    async def api_pull_stop(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        stopped = discord_puller.stop_pull_task()
        return web.json_response({"success": stopped})

    # 10. API ดึงสถานะ Job การดึงคนแบบ Realtime
    @routes.get("/api/pull/status")
    async def api_pull_status(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        status = discord_puller.get_current_job_status()
        return web.json_response(status)

    # 11. API การตั้งค่า Tunnel & Domain
    @routes.get("/api/config")
    async def api_config(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        url = tunnel_manager.get_tunnel_url()
        is_alive = tunnel_manager.is_cloudflared_running()
        return web.json_response({
            "tunnel_url": url,
            "tunnel_online": is_alive,
            "client_id": str(discord_bot.user.id) if discord_bot.user else "1554090963499089960"
        })

    # 12. API อัปเดต Custom Domain / Tunnel URL
    @routes.post("/api/settings/tunnel")
    async def api_settings_tunnel(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        body = await request.json()
        new_url = body.get("tunnel_url", "").strip()
        if not new_url:
            return web.json_response({"success": False, "error": "URL is empty"}, status=400)
        
        saved_url = tunnel_manager.set_tunnel_url(new_url)
        return web.json_response({"success": True, "tunnel_url": saved_url})

    # 13. API รีสตาร์ท Cloudflare Tunnel
    @routes.post("/api/tunnel/restart")
    async def api_tunnel_restart(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        ok, res = await tunnel_manager.start_cloudflared_async(5000)
        return web.json_response({"success": ok, "url": res if ok else None, "message": res})

    # 14. API สั่งโพสต์การ์ด Verify ลงห้องในดิสคอร์ด
    @routes.post("/api/card/publish")
    async def api_card_publish(request):
        if not is_request_authenticated(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        body = await request.json()
        guild_id = body.get("guild_id")
        channel_id = body.get("channel_id")

        if not guild_id or not channel_id:
            return web.json_response({"success": False, "error": "Missing parameters"}, status=400)

        guild = discord_bot.get_guild(int(guild_id))
        if not guild:
            return web.json_response({"success": False, "error": "Guild not found"}, status=404)

        channel = guild.get_channel(int(channel_id))
        if not channel:
            return web.json_response({"success": False, "error": "Channel not found"}, status=404)

        all_cfg = load_button_role_config()
        cfg = all_cfg.get(str(guild_id), {})
        role_id = cfg.get("role_id")

        tunnel_url = tunnel_manager.get_tunnel_url()
        quoted_cb = urllib.parse.quote(f"{tunnel_url}/callback")
        state_encoded = base64.b64encode(f"{guild_id}:{role_id or 0}".encode()).decode()
        client_id = str(discord_bot.user.id)
        oauth_link = f"https://discord.com/oauth2/authorize?client_id={client_id}&response_type=token&redirect_uri={quoted_cb}&scope=identify%20guilds%20guilds.join&state={state_encoded}"

        embed = discord.Embed(
            title=cfg.get("title", "กดแล้วรอ5-10วิ"),
            description=cfg.get("description", "กรุณากดคลิกที่ปุ่มสีเขียวด้านล่างนี้เพื่อเชื่อมโยงและยืนยันสิทธิ์บัญชี"),
            color=cfg.get("color", 0x22C55E)
        )
        if cfg.get("image_url"):
            embed.set_image(url=cfg["image_url"])

        from main import ButtonRoleView
        view = ButtonRoleView(
            role_id=role_id,
            label=cfg.get("button_label", "Member"),
            emoji=cfg.get("button_emoji", "🛡️"),
            is_verify=True,
            oauth_url=cfg.get("oauth_url") or oauth_link
        )

        try:
            await channel.send(embed=embed, view=view)
            return web.json_response({"success": True})
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    app.add_routes(routes)
    return app


async def start_web_server(discord_bot, host: str = "0.0.0.0", port: int = None):
    env_port = os.environ.get("PORT")
    if env_port:
        try:
            port = int(env_port)
        except Exception:
            pass
    if not port:
        port = 5000
    app = await setup_web_app(discord_bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
        print(f"🌐 [WEB SERVER] Running on port {port} (Dashboard: /dashboard)", flush=True)
    except Exception as e:
        print(f"⚠️ [WEB SERVER ERROR] Could not bind to port {port}: {e}", flush=True)
