<?php
// language: PHP, file: web_php/config.php, target: PHP 8.0+
declare(strict_types=1);

// ฟังก์ชันโหลด .env จากโฟลเดอร์หลัก
function loadEnv(string $path): array {
    if (!file_exists($path)) {
        return [];
    }
    $lines = file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    $env = [];
    foreach ($lines as $line) {
        $line = trim($line);
        if ($line === '' || str_starts_with($line, '#')) {
            continue;
        }
        $parts = explode('=', $line, 2);
        if (count($parts) === 2) {
            $key = trim($parts[0]);
            $val = trim($parts[1]);
            $env[$key] = trim($val, "\"' ");
        }
    }
    return $env;
}

$parentEnv = loadEnv(__DIR__ . '/../.env');

// ดึง Tunnel URL จาก tunnel_url.txt
$tunnelUrlFile = __DIR__ . '/../tunnel_url.txt';
$tunnelUrl = "http://localhost:8000";
if (file_exists($tunnelUrlFile)) {
    $tVal = trim((string)file_get_contents($tunnelUrlFile));
    if (str_starts_with($tVal, 'http')) {
        $tunnelUrl = rtrim($tVal, '/');
    }
}

return [
    'bot_token' => $parentEnv['DISCORD_TOKEN'] ?? '',
    'client_id' => '1554090963499089960', // ID ของ Application บอท
    'client_secret' => $parentEnv['DISCORD_CLIENT_SECRET'] ?? '',
    'tunnel_url' => $tunnelUrl,
    'redirect_uri' => $tunnelUrl . '/callback.php',
    'db_file' => __DIR__ . '/../verified_users.json',
];
