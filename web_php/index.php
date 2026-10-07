<?php
// language: PHP, file: web_php/index.php, target: PHP 8.0+
declare(strict_types=1);

$config = require __DIR__ . '/config.php';
require_once __DIR__ . '/DiscordAPI.php';
require_once __DIR__ . '/database.php';

$api = new DiscordAPI($config['bot_token'], $config['client_id'], $config['client_secret']);
$db = new MemberDB($config['db_file']);

$appInfoRes = $api->getApplicationInfo();
$app = $appInfoRes['data'] ?? [];

$appName = $app['name'] ?? 'Discord OAuth Hub';
$appId = (string)($app['id'] ?? $config['client_id']);
$appDesc = $app['description'] ?? 'ระบบจัดการสิทธิ์ Discord OAuth2 ดึงสมาชิกอัตโนมัติ และฐานข้อมูลความปลอดภัยระดับสูง';
$appIcon = !empty($app['icon']) 
    ? "https://cdn.discordapp.com/app-icons/{$appId}/{$app['icon']}.png?size=256" 
    : "https://cdn.discordapp.com/embed/avatars/0.png";

$isPublic = !empty($app['bot_public']);
$approxGuilds = $app['approximate_guild_count'] ?? 0;
$verifyKey = $app['verify_key'] ?? 'N/A';

