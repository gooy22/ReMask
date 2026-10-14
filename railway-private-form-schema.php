<?php
declare(strict_types=1);

/** Metadata and validation from the same reviewed private catalog as the sender. */
final class RemaskPrivateFormSchema
{
    public static function load(): array {
        static $data = null;
        if (is_array($data)) return $data;
        $base = rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: 'http://127.0.0.1:8081'), '/');
        $key = (string)(getenv('REMASK_WORKER_API_KEY') ?: '');
        if ($key === '') throw new RuntimeException('PRIVATE_FORM_SCHEMA_UNAVAILABLE');
        $context = stream_context_create(['http' => ['method' => 'GET', 'timeout' => 15,
            'ignore_errors' => true, 'follow_location' => 0,
            'header' => "Accept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n"]]);
        $raw = @file_get_contents($base.'/api/v1/private-launch/schema', false, $context, 0, 1048577);
        $row = is_string($raw) && strlen($raw) <= 1048576 ? json_decode($raw, true) : null;
        if (!is_array($row) || ($row['ok'] ?? null) !== true
            || ($row['source'] ?? '') !== 'reviewed_private_ads_manager_modules'
            || !is_array($row['schema'] ?? null) || !is_array($row['object_fields'] ?? null)
            || !preg_match('/^[a-f0-9]{64}$/D', (string)($row['source_digest'] ?? ''))) {
            throw new RuntimeException('PRIVATE_FORM_SCHEMA_UNAVAILABLE');
        }
        return $data = $row;
    }

    public static function sanitizeBuilder(array $builder): array {
        $schema = self::load();
        $sections = ['campaign','adset','targeting','creative','ad','identity','existing_media'];
        if (array_diff(array_keys($builder), $sections)) throw new InvalidArgumentException('PRIVATE_BUILDER_SECTION_UNKNOWN');
        $out = [];
        foreach ($sections as $section) {
            $value = $builder[$section] ?? [];
            if (!is_array($value)) throw new InvalidArgumentException('PRIVATE_BUILDER_OBJECT_REQUIRED: '.$section);
            $allowed = match ($section) {
                'identity' => ['page_id','instagram_actor_id'],
                'existing_media' => ['image_hash','video_id','creative_id','source_instagram_media_id'],
                default => $schema['object_fields'][$section] ?? [],
            };
            if (array_diff(array_keys($value), $allowed)) throw new InvalidArgumentException('PRIVATE_BUILDER_FIELD_UNREVIEWED: '.$section);
            if (in_array($section, ['campaign','adset','ad'], true)
                && array_intersect(array_keys($value), ['id','account_id','campaign_id','adset_id','creative','acting_business_id'])) {
                throw new InvalidArgumentException('PRIVATE_BUILDER_OWNED_FIELD: '.$section);
            }
            if ($section === 'targeting' && (isset($value['adgroup_id'])
                || array_filter(array_keys($value), static fn($key) => str_starts_with((string)$key, 'effective_')))) {
                throw new InvalidArgumentException('PRIVATE_BUILDER_READ_ONLY_TARGETING');
            }
            self::rejectSecrets($value);
            $out[$section] = $value;
        }
        return $out;
    }

    /** Normalize legacy and new templates without exposing destination-owned IDs. */
    public static function unbindTemplate(array $builder): array {
        $owned = ['page_id','instagram_actor_id','instagram_user_id','image_hash','video_id','creative_id',
            'source_instagram_media_id','object_story_id','object_story_spec','source_facebook_post_id',
            'pixel_id','custom_conversion_id','product_set_id','product_catalog_id','application_id',
            'custom_audiences','excluded_custom_audiences'];
        $clean = static function(array $value, int $depth = 0) use (&$clean, $owned): array {
            if ($depth > 24) throw new InvalidArgumentException('PRIVATE_BUILDER_TOO_DEEP');
            foreach ($value as $key => $child) {
                if (in_array((string)$key, $owned, true)) { unset($value[$key]); continue; }
                if (is_array($child)) $value[$key] = $clean($child, $depth+1);
            }
            return $value;
        };
        $out = $clean($builder);
        $out['identity'] = []; $out['existing_media'] = [];
        return $out;
    }

    private static function rejectSecrets(mixed $value, int $depth = 0): void {
        if ($depth > 24) throw new InvalidArgumentException('PRIVATE_BUILDER_TOO_DEEP');
        if (!is_array($value)) return;
        foreach ($value as $key => $child) {
            if (in_array(strtolower((string)$key), ['access_token','token','cookies','cookie','password',
                'authorization','cvv','cvc','card_number','fb_dtsg','lsd','jazoest'], true)) {
                throw new InvalidArgumentException('PRIVATE_BUILDER_AUTH_MATERIAL');
            }
            self::rejectSecrets($child, $depth + 1);
        }
    }
}
