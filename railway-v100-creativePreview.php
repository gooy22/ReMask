<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MediaLibraryStoreFactory.php';
require_once __DIR__ . '/../classes/CreativePresetStore.php';

try {
    $id = trim((string)($_GET['id'] ?? ''));
    $cardIndex = isset($_GET['card']) ? (int)$_GET['card'] : null;

    $store = new CreativePresetStore('/var/lib/remask/creative-presets');
    $row = $store->get($id);
    if ($row === null) throw new RuntimeException('Creative not found.');

    $format = strtoupper((string)($row['format'] ?? 'SINGLE'));
    if ($format === 'CAROUSEL') {
        $cards = array_values(array_filter((array)($row['carousel'] ?? []), 'is_array'));
        if ($cardIndex === null) $cardIndex = 0;
        $mediaId = trim((string)($cards[$cardIndex]['media_library_id'] ?? ''));
    } else {
        $mediaId = trim((string)($row['media_library_id'] ?? ''));
    }
    if ($mediaId === '') throw new RuntimeException('Creative has no preview media.');

    $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
    $media = $library->getInternal($mediaId);
    if ($media === null) throw new RuntimeException('Creative media is missing.');

    $path = (string)$media['path'];
    $mime = (string)($media['mime_type'] ?? 'application/octet-stream');
    $size = filesize($path);
    header('Content-Type: ' . $mime);
    if ($size !== false) header('Content-Length: ' . $size);
    header('Content-Disposition: inline; filename="' . addcslashes((string)($media['original_name'] ?? 'creative'), "\"\\") . '"');
    header('Cache-Control: private, max-age=3600');
    readfile($path);
} catch (Throwable $e) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    echo $e->getMessage();
}
