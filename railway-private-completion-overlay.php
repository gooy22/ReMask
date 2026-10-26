<?php
declare(strict_types=1);
$root = '/var/www/html';
foreach (['railway-private-form-schema.php'=>'classes/RemaskPrivateFormSchema.php',
          'railway-readiness.php'=>'classes/RemaskReadiness.php',
          'railway-ready-endpoint.php'=>'ready.php',
          'railway-private-inventory-identity.php'=>'classes/RemaskPrivateInventoryIdentity.php',
          'railway-template-targeting-context.php'=>'classes/RemaskTemplateTargetingContext.php',
          'railway-advertising-helper-client.php'=>'classes/RemaskAdvertisingHelperClient.php',
          'railway-template-advertising-helper.php'=>'ajax/metaTemplateAdvertisingHelper.php',
          'railway-template-targeting-search.php'=>'ajax/metaTemplateTargetingSearch.php',
          'railway-private-creative-capabilities.php'=>'ajax/metaCreativeCapabilities.php'] as $source=>$target) {
    if (!copy('/tmp/'.$source, $root.'/'.$target)) throw new RuntimeException('PRIVATE_COMPLETION_COPY_FAILED');
}
if (!copy('/opt/remask-python/app/contracts/meta_targeting_reference_20261014.json',
    $root.'/classes/meta-targeting-reference.json')) throw new RuntimeException('TARGETING_MODULE_COPY_FAILED');
if (!copy('/opt/remask-python/app/contracts/meta_editor_modules_20261014.json',
    $root.'/classes/meta-editor-modules.json')) throw new RuntimeException('EDITOR_MODULE_COPY_FAILED');
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
$resolver = file_get_contents($root.'/ajax/pythonProfileContext.php');
completionReplace($resolver, "require_once __DIR__ . '/../classes/MetaEndpoint.php';",
    "require_once __DIR__ . '/../classes/MetaEndpoint.php';\nrequire_once __DIR__ . '/../classes/RemaskPrivateInventoryIdentity.php';", 'resolver-identities-class');
completionReplace($resolver, '    $liveInventory = rmx_py_live_inventory($profile);', <<<'PHP_CODE'
    $liveInventory = rmx_py_live_inventory($profile);
    $confirmedBindings = json_decode((string)@file_get_contents('/var/lib/remask/workspace-provisioning-bindings.json'), true);
    $personalScope = (string)(rmx_py_cookie_map($account->cookies)['c_user'] ?? '');
    $liveInventory = RemaskPrivateInventoryIdentity::merge($liveInventory,
        is_array($confirmedBindings) ? $confirmedBindings : [], $profile, $personalScope);
PHP_CODE, 'resolver-confirmed-identities');
file_put_contents($root.'/ajax/pythonProfileContext.php', $resolver);

// REMASK_PROFILE_COOKIE_REFRESH_V1: persist only Meta-issued same-actor session updates.

