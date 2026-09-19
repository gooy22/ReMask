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
        .creative-wrap{max-width:1540px;margin:0 auto 50px;padding:0 18px;color:#d9dde6;text-align:left}
        .creative-head{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin:14px 0 18px}
        .creative-head h2{margin:0;color:#eef1f6;font-size:27px}.creative-sub{color:#929aaa;font-size:12px;margin-top:5px}
        .creative-layout{display:grid;grid-template-columns:minmax(360px,460px) 1fr;gap:14px;align-items:start}
        .creative-card{background:#262a33;border:1px solid #3b414d;border-radius:8px;padding:16px}
        .creative-card h5{margin:0 0 14px;color:#eef1f6}.creative-form label{font-size:12px;color:#aeb5c4;margin-bottom:5px}
        .creative-form .form-control{background:#22262e;border:1px solid #414855;color:#e0e4ec}
        .creative-form textarea{resize:vertical}.creative-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
        .creative-library-head{display:flex;gap:8px;align-items:center;margin-bottom:12px}.creative-library-head input{flex:1;background:#22262e;border:1px solid #414855;color:#e0e4ec}
        .creative-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
        .creative-item{background:#20242c;border:1px solid #3b414d;border-radius:8px;overflow:hidden;display:flex;flex-direction:column;min-height:330px}
        .creative-preview{height:180px;background:#171a20;display:flex;align-items:center;justify-content:center;overflow:hidden;border-bottom:1px solid #343a45}
        .creative-preview img,.creative-preview video{width:100%;height:100%;object-fit:contain;background:#11141a}
        .creative-item-body{padding:12px;display:flex;flex-direction:column;gap:7px;flex:1}.creative-name{font-weight:700;color:#f0f2f6}
        .creative-meta{font-size:11px;color:#8f98a9}.creative-copy{font-size:12px;color:#c3c9d4;white-space:pre-wrap;max-height:54px;overflow:hidden}
        .creative-item-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto;padding-top:6px}.creative-item-actions .btn{font-size:11px;padding:5px 8px}
        .creative-empty{padding:50px 20px;text-align:center;color:#8e97a8;border:1px dashed #414855;border-radius:8px}
        .creative-status{font-size:12px;color:#9ba4b4;margin-top:8px;min-height:18px}.creative-status.bad{color:#ff8f8f}.creative-status.ok{color:#67d89b}
        .creative-file-name{font-size:11px;color:#8e97a8;margin-top:5px}
        @media(max-width:980px){.creative-layout{grid-template-columns:1fr}.creative-wrap{padding:0 10px}}
    </style>
</head>
<body class="app-shell">
<?php include 'menu.php' ?>
<main class="app-main">
<div class="creative-wrap">
    <div class="creative-head">
        <div>
            <div class="app-eyebrow">CREATIVE LIBRARY</div>
            <h2>Креативы</h2>
            <div class="creative-sub">Сохрани файл и текст один раз. В «Автозаливе» ReMask сам загрузит этот media-файл в каждый выбранный RK.</div>
        </div>
        <a href="launch.php" class="btn btn-success"><i class="fa-solid fa-wand-magic-sparkles mr-1"></i> АВТОЗАЛИВ</a>
    </div>

    <div class="creative-layout">
        <section class="creative-card">
            <h5 id="creativeEditorTitle">Новое крео</h5>
            <form id="creativeForm" class="creative-form" autocomplete="off">
                <input id="creativeId" type="hidden">
                <div class="form-group">
                    <label>Название в библиотеке</label>
                    <input id="presetName" class="form-control" placeholder="Например: Betting 01">
                </div>
                <div class="form-group">
                    <label>Изображение / видео</label>
                    <input id="presetMedia" name="media" type="file" class="form-control" accept="image/*,video/*">
                    <div id="presetCurrentMedia" class="creative-file-name">Для нового крео файл обязателен.</div>
                </div>
                <div class="row">
                    <div class="col-md-6 form-group"><label>Creative name</label><input id="presetCreativeName" class="form-control" placeholder="Meta Creative name"></div>
                    <div class="col-md-6 form-group"><label>Ad name</label><input id="presetAdName" class="form-control" placeholder="Ad name"></div>
                </div>
                <div class="form-group"><label>Основной текст</label><textarea id="presetMessage" class="form-control" rows="4" placeholder="Primary text"></textarea></div>
                <div class="row">
                    <div class="col-md-6 form-group"><label>Заголовок</label><input id="presetHeadline" class="form-control"></div>
                    <div class="col-md-6 form-group"><label>Описание</label><input id="presetDescription" class="form-control"></div>
                </div>
                <div class="form-group"><label>Ссылка</label><input id="presetUrl" class="form-control" placeholder="https://..."></div>
                <div class="row">
                    <div class="col-md-5 form-group">
                        <label>CTA</label>
                        <select id="presetCta" class="form-control">
                            <option value="LEARN_MORE">Подробнее</option>
                            <option value="SIGN_UP">Регистрация</option>
                            <option value="APPLY_NOW">Подать заявку</option>
                            <option value="CONTACT_US">Связаться</option>
                            <option value="SHOP_NOW">Купить</option>
                            <option value="GET_OFFER">Получить предложение</option>
                        </select>
                    </div>
                    <div class="col-md-7 form-group"><label>UTM / URL tags</label><input id="presetTags" class="form-control" placeholder="utm_source=facebook&..."></div>
                </div>
                <div class="creative-actions">
                    <button id="saveCreative" type="submit" class="btn btn-primary">СОХРАНИТЬ КРЕО</button>
                    <button id="resetCreative" type="button" class="btn btn-secondary">НОВОЕ</button>
                </div>
                <div id="creativeStatus" class="creative-status"></div>
            </form>
        </section>

        <section class="creative-card">
            <div class="creative-library-head">
                <input id="creativeSearch" class="form-control" placeholder="Поиск по библиотеке...">
                <button id="refreshCreatives" type="button" class="btn btn-secondary">ОБНОВИТЬ</button>
            </div>
            <div id="creativeGrid" class="creative-grid"><div class="creative-empty">Загрузка библиотеки…</div></div>
        </section>
    </div>
</div>
<script src="scripts/creatives.js?v=20260919-creative-library-v98" type="module"></script>
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

function csrfToken(){ return document.querySelector('meta[name="remask-csrf"]')?.content || ''; }
function esc(value){ return String(value ?? '').replace(/[&<>'"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];}); }
function formatBytes(bytes){ const n=Number(bytes||0); if(n<1024)return n+' B'; if(n<1048576)return (n/1024).toFixed(1)+' KB'; return (n/1048576).toFixed(1)+' MB'; }

async function api(url,options){
    options=options||{};
    options.headers=Object.assign({},options.headers||{});
    if((options.method||'GET').toUpperCase()!=='GET') options.headers['X-ReMask-CSRF']=csrfToken();
    const r=await fetch(url,options);
    const text=await r.text();
    let j; try{j=JSON.parse(text);}catch(e){throw new Error('Invalid JSON ('+r.status+'): '+text);}
    if(!r.ok||j.ok===false){const err=j.error||j;throw new Error(err.message||('HTTP '+r.status));}
    return j.data;
}

function setStatus(message,type){
    const el=$('creativeStatus');
    el.textContent=message||'';
    el.className='creative-status'+(type?' '+type:'');
}

function resetEditor(){
    editing=null;
    $('creativeId').value='';
    $('creativeEditorTitle').textContent='Новое крео';
    $('creativeForm').reset();
    $('presetCta').value='LEARN_MORE';
    $('presetCurrentMedia').textContent='Для нового крео файл обязателен.';
    setStatus('');
}

function editItem(id){
    const item=items.find(function(x){return x.id===id;});
    if(!item)return;
    editing=item;
    $('creativeId').value=item.id;
    $('creativeEditorTitle').textContent='Редактирование: '+(item.name||item.id);
    $('presetName').value=item.name||'';
    $('presetCreativeName').value=item.creative_name||'';
    $('presetAdName').value=item.ad_name||'';
    $('presetMessage').value=item.message||'';
    $('presetHeadline').value=item.headline||'';
    $('presetDescription').value=item.description||'';
    $('presetUrl').value=item.destination_url||'';
    $('presetCta').value=item.cta||'LEARN_MORE';
    $('presetTags').value=item.url_tags||'';
    $('presetMedia').value='';
    $('presetCurrentMedia').textContent=item.media ? ('Текущий файл: '+item.media.original_name+' · '+formatBytes(item.media.size_bytes)) : 'Файл отсутствует — загрузи новый.';
    setStatus('');
    window.scrollTo({top:0,behavior:'smooth'});
}

function render(){
    const q=$('creativeSearch').value.trim().toLowerCase();
    const rows=items.filter(function(item){
        const hay=[item.name,item.creative_name,item.ad_name,item.message,item.headline,item.description,item.destination_url,item.media?.original_name].join(' ').toLowerCase();
        return !q||hay.includes(q);
    });
    if(!rows.length){
        $('creativeGrid').innerHTML='<div class="creative-empty">'+(items.length?'Ничего не найдено.':'Библиотека пока пустая. Загрузи первое крео слева.')+'</div>';
        return;
    }
    $('creativeGrid').innerHTML=rows.map(function(item){
        const media=item.media||{};
        const isVideo=media.media_type==='video';
        const preview=item.missing_media
            ? '<div class="creative-meta">Файл отсутствует</div>'
            : (isVideo
                ? '<video src="'+esc(item.preview_url)+'" muted controls preload="metadata"></video>'
                : '<img src="'+esc(item.preview_url)+'" alt="">');
        const text=item.message||item.headline||item.description||'Текст не сохранён';
        return '<article class="creative-item" data-id="'+esc(item.id)+'">'+
            '<div class="creative-preview">'+preview+'</div>'+
            '<div class="creative-item-body">'+
                '<div class="creative-name">'+esc(item.name||item.id)+'</div>'+
                '<div class="creative-meta">'+esc((media.media_type||'media').toUpperCase())+' · '+esc(media.original_name||'missing')+(media.size_bytes?' · '+esc(formatBytes(media.size_bytes)):'')+'</div>'+
                '<div class="creative-copy">'+esc(text)+'</div>'+
                '<div class="creative-meta">'+esc(item.headline||'')+(item.cta?' · '+esc(item.cta):'')+'</div>'+
                '<div class="creative-item-actions">'+
                    '<a class="btn btn-success" href="launch.php?creative_preset='+encodeURIComponent(item.id)+'">В АВТОЗАЛИВ</a>'+
                    '<button type="button" class="btn btn-outline-light" data-action="edit">ИЗМЕНИТЬ</button>'+
                    '<button type="button" class="btn btn-outline-light" data-action="duplicate">КОПИЯ</button>'+
                    '<button type="button" class="btn btn-danger" data-action="delete">УДАЛИТЬ</button>'+
                '</div>'+
            '</div>'+
        '</article>';
    }).join('');
}

async function load(){
    $('creativeGrid').innerHTML='<div class="creative-empty">Загрузка библиотеки…</div>';
    const data=await api('ajax/creativeLibrary.php?action=list');
    items=data.items||[];
    render();
}

async function save(e){
    e.preventDefault();
    const id=$('creativeId').value.trim();
    const file=$('presetMedia').files[0];
    if(!id&&!file){setStatus('Выбери изображение или видео.','bad');return;}
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
        resetEditor();
        setStatus('Крео сохранено. Теперь его можно выбрать в «Автозаливе».','ok');
        render();
    }catch(err){setStatus(err.message,'bad');}
    finally{$('saveCreative').disabled=false;}
}

async function itemAction(id,action){
    const item=items.find(function(x){return x.id===id;});
    if(!item)return;
    if(action==='edit'){editItem(id);return;}
    if(action==='delete'&&!confirm('Удалить крео "'+(item.name||id)+'"?'))return;
    const form=new FormData();form.append('action',action);form.append('id',id);
    try{
        const data=await api('ajax/creativeLibrary.php',{method:'POST',body:form});
        items=data.items||[];
        if(editing&&editing.id===id)resetEditor();
        render();
    }catch(err){setStatus(err.message,'bad');}
}

$('creativeForm').addEventListener('submit',save);
$('resetCreative').addEventListener('click',resetEditor);
$('refreshCreatives').addEventListener('click',function(){load().catch(function(e){setStatus(e.message,'bad');});});
$('creativeSearch').addEventListener('input',render);
$('creativeGrid').addEventListener('click',function(e){
    const button=e.target.closest('[data-action]');
    if(!button)return;
    const card=button.closest('[data-id]');
    if(!card)return;
    itemAction(card.dataset.id,button.dataset.action);
});
load().catch(function(e){$('creativeGrid').innerHTML='<div class="creative-empty">'+esc(e.message)+'</div>';});
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

fwrite(STDERR,"[creative-library] v98 dedicated library + bulk Launch handoff ready\n");
