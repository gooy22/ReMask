<?php
declare(strict_types=1);

/** Shop TXT data is untrusted. Passwords/2FA are discarded, never returned. */
final class RemaskCookieTxt
{
    public const MAX_BYTES = 2097152;
    public const MAX_RECORDS = 500;

    public static function parse(string $text): array
    {
        if (strlen($text) > self::MAX_BYTES) throw new InvalidArgumentException('TXT_TOO_LARGE: максимум 2 МБ.');
        if (!preg_match('//u', $text)) throw new InvalidArgumentException('TXT_ENCODING: нужен UTF-8.');
        $text = preg_replace('/^\xEF\xBB\xBF/', '', $text);
        $lines = preg_split('/\r\n|\n|\r/', $text);
        $records = [];
        for ($i = 0; $i < count($lines); $i++) {
            $line = $i + 1;
            $raw = trim($lines[$i]);
            if ($raw === '') continue;
            if (count($records) >= self::MAX_RECORDS) throw new InvalidArgumentException('TXT_TOO_MANY_RECORDS: максимум 500 записей.');
            // JSON starts after the shop's tab/pipe-separated login/password.
            // A bracket in a password must not be mistaken for cookie JSON.
            if (!preg_match('/(?:^|[\t|;])\s*([\[{])(?=\s*(?:[\{"\]}]|$))/', $raw, $match, PREG_OFFSET_CAPTURE)) {
                $records[] = self::error($line, 'COOKIE_JSON_MISSING');
                continue;
            }
            $start = $match[1][1];
            $prefix = trim(substr($raw, 0, $start), " \t|;");
            $scan = self::jsonEnd($raw, $start);
            $continuations = 0;
            while ($scan === null && $i + 1 < count($lines) && $continuations++ < 64 && strlen($raw) < 65536) {
                // Do not consume the next account if the current JSON is broken.
                if (preg_match('/^\s*[^\t|;]+[\t|;].*[\t|;]\s*\[\s*\{/', $lines[$i + 1])) break;
                $raw .= "\n" . $lines[++$i];
                $scan = self::jsonEnd($raw, $start);
            }
            if ($scan === null || $scan === false) {
                $records[] = self::error($line, 'COOKIE_JSON_INVALID');
                continue;
            }
            try {
                $cookies = json_decode(substr($raw, $start, $scan - $start), true, 64, JSON_THROW_ON_ERROR);
                if (!is_array($cookies)) throw new InvalidArgumentException();
                $cookies = RemaskCookieProfile::validate($cookies, null, false);
                $uid = self::userId($cookies);
                foreach ($cookies as $cookie) {
                    if (!in_array($cookie['name'], ['c_user','xs'], true) || !isset($cookie['domain'])) continue;
                    $domain = strtolower(ltrim((string)$cookie['domain'], '.'));
                    if ($domain !== 'facebook.com' && !str_ends_with($domain, '.facebook.com')) throw new InvalidArgumentException();
                }
            } catch (Throwable $e) {
                $records[] = self::error($line, 'COOKIE_SESSION_INVALID');
                continue;
            }
            $fields = preg_split('/[\t|;]/', $prefix);
            $login = trim((string)($fields[0] ?? ''));
            if (ctype_digit($login) && $login !== $uid) {
                $records[] = self::error($line, 'LOGIN_COOKIE_MISMATCH', $uid);
                continue;
            }
            // A second session in one record is ambiguous: do not guess.
            $suffix = substr($raw, $scan);
            if (preg_match('/[\[{]\s*(?:\{|"(?:c_user|name)"\s*:)/', $suffix)) {
                $records[] = self::error($line, 'MULTIPLE_COOKIE_SESSIONS', $uid);
                continue;
            }
            $records[] = ['line'=>$line, 'user_id'=>$uid, 'status'=>'ready', 'cookie_count'=>count($cookies), 'cookies'=>$cookies];
        }
        if (!$records) throw new InvalidArgumentException('TXT_EMPTY: файл не содержит записей.');
        $seen = [];
        foreach ($records as $index => &$record) {
            if ($record['status'] !== 'ready') continue;
            $uid = $record['user_id'];
            if (isset($seen[$uid])) {
                $previous = &$records[$seen[$uid]];
                if ($previous['status'] === 'error' || self::authXs($previous['cookies'] ?? []) !== self::authXs($record['cookies'])) {
                    $previous['status'] = 'error';
                    $previous['error'] = 'CONFLICTING_COOKIE_SESSIONS';
                    unset($previous['cookies']);
                    $record['status'] = 'error';
                    $record['error'] = 'CONFLICTING_COOKIE_SESSIONS';
                } else $record['status'] = 'duplicate_in_file';
                unset($previous);
                unset($record['cookies']);
            } else $seen[$uid] = $index;
        }
        unset($record);
        return $records;
    }

    public static function userId(array $cookies): string
    {
        foreach ($cookies as $cookie) if (($cookie['name'] ?? '') === 'c_user') return (string)$cookie['value'];
        return '';
    }

    public static function safe(array $record): array
    {
        unset($record['cookies']);
        return $record;
    }

    private static function authXs(array $cookies): string
    {
        foreach ($cookies as $cookie) if (($cookie['name'] ?? '') === 'xs') return (string)$cookie['value'];
        return '';
    }

    private static function error(int $line, string $code, string $uid = ''): array
    {
        return ['line'=>$line, 'user_id'=>$uid, 'status'=>'error', 'error'=>$code];
    }

    /** Balanced scan respects JSON strings and escaped quotes. */
    private static function jsonEnd(string $text, int $start): int|bool|null
    {
        $stack = []; $quoted = false; $escaped = false;
        for ($i = $start; $i < strlen($text); $i++) {
            $c = $text[$i];
            if ($quoted) {
                if ($escaped) $escaped = false;
                elseif ($c === '\\') $escaped = true;
                elseif ($c === '"') $quoted = false;
                continue;
            }
            if ($c === '"') { $quoted = true; continue; }
            if ($c === '[' || $c === '{') {
                $stack[] = $c;
                if (count($stack) > 64) return false;
            } elseif ($c === ']' || $c === '}') {
                $open = array_pop($stack);
                if (($c === ']' && $open !== '[') || ($c === '}' && $open !== '{')) return false;
                if (!$stack) return $i + 1;
            }
        }
        return null;
    }
}