$profileContextPath = $root . '/ajax/pythonProfileContext.php';
$profileContextSource = file_get_contents($profileContextPath);
if (!is_string($profileContextSource) || substr_count($profileContextSource, '<?php') !== 1) {
    throw new RuntimeException('Python profile context boundary missing');
}
$cookieRefreshHandler = <<<'PROFILE_COOKIE_REFRESH'
if (strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET')) === 'POST') {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    $expectedKey = trim((string)(getenv('REMASK_INTERNAL_KEY') ?: ''));
    $providedKey = trim((string)($_SERVER['HTTP_X_REMASK_INTERNAL_KEY'] ?? ''));
    if ($expectedKey === '' || $providedKey === '' || !hash_equals($expectedKey, $providedKey)) {
        http_response_code(401);
        echo json_encode(['ok'=>false,'error'=>'UNAUTHORIZED']);
        exit;
    }
    try {
        $raw = file_get_contents('php://input');
        if (!is_string($raw) || strlen($raw) > 2097152) throw new InvalidArgumentException();
        $input = json_decode($raw, true, 64, JSON_THROW_ON_ERROR);
        if (!is_array($input) || ($input['action'] ?? '') !== 'refresh_profile_cookies') {
            http_response_code(400);
            echo json_encode(['ok'=>false,'error'=>'INVALID_ACTION']);
            exit;
        }
        $profile = trim((string)($input['profile_id'] ?? ''));
        $actor = trim((string)($input['actor_id'] ?? ''));
        $expectedXs = (string)($input['expected_xs'] ?? '');
        $cookieInput = $input['cookies'] ?? null;
        if ($profile === '' || strlen($profile) > 255 || $actor === '' ||
            !ctype_digit($actor) || $expectedXs === '' || !is_array($cookieInput)) {
            throw new InvalidArgumentException();
        }
        require_once __DIR__ . '/../settings.php';
        require_once __DIR__ . '/../classes/FbAccount.php';
        require_once __DIR__ . '/../classes/AccountStoreFactory.php';
        require_once __DIR__ . '/../classes/RemaskCookieProfile.php';
        $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
        if (!method_exists($store, 'compareAndSwapCookies')) {
            throw new RuntimeException('PROFILE_COOKIE_CAS_UNAVAILABLE');
        }
        if (!$store->compareAndSwapCookies($profile, $actor, $expectedXs, $cookieInput)) {
            http_response_code(409);
            echo json_encode(['ok'=>false,'error'=>'PROFILE_SESSION_CHANGED']);
            exit;
        }
        echo json_encode(['ok'=>true,'updated'=>true]);
    } catch (Throwable $e) {
        http_response_code(400);
        echo json_encode(['ok'=>false,'error'=>'PROFILE_COOKIE_REFRESH_FAILED']);
    }
    exit;
}
PROFILE_COOKIE_REFRESH;
$profileContextSource = preg_replace(
    '/declare\s*\(\s*strict_types\s*=\s*1\s*\)\s*;/',
    '$0' . "\n" . $cookieRefreshHandler . "\n",
    $profileContextSource,
    1,
    $count
);
if ($count !== 1) throw new RuntimeException('Python profile context strict-types boundary missing');
file_put_contents($profileContextPath, $profileContextSource);
$creativeEndpoint = file_get_contents($root.'/ajax/creativeLibrary.php');
completionReplace($creativeEndpoint, "require_once __DIR__ . '/../classes/MetaOfficialFields.php';",
    "require_once __DIR__ . '/../classes/RemaskPrivateFormSchema.php';", 'creative-validator');
completionReplace($creativeEndpoint, 'MetaOfficialFields::sanitizeBuilder($metaBuilder)',
    'RemaskPrivateFormSchema::unbindTemplate(RemaskPrivateFormSchema::sanitizeBuilder($metaBuilder))', 'creative-builder');
file_put_contents($root.'/ajax/creativeLibrary.php', $creativeEndpoint);

$reference=json_decode(file_get_contents($root.'/classes/meta-targeting-reference.json'),true,512,JSON_THROW_ON_ERROR);
$names=[];
foreach ($reference['locales'] as $row) $names[$row['id']]=$row['name'];
$languageNames='const remaskModuleLanguageNames='.json_encode($names,JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR).";\n";
$moduleControls=file_get_contents('/tmp/railway-meta-module-controls.js')."\n";
$launch = $languageNames.$moduleControls.file_get_contents($root.'/scripts/launch.js');
$launch = str_replace("name:'Язык #'+id", "name:remaskModuleLanguageNames[String(id)]||('Язык #'+id)", $launch);
completionReplace($launch, "            binding.instagram_actor_id = String(page?.instagram_business_account?.id || '');",
    "            binding.instagram_actor_id = String(page?.instagram_business_account?.id || '');\n            binding.source_instagram_media_id = '';", 'instagram-destination-reset');
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
// Common audience suggestions are template data. Publishing identities stay
// explicit in Launch Review; the server selects the private lookup context.
completionReplace($launch, <<<'JS'
    if (!state.profile) {
        if (resultsEl) resultsEl.textContent = 'Run preflight first.';
        return;
    }
JS, '', 'template-audience-profile-guard');
completionReplace($launch, <<<'JS'
    if (isBehavior && !behaviorAccountId) {
        if (resultsEl) resultsEl.textContent = 'Select at least one RK first.';
        return;
    }
JS, '', 'template-behavior-account-guard');
completionReplace($launch, <<<'JS'
    if(!state.profile){
        if(results) results.textContent='Сначала выбери FB-профиль / RK.';
        return;
    }
JS, '', 'template-language-profile-guard');
// ZIP and market selections already restored by the preset editor must survive
// the final serializer, including a GEO consisting solely of a ZIP selection.
completionReplace($launch, "    if (!Object.keys(geoLocations).length) throw new Error('Select at least one GEO result.');",
    "    for (const [type,key] of [['zip','zips'],['geo_market','geo_markets']]) {\n".
    "        const rows = state.geo.filter(row => row.type === type && (row.key || row.id)).map(row => ({key:String(row.key || row.id)}));\n".
    "        if (rows.length) geoLocations[key] = rows;\n    }\n".
    "    if (!Object.keys(geoLocations).length) throw new Error('Select at least one GEO result.');", 'complete-geo');
