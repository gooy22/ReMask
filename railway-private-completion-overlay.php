<?php
declare(strict_types=1);
$root = '/var/www/html';
foreach (['railway-private-form-schema.php'=>'classes/RemaskPrivateFormSchema.php',
          'railway-private-creative-capabilities.php'=>'ajax/metaCreativeCapabilities.php'] as $source=>$target) {
    if (!copy('/tmp/'.$source, $root.'/'.$target)) throw new RuntimeException('PRIVATE_COMPLETION_COPY_FAILED');
}
$schemaEndpoint = <<<'PHP_CODE'
<?php
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/RemaskPrivateFormSchema.php';
try { MetaEndpoint::ok(RemaskPrivateFormSchema::load()); }
catch (Throwable $error) { MetaEndpoint::fail($error); }
PHP_CODE;
file_put_contents($root.'/ajax/metaPrivateSchema.php', $schemaEndpoint);
// Previously cached clients also receive private metadata, never SDK fallback.
file_put_contents($root.'/ajax/metaSdkSchema.php', $schemaEndpoint);
foreach (['metaAudienceEstimate.php','metaPlacementCapabilities.php','metaCreativePreview.php'] as $name) {
    if (!copy('/tmp/railway-private-advertising-helper.php', $root.'/ajax/'.$name))
        throw new RuntimeException('PRIVATE_HELPER_COPY_FAILED');
}
file_put_contents($root.'/ajax/metaBundleMedia.php', <<<'PHP_CODE'
<?php
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/MediaLibraryStoreFactory.php';
try {
    $input = MetaEndpoint::input();
    $ids = $input['ids'] ?? [];
    if (is_string($ids)) $ids = json_decode($ids, true, 16, JSON_THROW_ON_ERROR);
    if (!is_array($ids) || count($ids) < 1 || count($ids) > 10) throw new InvalidArgumentException('INVALID_MEDIA_SELECTION');
    $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
    $media = [];
    foreach ($ids as $id) {
        if (!is_string($id) || !preg_match('/^m-[a-f0-9]{24}$/D', $id)) throw new InvalidArgumentException('INVALID_MEDIA_REFERENCE');
        if (!$library->getInternal($id)) throw new RuntimeException('MEDIA_LIBRARY_ITEM_UNAVAILABLE');
        $row = $library->get($id);
        $media[] = array_intersect_key($row, array_flip(['id','sha256','size_bytes','media_type','mime_type','original_name']));
    }
    MetaEndpoint::ok(['media'=>$media]);
} catch (Throwable $error) { MetaEndpoint::fail($error); }
PHP_CODE);

function completionReplace(string &$body, string $old, string $new, string $name): void {
    $count = substr_count($body, $old);
    if ($count !== 1) throw new RuntimeException('PRIVATE_COMPLETION_ANCHOR: '.$name.' '.$count);
    $body = str_replace($old, $new, $body);
}
$creativeEndpoint = file_get_contents($root.'/ajax/creativeLibrary.php');
completionReplace($creativeEndpoint, "require_once __DIR__ . '/../classes/MetaOfficialFields.php';",
    "require_once __DIR__ . '/../classes/RemaskPrivateFormSchema.php';", 'creative-validator');
completionReplace($creativeEndpoint, 'MetaOfficialFields::sanitizeBuilder($metaBuilder)',
    'RemaskPrivateFormSchema::sanitizeBuilder($metaBuilder)', 'creative-builder');
file_put_contents($root.'/ajax/creativeLibrary.php', $creativeEndpoint);

$launch = file_get_contents($root.'/scripts/launch.js');
completionReplace($launch, 'function bundleFormState() {', 'function remaskLegacyBundleFormState() {', 'bundle-save');
completionReplace($launch, 'function applyBundleData(data) {', 'function remaskLegacyApplyBundleData(data) {', 'bundle-restore');
completionReplace($launch, 'data: JSON.stringify(bundleFormState()),',
    'data: JSON.stringify(await remaskPrivateCaptureBundle()),', 'bundle-stage');