$stats = $db->getStats();
$users = $db->getAllUsers();
$redirectUri = $config['redirect_uri'];
$oauthAuthorizeUrl = "https://discord.com/oauth2/authorize?client_id={$appId}&response_type=token&redirect_uri=" . urlencode($redirectUri) . "&scope=identify%20guilds%20guilds.join";
?>
<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title><?= htmlspecialchars($appName) ?> — Ultimate Discord OAuth2 Control Deck</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-deep: #030509;
            --bg-card: rgba(11, 15, 26, 0.72);
            --bg-card-hover: rgba(18, 24, 42, 0.85);
            --border-subtle: rgba(255, 255, 255, 0.07);
            --border-glow: rgba(88, 101, 242, 0.4);
            --blurple: #5865F2;
            --blurple-light: #7983f5;
            --emerald: #10b981;
            --emerald-glow: rgba(16, 185, 129, 0.35);
            --cyan: #06b6d4;
            --cyan-glow: rgba(6, 182, 212, 0.35);
            --violet: #8b5cf6;
            --rose: #f43f5e;
            --amber: #f59e0b;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-tertiary: #64748b;
            --radius-sm: 8px;
            --radius-md: 14px;
            --radius-lg: 22px;
            --radius-xl: 30px;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            background-color: var(--bg-deep);
            color: var(--text-primary);
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            min-height: 100vh;
            overflow-x: hidden;
            position: relative;
        }

        /* Ambient Dynamic Glow Orbs Background */
        .ambient-glow-1 {
            position: fixed;
            top: -150px;
            left: -100px;
            width: 650px;
            height: 650px;
            background: radial-gradient(circle, rgba(88, 101, 242, 0.18) 0%, transparent 70%);
            border-radius: 50%;
            pointer-events: none;
            z-index: 0;
            filter: blur(80px);
            animation: orbFloat1 20s infinite ease-in-out;
        }
        .ambient-glow-2 {
            position: fixed;
            bottom: -200px;
            right: -150px;
            width: 750px;
            height: 750px;
            background: radial-gradient(circle, rgba(16, 185, 129, 0.14) 0%, transparent 70%);
            border-radius: 50%;
            pointer-events: none;
            z-index: 0;
            filter: blur(90px);
            animation: orbFloat2 24s infinite ease-in-out;
        }
        .grid-overlay {
            position: fixed;
            inset: 0;
            background-image: 
                linear-gradient(rgba(255, 255, 255, 0.015) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255, 255, 255, 0.015) 1px, transparent 1px);
            background-size: 36px 36px;
            pointer-events: none;
            z-index: 0;
        }

        @keyframes orbFloat1 {
            0%, 100% { transform: translate(0, 0) scale(1); }
            50% { transform: translate(80px, 60px) scale(1.1); }
        }
        @keyframes orbFloat2 {
            0%, 100% { transform: translate(0, 0) scale(1); }
            50% { transform: translate(-60px, -50px) scale(1.15); }
        }

        /* Top Navigation Bar */
        header {
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(28px);
            -webkit-backdrop-filter: blur(28px);
            background: rgba(6, 8, 14, 0.75);
            border-bottom: 1px solid var(--border-subtle);
            padding: 16px 36px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .nav-brand {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .nav-avatar-box {
            position: relative;
        }
        .nav-avatar {
            width: 44px;
            height: 44px;
            border-radius: 14px;
            object-fit: cover;
            border: 2px solid rgba(88, 101, 242, 0.5);
            box-shadow: 0 0 20px rgba(88, 101, 242, 0.45);
        }
        .online-dot {
            position: absolute;
            bottom: -2px;
            right: -2px;
            width: 12px;
            height: 12px;
            background: var(--emerald);
            border: 2.5px solid var(--bg-deep);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--emerald);
        }
        .brand-title {
            font-size: 18px;
            font-weight: 800;
            letter-spacing: -0.5px;
            background: linear-gradient(135deg, #ffffff 40%, #cbd5e1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .brand-subtitle {
            font-size: 11px;
            font-family: 'JetBrains Mono', monospace;
            color: var(--cyan);
            letter-spacing: 0.5px;
        }
        .nav-actions {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .badge-live {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 700;
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid rgba(16, 185, 129, 0.35);
            color: var(--emerald);
        }
        .pulse-indicator {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
            box-shadow: 0 0 10px currentColor;
            animation: pulseFade 1.6s infinite ease-in-out;
        }
        @keyframes pulseFade {
            0%, 100% { opacity: 0.4; transform: scale(0.85); }
            50% { opacity: 1; transform: scale(1.2); }
        }

        .btn-devportal {
            background: linear-gradient(135deg, rgba(88, 101, 242, 0.2), rgba(88, 101, 242, 0.08));
            color: #fff;
            border: 1px solid var(--border-glow);
            padding: 9px 18px;
            border-radius: 10px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .btn-devportal:hover {
            background: var(--blurple);
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(88, 101, 242, 0.5);
        }

        /* Container & Grid */
        .main-container {
            position: relative;
            z-index: 1;
            max-width: 1440px;
            margin: 0 auto;
            padding: 32px 36px 80px;
        }

        /* Hero App Card */
        .app-hero-card {
            background: linear-gradient(145deg, rgba(17, 24, 39, 0.8), rgba(10, 14, 26, 0.9));
            border: 1px solid var(--border-subtle);
            border-top: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: var(--radius-xl);
            padding: 32px 36px;
            margin-bottom: 30px;
            box-shadow: 0 30px 60px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(20px);
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 30px;
            flex-wrap: wrap;
            position: relative;
            overflow: hidden;
        }
        .app-hero-card::after {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--blurple), var(--emerald), transparent);
        }
        .hero-left {
            display: flex;
            align-items: center;
            gap: 24px;
        }
        .hero-icon-box {
            position: relative;
        }
        .hero-app-icon {
            width: 86px;
            height: 86px;
            border-radius: 24px;
            border: 3px solid rgba(88, 101, 242, 0.4);
            box-shadow: 0 0 30px rgba(88, 101, 242, 0.35);
        }
        .hero-title-group h1 {
            font-size: 26px;
            font-weight: 800;
            letter-spacing: -0.5px;
            margin-bottom: 6px;
        }
        .hero-desc {
            font-size: 14px;
            color: var(--text-secondary);
            max-width: 620px;
            line-height: 1.5;
            margin-bottom: 12px;
        }
        .hero-badges-row {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 12px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: #cbd5e1;
        }
        .chip.code {
            font-family: 'JetBrains Mono', monospace;
            color: var(--cyan);
        }
        .btn-authorize {
            background: linear-gradient(135deg, var(--emerald), #059669);
            color: #fff;
            padding: 14px 26px;
            border-radius: var(--radius-md);
            font-size: 15px;
            font-weight: 700;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            box-shadow: 0 10px 25px var(--emerald-glow);
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            white-space: nowrap;
        }
        .btn-authorize:hover {
            transform: translateY(-2px);
            box-shadow: 0 14px 35px rgba(16, 185, 129, 0.55);
        }

        /* Redirect URI Solver Banner */
        .solver-banner {
            background: linear-gradient(135deg, rgba(88, 101, 242, 0.14), rgba(6, 182, 212, 0.08));
            border: 1px solid rgba(88, 101, 242, 0.3);
            border-radius: var(--radius-lg);
            padding: 22px 28px;
            margin-bottom: 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            flex-wrap: wrap;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.35);
            position: relative;
        }
        .solver-banner::before {
            content: '';
            position: absolute;
            left: 0;
            top: 0;
            bottom: 0;
            width: 4px;
            border-radius: 4px 0 0 4px;
            background: linear-gradient(to bottom, var(--blurple), var(--cyan));
        }
        .solver-title {
            font-size: 15px;
            font-weight: 700;
            color: #fff;
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }
        .solver-desc {
            font-size: 13px;
            color: var(--text-secondary);
            line-height: 1.4;
        }
        .uri-display-box {
            display: flex;
            align-items: center;
            gap: 10px;
            background: rgba(0, 0, 0, 0.55);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 10px;
            padding: 9px 14px;
            min-width: 480px;
            max-width: 100%;
        }
        .uri-display-box code {
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            color: var(--cyan);
            word-break: break-all;
            flex: 1;
        }
        .btn-quick-copy {
            background: var(--blurple);
            color: #fff;
            border: none;
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s;
            white-space: nowrap;
        }
        .btn-quick-copy:hover {
            background: var(--blurple-light);
            transform: scale(1.02);
            box-shadow: 0 4px 15px rgba(88, 101, 242, 0.4);
        }

        /* Stats Cards 4 Columns */
        .stats-deck {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 20px;
            margin-bottom: 36px;
        }
        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-top: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: var(--radius-lg);
            padding: 24px 26px;
            position: relative;
            overflow: hidden;
            backdrop-filter: blur(16px);
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .stat-card:hover {
            transform: translateY(-3px);
            border-color: var(--border-glow);
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.45);
        }
        .stat-top-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 12px;
        }
        .stat-icon-wrapper {
            width: 42px;
            height: 42px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
        }
        .icon-users { background: rgba(88, 101, 242, 0.16); color: var(--blurple-light); border: 1px solid rgba(88, 101, 242, 0.3); }
        .icon-tokens { background: rgba(16, 185, 129, 0.16); color: var(--emerald); border: 1px solid rgba(16, 185, 129, 0.3); }
        .icon-guilds { background: rgba(6, 182, 212, 0.16); color: var(--cyan); border: 1px solid rgba(6, 182, 212, 0.3); }
        .icon-pulls { background: rgba(139, 92, 246, 0.16); color: var(--violet); border: 1px solid rgba(139, 92, 246, 0.3); }
        .stat-badge {
            font-size: 11px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-secondary);
        }
        .stat-num {
            font-size: 34px;
            font-weight: 800;
            letter-spacing: -1px;
            color: #fff;
            margin-bottom: 4px;
            font-variant-numeric: tabular-nums;
        }
        .stat-title {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-secondary);
        }

        /* 2-Column Split Body */
        .workspace-grid {
            display: grid;
            grid-template-columns: 1fr 420px;
            gap: 28px;
        }
        @media (max-width: 1120px) {
            .workspace-grid { grid-template-columns: 1fr; }
        }

        /* Glass Panels */
        .glass-panel {
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: var(--radius-xl);
            padding: 28px;
            backdrop-filter: blur(20px);
            box-shadow: 0 20px 45px rgba(0, 0, 0, 0.4);
            margin-bottom: 28px;
            position: relative;
        }
        .panel-heading {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 22px;
            padding-bottom: 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }
        .heading-title {
            font-size: 18px;
            font-weight: 800;
            color: #fff;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .heading-sub {
            font-size: 12.5px;
            color: var(--text-tertiary);
            margin-top: 2px;
        }

        /* Search Filter Box */
        .search-bar-wrap {
            position: relative;
            margin-bottom: 18px;
        }
        .search-input {
            width: 100%;
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 12px 18px 12px 42px;
            color: #fff;
            font-size: 14px;
            outline: none;
            transition: all 0.2s;
        }
        .search-input:focus {
            border-color: var(--blurple);
            box-shadow: 0 0 0 3px rgba(88, 101, 242, 0.25);
        }
        .search-icon {
            position: absolute;
            left: 14px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-tertiary);
            font-size: 15px;
        }

        /* Table */
        .table-scroll {
            overflow-x: auto;
            border-radius: var(--radius-md);
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13.5px;
            text-align: left;
        }
        th {
            background: rgba(0, 0, 0, 0.4);
            color: var(--text-secondary);
            font-weight: 600;
            padding: 14px 18px;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
        }
        td {
            padding: 16px 18px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.035);
            vertical-align: middle;
        }
        tr:hover td {
            background: rgba(255, 255, 255, 0.025);
        }
        .user-identity {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .avatar-thumb {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            object-fit: cover;
            border: 2px solid rgba(88, 101, 242, 0.3);
            transition: transform 0.2s;
        }
        .avatar-thumb:hover {
            transform: scale(1.15);
        }
        .user-name-bold {
            font-weight: 700;
            color: #fff;
        }
        .user-tag-sub {
            font-size: 11px;
            color: var(--text-tertiary);
            font-family: 'JetBrains Mono', monospace;
        }
        .status-pill-ok {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 700;
            background: rgba(16, 185, 129, 0.12);
            color: var(--emerald);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }
        .btn-action-pull {
            background: rgba(88, 101, 242, 0.15);
            color: #fff;
            border: 1px solid rgba(88, 101, 242, 0.35);
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            white-space: nowrap;
        }
        .btn-action-pull:hover {
            background: var(--blurple);
            box-shadow: 0 4px 12px rgba(88, 101, 242, 0.4);
        }

        /* Puller Controller Styling */
        .controller-box {
            background: rgba(8, 11, 20, 0.6);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: var(--radius-lg);
            padding: 24px;
        }
        .input-group {
            margin-bottom: 20px;
        }
        .input-label {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 13px;
            font-weight: 700;
            color: #cbd5e1;
            margin-bottom: 8px;
        }
        .custom-select, .custom-field {
            width: 100%;
            background: rgba(0, 0, 0, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 10px;
            padding: 12px 16px;
            color: #fff;
            font-size: 14px;
            outline: none;
            transition: all 0.2s;
        }
        .custom-select:focus, .custom-field:focus {
            border-color: var(--blurple);
            box-shadow: 0 0 0 3px rgba(88, 101, 242, 0.25);
        }
        .btn-big-pull {
            width: 100%;
            background: linear-gradient(135deg, var(--emerald), #047857);
            color: #fff;
            border: none;
            padding: 16px;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 800;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            box-shadow: 0 10px 28px var(--emerald-glow);
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .btn-big-pull:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 14px 38px rgba(16, 185, 129, 0.55);
            background: linear-gradient(135deg, #34d399, #059669);
        }
        .btn-big-pull:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }

        /* Terminal Console */
        .terminal-panel {
            background: #04060a;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: var(--radius-md);
            padding: 16px;
            margin-top: 20px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            height: 230px;
            overflow-y: auto;
            color: #94a3b8;
            line-height: 1.6;
            position: relative;
        }
        .terminal-header {
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            font-size: 11px;
            color: var(--text-tertiary);
        }
        .term-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
        .term-red { background: #ef4444; }
        .term-yellow { background: #f59e0b; }
        .term-green { background: #10b981; }
        .log-msg { margin-bottom: 4px; word-break: break-all; }
        .log-msg.ok { color: var(--emerald); }
        .log-msg.err { color: var(--rose); }

        /* Toast Container */
        .toast-deck {
            position: fixed;
            bottom: 30px;
            right: 30px;
            z-index: 1000;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .toast-bubble {
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--border-glow);
            padding: 16px 22px;
            border-radius: 12px;
            font-size: 13.5px;
            color: #fff;
            box-shadow: 0 15px 40px rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(16px);
            display: flex;
            align-items: center;
            gap: 12px;
            animation: popIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }
        @keyframes popIn {
            from { transform: translateX(60px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
    </style>
</head>
<body>

    <div class="ambient-glow-1"></div>
    <div class="ambient-glow-2"></div>
    <div class="grid-overlay"></div>

    <header>
        <div class="nav-brand">
            <div class="nav-avatar-box">
                <img src="<?= htmlspecialchars($appIcon) ?>" class="nav-avatar" alt="App Icon"/>
                <div class="online-dot"></div>
            </div>
            <div>
                <div class="brand-title"><?= htmlspecialchars($appName) ?></div>
                <div class="brand-subtitle">PHP 8.2 • RESTORECORD OAUTH ENGINE</div>
            </div>
        </div>
        <div class="nav-actions">
            <div class="badge-live">
                <span class="pulse-indicator"></span>
                <span>SYSTEM OPERATIONAL</span>
            </div>
            <a href="https://discord.com/developers/applications/<?= htmlspecialchars($appId) ?>/oauth2" target="_blank" class="btn-devportal">
                ⚙️ Discord Developer Portal ↗
            </a>
        </div>
    </header>

    <div class="main-container">

        <!-- Application Resource Showcase Card -->
        <div class="app-hero-card">
            <div class="hero-left">
                <div class="hero-icon-box">
                    <img src="<?= htmlspecialchars($appIcon) ?>" class="hero-app-icon" alt="App Icon"/>
                </div>
                <div class="hero-title-group">
                    <h1><?= htmlspecialchars($appName) ?></h1>
                    <p class="hero-desc">
                        <?= htmlspecialchars($appDesc) ?>
                    </p>
                    <div class="hero-badges-row">
                        <span class="chip code">APP ID: <?= htmlspecialchars($appId) ?></span>
                        <span class="chip">🛡️ BOT PUBLIC: <?= $isPublic ? 'ENABLED' : 'PRIVATE' ?></span>
                        <span class="chip">🌐 GUILDS CONNECTED: <?= (int)$approxGuilds ?></span>
                        <span class="chip">🔑 SCOPES: identify, guilds.join</span>
                    </div>
                </div>
            </div>
            <div>
                <a href="<?= htmlspecialchars($oauthAuthorizeUrl) ?>" target="_blank" class="btn-authorize">
                    ⚡ ทดสอบลิงก์กดยืนยัน (OAuth Link) ↗
                </a>
            </div>
        </div>

        <!-- 1-Click Redirect URI Solver -->
        <div class="solver-banner">
            <div>
                <div class="solver-title">
                    <span>🛠️ วิธีแก้ "Invalid OAuth2 redirect_uri" ที่ขึ้น Error</span>
                </div>
                <div class="solver-desc">
                    คัดลอกลิงก์ Redirect URI นี้ ไปวางในหน้า <strong>Discord Developer Portal &gt; OAuth2 &gt; Redirects</strong>
                </div>
            </div>
            <div class="uri-display-box">
                <code id="uri-val"><?= htmlspecialchars($redirectUri) ?></code>
                <button class="btn-quick-copy" onclick="copyRedirectUri()">
                    📋 คัดลอกลิงก์
                </button>
            </div>
        </div>

        <!-- 4 Stat Counters -->
        <div class="stats-deck">
            <div class="stat-card">
                <div class="stat-top-row">
                    <div class="stat-icon-wrapper icon-users">👥</div>
                    <span class="stat-badge">VERIFIED</span>
                </div>
                <div class="stat-num" id="stat-total"><?= (int)($stats['total_users'] ?? 0) ?></div>
                <div class="stat-title">สมาชิกที่ยืนยันตัวตนทั้งหมด</div>
            </div>

            <div class="stat-card">
                <div class="stat-top-row">
                    <div class="stat-icon-wrapper icon-tokens">🔑</div>
                    <span class="stat-badge" style="color:var(--emerald);">READY</span>
                </div>
                <div class="stat-num" id="stat-active" style="color:var(--emerald);"><?= (int)($stats['active_tokens'] ?? 0) ?></div>
                <div class="stat-title">Token พร้อมดึงเข้าดิสคอร์ด</div>
            </div>

            <div class="stat-card">
                <div class="stat-top-row">
                    <div class="stat-icon-wrapper icon-guilds">🛡️</div>
                    <span class="stat-badge" style="color:var(--cyan);">BOT ACTIVE</span>
                </div>
                <div class="stat-num" id="stat-guilds" style="color:var(--cyan);">0</div>
                <div class="stat-title">เซิร์ฟเวอร์ที่บอทอยู่</div>
            </div>

            <div class="stat-card">
                <div class="stat-top-row">
                    <div class="stat-icon-wrapper icon-pulls">🚀</div>
                    <span class="stat-badge" style="color:var(--violet);">SUCCESS</span>
                </div>
                <div class="stat-num" id="stat-pulled" style="color:var(--violet);"><?= (int)($stats['total_pulled'] ?? 0) ?></div>
                <div class="stat-title">ยอดดึงสมาชิกสำเร็จสะสม</div>
            </div>
        </div>

        <!-- Main Workspace 2 Columns -->
        <div class="workspace-grid">

            <!-- Left: Table of Verified Members -->
            <div class="glass-panel">
                <div class="panel-heading">
                    <div>
                        <div class="heading-title">👥 ฐานข้อมูลสมาชิกที่ยืนยัน (Verified Members)</div>
                        <div class="heading-sub">บันทึกสิทธิ์ OAuth2 ของคนที่กดยืนยันปุ่มในดิสคอร์ดทั้งหมด</div>
                    </div>
                    <button class="btn-quick-copy" onclick="location.reload()">🔄 รีเฟรช</button>
                </div>

                <div class="search-bar-wrap">
                    <span class="search-icon">🔍</span>
                    <input type="text" id="search-box" class="search-input" placeholder="ค้นหาด้วยชื่อผู้ใช้ หรือ User ID..." onkeyup="filterUsers()"/>
                </div>

                <div class="table-scroll">
                    <table id="members-table">
                        <thead>
                            <tr>
                                <th>ผู้ใช้งาน Discord</th>
                                <th>User ID</th>
                                <th>วันที่กดยืนยัน</th>
                                <th>สถานะ Token</th>
                                <th>คำสั่ง</th>
                            </tr>
                        </thead>
                        <tbody>
                            <?php if (empty($users)): ?>
                                <tr>
                                    <td colspan="5" style="text-align:center; padding:36px; color:var(--text-tertiary);">
                                        ยังไม่มีสมาชิกกดยืนยันตัวตน กรุณานำลิงก์ไปให้คนกดในดิสคอร์ด
                                    </td>
                                </tr>
                            <?php else: ?>
                                <?php foreach ($users as $u): ?>
                                    <tr class="user-row">
                                        <td>
                                            <div class="user-identity">
                                                <img src="<?= htmlspecialchars($u['avatar']) ?>" class="avatar-thumb" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png'"/>
                                                <div>
                                                    <div class="user-name-bold u-name"><?= htmlspecialchars($u['global_name'] ?? $u['username']) ?></div>
                                                    <div class="user-tag-sub">@<?= htmlspecialchars($u['username']) ?></div>
                                                </div>
                                            </div>
                                        </td>
                                        <td>
                                            <code class="chip code u-id" style="padding:3px 8px; font-size:11px;"><?= htmlspecialchars($u['user_id']) ?></code>
                                        </td>
                                        <td style="font-size:12px; color:var(--text-secondary);">
                                            <?= htmlspecialchars($u['verified_at'] ? date('d/m/Y H:i', strtotime($u['verified_at'])) : '-') ?>
                                        </td>
                                        <td>
                                            <span class="status-pill-ok">
                                                <span class="pulse-indicator" style="width:6px; height:6px;"></span>
                                                พร้อมดึง
                                            </span>
                                        </td>
                                        <td>
                                            <button class="btn-action-pull" onclick="pullSingleUser('<?= htmlspecialchars($u['user_id']) ?>', '<?= htmlspecialchars(addslashes($u['global_name'] ?? $u['username'])) ?>')">
                                                ดึงคนนี้ ↗
                                            </button>
                                        </td>
                                    </tr>
                                <?php endforeach; ?>
                            <?php endif; ?>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Right: Member Puller Controller -->
            <div>
                <div class="glass-panel">
                    <div class="panel-heading">
                        <div>
                            <div class="heading-title">🧲 ระบบดึงคนเข้าดิสคอร์ด (OAuth Puller)</div>
                            <div class="heading-sub">ดึงสมาชิกที่ยืนยันทั้งหมดเข้าเซิร์ฟเวอร์ที่เลือก</div>
                        </div>
                    </div>

                    <div class="controller-box">
                        <div class="input-group">
                            <label class="input-label">
                                <span>เซิร์ฟเวอร์เป้าหมาย</span>
                                <span style="color:var(--text-tertiary); font-weight:normal;">(ที่บอทอยู่)</span>
                            </label>
                            <select id="guild-select" class="custom-select">
                                <option value="">-- กำลังโหลดเซิร์ฟเวอร์... --</option>
                            </select>
                        </div>

                        <div class="input-group">
                            <label class="input-label">
                                <span>ความเร็วในการดึง</span>
                                <span style="color:var(--emerald); font-weight:normal;">ป้องกัน Rate Limit</span>
                            </label>
                            <select id="delay-select" class="custom-select">
                                <option value="2.0">มาตรฐาน (2.0 วินาที/คน) — แนะนำ</option>
                                <option value="1.5">เร็ว (1.5 วินาที/คน)</option>
                                <option value="3.0">ปลอดภัยสูง (3.0 วินาที/คน)</option>
                            </select>
                        </div>

                        <button id="btn-pull-all" class="btn-big-pull" onclick="startPullBatch()">
                            🚀 เริ่มดึงคนเข้าดิสคอร์ดทันที
                        </button>

                        <div class="terminal-panel" id="terminal-console">
                            <div class="terminal-header">
                                <span class="term-dot term-red"></span>
                                <span class="term-dot term-yellow"></span>
                                <span class="term-dot term-green"></span>
                                <span style="margin-left:6px;">LIVE TERMINAL OUTPUT</span>
                            </div>
                            <div class="log-msg">> ระบบ PHP Discord Puller พร้อมทำงาน...</div>
                            <div class="log-msg">> เลือกเซิร์ฟเวอร์เป้าหมายแล้วกดปุ่มเพื่อเริ่มดึงคน</div>
                        </div>
                    </div>
                </div>

                <!-- Custom Domain / Tunnel Setting -->
                <div class="glass-panel">
                    <div class="panel-heading">
                        <div class="heading-title">🌐 โดเมน / Cloudflare Tunnel</div>
                    </div>
                    <div class="input-group">
                        <label class="input-label">URL สำหรับ Discord OAuth2</label>
                        <input type="text" id="tunnel-url-input" class="custom-field" value="<?= htmlspecialchars($config['tunnel_url']) ?>"/>
                    </div>
                    <button class="btn-quick-copy" style="width:100%; padding:12px; justify-content:center;" onclick="saveTunnelUrl()">
                        💾 บันทึกและอัปเดตระบบ
                    </button>
                </div>
            </div>

        </div>

    </div>

    <div class="toast-deck" id="toasts"></div>

    <script>
        function showToast(text, isSuccess = true) {
            const deck = document.getElementById('toasts');
            const toast = document.createElement('div');
            toast.className = 'toast-bubble';
            toast.innerHTML = (isSuccess ? '✅ ' : '❌ ') + text;
            deck.appendChild(toast);
            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transform = 'translateX(50px)';
                setTimeout(() => toast.remove(), 300);
            }, 3500);
        }

        function copyRedirectUri() {
            const uri = document.getElementById('uri-val').innerText;
            navigator.clipboard.writeText(uri).then(() => {
                showToast("คัดลอก Redirect URI แล้ว! นำไปวางในหน้า Discord Developer Portal ได้เลย");
            });
        }

        async function loadGuilds() {
            try {
                const res = await fetch('api.php?action=guilds');
                const guilds = await res.json();
                const sel = document.getElementById('guild-select');
                sel.innerHTML = '<option value="">-- เลือกเซิร์ฟเวอร์ที่ต้องการ --</option>';
                guilds.forEach(g => {
                    const opt = document.createElement('option');
                    opt.value = g.id;
                    opt.innerText = g.name;
                    sel.appendChild(opt);
                });
                document.getElementById('stat-guilds').innerText = guilds.length;
            } catch (e) {
                console.error(e);
            }
        }

        function filterUsers() {
            const query = document.getElementById('search-box').value.toLowerCase();
            const rows = document.querySelectorAll('.user-row');
            rows.forEach(r => {
                const name = r.querySelector('.u-name')?.innerText.toLowerCase() || '';
                const uid = r.querySelector('.u-id')?.innerText.toLowerCase() || '';
                if (name.includes(query) || uid.includes(query)) {
                    r.style.display = '';
                } else {
                    r.style.display = 'none';
                }
            });
        }

        async function startPullBatch(userIds = null) {
            const sel = document.getElementById('guild-select');
            const gid = sel.value;
            const gname = sel.options[sel.selectedIndex]?.text || 'Guild';
            const delay = parseFloat(document.getElementById('delay-select').value);

            if (!gid) {
                showToast("กรุณาเลือกเซิร์ฟเวอร์เป้าหมายก่อนครับ", false);
                return;
            }

            const btn = document.getElementById('btn-pull-all');
            btn.disabled = true;
            btn.innerText = "⏳ กำลังดึงสมาชิกเข้าเซิร์ฟเวอร์...";

            const term = document.getElementById('terminal-console');
            term.innerHTML += `<div class="log-msg">> เริ่มกระบวนการดึงคนเข้า '${gname}'...</div>`;
            term.scrollTop = term.scrollHeight;

            try {
                const res = await fetch('api.php?action=pull', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ guild_id: gid, guild_name: gname, delay: delay, user_ids: userIds })
                });
                const data = await res.json();

                if (data.logs && data.logs.length > 0) {
                    data.logs.forEach(l => {
                        const isErr = l.includes('❌');
                        term.innerHTML += `<div class="log-msg ${isErr ? 'err' : 'ok'}">${l}</div>`;
                    });
                }

                term.innerHTML += `<div class="log-msg ok">> สำเร็จ: ${data.success_count}, เดิมมี: ${data.already_member}, ล้มเหลว: ${data.failed_count}</div>`;
                term.scrollTop = term.scrollHeight;
                showToast(`🎉 ดึงสมาชิกสำเร็จ ${data.success_count} คน!`);
            } catch (e) {
                term.innerHTML += `<div class="log-msg err">> เชื่อมต่อเซิร์ฟเวอร์ล้มเหลว</div>`;
                showToast("เกิดข้อผิดพลาดในการเชื่อมต่อ", false);
            }

            btn.disabled = false;
            btn.innerText = "🚀 เริ่มดึงคนเข้าดิสคอร์ดทันที";
        }

        function pullSingleUser(uid, uname) {
            startPullBatch([uid]);
        }

        async function saveTunnelUrl() {
            const url = document.getElementById('tunnel-url-input').value.trim();
            if (!url) return;
            const res = await fetch('api.php?action=save_tunnel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tunnel_url: url })
            });
            const data = await res.json();
            if (data.success) {
                showToast("บันทึกโดเมนสำเร็จ!");
                setTimeout(() => location.reload(), 800);
            } else {
                showToast("บันทึกโดเมนล้มเหลว", false);
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            loadGuilds();
        });
    </script>
</body>
</html>
