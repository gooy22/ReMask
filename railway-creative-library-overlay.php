<?php
/**
 * v98 — real Creative Library.
 * Creates a dedicated creatives.php library and connects saved presets to bulk Launch
 * through the already-supported media_library_id flow.
 */
$root='/var/www/html';
$settingsPath=$root.'/settings.php';
$menuPath=$root.'/menu.php';
$launchPhpPath=$root.'/launch.php';
$launchJsPath=$root.'/scripts/launch.js';

foreach([$settingsPath,$menuPath,$launchPhpPath,$launchJsPath] as $path){
    if(!is_file($path)){fwrite(STDERR,"[creative-library] missing $path\n");exit(251);}
}

/* ---------- persistent media path fallback ---------- */
$settings=file_get_contents($settingsPath);
if(strpos($settings,'REMASK_CREATIVE_LIBRARY_V1')===false){
    $settings .= <<<'PHP_CODE'

/* REMASK_CREATIVE_LIBRARY_V1 */
if (!defined('REMASK_MEDIA_LIBRARY_DIR')) define('REMASK_MEDIA_LIBRARY_DIR', '/var/lib/remask/media-library');
if (!defined('REMASK_MEDIA_LIBRARY_MAX_BYTES')) define('REMASK_MEDIA_LIBRARY_MAX_BYTES', 157286400);

PHP_CODE;
    file_put_contents($settingsPath,$settings);
}

/* ---------- CreativePresetStore ---------- */
$store=<<<'PHP_CODE'
<?php

final class CreativePresetStore
{
    private string $root;
    private string $indexFile;

    public function __construct(?string $root = null)
    {
        $this->root = rtrim($root ?: '/var/lib/remask/creative-presets', '/');
        $this->indexFile = $this->root . '/index.json';
        if (!is_dir($this->root) && !@mkdir($this->root, 0700, true) && !is_dir($this->root)) {
            throw new RuntimeException('Could not create Creative Library directory.');
        }
        if (!is_file($this->indexFile)) {
            if (file_put_contents($this->indexFile, "[]\n", LOCK_EX) === false) {
                throw new RuntimeException('Could not initialize Creative Library.');
            }
            @chmod($this->indexFile, 0600);
        }
    }

    public function list(): array
    {
        $rows = $this->readAll();
        usort($rows, static fn(array $a, array $b): int =>
            strcmp((string)($b['updated_at'] ?? $b['created_at'] ?? ''), (string)($a['updated_at'] ?? $a['created_at'] ?? ''))
        );
        return $rows;
    }

    public function get(string $id): ?array
    {
        $id = trim($id);
        foreach ($this->readAll() as $row) {
            if ((string)($row['id'] ?? '') === $id) return $row;
        }
        return null;
    }

    public function save(?string $id, array $data): array
    {
        $id = trim((string)$id);
        $h = $this->lock();
        try {
            $rows = $this->readHandle($h);
            $now = gmdate('c');
            $found = null;
            foreach ($rows as $i => $row) {
                if ($id !== '' && (string)($row['id'] ?? '') === $id) {
                    $found = $i;
                    break;
                }
            }
            if ($found === null) {
                $id = 'cr-' . bin2hex(random_bytes(8));
                $record = array_merge([
                    'id' => $id,
                    'created_at' => $now,
                ], $data, ['updated_at' => $now]);
                $rows[] = $record;
            } else {
                $record = array_merge($rows[$found], $data, ['id'=>$id,'updated_at'=>$now]);
                $rows[$found] = $record;
            }
            $this->writeHandle($h,$rows);
            return $record;
        } finally {
            $this->unlock($h);
        }
    }

    public function duplicate(string $id): array
    {
        $row = $this->get($id);
        if ($row === null) throw new InvalidArgumentException('Creative not found.');
        unset($row['id'],$row['created_at'],$row['updated_at']);
        $row['name'] = trim((string)($row['name'] ?? 'Creative')) . ' copy';
        return $this->save(null,$row);
    }

    public function delete(string $id): ?array
    {
        $id = trim($id);
        $h = $this->lock();
        try {
            $rows = $this->readHandle($h);
            $removed = null;
            $kept = [];
            foreach ($rows as $row) {
                if ((string)($row['id'] ?? '') === $id) $removed = $row;
                else $kept[] = $row;
            }
            if ($removed !== null) $this->writeHandle($h,$kept);
            return $removed;
        } finally {
            $this->unlock($h);
        }
    }

    public function mediaReferenced(string $mediaId): bool
    {
        foreach ($this->readAll() as $row) {
            if ((string)($row['media_library_id'] ?? '') === $mediaId) return true;
        }
        return false;
    }

    private function readAll(): array
    {
        $raw = @file_get_contents($this->indexFile);
        $rows = json_decode($raw ?: '[]', true);
        return is_array($rows) ? array_values(array_filter($rows,'is_array')) : [];
    }

    private function lock()
    {
        $h = fopen($this->indexFile,'c+');
        if ($h === false || !flock($h,LOCK_EX)) throw new RuntimeException('Could not lock Creative Library.');
        return $h;
    }

    private function unlock($h): void
    {
        if (is_resource($h)) { flock($h,LOCK_UN); fclose($h); }
    }

    private function readHandle($h): array
    {
        rewind($h);
        $rows = json_decode(stream_get_contents($h) ?: '[]', true);
        return is_array($rows) ? array_values(array_filter($rows,'is_array')) : [];
    }

