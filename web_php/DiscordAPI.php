<?php
// language: PHP, file: web_php/DiscordAPI.php, target: PHP 8.0+
declare(strict_types=1);

class DiscordAPI {
    private const API_BASE = 'https://discord.com/api/v10';
    private string $botToken;
    private string $clientId;
    private string $clientSecret;

    public function __construct(string $botToken, string $clientId, string $clientSecret = '') {
        $this->botToken = $botToken;
        $this->clientId = $clientId;
        $this->clientSecret = $clientSecret;
    }

    private function request(string $method, string $endpoint, array $headers = [], $body = null): array {
        $ch = curl_init();
        $url = str_starts_with($endpoint, 'http') ? $endpoint : self::API_BASE . $endpoint;

        curl_setopt($ch, CURLOPT_URL, $url);
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 15);
        curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);

        if ($body !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
        }

        curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);

        $response = curl_exec($ch);
        $statusCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);

        $json = json_decode((string)$response, true);
        return [
            'status' => $statusCode,
            'data' => $json,
            'raw' => $response,
            'error' => $error
        ];
    }

    /**
     * ดึงข้อมูล Application Resource ของบอท: GET /applications/@me
     * (ตาม Discord Application Object Documentation)
     */
    public function getApplicationInfo(): array {
        $headers = [
            'Authorization: Bot ' . $this->botToken,
            'Content-Type: application/json'
        ];
        return $this->request('GET', '/applications/@me', $headers);
    }

    /**
     * แลก Code เป็น Access Token: POST /oauth2/token
     * (Content-Type: application/x-www-form-urlencoded ตาม RFC)
     */
    public function exchangeCode(string $code, string $redirectUri): array {
        $headers = [
            'Content-Type: application/x-www-form-urlencoded'
        ];
        $postData = http_build_query([
            'client_id' => $this->clientId,
            'client_secret' => $this->clientSecret,
            'grant_type' => 'authorization_code',
            'code' => $code,
            'redirect_uri' => $redirectUri
        ]);
        return $this->request('POST', '/oauth2/token', $headers, $postData);
    }

    /**
     * รีเฟรช Token เมื่อหมดอายุ: POST /oauth2/token
     */
    public function refreshToken(string $refreshToken): array {
        $headers = [
            'Content-Type: application/x-www-form-urlencoded'
        ];
        $postData = http_build_query([
            'client_id' => $this->clientId,
            'client_secret' => $this->clientSecret,
            'grant_type' => 'refresh_token',
            'refresh_token' => $refreshToken
        ]);
        return $this->request('POST', '/oauth2/token', $headers, $postData);
    }

    /**
     * ดึงข้อมูลสมาชิกผู้ใช้: GET /users/@me
     */
    public function getUserInfo(string $userAccessToken): array {
        $headers = [
            'Authorization: Bearer ' . $userAccessToken,
            'Content-Type: application/json'
        ];
        return $this->request('GET', '/users/@me', $headers);
    }

    /**
     * ดึงรายชื่อเซิร์ฟเวอร์ที่บอทอยู่: GET /users/@me/guilds
     */
    public function getBotGuilds(): array {
        $headers = [
            'Authorization: Bot ' . $this->botToken,
            'Content-Type: application/json'
        ];
        return $this->request('GET', '/users/@me/guilds', $headers);
    }

    /**
     * ดึงคนเข้าดิสคอร์ด: PUT /guilds/{guild_id}/members/{user_id}
     * (สิทธิ์ guilds.join ของ Discord OAuth2)
     */
    public function addGuildMember(string $guildId, string $userId, string $userAccessToken, array $roles = []): array {
        $headers = [
            'Authorization: Bot ' . $this->botToken,
            'Content-Type: application/json'
        ];
        $payload = [
            'access_token' => $userAccessToken
        ];
        if (!empty($roles)) {
            $payload['roles'] = $roles;
        }

        return $this->request('PUT', "/guilds/{$guildId}/members/{$userId}", $headers, json_encode($payload));
    }
}
