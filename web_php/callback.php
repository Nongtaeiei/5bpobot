<?php
// language: PHP, file: web_php/callback.php, target: PHP 8.0+
declare(strict_types=1);

$config = require __DIR__ . '/config.php';
require_once __DIR__ . '/DiscordAPI.php';
require_once __DIR__ . '/database.php';

$api = new DiscordAPI($config['bot_token'], $config['client_id'], $config['client_secret']);
$db = new MemberDB($config['db_file']);

$code = $_GET['code'] ?? null;
$state = $_GET['state'] ?? null;
$userDisplayName = "Discord User";
$isSuccess = false;

if ($code && !empty($config['client_secret'])) {
    $tokenRes = $api->exchangeCode($code, $config['redirect_uri']);
    if ($tokenRes['status'] === 200 && isset($tokenRes['data']['access_token'])) {
        $accToken = $tokenRes['data']['access_token'];
        $refToken = $tokenRes['data']['refresh_token'] ?? null;
        $scope = $tokenRes['data']['scope'] ?? 'identify guilds guilds.join';

        $userRes = $api->getUserInfo($accToken);
        if ($userRes['status'] === 200 && isset($userRes['data']['id'])) {
            $uInfo = $userRes['data'];
            $userDisplayName = $uInfo['global_name'] ?? ($uInfo['username'] ?? 'Member');
            $db->addOrUpdateUser($uInfo, $accToken, $refToken, $scope, $_SERVER['REMOTE_ADDR'] ?? '');
            $isSuccess = true;
        }
    }
}
?>
<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>DISCORD OAUTH2 AUTHENTICATED</title>
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
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg);
            background-image: 
                radial-gradient(circle at 50% -20%, rgba(34, 197, 94, 0.15) 0%, transparent 60%),
                radial-gradient(circle at 50% 110%, rgba(56, 189, 248, 0.05) 0%, transparent 50%),
                linear-gradient(rgba(255, 255, 255, 0.012) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255, 255, 255, 0.012) 1px, transparent 1px);
            background-size: 100% 100%, 100% 100%, 24px 24px, 24px 24px;
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            overflow: hidden;
            position: relative;
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
            box-shadow: 0 30px 60px rgba(0, 0, 0, 0.85), inset 0 1px 0 rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(24px);
        }
        .shield-container {
            position: relative;
            width: 100px;
            height: 100px;
            margin: 0 auto 32px;
        }
        .shield-svg {
            width: 100%;
            height: 100%;
            fill: none;
            stroke: var(--success);
            stroke-width: 3.5;
            filter: drop-shadow(0 0 16px rgba(34, 197, 94, 0.45));
        }
        h1 {
            font-size: 20px;
            font-weight: 800;
            color: #fff;
            margin-bottom: 10px;
            letter-spacing: 1px;
        }
        p {
            font-size: 13.5px;
            line-height: 1.65;
            color: #94a3b8;
            margin-bottom: 24px;
        }
        p span.user {
            color: var(--success);
            font-weight: 700;
        }
        .audit-grid {
            background: rgba(0, 0, 0, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 14px;
            padding: 16px;
            font-size: 12px;
            margin-bottom: 24px;
            text-align: left;
            font-family: monospace;
        }
        .audit-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        .audit-row:last-child { margin-bottom: 0; }
        .audit-val {
            font-weight: 700;
            padding: 2px 6px;
            border-radius: 4px;
        }
        .audit-val.cyan { color: #38bdf8; background: rgba(56, 189, 248, 0.1); }
        .audit-val.green { color: #4ade80; background: rgba(74, 222, 128, 0.1); }
        .footer-bar {
            font-family: monospace;
            font-size: 11px;
            color: var(--text-muted);
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            padding-top: 14px;
            display: flex;
            justify-content: space-between;
        }
        .btn-home {
            display: inline-block;
            margin-top: 15px;
            background: #5865F2;
            color: #fff;
            text-decoration: none;
            padding: 8px 18px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
        }
    </style>
</head>
<body>
    <div class="vault-container">
        <div class="shield-container">
            <svg class="shield-svg" viewBox="0 0 100 100">
                <path d="M50,15 L80,24 L80,55 C80,74 50,85 50,85 C50,85 20,74 20,55 L20,24 Z" stroke-linecap="round"></path>
                <path d="M36,52 L47,63 L65,39" stroke-linecap="round"></path>
            </svg>
        </div>
        <h1>IDENTITY AUTHENTICATED</h1>
        <p>
            สิทธิ์การใช้งานบัญชี <span class="user" id="user-display">• <?= htmlspecialchars($userDisplayName) ?></span> ได้รับการอนุมัติและบันทึกลงในระบบเรียบร้อยแล้ว
        </p>
        <div class="audit-grid">
            <div class="audit-row">
                <span style="color:var(--text-muted);">&gt; PROTOCOL</span>
                <span class="audit-val cyan">PHP_OAUTH_2.0 // guilds.join</span>
            </div>
            <div class="audit-row">
                <span style="color:var(--text-muted);">&gt; DATABASE</span>
                <span class="audit-val green">RECORD_SAVED</span>
            </div>
            <div class="audit-row">
                <span style="color:var(--text-muted);">&gt; RESTORECORD</span>
                <span class="audit-val cyan">READY_FOR_PULL</span>
            </div>
        </div>
        <div class="footer-bar">
            <span>ENGINE: PHP 8.2</span>
            <span id="live-time"></span>
        </div>
        <a href="index.php" class="btn-home">เข้าสู่หน้าแดชบอร์ดหลัก ↗</a>
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
            const hashParams = new URLSearchParams(hash);
            const accessToken = hashParams.get("access_token");

            if (accessToken) {
                try {
                    const res = await fetch("api.php?action=claim_token", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ access_token: accessToken })
                    });
                    const data = await res.json();
                    if (data && data.success && data.username) {
                        const el = document.getElementById("user-display");
                        if (el) el.innerText = "• " + data.username;
                    }
                } catch (e) {
                    console.error("Token claim error:", e);
                }
            }
        });
    </script>
</body>
</html>