    private function writeHandle($h,array $rows): void
    {
        rewind($h);
        if (!ftruncate($h,0)) throw new RuntimeException('Could not truncate Creative Library.');
        $json = json_encode(array_values($rows), JSON_PRETTY_PRINT|JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR) . "\n";
        if (fwrite($h,$json) === false) throw new RuntimeException('Could not write Creative Library.');
        fflush($h);
        @chmod($this->indexFile,0600);
    }
}
PHP_CODE;
file_put_contents($root.'/classes/CreativePresetStore.php',$store);

/* ---------- API ---------- */
$api=<<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MediaLibraryStoreFactory.php';
require_once __DIR__ . '/../classes/CreativePresetStore.php';

function creative_library_string(string $key, int $max = 5000): string {
    $value = trim((string)($_POST[$key] ?? ''));
    if (strlen($value) > $max) throw new InvalidArgumentException($key . ' is too long.');
    return $value;
}

function creative_library_items(CreativePresetStore $store, MediaLibraryStoreInterface $library): array {
    $out = [];
    foreach ($store->list() as $row) {
        $mediaId = (string)($row['media_library_id'] ?? '');
        $media = $mediaId !== '' ? $library->get($mediaId) : null;
        $row['media'] = $media;
        $row['missing_media'] = $media === null;
        $row['preview_url'] = 'ajax/creativePreview.php?id=' . rawurlencode((string)$row['id']) . '&v=' . rawurlencode((string)($row['updated_at'] ?? ''));
        $out[] = $row;
    }
    return $out;
}

