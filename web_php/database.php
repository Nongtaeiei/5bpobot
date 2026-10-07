<?php
// language: PHP, file: web_php/database.php, target: PHP 8.0+
declare(strict_types=1);

class MemberDB {
    private string $filePath;

    public function __construct(string $filePath) {
        $this->filePath = $filePath;
    }

    public function load(): array {
        if (!file_exists($this->filePath)) {
            return [
                'version' => 1,
                'users' => [],
                'pull_history' => []
            ];
        }
        $content = file_get_contents($this->filePath);
        $json = json_decode((string)$content, true);
        return is_array($json) ? $json : ['version' => 1, 'users' => [], 'pull_history' => []];
    }

    public function save(array $data): bool {
        $content = json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        return file_put_contents($this->filePath, $content, LOCK_EX) !== false;
    }

    public function addOrUpdateUser(array $userInfo, string $accessToken, ?string $refreshToken = null, ?string $scope = null, ?string $ip = null): array {
        $db = $this->load();
        $uid = (string)($userInfo['id'] ?? '');
        if ($uid === '') {
            return [];
        }

        $nowIso = gmdate('Y-m-d\TH:i:s\Z');
        $existing = $db['users'][$uid] ?? [];

        $avatarHash = $userInfo['avatar'] ?? null;
        $avatarUrl = $avatarHash 
            ? "https://cdn.discordapp.com/avatars/{$uid}/{$avatarHash}.png?size=128" 
            : "https://cdn.discordapp.com/embed/avatars/0.png";

        $record = [
            'user_id' => $uid,
            'username' => $userInfo['username'] ?? 'Unknown',
            'global_name' => $userInfo['global_name'] ?? ($userInfo['username'] ?? 'Unknown'),
            'avatar' => $avatarUrl,
            'access_token' => $accessToken,
            'refresh_token' => $refreshToken ?? ($existing['refresh_token'] ?? null),
            'scope' => $scope ?? ($existing['scope'] ?? 'identify guilds guilds.join'),
            'verified_at' => $existing['verified_at'] ?? $nowIso,
            'last_updated' => $nowIso,
            'ip' => $ip ?? ($existing['ip'] ?? ''),
            'guilds_joined' => $existing['guilds_joined'] ?? [],
            'status' => 'active'
        ];

        $db['users'][$uid] = $record;
        $this->save($db);
        return $record;
    }

    public function markGuildJoined(string $userId, string $guildId): void {
        $db = $this->load();
        if (isset($db['users'][$userId])) {
            $guilds = $db['users'][$userId]['guilds_joined'] ?? [];
            if (!in_array($guildId, $guilds, true)) {
                $db['users'][$userId]['guilds_joined'][] = $guildId;
                $this->save($db);
            }
        }
    }

    public function markTokenInvalid(string $userId, string $reason = 'expired'): void {
        $db = $this->load();
        if (isset($db['users'][$userId])) {
            $db['users'][$userId]['status'] = $reason;
            $this->save($db);
        }
    }

    public function getAllUsers(): array {
        $db = $this->load();
        return array_values($db['users'] ?? []);
    }

    public function getStats(): array {
        $db = $this->load();
        $users = $db['users'] ?? [];
        $activeCount = 0;
        foreach ($users as $u) {
            if (($u['status'] ?? '') === 'active') {
                $activeCount++;
            }
        }
        $history = $db['pull_history'] ?? [];
        $totalPulled = 0;
        foreach ($history as $h) {
            $totalPulled += (int)($h['success_count'] ?? 0);
        }

        return [
            'total_users' => count($users),
            'active_tokens' => $activeCount,
            'total_pulled' => $totalPulled,
            'total_pull_operations' => count($history)
        ];
    }

    public function recordPullHistory(array $entry): void {
        $db = $this->load();
        $entry['timestamp'] = gmdate('Y-m-d\TH:i:s\Z');
        array_unshift($db['pull_history'], $entry);
        if (count($db['pull_history']) > 50) {
            $db['pull_history'] = array_slice($db['pull_history'], 0, 50);
        }
        $this->save($db);
    }
}
