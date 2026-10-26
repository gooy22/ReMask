<?php
declare(strict_types=1);
require_once __DIR__.'/RemaskCookieProfile.php';

/** Pure validation. The calling store must hold its own transaction/lock. */
final class RemaskCookieSession
{
    public static function map(array $cookies): array
    {
        $result = [];
        foreach ($cookies as $key=>$value) {
            if (is_array($value)) { $key=$value['name'] ?? null; $value=$value['value'] ?? null; }
            if ((!is_string($key) && !is_int($key)) || (!is_string($value) && !is_int($value))) {
                throw new InvalidArgumentException('INVALID_COOKIE_SESSION');
            }
            $key=(string)$key; $value=(string)$value;
            if (!preg_match('/^[A-Za-z0-9_.-]{1,256}$/D', $key) || strlen($value)>8192
                || preg_match('/[\x00-\x1f\x7f]/', $value)
                || (isset($result[$key]) && $result[$key]!==$value)) {
                throw new InvalidArgumentException('INVALID_COOKIE_SESSION');
            }
            $result[$key]=$value;
        }
        return $result;
    }

    public static function replacement(FbAccount $account, string $actor, string $expectedXs, array $incoming): ?array
    {
        $old=self::map((array)$account->cookies);
        if ($actor==='' || $expectedXs==='' || ($old['c_user'] ?? '')!==$actor
            || !hash_equals((string)($old['xs'] ?? ''), $expectedXs)) return null;
        if (count($incoming)>500) throw new InvalidArgumentException('INVALID_COOKIE_SESSION');
        $rows=RemaskCookieProfile::validate($incoming, $account->proxy, false, $account->name);
        $next=self::map($rows);
        if (($next['c_user'] ?? '')!==$actor || trim((string)($next['xs'] ?? ''))==='') {
            throw new InvalidArgumentException('INVALID_COOKIE_SESSION');
        }
        // FbAccount hydrates userId/getCurlCookies from name/value rows.
        // Storing a flat map would lose actor identity on the next load.
        return $rows;
    }
}