try {
    $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
    $store = new CreativePresetStore('/var/lib/remask/creative-presets');
    $action = strtolower(trim((string)($_REQUEST['action'] ?? 'list')));

    if ($action === 'list') {
        MetaEndpoint::ok(['items'=>creative_library_items($store,$library)]);
        exit;
    }

    if ($action === 'get') {
        $id = trim((string)($_REQUEST['id'] ?? ''));
        $row = $store->get($id);
        if ($row === null) throw new InvalidArgumentException('Creative not found.');
        $mediaId = (string)($row['media_library_id'] ?? '');
        $row['media'] = $mediaId !== '' ? $library->get($mediaId) : null;
        $row['preview_url'] = 'ajax/creativePreview.php?id=' . rawurlencode($id) . '&v=' . rawurlencode((string)($row['updated_at'] ?? ''));
        MetaEndpoint::ok($row);
        exit;
    }

    if ($_SERVER['REQUEST_METHOD'] !== 'POST') throw new InvalidArgumentException('POST required.');

    if ($action === 'save') {
        $id = trim((string)($_POST['id'] ?? ''));
        $current = $id !== '' ? $store->get($id) : null;
        if ($id !== '' && $current === null) throw new InvalidArgumentException('Creative not found.');

        $mediaId = (string)($current['media_library_id'] ?? '');
        $oldMediaId = $mediaId;
        $upload = isset($_FILES['media']) && is_array($_FILES['media']) ? $_FILES['media'] : null;
        if ($upload !== null && (($upload['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_NO_FILE)) {
            if (($upload['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK) {
                throw new RuntimeException('Media upload failed with PHP code ' . (int)($upload['error'] ?? -1));
            }
            $tmp = (string)($upload['tmp_name'] ?? '');
            if ($tmp === '' || (!is_uploaded_file($tmp) && !is_file($tmp))) throw new RuntimeException('Uploaded file is unavailable.');
            $media = $library->add($tmp,(string)($upload['name'] ?? 'creative'));
            $mediaId = (string)$media['id'];
        }
        if ($mediaId === '') throw new InvalidArgumentException('Select an image or video.');

        $cta = strtoupper(creative_library_string('cta',50));
        $allowedCta = ['LEARN_MORE','SIGN_UP','APPLY_NOW','CONTACT_US','SHOP_NOW','GET_OFFER'];
        if (!in_array($cta,$allowedCta,true)) $cta = 'LEARN_MORE';

        $destination = creative_library_string('destination_url',2000);
        if ($destination !== '' && !filter_var($destination,FILTER_VALIDATE_URL)) throw new InvalidArgumentException('Destination URL is invalid.');

        $name = creative_library_string('name',180);
        if ($name === '') $name = 'Creative ' . gmdate('Y-m-d H:i');

        $record = $store->save($id !== '' ? $id : null,[
            'name'=>$name,
            'media_library_id'=>$mediaId,
            'creative_name'=>creative_library_string('creative_name',180),
            'ad_name'=>creative_library_string('ad_name',180),
            'message'=>creative_library_string('message',12000),
            'headline'=>creative_library_string('headline',500),
            'description'=>creative_library_string('description',1000),
            'destination_url'=>$destination,
            'cta'=>$cta,
            'url_tags'=>creative_library_string('url_tags',2000),
        ]);

        if ($oldMediaId !== '' && $oldMediaId !== $mediaId && !$store->mediaReferenced($oldMediaId)) {
            try { $library->delete($oldMediaId); } catch (Throwable $ignored) {}
        }

        MetaEndpoint::ok([
            'item'=>$record,
            'items'=>creative_library_items($store,$library),
        ]);
        exit;
    }

    if ($action === 'duplicate') {
        $id = trim((string)($_POST['id'] ?? ''));
        $record = $store->duplicate($id);
        MetaEndpoint::ok(['item'=>$record,'items'=>creative_library_items($store,$library)]);
        exit;
    }

    if ($action === 'delete') {
        $id = trim((string)($_POST['id'] ?? ''));
        $removed = $store->delete($id);
        if ($removed === null) throw new InvalidArgumentException('Creative not found.');
        $mediaId = (string)($removed['media_library_id'] ?? '');
        if ($mediaId !== '' && !$store->mediaReferenced($mediaId)) {
            try { $library->delete($mediaId); } catch (Throwable $ignored) {}
        }
        MetaEndpoint::ok(['deleted'=>true,'items'=>creative_library_items($store,$library)]);
        exit;
    }

    throw new InvalidArgumentException('Unsupported action.');
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
PHP_CODE;
file_put_contents($root.'/ajax/creativeLibrary.php',$api);

/* ---------- preview endpoint ---------- */
$preview=<<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MediaLibraryStoreFactory.php';
require_once __DIR__ . '/../classes/CreativePresetStore.php';

try {
    $id = trim((string)($_GET['id'] ?? ''));
    $store = new CreativePresetStore('/var/lib/remask/creative-presets');
    $row = $store->get($id);
    if ($row === null) throw new RuntimeException('Creative not found.');
    $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
    $media = $library->getInternal((string)($row['media_library_id'] ?? ''));
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
PHP_CODE;
file_put_contents($root.'/ajax/creativePreview.php',$preview);

/* ---------- creatives page ---------- */
$page=<<<'PHP_CODE'
<?php
require_once __DIR__ . '/settings.php';
require_once __DIR__ . '/checkpassword.php';
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8"/>
    <meta name="remask-csrf" content="<?= htmlspecialchars(remask_csrf_token(), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?>"/>
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no"/>
    <link href="styles/bootstrap.min.css" rel="stylesheet"/>
    <link href="styles/signin.css" rel="stylesheet"/>
    <link href="styles/app.css" rel="stylesheet"/>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.1.0/css/all.min.css" rel="stylesheet">
    <link rel="icon" type="image/png" href="styles/img/favicon.png">
    <title><?php include 'version.php' ?> — Креативы</title>
    <style>
        .cr-page{max-width:1500px;margin:0 auto;padding:18px 22px 56px;text-align:left;color:#e7e9ee}
        .cr-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:2px 0 18px}
        .cr-title{font-size:28px;font-weight:700;line-height:1.15;margin:0}
        .cr-head-actions{display:flex;gap:8px}
        .cr-primary{background:#2d67e8;border:1px solid #2d67e8;color:#fff;border-radius:7px;padding:9px 14px;font-weight:700}
        .cr-secondary{background:#2b3039;border:1px solid #3b424e;color:#dfe3eb;border-radius:7px;padding:9px 12px}
        .cr-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:16px}
        .cr-search-wrap{position:relative;max-width:440px;width:100%}
        .cr-search-wrap i{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:#707989;font-size:13px}
        .cr-search{width:100%;height:40px;background:#20242b;border:1px solid #343a45;border-radius:8px;color:#e5e8ee;padding:0 12px 0 36px;outline:0}
        .cr-count{font-size:12px;color:#7f8898;white-space:nowrap}
        .cr-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(235px,1fr));gap:14px}
        .cr-card{background:#23272f;border:1px solid #313742;border-radius:10px;overflow:hidden;transition:.16s ease;min-width:0}
        .cr-card:hover{transform:translateY(-1px);border-color:#46505f}
        .cr-preview{position:relative;aspect-ratio:4/3;background:#15181d;overflow:hidden}
        .cr-preview img,.cr-preview video{width:100%;height:100%;object-fit:cover;display:block}
        .cr-preview-empty{height:100%;display:flex;align-items:center;justify-content:center;color:#707989}
        .cr-type{position:absolute;left:9px;top:9px;background:rgba(15,17,21,.78);backdrop-filter:blur(8px);padding:4px 7px;border-radius:5px;font-size:10px;font-weight:700;letter-spacing:.5px;color:#dce1e9}
        .cr-body{padding:11px}
        .cr-name{font-size:14px;font-weight:700;color:#edf0f5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .cr-file{font-size:11px;color:#7f8898;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .cr-actions{display:flex;gap:7px;margin-top:11px}
        .cr-launch{flex:1;background:#248653;border:1px solid #248653;color:#fff;border-radius:7px;padding:7px 9px;font-weight:700;font-size:12px;text-align:center;text-decoration:none!important}
        .cr-icon{width:34px;height:34px;display:inline-flex;align-items:center;justify-content:center;background:#2b3038;border:1px solid #3b424d;color:#cfd5df;border-radius:7px}
        .cr-empty{grid-column:1/-1;padding:70px 20px;text-align:center;color:#788292}
        .cr-empty i{font-size:28px;margin-bottom:12px;color:#525b69;display:block}
        .cr-backdrop{display:none;position:fixed;inset:0;background:rgba(7,9,12,.74);z-index:900;align-items:center;justify-content:center;padding:24px}
        .cr-backdrop.open{display:flex}
        .cr-modal{width:min(920px,100%);max-height:90vh;overflow:auto;background:#242831;border:1px solid #373e49;border-radius:12px;box-shadow:0 24px 80px rgba(0,0,0,.45)}
        .cr-modal-head{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid #343a45}
        .cr-modal-head h3{font-size:18px;margin:0;color:#f0f2f6}.cr-close{border:0;background:transparent;color:#8f98a8;font-size:24px;line-height:1}
        .cr-modal-body{display:grid;grid-template-columns:300px 1fr;gap:20px;padding:18px}
        .cr-editor-preview{aspect-ratio:4/3;background:#171a1f;border-radius:9px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#737d8c}
        .cr-editor-preview img,.cr-editor-preview video{width:100%;height:100%;object-fit:contain}
        .cr-file-button{display:flex;align-items:center;justify-content:center;width:100%;margin-top:10px;height:38px;border:1px solid #414955;border-radius:7px;background:#2a2f38;color:#dbe0e8;cursor:pointer;font-size:12px;font-weight:700}
        .cr-file-button input{display:none}
        .cr-current-file{font-size:11px;color:#7f8898;margin-top:7px;overflow-wrap:anywhere}
        .cr-form label{font-size:11px;color:#929bab;margin:0 0 5px}
        .cr-form .form-control{background:#1f232a;border:1px solid #373e49;color:#e6e9ef;border-radius:7px}
        .cr-form .form-group{margin-bottom:12px}
        .cr-form textarea{resize:vertical;min-height:90px}
        .cr-more{margin-top:5px;border-top:1px solid #343a45;padding-top:10px}
        .cr-more summary{cursor:pointer;color:#9fa8b7;font-size:12px;list-style:none}
        .cr-more summary::-webkit-details-marker{display:none}
        .cr-more[open] summary{margin-bottom:12px}
        .cr-modal-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;border-top:1px solid #343a45}
        .cr-status{font-size:12px;color:#8f98a8;min-height:18px}.cr-status.bad{color:#ff8f8f}.cr-status.ok{color:#69d99b}
        @media(max-width:760px){.cr-page{padding:12px}.cr-head{align-items:flex-start}.cr-modal-body{grid-template-columns:1fr}.cr-editor-preview{max-height:260px}.cr-grid{grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}}
    </style>
</head>
<body class="app-shell">
<?php include 'menu.php' ?>
<main class="app-main">
<div class="cr-page">
    <div class="cr-head">
        <h1 class="cr-title">Креативы</h1>
        <div class="cr-head-actions">
            <button id="newCreative" type="button" class="cr-primary"><i class="fa-solid fa-plus mr-1"></i> ДОБАВИТЬ КРЕО</button>
        </div>
    </div>

    <div class="cr-toolbar">
        <div class="cr-search-wrap">
            <i class="fa-solid fa-magnifying-glass"></i>
            <input id="creativeSearch" class="cr-search" placeholder="Поиск">
        </div>
        <span id="creativeCount" class="cr-count"></span>
        <button id="refreshCreatives" type="button" class="cr-secondary" title="Обновить"><i class="fa-solid fa-rotate"></i></button>
    </div>

    <div id="creativeGrid" class="cr-grid">
        <div class="cr-empty"><i class="fa-regular fa-images"></i>Загрузка…</div>
    </div>
</div>

<div id="creativeModal" class="cr-backdrop" aria-hidden="true">
    <div class="cr-modal" role="dialog" aria-modal="true">
        <div class="cr-modal-head">
            <h3 id="creativeEditorTitle">Новое крео</h3>
            <button id="closeCreative" type="button" class="cr-close">×</button>
        </div>
        <form id="creativeForm">
            <input id="creativeId" type="hidden">
            <div class="cr-modal-body">
                <div>
                    <div id="editorPreview" class="cr-editor-preview"><i class="fa-regular fa-image"></i></div>
                    <label class="cr-file-button">ВЫБРАТЬ ФАЙЛ<input id="presetMedia" name="media" type="file" accept="image/*,video/*"></label>
                    <div id="presetCurrentMedia" class="cr-current-file"></div>
                </div>
                <div class="cr-form">
                    <div class="form-group">
                        <label>Название</label>
                        <input id="presetName" class="form-control" placeholder="Название крео">
                    </div>
                    <div class="form-group">
                        <label>Основной текст</label>
                        <textarea id="presetMessage" class="form-control" rows="4" placeholder="Primary text"></textarea>
                    </div>
                    <div class="row">
                        <div class="col-md-6 form-group"><label>Заголовок</label><input id="presetHeadline" class="form-control"></div>
                        <div class="col-md-6 form-group"><label>CTA</label>
                            <select id="presetCta" class="form-control">
                                <option value="LEARN_MORE">Подробнее</option>
                                <option value="SIGN_UP">Регистрация</option>
                                <option value="APPLY_NOW">Подать заявку</option>
                                <option value="CONTACT_US">Связаться</option>
                                <option value="SHOP_NOW">Купить</option>
                                <option value="GET_OFFER">Получить предложение</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group"><label>Ссылка</label><input id="presetUrl" class="form-control" placeholder="https://..."></div>

                    <details class="cr-more">
                        <summary>Дополнительные поля</summary>
                        <div class="row">
                            <div class="col-md-6 form-group"><label>Creative name</label><input id="presetCreativeName" class="form-control"></div>
                            <div class="col-md-6 form-group"><label>Ad name</label><input id="presetAdName" class="form-control"></div>
                        </div>
                        <div class="form-group"><label>Description</label><input id="presetDescription" class="form-control"></div>
                        <div class="form-group"><label>UTM / URL tags</label><input id="presetTags" class="form-control"></div>
                    </details>
                </div>
            </div>
            <div class="cr-modal-foot">
                <div id="creativeStatus" class="cr-status"></div>
                <div>
                    <button id="cancelCreative" type="button" class="cr-secondary mr-2">ОТМЕНА</button>
                    <button id="saveCreative" type="submit" class="cr-primary">СОХРАНИТЬ</button>
                </div>
            </div>
        </form>
    </div>
</div>

<script src="scripts/creatives.js?v=20260919-creative-library-v99" type="module"></script>
<div class="app-footer"><?php include 'copyright.php' ?></div>
</main>
</body>
</html>
PHP_CODE;
file_put_contents($root.'/creatives.php',$page);

/* ---------- creatives page JS ---------- */
$creativeJs=<<<'JS_CODE'
const $ = (id) => document.getElementById(id);
let items = [];
let editing = null;
let previewObjectUrl = '';

function csrfToken(){ return document.querySelector('meta[name="remask-csrf"]')?.content || ''; }
function esc(value){ return String(value ?? '').replace(/[&<>'"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];}); }
function formatBytes(bytes){ const n=Number(bytes||0); if(n<1024)return n+' B'; if(n<1048576)return (n/1024).toFixed(1)+' KB'; return (n/1048576).toFixed(1)+' MB'; }

async function api(url,options){
    options=options||{};
    options.headers=Object.assign({},options.headers||{});
    if((options.method||'GET').toUpperCase()!=='GET') options.headers['X-ReMask-CSRF']=csrfToken();
    const r=await fetch(url,options);
    const text=await r.text();
    let j; try{j=JSON.parse(text);}catch(e){throw new Error('Invalid JSON ('+r.status+')');}
    if(!r.ok||j.ok===false){const err=j.error||j;throw new Error(err.message||('HTTP '+r.status));}
    return j.data;
}

function setStatus(message,type){
    const el=$('creativeStatus');
    el.textContent=message||'';
    el.className='cr-status'+(type?' '+type:'');
}

function closeEditor(){
    $('creativeModal').classList.remove('open');
    $('creativeModal').setAttribute('aria-hidden','true');
    if(previewObjectUrl){URL.revokeObjectURL(previewObjectUrl);previewObjectUrl='';}
}

function previewHtml(src,type){
    if(!src) return '<i class="fa-regular fa-image"></i>';
    return type==='video'
        ? '<video src="'+esc(src)+'" muted controls preload="metadata"></video>'
        : '<img src="'+esc(src)+'" alt="">';
}

function openEditor(item){
    editing=item||null;
    $('creativeForm').reset();
    $('creativeId').value=item?.id||'';
    $('creativeEditorTitle').textContent=item?'Редактирование':'Новое крео';
    $('presetName').value=item?.name||'';
    $('presetCreativeName').value=item?.creative_name||'';
    $('presetAdName').value=item?.ad_name||'';
    $('presetMessage').value=item?.message||'';
    $('presetHeadline').value=item?.headline||'';
    $('presetDescription').value=item?.description||'';
    $('presetUrl').value=item?.destination_url||'';
    $('presetCta').value=item?.cta||'LEARN_MORE';
    $('presetTags').value=item?.url_tags||'';
    $('presetCurrentMedia').textContent=item?.media
        ? item.media.original_name+' · '+formatBytes(item.media.size_bytes)
        : 'Для нового крео выбери изображение или видео';
    $('editorPreview').innerHTML=item?.media ? previewHtml(item.preview_url,item.media.media_type) : '<i class="fa-regular fa-image"></i>';
    setStatus('');
    $('creativeModal').classList.add('open');
    $('creativeModal').setAttribute('aria-hidden','false');
}

function render(){
    const q=$('creativeSearch').value.trim().toLowerCase();
    const rows=items.filter(function(item){
        const hay=[item.name,item.message,item.headline,item.media?.original_name].join(' ').toLowerCase();
        return !q||hay.includes(q);
    });
    $('creativeCount').textContent=rows.length+(rows.length===1?' крео':' крео');
    if(!rows.length){
        $('creativeGrid').innerHTML='<div class="cr-empty"><i class="fa-regular fa-images"></i>'+(items.length?'Ничего не найдено':'Пока пусто')+'</div>';
        return;
    }
    $('creativeGrid').innerHTML=rows.map(function(item){
        const media=item.media||{};
        const type=(media.media_type||'media').toUpperCase();
        const preview=item.missing_media
            ? '<div class="cr-preview-empty"><i class="fa-regular fa-image"></i></div>'
            : previewHtml(item.preview_url,media.media_type);
        return '<article class="cr-card" data-id="'+esc(item.id)+'">'+
            '<div class="cr-preview">'+preview+'<span class="cr-type">'+esc(type)+'</span></div>'+
            '<div class="cr-body">'+
                '<div class="cr-name" title="'+esc(item.name||item.id)+'">'+esc(item.name||item.id)+'</div>'+
                '<div class="cr-file">'+esc(media.original_name||'Файл отсутствует')+'</div>'+
                '<div class="cr-actions">'+
                    '<a class="cr-launch" href="launch.php?creative_preset='+encodeURIComponent(item.id)+'">В АВТОЗАЛИВ</a>'+
                    '<button class="cr-icon" type="button" data-action="edit" title="Изменить"><i class="fa-solid fa-pen"></i></button>'+
                    '<button class="cr-icon" type="button" data-action="duplicate" title="Дублировать"><i class="fa-regular fa-copy"></i></button>'+
                    '<button class="cr-icon" type="button" data-action="delete" title="Удалить"><i class="fa-regular fa-trash-can"></i></button>'+
                '</div>'+
            '</div>'+
        '</article>';
    }).join('');
}

async function load(){
    const data=await api('ajax/creativeLibrary.php?action=list');
    items=data.items||[];
    render();
}

async function save(e){
    e.preventDefault();
    const id=$('creativeId').value.trim();
    const file=$('presetMedia').files[0];
    if(!id&&!file){setStatus('Выбери файл.','bad');return;}
    const form=new FormData();
    form.append('action','save');
    if(id)form.append('id',id);
    form.append('name',$('presetName').value.trim());
    form.append('creative_name',$('presetCreativeName').value.trim());
    form.append('ad_name',$('presetAdName').value.trim());
    form.append('message',$('presetMessage').value.trim());
    form.append('headline',$('presetHeadline').value.trim());
    form.append('description',$('presetDescription').value.trim());
    form.append('destination_url',$('presetUrl').value.trim());
    form.append('cta',$('presetCta').value);
    form.append('url_tags',$('presetTags').value.trim());
    if(file)form.append('media',file,file.name);
    $('saveCreative').disabled=true;
    setStatus('Сохраняю…');
    try{
        const data=await api('ajax/creativeLibrary.php',{method:'POST',body:form});
        items=data.items||[];
        render();
        setStatus('Сохранено.','ok');
        setTimeout(closeEditor,300);
    }catch(err){setStatus(err.message,'bad');}
    finally{$('saveCreative').disabled=false;}
}

async function itemAction(id,action){
    const item=items.find(function(x){return x.id===id;});
    if(!item)return;
    if(action==='edit'){openEditor(item);return;}
    if(action==='delete'&&!confirm('Удалить "'+(item.name||id)+'"?'))return;
    const form=new FormData();form.append('action',action);form.append('id',id);
    try{
        const data=await api('ajax/creativeLibrary.php',{method:'POST',body:form});
        items=data.items||[];
        render();
    }catch(err){alert(err.message);}
}

$('newCreative').addEventListener('click',()=>openEditor(null));
$('closeCreative').addEventListener('click',closeEditor);
$('cancelCreative').addEventListener('click',closeEditor);
$('creativeModal').addEventListener('click',(e)=>{if(e.target===$('creativeModal'))closeEditor();});
document.addEventListener('keydown',(e)=>{if(e.key==='Escape'&&$('creativeModal').classList.contains('open'))closeEditor();});
$('creativeForm').addEventListener('submit',save);
$('refreshCreatives').addEventListener('click',()=>load().catch(e=>alert(e.message)));
$('creativeSearch').addEventListener('input',render);
$('creativeGrid').addEventListener('click',function(e){
    const button=e.target.closest('[data-action]');
    if(!button)return;
    const card=button.closest('[data-id]');
    if(card)itemAction(card.dataset.id,button.dataset.action);
});
$('presetMedia').addEventListener('change',function(){
    const file=this.files[0];
    if(!file)return;
    if(previewObjectUrl)URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl=URL.createObjectURL(file);
    $('editorPreview').innerHTML=previewHtml(previewObjectUrl,file.type.startsWith('video/')?'video':'image');
    $('presetCurrentMedia').textContent=file.name+' · '+formatBytes(file.size);
});
load().catch(function(e){
    $('creativeGrid').innerHTML='<div class="cr-empty"><i class="fa-solid fa-triangle-exclamation"></i>'+esc(e.message)+'</div>';
});
JS_CODE;
file_put_contents($root.'/scripts/creatives.js',$creativeJs);

/* ---------- menu ---------- */
$menu=file_get_contents($menuPath);
$oldMenu="    ['workspace.php?tab=ad_accounts&assets=1#assetsWorkspace','fa-images','Креативы', false],";
$newMenu="    ['creatives.php','fa-images','Креативы', \$currentPage === 'creatives.php'],";
if(strpos($menu,$oldMenu)!==false) $menu=str_replace($oldMenu,$newMenu,$menu,$n);
elseif(strpos($menu,"['creatives.php','fa-images','Креативы'")===false){fwrite(STDERR,"[creative-library] menu creative entry missing\n");exit(252);}
if(strpos($menu,"    'creatives.php' => 'Креативы',")===false){
    $anchor="    'launch.php' => 'Автозалив',";
    if(strpos($menu,$anchor)===false){fwrite(STDERR,"[creative-library] menu section anchor missing\n");exit(253);}
    $menu=str_replace($anchor,"    'creatives.php' => 'Креативы',\n".$anchor,$menu,$n);
}
file_put_contents($menuPath,$menu);

/* ---------- Launch markup ---------- */
$launchPhp=file_get_contents($launchPhpPath);
if(strpos($launchPhp,'id="creativeLibrarySelect"')===false){
    $anchor=<<<'HTML'
    <div class="launch-card">
        <h5>Creative</h5>
HTML;
    $replacement=<<<'HTML'
    <div class="launch-card">
        <h5>Creative</h5>
        <div class="row mt-2 mb-3" id="creativeLibraryLaunch">
            <div class="col-md-8">
                <label>Сохранённое крео из библиотеки</label>
                <select id="creativeLibrarySelect" class="form-control"><option value="">Не выбрано — использовать файл ниже</option></select>
                <div id="creativeLibraryStatus" class="muted mt-1">Можно сохранить крео во вкладке «Креативы» и использовать его на любом количестве RK.</div>
            </div>
            <div class="col-md-4 d-flex align-items-end">
                <button id="refreshCreativeLibrary" type="button" class="btn btn-secondary mr-1">ОБНОВИТЬ</button>
                <button id="clearCreativeLibrary" type="button" class="btn btn-outline-light">СБРОСИТЬ</button>
            </div>
        </div>
HTML;
    if(strpos($launchPhp,$anchor)===false){fwrite(STDERR,"[creative-library] Launch Creative anchor missing\n");exit(254);}
    $launchPhp=str_replace($anchor,$replacement,$launchPhp,$n);
}
$launchPhp=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260919-creative-library-v98" type="module"></script>',
    $launchPhp,1,$lc
) ?? $launchPhp;
if($lc!==1){fwrite(STDERR,"[creative-library] Launch script tag missing\n");exit(255);}
file_put_contents($launchPhpPath,$launchPhp);

/* ---------- Launch JS ---------- */
$js=file_get_contents($launchJsPath);
if(strpos($js,'REMASK_CREATIVE_LIBRARY_LAUNCH_V1')===false){
    $anchor="function creativeFormat() { return \$('creativeFormat')?.value || 'SINGLE'; }";
    $libraryJs=<<<'JS_CODE'
/* REMASK_CREATIVE_LIBRARY_LAUNCH_V1 */
let remaskCreativePresets = [];
let remaskSelectedCreativePreset = null;

function remaskCreativePresetLabel(item) {
    const media = item?.media || {};
    return (item?.name || item?.id || 'Creative') + (media.original_name ? ' — ' + media.original_name : '');
}

function clearPerAccountExistingMediaForLibrary() {
    if (!targetMode()) return;
    for (const target of selectedTargets()) {
        const binding = bindingFor(target.account_id);
        binding.existing_image_hash = '';
        binding.existing_video_id = '';
        binding.existing_creative_id = '';
        binding.source_instagram_media_id = '';
    }
    renderTargetBindings();
}

function renderCreativeLibraryStatus() {
    const status = $('creativeLibraryStatus');
    if (!status) return;
    if (!remaskSelectedCreativePreset) {
        status.textContent = 'Не выбрано. Можно загрузить файл ниже или выбрать существующий Meta asset.';
        if (!state.pendingWorkspaceMedia) $('media').disabled = false;
        return;
    }
    const media = remaskSelectedCreativePreset.media || {};
    status.textContent = 'Библиотека: ' + (remaskSelectedCreativePreset.name || remaskSelectedCreativePreset.id) +
        (media.original_name ? ' · ' + media.original_name : '') + '. При Launch файл будет загружен в каждый выбранный RK.';
    $('media').value = '';
    $('media').disabled = true;
}

function clearCreativeLibrarySelection() {
    remaskSelectedCreativePreset = null;
    if ($('creativeLibrarySelect')) $('creativeLibrarySelect').value = '';
    renderCreativeLibraryStatus();
    invalidateLaunchReview();
    validateReady();
}

function applyCreativeLibrarySelection() {
    const select = $('creativeLibrarySelect');
    const id = select?.value || '';
    const item = remaskCreativePresets.find(function(row){ return row.id === id; }) || null;
    if (!item) {
        clearCreativeLibrarySelection();
        return;
    }
    if (item.missing_media || !item.media_library_id) {
        alert('У этого крео отсутствует сохранённый media-файл.');
        clearCreativeLibrarySelection();
        return;
    }

    clearExistingMediaSelection();
    clearPerAccountExistingMediaForLibrary();
    remaskSelectedCreativePreset = item;

    if (creativeFormat() !== 'SINGLE') {
        $('creativeFormat').value = 'SINGLE';
        renderCreativeFormat();
    }
    $('creativeName').value = item.creative_name || item.name || '';
    $('adName').value = item.ad_name || item.name || '';
    $('message').value = item.message || '';
    $('headline').value = item.headline || '';
    $('description').value = item.description || '';
    $('destinationUrl').value = item.destination_url || '';
    if (item.cta && hasOptionValue($('cta'), item.cta)) $('cta').value = item.cta;
    $('urlTags').value = item.url_tags || '';
    renderCreativeLibraryStatus();
    invalidateLaunchReview();
    validateReady();
}

async function loadCreativeLibraryForLaunch(autoApplyId) {
    const data = await apiJson('ajax/creativeLibrary.php?action=list');
    remaskCreativePresets = data.items || [];
    const select = $('creativeLibrarySelect');
    if (!select) return;
    const current = remaskSelectedCreativePreset?.id || '';
    select.innerHTML = '<option value="">Не выбрано — использовать файл ниже</option>';
    for (const item of remaskCreativePresets) {
        const o = document.createElement('option');
        o.value = item.id;
        o.textContent = remaskCreativePresetLabel(item);
        o.disabled = Boolean(item.missing_media);
        select.appendChild(o);
    }
    const requested = autoApplyId || current;
    if (requested && hasOptionValue(select,requested)) {
        select.value = requested;
        applyCreativeLibrarySelection();
    } else {
        renderCreativeLibraryStatus();
    }
}

JS_CODE;
    if(strpos($js,$anchor)===false){fwrite(STDERR,"[creative-library] creativeFormat anchor missing\n");exit(256);}
    $js=str_replace($anchor,$libraryJs.$anchor,$js,$n);

    $old=<<<'JS_CODE'
    const file = (isCarousel || isInstagramPost) ? null : $('media').files[0];
    const existingMedia = (isCarousel || isInstagramPost) ? null : state.pendingWorkspaceMedia;
    const perAccountExisting = !isCarousel && targetMode() && selectedTargets().every((target) => {
JS_CODE;
    $new=<<<'JS_CODE'
    const file = (isCarousel || isInstagramPost) ? null : $('media').files[0];
    const existingMedia = (isCarousel || isInstagramPost) ? null : state.pendingWorkspaceMedia;
    const libraryPreset = (isCarousel || isInstagramPost) ? null : remaskSelectedCreativePreset;
    const perAccountExisting = !isCarousel && targetMode() && selectedTargets().every((target) => {
JS_CODE;
    if(strpos($js,$old)===false){fwrite(STDERR,"[creative-library] createJob media vars anchor missing\n");exit(257);}
    $js=str_replace($old,$new,$js,$n);

    $old="    if (!isCarousel && !isInstagramPost && !file && !existingMedia && !perAccountExisting) return alert('Select image/video, choose an existing Meta Image/Video/Creative, or map Existing Media for every RK.');";
    $new="    if (!isCarousel && !isInstagramPost && !file && !existingMedia && !libraryPreset && !perAccountExisting) return alert('Выбери крео из библиотеки, image/video, существующий Meta asset или Existing Media для каждого RK.');";
    if(strpos($js,$old)===false){fwrite(STDERR,"[creative-library] createJob validation anchor missing\n");exit(258);}
    $js=str_replace($old,$new,$js,$n);

    $old=<<<'JS_CODE'
    } else if (isInstagramPost && !targetMode()) form.append('source_instagram_media_id', $('instagramMediaId').value.trim());
    else if (existingMedia?.type === 'image_hash') form.append('existing_image_hash', existingMedia.image_hash || '');
    else if (existingMedia?.type === 'video_id') form.append('existing_video_id', existingMedia.video_id || '');
    else if (existingMedia?.type === 'creative_id') form.append('existing_creative_id', existingMedia.creative_id || '');
    else if (file) form.append('media', file);
JS_CODE;
    $new=<<<'JS_CODE'
    } else if (isInstagramPost && !targetMode()) form.append('source_instagram_media_id', $('instagramMediaId').value.trim());
    else if (libraryPreset?.media_library_id) form.append('media_library_id', libraryPreset.media_library_id);
    else if (existingMedia?.type === 'image_hash') form.append('existing_image_hash', existingMedia.image_hash || '');
    else if (existingMedia?.type === 'video_id') form.append('existing_video_id', existingMedia.video_id || '');
    else if (existingMedia?.type === 'creative_id') form.append('existing_creative_id', existingMedia.creative_id || '');
    else if (file) form.append('media', file);
JS_CODE;
    if(strpos($js,$old)===false){fwrite(STDERR,"[creative-library] FormData media anchor missing\n");exit(259);}
    $js=str_replace($old,$new,$js,$n);

    $eventAnchor="\$('creativeFormat')?.addEventListener('change', renderCreativeFormat);";
    $eventReplacement=<<<'JS_CODE'
$('creativeFormat')?.addEventListener('change', () => {
    if (creativeFormat() !== 'SINGLE' && remaskSelectedCreativePreset) clearCreativeLibrarySelection();
    renderCreativeFormat();
});
$('creativeLibrarySelect')?.addEventListener('change', applyCreativeLibrarySelection);
$('refreshCreativeLibrary')?.addEventListener('click', () => loadCreativeLibraryForLaunch('').catch((e) => show($('launchResult'), e.payload || e.message, 'failed')));
$('clearCreativeLibrary')?.addEventListener('click', clearCreativeLibrarySelection);
$('media')?.addEventListener('change', () => {
    if ($('media').files[0] && remaskSelectedCreativePreset) {
        remaskSelectedCreativePreset = null;
        if ($('creativeLibrarySelect')) $('creativeLibrarySelect').value = '';
        renderCreativeLibraryStatus();
    }
});
const requestedCreativePreset = new URLSearchParams(window.location.search).get('creative_preset') || '';
loadCreativeLibraryForLaunch(requestedCreativePreset).catch((e) => {
    const status = $('creativeLibraryStatus');
    if (status) status.textContent = 'Не удалось загрузить библиотеку: ' + (e.payload?.message || e.message);
});
JS_CODE;
    if(strpos($js,$eventAnchor)===false){fwrite(STDERR,"[creative-library] creativeFormat event anchor missing\n");exit(260);}
    $js=str_replace($eventAnchor,$eventReplacement,$js,$n);
}
file_put_contents($launchJsPath,$js);

fwrite(STDERR,"[creative-library] v99 minimal creative library UI + bulk Launch handoff ready\n");
