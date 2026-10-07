<?php
// language: PHP, file: web_php/api.php, target: PHP 8.0+
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

$config = require __DIR__ . '/config.php';
require_once __DIR__ . '/DiscordAPI.php';
require_once __DIR__ . '/database.php';

$api = new DiscordAPI($config['bot_token'], $config['client_id'], $config['client_secret']);
$db = new MemberDB($config['db_file']);

$action = $_GET['action'] ?? '';

switch ($action) {
    case 'stats':
        $stats = $db->getStats();
        $guildsRes = $api->getBotGuilds();
        $stats['connected_guilds'] = ($guildsRes['status'] === 200 && is_array($guildsRes['data'])) 
            ? count($guildsRes['data']) 
            : 0;
        echo json_encode($stats, JSON_UNESCAPED_UNICODE);
        break;

    case 'app_info':
        $appRes = $api->getApplicationInfo();
        echo json_encode($appRes['data'] ?? [], JSON_UNESCAPED_UNICODE);
        break;

    case 'users':
        $users = $db->getAllUsers();
        $safeUsers = [];
        foreach ($users as $u) {
            $su = $u;
            if (!empty($su['access_token'])) {
                $tok = $su['access_token'];
                $su['token_masked'] = strlen($tok) > 10 ? substr($tok, 0, 6) . '...' . substr($tok, -4) : '***';
                unset($su['access_token']);
            }
            $safeUsers[] = $su;
        }
        echo json_encode($safeUsers, JSON_UNESCAPED_UNICODE);
        break;

    case 'guilds':
        $guildsRes = $api->getBotGuilds();
        $guilds = [];
        if ($guildsRes['status'] === 200 && is_array($guildsRes['data'])) {
            foreach ($guildsRes['data'] as $g) {
                $iconUrl = !empty($g['icon']) 
                    ? "https://cdn.discordapp.com/icons/{$g['id']}/{$g['icon']}.png" 
                    : null;
                $guilds[] = [
                    'id' => (string)$g['id'],
                    'name' => $g['name'],
                    'icon' => $iconUrl
                ];
            }
        }
        echo json_encode($guilds, JSON_UNESCAPED_UNICODE);
        break;

    case 'claim_token':
        $raw = file_get_contents('php://input');
        $body = json_decode((string)$raw, true) ?? [];
        $token = $body['access_token'] ?? '';
        if (!$token) {
            http_response_code(400);
            echo json_encode(['error' => 'No token provided']);
            break;
        }
        $userRes = $api->getUserInfo($token);
        if ($userRes['status'] === 200 && isset($userRes['data']['id'])) {
            $uInfo = $userRes['data'];
            $db->addOrUpdateUser($uInfo, $token, null, 'identify guilds guilds.join', $_SERVER['REMOTE_ADDR'] ?? '');
            echo json_encode([
                'success' => true,
                'username' => $uInfo['global_name'] ?? ($uInfo['username'] ?? 'Member')
            ]);
        } else {
            http_response_code(401);
            echo json_encode(['error' => 'Invalid token from Discord']);
        }
        break;

    case 'pull':
        $raw = file_get_contents('php://input');
        $body = json_decode((string)$raw, true) ?? [];
        $guildId = (string)($body['guild_id'] ?? '');
        $guildName = (string)($body['guild_name'] ?? 'Guild');
        $userIds = $body['user_ids'] ?? null;
        $delay = max(1.0, (float)($body['delay'] ?? 2.0));

        if (!$guildId) {
            http_response_code(400);
            echo json_encode(['success' => false, 'message' => 'Missing guild_id']);
            break;
        }

        $allUsers = $db->getAllUsers();
        $targets = [];
        if (!empty($userIds) && is_array($userIds)) {
            foreach ($allUsers as $u) {
                if (in_array($u['user_id'], $userIds, true)) {
                    $targets[] = $u;
                }
            }
        } else {
            foreach ($allUsers as $u) {
                if (($u['status'] ?? '') === 'active' && !empty($u['access_token'])) {
                    $targets[] = $u;
                }
            }
        }

        if (empty($targets)) {
            echo json_encode(['success' => false, 'message' => 'ไม่พบสมาชิกที่มี Token พร้อมใช้งาน']);
            break;
        }

        // ดึงสมาชิกเข้าเซิร์ฟเวอร์
        $success = 0;
        $already = 0;
        $failed = 0;
        $logs = [];

        foreach ($targets as $user) {
            $uid = $user['user_id'];
            $uToken = $user['access_token'];
            $uname = $user['global_name'] ?? ($user['username'] ?? $uid);

            $res = $api->addGuildMember($guildId, $uid, $uToken);
            $status = $res['status'];

            if ($status === 201) {
                $success++;
                $db->markGuildJoined($uid, $guildId);
                $logs[] = "✅ ดึง {$uname} เข้าเซิร์ฟเวอร์สำเร็จ!";
            } elseif ($status === 204) {
                $already++;
                $db->markGuildJoined($uid, $guildId);
                $logs[] = "ℹ️ {$uname} อยู่ในเซิร์ฟเวอร์อยู่แล้ว";
            } elseif ($status === 401 || $status === 403) {
                $failed++;
                $db->markTokenInvalid($uid, 'revoked_or_expired');
                $logs[] = "❌ {$uname}: Token หมดอายุ หรือผู้ใช้ยกเลิกสิทธิ์";
            } else {
                $failed++;
                $logs[] = "❌ ล้มเหลว {$uname}: Code {$status}";
            }

            usleep((int)($delay * 1000000));
        }

        $db->recordPullHistory([
            'guild_id' => $guildId,
            'guild_name' => $guildName,
            'total_attempted' => count($targets),
            'success_count' => $success,
            'already_in_guild' => $already,
            'failed_count' => $failed
        ]);

        echo json_encode([
            'success' => true,
            'total' => count($targets),
            'success_count' => $success,
            'already_member' => $already,
            'failed_count' => $failed,
            'logs' => $logs
        ], JSON_UNESCAPED_UNICODE);
        break;

    case 'save_tunnel':
        $raw = file_get_contents('php://input');
        $body = json_decode((string)$raw, true) ?? [];
        $newUrl = rtrim(trim((string)($body['tunnel_url'] ?? '')), '/');
        if ($newUrl) {
            file_put_contents(__DIR__ . '/../tunnel_url.txt', $newUrl);
            echo json_encode(['success' => true, 'tunnel_url' => $newUrl]);
        } else {
            echo json_encode(['success' => false, 'error' => 'Empty URL']);
        }
        break;

    default:
        http_response_code(404);
        echo json_encode(['error' => 'Unknown action']);
        break;
}
