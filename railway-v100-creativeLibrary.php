<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MediaLibraryStoreFactory.php';
require_once __DIR__ . '/../classes/CreativePresetStore.php';
require_once __DIR__ . '/../classes/MetaOfficialFields.php';

function cl100_string(string $key, int $max = 5000): string {
    $value = trim((string)($_POST[$key] ?? ''));
    if (strlen($value) > $max) throw new InvalidArgumentException($key . ' is too long.');
    return $value;
}
function cl100_format(string $value): string {
    $value = strtoupper(trim($value));
    if (!in_array($value, ['SINGLE','CAROUSEL','INSTAGRAM_POST'], true)) {
        throw new InvalidArgumentException('Unsupported creative format.');
    }
    return $value;
}
function cl100_meta_media_ref(array $record): array {
    $asset = is_array($record['meta_asset'] ?? null) ? $record['meta_asset'] : [];
    $type = strtolower(trim((string)($asset['type'] ?? '')));
    $value = trim((string)($asset['value'] ?? ''));
    if (in_array($type, ['image','video','creative'], true) && $value !== '') {
        return [
            'type' => $type,
            'value' => $value,
            'name' => trim((string)($asset['name'] ?? '')),
            'preview_url' => trim((string)($asset['preview_url'] ?? '')),
            'preview_type' => strtolower(trim((string)($asset['preview_type'] ?? ''))),
        ];
    }
    $builder = is_array($record['meta_builder'] ?? null) ? $record['meta_builder'] : [];
    $existing = is_array($builder['existing_media'] ?? null) ? $builder['existing_media'] : [];
    $image = trim((string)($existing['image_hash'] ?? ''));
    if ($image !== '') return ['type'=>'image','value'=>$image,'name'=>'Meta image','preview_url'=>''];
    $video = trim((string)($existing['video_id'] ?? ''));
    if ($video !== '') return ['type'=>'video','value'=>$video,'name'=>'Meta video','preview_url'=>''];
    $creativeId = trim((string)($existing['creative_id'] ?? ''));
    if ($creativeId !== '') return ['type'=>'creative','value'=>$creativeId,'name'=>'Meta creative','preview_url'=>''];
    return [];
}

