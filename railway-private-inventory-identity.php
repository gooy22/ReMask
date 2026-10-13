<?php
declare(strict_types=1);

/** Saved worker confirmations supply identity only; live permission remains a separate proof. */
final class RemaskPrivateInventoryIdentity {
    private static function id(mixed $value): string {
        if (!is_string($value) && !is_int($value)) return '';
        $id = preg_replace('/^act_/', '', trim((string)$value));
        return preg_match('/^[0-9]{5,30}$/D', $id) ? $id : '';
    }
    public static function merge(array $inventory, array $bindings, string $profile, string $personalScope): array {
        $record = $bindings[$profile] ?? [];
        $entries = is_array($record) && isset($record['ad_account_id']) ? [$record]
            : (is_array($record['ad_accounts'] ?? null) ? $record['ad_accounts'] : []);
        $pairs = [];
        foreach ($entries as $businessKey=>$row) {
            if (!is_array($row) || (isset($row['profile']) && (string)$row['profile'] !== $profile)) continue;
            $account = self::id($row['ad_account_id'] ?? '');
            $business = self::id($row['business_id'] ?? $businessKey);
            if ($account === '' || $business === '' || $business === $personalScope) continue;
            if (isset($pairs[$account]) && $pairs[$account]['business_id'] !== $business)
                throw new RuntimeException('PRIVATE_ACCOUNT_RELATION_CONFLICT');
            $pairs[$account] = ['id'=>$account,'account_id'=>$account,'ad_account_id'=>$account,
                'business_id'=>$business,'name'=>trim((string)($row['account_name'] ?? $account)),
                'account_status'=>null,'currency'=>'','source'=>'python_worker_confirmed_create',
                'identity_only'=>true,'advertising_access_verified'=>false];
        }
        $accounts = (array)($inventory['ad_accounts'] ?? []);
        $businesses = (array)($inventory['businesses'] ?? []);
        foreach ($pairs as $account=>$pair) {
            $present = false;
            foreach ($accounts as $row) {
                if (!is_array($row) || self::id($row['ad_account_id'] ?? $row['account_id'] ?? $row['id'] ?? '') !== (string)$account) continue;
                if (self::id($row['business_id'] ?? '') !== $pair['business_id'])
                    throw new RuntimeException('PRIVATE_ACCOUNT_RELATION_CONFLICT');
                $present = true;
            }
            if (!$present) $accounts[] = $pair;
            if (!array_filter($businesses, static fn($row)=>is_array($row)
                && self::id($row['business_id'] ?? $row['id'] ?? '') === $pair['business_id']))
                $businesses[] = ['id'=>$pair['business_id'],'business_id'=>$pair['business_id'],
                    'name'=>$pair['business_id'],'source'=>'python_worker_confirmed_create','identity_only'=>true];
        }
        $inventory['ad_accounts'] = $accounts;
        $inventory['businesses'] = $businesses;
        // Do not refresh timestamps/status or turn saved creation into live advertising access.
        return $inventory;
    }
}
