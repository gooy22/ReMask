<?php
declare(strict_types=1);

/** Read transport only. This context is never a template destination. */
final class RemaskTemplateTargetingContext {
    public static function resolve(array $profiles, callable $loadCatalog, string $preferred = ''): array {
        $candidates = [];
        foreach ($profiles as $profile) {
            $name = (string)($profile['name'] ?? '');
            if ($name === '' || (($profile['proxy_configured'] ?? false) !== true && $name !== $preferred)) continue;
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
        if (!$candidates) throw new RuntimeException('Для поиска аудитории нужен подключённый профиль Meta с доступным кабинетом. Шаблон к кабинету не привязывается.');
        return array_intersect_key($candidates[0], array_flip(['profile','account_id','catalog']));
    }
}
