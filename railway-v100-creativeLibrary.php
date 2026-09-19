<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MediaLibraryStoreFactory.php';
require_once __DIR__ . '/../classes/CreativePresetStore.php';

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
        $row['media'] = null;
        $row['missing_media'] = false;
        $row['preview_url'] = '';

        if ($format === 'SINGLE') {
            $id = trim((string)($row['media_library_id'] ?? ''));
            $row['media'] = $id !== '' ? $library->get($id) : null;
            $row['missing_media'] = $row['media'] === null;
            $row['preview_url'] = 'ajax/creativePreview.php?id=' . rawurlencode((string)$row['id']) . '&v=' . rawurlencode((string)($row['updated_at'] ?? ''));
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
        if (!in_array($cta, $allowedCta, true)) $cta = 'LEARN_MORE';

        $destination = cl100_string('destination_url', 2000);
        if ($format !== 'INSTAGRAM_POST' && $destination !== '' && !filter_var($destination, FILTER_VALIDATE_URL)) {
            throw new InvalidArgumentException('Destination URL is invalid.');
        }

        $name = cl100_string('name', 180);
        if ($name === '') $name = 'Creative ' . gmdate('Y-m-d H:i');

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
        ];

        if ($format === 'SINGLE') {
            $upload = isset($_FILES['media']) && is_array($_FILES['media']) ? $_FILES['media'] : null;
            $hasUpload = $upload !== null && (($upload['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_NO_FILE);
            if ($hasUpload) {
                $media = cl100_add_upload($library, $upload, 'creative');
                $record['media_library_id'] = (string)$media['id'];
            } elseif ($current && strtoupper((string)($current['format'] ?? 'SINGLE')) === 'SINGLE') {
                $record['media_library_id'] = trim((string)($current['media_library_id'] ?? ''));
            }
            if ($record['media_library_id'] === '') throw new InvalidArgumentException('Select image or video.');
        } elseif ($format === 'CAROUSEL') {
            $cardsRaw = trim((string)($_POST['carousel_cards'] ?? ''));
            $cardsMeta = $cardsRaw !== '' ? json_decode($cardsRaw, true, 512, JSON_THROW_ON_ERROR) : [];
            if (!is_array($cardsMeta)) throw new InvalidArgumentException('carousel_cards must be an array.');

            $upload = isset($_FILES['carousel_media']) && is_array($_FILES['carousel_media']) ? $_FILES['carousel_media'] : null;
            $names = $upload && is_array($upload['name'] ?? null) ? array_values((array)$upload['name']) : [];
            $hasUpload = count(array_filter($names, static fn($v) => trim((string)$v) !== '')) > 0;

            if ($hasUpload) {
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
                    if (($media['media_type'] ?? '') !== 'image') throw new InvalidArgumentException('Carousel currently supports image cards only.');
                    $meta = is_array($cardsMeta[$index] ?? null) ? $cardsMeta[$index] : [];
                    $record['carousel'][] = [
                        'media_library_id' => (string)$media['id'],
                        'headline' => trim((string)($meta['headline'] ?? '')),
                        'description' => trim((string)($meta['description'] ?? '')),
                        'link' => trim((string)($meta['link'] ?? '')),
                    ];
                }
            } else {
                $existing = ($current && strtoupper((string)($current['format'] ?? '')) === 'CAROUSEL')
                    ? array_values((array)($current['carousel'] ?? []))
                    : [];
                if (count($existing) < 2 || count($existing) > 10) throw new InvalidArgumentException('Select 2 to 10 carousel images.');
                foreach ($existing as $index => $card) {
                    if (!is_array($card)) continue;
                    $meta = is_array($cardsMeta[$index] ?? null) ? $cardsMeta[$index] : $card;
                    $record['carousel'][] = [
                        'media_library_id' => trim((string)($card['media_library_id'] ?? '')),
                        'headline' => trim((string)($meta['headline'] ?? '')),
                        'description' => trim((string)($meta['description'] ?? '')),
                        'link' => trim((string)($meta['link'] ?? '')),
                    ];
                }
            }
            if (count($record['carousel']) < 2 || count($record['carousel']) > 10) throw new InvalidArgumentException('Carousel requires 2 to 10 images.');
        } else {
            $ig = cl100_string('instagram_media_id', 100);
            if (!preg_match('/^[0-9]+$/', $ig)) throw new InvalidArgumentException('Instagram media ID must be numeric.');
            $record['instagram_media_id'] = $ig;
        }

        $saved = $store->save($id !== '' ? $id : null, $record);
        $newMedia = cl100_media_ids($saved);
        foreach (array_diff($previousMedia, $newMedia) as $oldId) {
            if (!cl100_referenced($store, $oldId)) {
                try { $library->delete($oldId); } catch (Throwable $ignored) {}
            }
        }

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
        foreach (cl100_media_ids($removed) as $oldId) {
            if (!cl100_referenced($store, $oldId)) {
                try { $library->delete($oldId); } catch (Throwable $ignored) {}
            }
        }
        MetaEndpoint::ok(['deleted' => true, 'items' => cl100_public_items($store, $library)]);
        exit;
    }

    throw new InvalidArgumentException('Unsupported action.');
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