$launch .= "\n".file_get_contents('/tmp/railway-private-launch-completion.js');
$launch .= "\n".file_get_contents('/tmp/railway-targeting-pickers.js');
$launch .= "\n".file_get_contents('/tmp/railway-advertising-tools.js');
$launch = str_replace("addEventListener('change', applyCreativeLibrarySelection)",
    "addEventListener('change', () => applyCreativeLibrarySelection())", $launch);
$launch = str_replace("addEventListener('click', clearCreativeLibrarySelection)",
    "addEventListener('click', () => clearCreativeLibrarySelection())", $launch);
$launch = str_replace('Number.isInteger(activeBudget)', 'Number.isSafeInteger(activeBudget)', $launch);
$launch = str_replace('ajax/metaSdkSchema.php', 'ajax/metaPrivateSchema.php', $launch);
$launch = str_replace('Официальная SDK-схема загружена. Поля ниже участвуют в Launch Review и создании Job.',
    'Приватная схема Ads Manager загружена. Доступ к ресурсам проверяется для выбранного РК.', $launch);
file_put_contents($root.'/scripts/launch.js', $launch);
$creatives = $languageNames.$moduleControls.file_get_contents($root.'/scripts/creatives.js');
$creatives = str_replace("name:'Язык #' + id", "name:remaskModuleLanguageNames[String(id)]||('Язык #' + id)", $creatives);
$creatives = str_replace(['SDK-схема загружена','SDK-схема недоступна','Official Meta SDK placements',
    'Нет в текущей Meta SDK schema'], ['Приватная схема Ads Manager загружена','Приватная схема недоступна',
    'Места показа из приватных модулей Ads Manager','Нет в проверенной приватной схеме'], $creatives);
$creatives .= "\n".file_get_contents('/tmp/railway-advertising-tools.js');
file_put_contents($root.'/scripts/creatives.js', $creatives);
foreach (['launch.php','creatives.php'] as $name) {
    $page = file_get_contents($root.'/'.$name);
    // These editors use native handlers. The obsolete Bootstrap 4 JS crashes
    // without jQuery; retain Bootstrap CSS and the editors' own interactions.
    $page = str_replace('<script src="styles/bootstrap.min.js"></script>', '', $page);
    $module = $name === 'launch.php' ? 'launch' : 'creatives';
    $revision = substr(hash_file('sha256', $root.'/scripts/'.$module.'.js'), 0, 16);
    $page = preg_replace('~(scripts/'.$module.'\.js)\?v=[^"\s]+~', '$1?v=private-'.$revision, $page);
    $page = str_replace('20261013-2', '20261013-3', $page);
    $page = str_replace('accept="image/*"', 'accept="image/*,video/*"', $page);
    $page = str_replace('Расширенный Targeting JSON (официальные Meta-поля)', 'Расширенные настройки аудитории', $page);
    $page = str_replace(['ВЫБРАТЬ 2–10 ИЗОБРАЖЕНИЙ','Carousel images'],
        ['ВЫБРАТЬ 2–10 ФОТО / ВИДЕО','Фото и видео карусели'], $page);
    $page = str_replace('Каждая картинка будет загружена отдельно в каждый выбранный RK. Hash каждого upload сохраняется в Job, поэтому Retry не повторяет уже успешные карточки.',
        'Можно сочетать изображения и видео. Порядок карточек и их настройки сохраняются в наборе.', $page);
    file_put_contents($root.'/'.$name, $page);
}
fwrite(STDERR, "[private-completion] private schema and durable Launch media installed\n");
