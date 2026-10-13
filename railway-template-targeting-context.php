<?php
declare(strict_types=1);

/** Read transport only. This context is never a template destination. */
final class RemaskTemplateTargetingContext {
    public static function resolve(array $profiles, callable $loadCatalog, string $preferred = ''): array {
        $candidates = [];
        foreach ($profiles as $profile) {
            $name = (string)($profile['name'] ?? '');
            if ($name === '' || ($profile['cookies_ready'] ?? false) !== true
                || (($profile['proxy_configured'] ?? false) !== true && $name !== $preferred)) continue;
            $catalog = $loadCatalog($name);
            if (($catalog['profile'] ?? '') !== $name) throw new RuntimeException('TEMPLATE_LOOKUP_PROFILE_MISMATCH');
            foreach ((array)($catalog['ad_accounts']['data'] ?? []) as $row) {
                if (!is_array($row) || (isset($row['profile']) && (string)$row['profile'] !== $name)) continue;
                $account = (string)($row['account_id'] ?? '');
                $business = (string)($row['business_id'] ?? '');
                if (!preg_match('/^[0-9]{5,30}$/D', $account) || !preg_match('/^[0-9]{5,30}$/D', $business)) continue;
                $status = $row['account_status'] ?? null;
                if ($status !== null && (int)$status !== 1) continue;
                $candidates[] = ['profile'=>$name, 'account_id'=>$account, 'catalog'=>$catalog,
                    'priority'=>$name === $preferred ? 2 : ($status !== null ? 1 : 0),
                    'updated'=>(int)($catalog['_cache']['fetched_at'] ?? 0)];
            }
        }
        usort($candidates, static fn($a,$b)=>($b['priority'] <=> $a['priority']) ?: ($b['updated'] <=> $a['updated'])
            ?: strcmp($a['profile'], $b['profile']) ?: strcmp($a['account_id'], $b['account_id']));
        if (!$candidates) throw new RuntimeException('TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE');
        return array_intersect_key($candidates[0], array_flip(['profile','account_id','catalog']));
    }

    public static function failure(Throwable $error, string $profile = ''): array {
        // Only a typed code crosses the boundary; never echo a raw transport error.
        $raw = $error->getMessage();
        $code = preg_match('/^[A-Z][A-Z0-9_]{2,119}$/D', $raw) ? $raw : 'TEMPLATE_LOOKUP_UNAVAILABLE';
        preg_match('/\A.{0,160}/us', $profile, $profileMatch);
        $profile = $profileMatch[0] ?? '';
        $source = $profile !== '' ? 'профиле «'.$profile.'»' : 'подключённом профиле';
        $message = match (true) {
            str_ends_with($code, 'CHECKPOINT_REQUIRED') => 'Meta требует подтверждения входа в '.$source.'. После подтверждения обновите его cookies в «Аккаунтах».',
            str_ends_with($code, 'SESSION_EXPIRED'), str_ends_with($code, 'LOGIN_REQUIRED'),
            $code === 'BUSINESS_LOGIN_GATE', $code === 'META_AUTH_TOKEN_UNAVAILABLE' => 'Сессия Meta в '.$source.' недоступна. Обновите cookies этого профиля в «Аккаунтах».',
            $code === 'TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE' => 'Нет подключённого профиля с cookies, доступным кабинетом и настроенным соединением. Проверьте подключение в «Аккаунтах».',
            str_contains($code, 'TIMEOUT') => 'Meta не ответила вовремя. Проверьте соединение и прокси подключённого профиля.',
            $code === 'UNKNOWN_TARGETING_TYPE', $code === 'INVALID_TARGETING_QUERY' => 'Для поиска аудитории введите от 2 до 200 символов.',
            default => 'Не удалось загрузить подсказки Meta. Проверьте сессию и соединение подключённого профиля в «Аккаунтах».',
        };
        return ['code'=>$code,'message'=>$message.' Уже введённые настройки можно сохранить.',
            'source_profile'=>$profile,'settings_preserved'=>true];
    }
}