function cl100_media_ids(array $record): array {
    $ids = [];
    $single = trim((string)($record['media_library_id'] ?? ''));
    if ($single !== '') $ids[] = $single;
    foreach ((array)($record['carousel'] ?? []) as $card) {
        if (!is_array($card)) continue;
        $id = trim((string)($card['media_library_id'] ?? ''));
        if ($id !== '') $ids[] = $id;
    }
    return array_values(array_unique($ids));
}
function cl100_referenced(CreativePresetStore $store, string $mediaId): bool {
    foreach ($store->list() as $record) {
        if (in_array($mediaId, cl100_media_ids($record), true)) return true;
    }
    return false;
}
function cl100_public_items(CreativePresetStore $store, $library): array {
    $out = [];
    foreach ($store->list() as $row) {
        $format = strtoupper((string)($row['format'] ?? 'SINGLE'));
        if (!in_array($format, ['SINGLE','CAROUSEL','INSTAGRAM_POST'], true)) $format = 'SINGLE';
        $row['format'] = $format;
        $row['meta_builder'] = RemaskPrivateFormSchema::unbindTemplate((array)($row['meta_builder'] ?? []));
        $row['instagram_media_id'] = '';
        $row['meta_asset'] = [];
        unset($row['profile'], $row['account_id'], $row['business_id']);
        $row['template_account_bound'] = false;
        $row['media'] = null;
        $row['missing_media'] = false;
        $row['preview_url'] = '';

        if ($format === 'SINGLE') {
            $id = trim((string)($row['media_library_id'] ?? ''));
            $row['media'] = $id !== '' ? $library->get($id) : null;
            $metaMedia = cl100_meta_media_ref($row);
            if ($row['media'] === null && $metaMedia !== []) {
                $row['media'] = [
                    'id' => 'meta:' . $metaMedia['value'],
                    'original_name' => $metaMedia['name'] !== '' ? $metaMedia['name'] : ('Meta ' . $metaMedia['type']),
                    'media_type' => in_array(($metaMedia['preview_type'] ?? ''), ['image','video'], true)
                        ? $metaMedia['preview_type']
                        : ($metaMedia['type'] === 'video' ? 'video' : 'image'),
                    'source' => 'meta',
                ];
                $row['missing_media'] = false;
                $row['preview_url'] = $metaMedia['preview_url'];
            } else {
                $row['missing_media'] = $row['media'] === null;
                $row['preview_url'] = $row['media'] !== null
                    ? 'ajax/creativePreview.php?id=' . rawurlencode((string)$row['id']) . '&v=' . rawurlencode((string)($row['updated_at'] ?? ''))
                    : '';
            }
        } elseif ($format === 'CAROUSEL') {
            $cards = [];
            foreach (array_values((array)($row['carousel'] ?? [])) as $index => $card) {
                if (!is_array($card)) continue;
                $mediaId = trim((string)($card['media_library_id'] ?? ''));
                $media = $mediaId !== '' ? $library->get($mediaId) : null;
                $card['media'] = $media;
                $card['missing_media'] = $media === null;
                $card['preview_url'] = 'ajax/creativePreview.php?id=' . rawurlencode((string)$row['id']) . '&card=' . $index . '&v=' . rawurlencode((string)($row['updated_at'] ?? ''));
                if ($media === null) $row['missing_media'] = true;
                $cards[] = $card;
            }
            $row['carousel'] = $cards;
            $row['preview_url'] = (string)($cards[0]['preview_url'] ?? '');
            if (count($cards) < 2 || count($cards) > 10) $row['missing_media'] = true;
        }
        $out[] = $row;
    }
    return $out;
}
function cl100_add_upload($library, array $upload, string $fallback): array {
    if (($upload['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK) {
        throw new RuntimeException('Media upload failed with PHP code ' . (int)($upload['error'] ?? -1));
    }
    $tmp = (string)($upload['tmp_name'] ?? '');
    if ($tmp === '' || (!is_uploaded_file($tmp) && !is_file($tmp))) {
        throw new RuntimeException('Uploaded media file is unavailable.');
    }
    return $library->add($tmp, (string)($upload['name'] ?? $fallback));
}

try {
    $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
    $store = new CreativePresetStore('/var/lib/remask/creative-presets');
    $action = strtolower(trim((string)($_REQUEST['action'] ?? 'list')));

    if ($action === 'list') {
        MetaEndpoint::ok(['items' => cl100_public_items($store, $library)]);
        exit;
    }
    if ($action === 'get') {
        $id = trim((string)($_REQUEST['id'] ?? ''));
        foreach (cl100_public_items($store, $library) as $row) {
            if ((string)$row['id'] === $id) {
                MetaEndpoint::ok($row);
                exit;
            }
        }
        throw new InvalidArgumentException('Creative not found.');
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') throw new InvalidArgumentException('POST required.');

    if ($action === 'save') {
        $id = trim((string)($_POST['id'] ?? ''));
        $current = $id !== '' ? $store->get($id) : null;
        if ($id !== '' && $current === null) throw new InvalidArgumentException('Creative not found.');
        $previousMedia = $current ? cl100_media_ids($current) : [];

        $format = cl100_format((string)($_POST['format'] ?? 'SINGLE'));
        $cta = strtoupper(cl100_string('cta', 50));
        $allowedCta = ['LEARN_MORE','SIGN_UP','APPLY_NOW','CONTACT_US','SHOP_NOW','GET_OFFER'];
        if (!preg_match('/^[A-Z][A-Z0-9_]{0,49}$/D', $cta)) throw new InvalidArgumentException('Invalid call-to-action type.');

        $destination = cl100_string('destination_url', 2000);
        if ($format !== 'INSTAGRAM_POST' && $destination !== '' && !filter_var($destination, FILTER_VALIDATE_URL)) {
            throw new InvalidArgumentException('Destination URL is invalid.');
        }

        $name = cl100_string('name', 180);
        if ($name === '') $name = 'Creative ' . gmdate('Y-m-d H:i');

        $metaBuilderRaw = trim((string)($_POST['meta_builder'] ?? '{}'));
        $metaBuilder = $metaBuilderRaw !== '' ? json_decode($metaBuilderRaw, true, 512, JSON_THROW_ON_ERROR) : [];
        if (!is_array($metaBuilder)) throw new InvalidArgumentException('meta_builder must be a JSON object.');
        $metaBuilder = MetaOfficialFields::sanitizeBuilder($metaBuilder);

        $record = [
            'name' => $name,
            'format' => $format,
            'creative_name' => cl100_string('creative_name', 180),
            'ad_name' => cl100_string('ad_name', 180),
            'message' => cl100_string('message', 12000),
            'headline' => cl100_string('headline', 500),
            'description' => cl100_string('description', 1000),
            'destination_url' => $destination,
            'cta' => $cta,
            'url_tags' => cl100_string('url_tags', 2000),
            'media_library_id' => '',
            'carousel' => [],
            'instagram_media_id' => '',
            'meta_builder' => $metaBuilder,
            'meta_asset' => [],
        ];

        $preparedRaw = trim((string)($_POST['prepared_media'] ?? ''));
        $prepared = $preparedRaw !== '' ? json_decode($preparedRaw, true, 32, JSON_THROW_ON_ERROR) : [];
        if (!is_array($prepared) || !array_is_list($prepared) || count($prepared) > 10) throw new InvalidArgumentException('INVALID_PREPARED_MEDIA');
        foreach ($prepared as $mediaId) {
            if (!is_string($mediaId) || !preg_match('/^m-[a-f0-9]{24}$/D', $mediaId) || $library->get($mediaId) === null)
                throw new InvalidArgumentException('PREPARED_MEDIA_UNAVAILABLE');
        }

        if ($format === 'SINGLE') {
            $upload = isset($_FILES['media']) && is_array($_FILES['media']) ? $_FILES['media'] : null;
            $hasUpload = $upload !== null && (($upload['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_NO_FILE);
            if ($hasUpload) {
                if ($prepared !== []) throw new InvalidArgumentException('AMBIGUOUS_PREPARED_MEDIA');
                $media = cl100_add_upload($library, $upload, 'creative');
                $record['media_library_id'] = (string)$media['id'];
            } elseif (count($prepared) === 1) {
                $record['media_library_id'] = $prepared[0];
            } elseif ($prepared !== []) {
                throw new InvalidArgumentException('SINGLE_PREPARED_MEDIA_COUNT');
            } elseif ($current && strtoupper((string)($current['format'] ?? 'SINGLE')) === 'SINGLE') {
                $record['media_library_id'] = trim((string)($current['media_library_id'] ?? ''));
            }
            if ($record['media_library_id'] === '') {
                throw new InvalidArgumentException('Загрузите изображение или видео в общий шаблон. Ресурсы Meta выбираются в автозаливе.');
            }
        } elseif ($format === 'CAROUSEL') {
            $cardsRaw = trim((string)($_POST['carousel_cards'] ?? ''));
            $cardsMeta = $cardsRaw !== '' ? json_decode($cardsRaw, true, 512, JSON_THROW_ON_ERROR) : [];
            if (!is_array($cardsMeta)) throw new InvalidArgumentException('carousel_cards must be an array.');

            $upload = isset($_FILES['carousel_media']) && is_array($_FILES['carousel_media']) ? $_FILES['carousel_media'] : null;
            $names = $upload && is_array($upload['name'] ?? null) ? array_values((array)$upload['name']) : [];
            $hasUpload = count(array_filter($names, static fn($v) => trim((string)$v) !== '')) > 0;

            if ($hasUpload) {
                if ($prepared !== []) throw new InvalidArgumentException('AMBIGUOUS_PREPARED_MEDIA');
                if (count($names) < 2 || count($names) > 10) throw new InvalidArgumentException('Carousel requires 2 to 10 images.');
                if (count($cardsMeta) !== count($names)) throw new InvalidArgumentException('Carousel card metadata count must match images.');
                $tmpNames = array_values((array)($upload['tmp_name'] ?? []));
                $errors = array_values((array)($upload['error'] ?? []));
                foreach ($names as $index => $fileName) {
                    $one = [
                        'name' => $fileName,
                        'tmp_name' => $tmpNames[$index] ?? '',
                        'error' => $errors[$index] ?? UPLOAD_ERR_NO_FILE,
                    ];
                    $media = cl100_add_upload($library, $one, 'carousel-' . ($index + 1));
                    if (!in_array(($media['media_type'] ?? ''), ['image','video'], true)) throw new InvalidArgumentException('Carousel requires image or video media.');
                    $meta = is_array($cardsMeta[$index] ?? null) ? $cardsMeta[$index] : [];
                    $record['carousel'][] = [
                        'media_library_id' => (string)$media['id'],
                        'headline' => trim((string)($meta['headline'] ?? '')),
                        'description' => trim((string)($meta['description'] ?? '')),
                        'link' => trim((string)($meta['link'] ?? '')),
                        'call_to_action' => trim((string)($meta['call_to_action'] ?? '')),
                    ];
                }
            } else {
                $existing = $prepared !== [] ? array_map(static fn($id) => ['media_library_id'=>$id], $prepared) : (($current && strtoupper((string)($current['format'] ?? '')) === 'CAROUSEL')
                    ? array_values((array)($current['carousel'] ?? []))
                    : []);
                if (count($existing) < 2 || count($existing) > 10) throw new InvalidArgumentException('Select 2 to 10 carousel images.');
                if (count($cardsMeta) !== count($existing)) throw new InvalidArgumentException('Carousel metadata count must match media');
                foreach ($existing as $index => $card) {
                    if (!is_array($card)) continue;
                    $meta = is_array($cardsMeta[$index] ?? null) ? $cardsMeta[$index] : $card;
                    $record['carousel'][] = [
                        'media_library_id' => trim((string)($card['media_library_id'] ?? '')),
                        'headline' => trim((string)($meta['headline'] ?? '')),
                        'description' => trim((string)($meta['description'] ?? '')),
                        'link' => trim((string)($meta['link'] ?? '')),
                        'call_to_action' => trim((string)($meta['call_to_action'] ?? '')),
                    ];
                }
            }
            if (count($record['carousel']) < 2 || count($record['carousel']) > 10) throw new InvalidArgumentException('Carousel requires 2 to 10 images.');
        } else {
            // Publication is selected and privately verified per destination in Launch.
            $record['instagram_media_id'] = '';
        }

        $saved = $store->save($id !== '' ? $id : null, $record);
        // Media Library files are shared by saved bundles and private Jobs.
        // Editing a preset cannot dispose of their immutable media references.

        MetaEndpoint::ok(['item' => $saved, 'items' => cl100_public_items($store, $library)]);
        exit;
    }

    if ($action === 'duplicate') {
        $saved = $store->duplicate(trim((string)($_POST['id'] ?? '')));
        MetaEndpoint::ok(['item' => $saved, 'items' => cl100_public_items($store, $library)]);
        exit;
    }

    if ($action === 'delete') {
        $id = trim((string)($_POST['id'] ?? ''));
        $removed = $store->delete($id);
        if ($removed === null) throw new InvalidArgumentException('Creative not found.');
        // The explicit Media Library lifecycle owns deletion of shared files.
        MetaEndpoint::ok(['deleted' => true, 'items' => cl100_public_items($store, $library)]);
        exit;
    }

    throw new InvalidArgumentException('Unsupported action.');
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
