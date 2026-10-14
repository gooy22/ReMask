<?php
declare(strict_types=1);

/** Read transport only. This context is never a template destination. */
final class RemaskTemplateTargetingContext {
    public static function candidates(array $profiles, callable $loadCatalog, string $preferred = '', array $excluded = []): array {
        $candidates = [];
        foreach ($profiles as $profile) {
            $name = (string)($profile['name'] ?? '');
            if ($name === '' || in_array($name, $excluded, true) || ($profile['disabled'] ?? false) === true
                || ($profile['banned'] ?? false) === true || ($profile['cookies_ready'] ?? false) !== true
                || (($profile['proxy_configured'] ?? false) !== true && $name !== $preferred)) continue;
            try { $catalog = $loadCatalog($name); }
            catch (Throwable $error) { continue; }
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
        return array_map(static fn($row)=>array_intersect_key($row, array_flip(['profile','account_id','catalog'])), $candidates);
    }

    public static function resolve(array $profiles, callable $loadCatalog, string $preferred = '', array $excluded = []): array {
        return self::candidates($profiles, $loadCatalog, $preferred, $excluded)[0];
    }

    public static function failure(Throwable $error, string $profile = ''): array {
        $raw = $error->getMessage();
        $code = preg_match('/^[A-Z][A-Z0-9_]{2,119}$/D', $raw) ? $raw : 'TEMPLATE_LOOKUP_UNAVAILABLE';
        $message = in_array($code, ['UNKNOWN_TARGETING_TYPE','INVALID_TARGETING_QUERY'], true)
            ? 'Для поиска аудитории введите от 2 до 200 символов.'
            : 'Общий справочник Meta временно недоступен. Шаблон можно редактировать и сохранить без выбора профиля.';
        return ['code'=>$code,'message'=>$message,'settings_preserved'=>true,'template_account_bound'=>false];
    }
}

/** Workspace-scoped genuine reference results, never advertising permission. */
final class RemaskTemplateTargetingReference {
    public function __construct(private string $file) {}
    private function update(callable $operation): mixed {
        if (!is_dir(dirname($this->file)) && !@mkdir(dirname($this->file), 0700, true) && !is_dir(dirname($this->file)))
            throw new RuntimeException('TEMPLATE_REFERENCE_STORE_UNAVAILABLE');
        $handle = @fopen($this->file.'.lock', 'c');
        if (!$handle || !flock($handle, LOCK_EX)) throw new RuntimeException('TEMPLATE_REFERENCE_STORE_UNAVAILABLE');
        try {
            $raw = @file_get_contents($this->file);
            $state = is_string($raw) ? json_decode($raw, true) : null;
            if (!is_array($state)) $state = ['queries'=>[], 'blocked'=>[]];
            $result = $operation($state);
            $tmp = $this->file.'.'.bin2hex(random_bytes(6)).'.tmp';
            try {
                $json = json_encode($state, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
                if (file_put_contents($tmp, $json) !== strlen($json) || !chmod($tmp, 0600) || !rename($tmp, $this->file))
                    throw new RuntimeException('TEMPLATE_REFERENCE_STORE_UNAVAILABLE');
            } finally { if (is_file($tmp)) @unlink($tmp); }
            return $result;
        } finally { flock($handle, LOCK_UN); fclose($handle); }
    }
    private static function key(string $type, string $query): string {
        return hash('sha256', $type."\0".trim($query));
    }
    public function get(string $type, string $query, int $now): ?array {
        return $this->update(static function(array &$state) use ($type,$query,$now) {
            $row = $state['queries'][self::key($type,$query)] ?? null;
            if (!is_array($row) || !is_array($row['data'] ?? null) || $now-(int)($row['checked_at'] ?? 0)>2592000) return null;
            return ['data'=>$row['data'],'checked_live'=>false,'source'=>'private_meta_template_reference',
                'template_account_bound'=>false,'reference_checked_at'=>$row['checked_at'],
                'reference_stale'=>$now-(int)$row['checked_at']>86400];
        });
    }
    public function put(string $type, string $query, array $proof, int $now): void {
        if (($proof['checked_live'] ?? null)!==true || ($proof['account_scope_verified'] ?? null)!==true)
            throw new RuntimeException('TEMPLATE_LOOKUP_ACCESS_UNVERIFIED');
        $rows = [];
        foreach (array_slice((array)($proof['data'] ?? []),0,500) as $row) {
            if (!is_array($row)) continue;
            $safe = array_intersect_key($row,array_flip(['id','key','name','type','country_code','country_name',
                'region','region_id','radius','distance_unit','description','path']));
            if (!isset($safe['id']) && !isset($safe['key'])) continue;
            if (strlen(json_encode($safe,JSON_THROW_ON_ERROR))<=8192) $rows[]=$safe;
        }
        $this->update(static function(array &$state) use ($type,$query,$rows,$now) {
            $state['queries'][self::key($type,$query)] = ['data'=>$rows,'checked_at'=>$now];
            uasort($state['queries'], static fn($a,$b)=>(int)$b['checked_at']<=>(int)$a['checked_at']);
            $state['queries']=array_slice($state['queries'],0,200,true);
        });
    }
    public function blocked(int $now): array {
        return $this->update(static function(array &$state) use ($now) {
            $state['blocked']=array_filter((array)($state['blocked']??[]),static fn($until)=>(int)$until>$now);
            return array_map('strval',array_keys($state['blocked']));
        });
    }
    public function block(string $profile, int $now): void {
        $this->update(static function(array &$state) use ($profile,$now) { $state['blocked'][$profile]=$now+600; });
    }
    public function search(string $type, string $query, int $now): ?array {
        return $this->update(static function(array &$state) use ($type,$query,$now) {
            $row = $state['queries'][self::key($type,'')] ?? null;
            if (!is_array($row) || $now-(int)($row['checked_at'] ?? 0)>86400) return null;
            $rows = array_values(array_filter((array)$row['data'], static fn($item)=>
                preg_match('~'.preg_quote($query,'~').'~iu', (string)($item['name'] ?? '').' '.
                    implode(' ',(array)($item['path'] ?? [])).' '.(string)($item['id'] ?? ''))===1));
            if (!$rows) return null;
            return ['data'=>array_slice($rows,0,50),'checked_live'=>false,
                'source'=>'private_meta_template_reference','template_account_bound'=>false,
                'reference_checked_at'=>$row['checked_at']];
        });
    }
}

final class RemaskModuleTargetingReference {
    public static function search(string $type, string $query, string $file): ?array {
        if (!in_array($type,['locales','locations'],true)) return null;
        $freshFile=rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'),'/').'/private-targeting-reference.json';
        $fresh=json_decode((string)@file_get_contents($freshFile),true);
        if (is_array($fresh) && ($fresh['version'] ?? 0)===1 && !empty($fresh['source_evidence'])
            && time()-(int)($fresh['refreshed_at'] ?? 0)<=1800) $file=$freshFile;
        $data=json_decode((string)@file_get_contents($file),true);
        if (!is_array($data) || ($data['version'] ?? 0)!==1 || empty($data['source_evidence']))
            throw new RuntimeException('TARGETING_MODULE_REFERENCE_UNAVAILABLE');
        $rows=array_values(array_filter((array)($data[$type] ?? []), static function($row) use ($query) {
            $haystack=implode(' ',array_merge([(string)($row['name'] ?? ''),(string)($row['id'] ?? ''),
                (string)($row['country_code'] ?? '')],(array)($row['aliases'] ?? [])));
            return $query==='' || preg_match('~'.preg_quote($query,'~').'~iu',$haystack)===1;
        }));
        if (!$rows && $query!=='') return null;
        if ($query!=='') usort($rows, static function($a,$b) use ($query) {
            $score=static function($row) use ($query) {
                foreach (array_merge([(string)($row['name'] ?? '')],(array)($row['aliases'] ?? [])) as $name)
                    if (preg_match('~^'.preg_quote($query,'~').'~iu',$name)===1) return 1;
                return 0;
            };
            return $score($b)<=>$score($a);
        });
        foreach ($rows as &$row) unset($row['aliases']); unset($row);
        return ['data'=>array_slice($rows,0,100),'checked_live'=>false,'source'=>'private_meta_module_reference',
            'template_account_bound'=>false,'reference_captured_at'=>$data['captured_at'],
            'truncated'=>count($rows)>100];
    }
}
