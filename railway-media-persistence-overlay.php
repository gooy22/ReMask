<?php
/**
 * v76 persist a selected single image/video across page reloads.
 * Browsers intentionally clear <input type=file> on reload, so keep the File
 * in IndexedDB for a short-lived Launch draft and reuse it for Dry Run/Review/Launch.
 */
$root = '/var/www/html';
$jsPath = $root . '/scripts/launch.js';
$phpPath = $root . '/launch.php';

if (!is_file($jsPath) || !is_file($phpPath)) {
    fwrite(STDERR, "[media-persistence] runtime files missing\n");
    exit(131);
}

$js = file_get_contents($jsPath);
$php = file_get_contents($phpPath);
if ($js === false || $php === false) {
    fwrite(STDERR, "[media-persistence] cannot read runtime files\n");
    exit(132);
}

if (strpos($js, 'REMASK_MEDIA_DRAFT_PERSIST_V1') === false) {
    $stateNeedle = "    pendingWorkspaceMedia: null,";
    if (strpos($js, $stateNeedle) === false) {
        fwrite(STDERR, "[media-persistence] state insertion point missing\n");
        exit(133);
    }
    $js = str_replace(
        $stateNeedle,
        $stateNeedle . "\n    persistedMediaFile: null,",
        $js,
        $stateCount
    );
    if ($stateCount !== 1) {
        fwrite(STDERR, "[media-persistence] state insertion count={$stateCount}\n");
        exit(134);
    }

    $helperNeedle = "function currentDryRunMediaPlan() {";
    if (strpos($js, $helperNeedle) === false) {
        fwrite(STDERR, "[media-persistence] media plan insertion point missing\n");
        exit(135);
    }

    $helpers = <<<'JS'
/* REMASK_MEDIA_DRAFT_PERSIST_V1 */
const REMASK_MEDIA_DRAFT_TTL_MS = 30 * 60 * 1000;
const REMASK_MEDIA_DRAFT_DB = 'remask-launch-draft-v1';
const REMASK_MEDIA_DRAFT_STORE = 'files';
const REMASK_MEDIA_DRAFT_KEY = 'single-primary-media';

function openMediaDraftDb() {
    return new Promise((resolve, reject) => {
        if (!('indexedDB' in window)) return reject(new Error('IndexedDB unavailable'));
        const req = indexedDB.open(REMASK_MEDIA_DRAFT_DB, 1);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(REMASK_MEDIA_DRAFT_STORE)) {
                db.createObjectStore(REMASK_MEDIA_DRAFT_STORE);
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error || new Error('Could not open media draft database'));
    });
}

async function savePersistedMedia(file) {
    if (!file) return;
    state.persistedMediaFile = file;
    try {
        const db = await openMediaDraftDb();
        await new Promise((resolve, reject) => {
            const tx = db.transaction(REMASK_MEDIA_DRAFT_STORE, 'readwrite');
            tx.objectStore(REMASK_MEDIA_DRAFT_STORE).put({
                blob: file,
                name: file.name || 'media',
                type: file.type || '',
                lastModified: Number(file.lastModified || Date.now()),
                savedAt: Date.now(),
            }, REMASK_MEDIA_DRAFT_KEY);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error || new Error('Could not persist media draft'));
            tx.onabort = () => reject(tx.error || new Error('Media draft transaction aborted'));
        });
        db.close();
    } catch (e) {
        console.warn('Could not persist selected media:', e);
    }
}

async function clearPersistedMedia() {
    state.persistedMediaFile = null;
    try {
        const db = await openMediaDraftDb();
        await new Promise((resolve, reject) => {
            const tx = db.transaction(REMASK_MEDIA_DRAFT_STORE, 'readwrite');
            tx.objectStore(REMASK_MEDIA_DRAFT_STORE).delete(REMASK_MEDIA_DRAFT_KEY);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error || new Error('Could not clear media draft'));
        });
        db.close();
    } catch {}
}

async function restorePersistedMedia() {
    try {
        const db = await openMediaDraftDb();
        const record = await new Promise((resolve, reject) => {
            const tx = db.transaction(REMASK_MEDIA_DRAFT_STORE, 'readonly');
            const req = tx.objectStore(REMASK_MEDIA_DRAFT_STORE).get(REMASK_MEDIA_DRAFT_KEY);
            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => reject(req.error || new Error('Could not read media draft'));
        });
        db.close();

        if (!record || !record.blob) return;
        if (!record.savedAt || Date.now() - Number(record.savedAt) > REMASK_MEDIA_DRAFT_TTL_MS) {
            await clearPersistedMedia();
            return;
        }

        const file = new File(
            [record.blob],
            String(record.name || 'media'),
            {type: String(record.type || record.blob.type || ''), lastModified: Number(record.lastModified || record.savedAt || Date.now())}
        );
        state.persistedMediaFile = file;

        const status = $('existingMediaStatus');
        if (status && !state.pendingWorkspaceMedia) {
            status.textContent = `Restored local upload: ${file.name} · ${Math.round(file.size / 1024)} KB`;
        }
        validateReady();
    } catch (e) {
        console.warn('Could not restore selected media:', e);
    }
}

JS;

    $js = str_replace($helperNeedle, $helpers . $helperNeedle, $js, $helperCount);
    if ($helperCount !== 1) {
        fwrite(STDERR, "[media-persistence] helper insertion count={$helperCount}\n");
        exit(136);
    }

    $fileNeedle = "const file = $('media').files[0];";
    $fileReplacement = "const file = $('media').files[0] || state.persistedMediaFile;";
    $js = str_replace($fileNeedle, $fileReplacement, $js, $fileCount);
    if ($fileCount !== 2) {
        fwrite(STDERR, "[media-persistence] expected 2 single-media reads, got {$fileCount}\n");
        exit(137);
    }

    $initNeedle = "initializeLaunch();";
    if (strpos($js, $initNeedle) === false) {
        fwrite(STDERR, "[media-persistence] initializeLaunch call missing\n");
        exit(138);
    }
    $tail = <<<'JS'
initializeLaunch();
restorePersistedMedia();
$('media')?.addEventListener('change', () => {
    const file = $('media').files?.[0] || null;
    if (!file) return;
    state.persistedMediaFile = file;
    savePersistedMedia(file);
    const status = $('existingMediaStatus');
    if (status && !state.pendingWorkspaceMedia) {
        status.textContent = `Selected local upload: ${file.name} · ${Math.round(file.size / 1024)} KB`;
    }
    invalidateLaunchReview();
    validateReady();
});
JS;
    $js = str_replace($initNeedle, $tail, $js, $initCount);
    if ($initCount !== 1) {
        fwrite(STDERR, "[media-persistence] initializeLaunch replacement count={$initCount}\n");
        exit(139);
    }
}

$php = preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260918-media-draft-v76" type="module"></script>',
    $php,
    1,
    $scriptCount
) ?? $php;
if ($scriptCount !== 1) {
    fwrite(STDERR, "[media-persistence] launch.js script tag missing\n");
    exit(140);
}

file_put_contents($jsPath, $js);
file_put_contents($phpPath, $php);
fwrite(STDERR, "[media-persistence] v76 local media draft persistence ready\n");