$old = <<<'JS'
function loadSelectedBundle() {
    const bundle = selectedBundle();
    if (!bundle) return alert('Select a bundle.');
    $('bundleName').value = bundle.name;
    applyBundleData(bundle.data || {});
    show($('launchResult'), `Bundle loaded: ${bundle.name}`, 'ready');
}
JS;
$new = <<<'JS'
async function loadSelectedBundle() {
    const bundle = selectedBundle();
    if (!bundle) return alert('Выберите набор.');
    try {
        $('bundleName').value = bundle.name;
        await applyBundleData(bundle.data || {});
        show($('launchResult'), `Набор загружен: ${bundle.name}`, 'ready');
    } catch (error) {
        show($('launchResult'), error.payload || error.message, 'failed');
    }
}
JS;
completionReplace($launch, $old, $new, 'bundle-handler');
completionReplace($launch, '    const savedBuilder = remaskSelectedCreativePreset?.meta_builder || {};',
    '    const savedBuilder = {};', 'effective-budget-validation');
// ZIP and market selections already restored by the preset editor must survive
// the final serializer, including a GEO consisting solely of a ZIP selection.
completionReplace($launch, "    if (!Object.keys(geoLocations).length) throw new Error('Select at least one GEO result.');",
    "    for (const [type,key] of [['zip','zips'],['geo_market','geo_markets']]) {\n".
    "        const rows = state.geo.filter(row => row.type === type && (row.key || row.id)).map(row => ({key:String(row.key || row.id)}));\n".
    "        if (rows.length) geoLocations[key] = rows;\n    }\n".
    "    if (!Object.keys(geoLocations).length) throw new Error('Select at least one GEO result.');", 'complete-geo');
$launch .= "\n".file_get_contents('/tmp/railway-private-launch-completion.js');
$launch = str_replace("addEventListener('change', applyCreativeLibrarySelection)",
    "addEventListener('change', () => applyCreativeLibrarySelection())", $launch);
$launch = str_replace("addEventListener('click', clearCreativeLibrarySelection)",
    "addEventListener('click', () => clearCreativeLibrarySelection())", $launch);
$launch = str_replace('Number.isInteger(activeBudget)', 'Number.isSafeInteger(activeBudget)', $launch);
$launch = str_replace('ajax/metaSdkSchema.php', 'ajax/metaPrivateSchema.php', $launch);
$launch = str_replace('Официальная SDK-схема загружена. Поля ниже участвуют в Launch Review и создании Job.',
    'Приватная схема Ads Manager загружена. Доступ к ресурсам проверяется для выбранного РК.', $launch);
file_put_contents($root.'/scripts/launch.js', $launch);
$creatives = file_get_contents($root.'/scripts/creatives.js');
$creatives = str_replace(['SDK-схема загружена','SDK-схема недоступна','Official Meta SDK placements',
    'Нет в текущей Meta SDK schema'], ['Приватная схема Ads Manager загружена','Приватная схема недоступна',
    'Места показа из приватных модулей Ads Manager','Нет в проверенной приватной схеме'], $creatives);
file_put_contents($root.'/scripts/creatives.js', $creatives);
foreach (['launch.php','creatives.php'] as $name) {
    $page = file_get_contents($root.'/'.$name);
    $module = $name === 'launch.php' ? 'launch' : 'creatives';
    $revision = substr(hash_file('sha256', $root.'/scripts/'.$module.'.js'), 0, 16);
    $page = preg_replace('~(scripts/'.$module.'\.js)\?v=[^"\s]+~', '$1?v=private-'.$revision, $page);
    $page = str_replace('20261013-2', '20261013-3', $page);
    $page = str_replace('accept="image/*"', 'accept="image/*,video/*"', $page);
    $page = str_replace(['ВЫБРАТЬ 2–10 ИЗОБРАЖЕНИЙ','Carousel images'],
        ['ВЫБРАТЬ 2–10 ФОТО / ВИДЕО','Фото и видео карусели'], $page);
    $page = str_replace('Каждая картинка будет загружена отдельно в каждый выбранный RK. Hash каждого upload сохраняется в Job, поэтому Retry не повторяет уже успешные карточки.',
        'Можно сочетать изображения и видео. Порядок карточек и их настройки сохраняются в наборе.', $page);
    file_put_contents($root.'/'.$name, $page);
}
fwrite(STDERR, "[private-completion] private schema and durable Launch media installed\n");
