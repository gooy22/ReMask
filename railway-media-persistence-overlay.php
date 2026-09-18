<?php
/**
 * v77 lightweight Launch media draft persistence.
 *
 * Do not patch launch.js. Browsers clear <input type=file> on reload, so a
 * tiny classic script stores the selected File in IndexedDB and restores it
 * back into #media via DataTransfer. Existing launch.js then sees the restored
 * File through input.files[0] with no special integration.
 */
$root = '/var/www/html';
$scriptsDir = $root . '/scripts';
$phpPath = $root . '/launch.php';
$scriptPath = $scriptsDir . '/media-draft.js';

if (!is_dir($scriptsDir)) mkdir($scriptsDir, 0775, true);
if (!is_file($phpPath)) {
    fwrite(STDERR, "[media-draft] launch.php missing\n");
    exit(131);
}

$js = <<<'JS'
(() => {
  'use strict';

  const MARKER = 'REMASK_MEDIA_DRAFT_V2';
  const input = document.getElementById('media');
  if (!input || !('indexedDB' in window) || typeof DataTransfer === 'undefined') return;

  const DB_NAME = 'remask-launch-media-v2';
  const STORE = 'drafts';
  const KEY = 'single-primary-media';
  const TTL = 30 * 60 * 1000;

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('IndexedDB open failed'));
    });
  }

  async function putFile(file) {
    if (!file) return;
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put({
        blob: file,
        name: file.name || 'media',
        type: file.type || '',
        lastModified: Number(file.lastModified || Date.now()),
        savedAt: Date.now()
      }, KEY);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error || new Error('Media draft save failed'));
      tx.onabort = () => reject(tx.error || new Error('Media draft save aborted'));
    });
    db.close();
  }

  async function deleteDraft() {
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete(KEY);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error || new Error('Media draft delete failed'));
    });
    db.close();
  }

  async function getDraft() {
    const db = await openDb();
    const record = await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).get(KEY);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error || new Error('Media draft read failed'));
    });
    db.close();
    return record;
  }

  function setStatus(text) {
    const el = document.getElementById('existingMediaStatus');
    if (el && !el.textContent.includes('Existing Meta')) el.textContent = text;
  }

  input.addEventListener('change', () => {
    const file = input.files && input.files[0] ? input.files[0] : null;
    if (!file) return;
    putFile(file)
      .then(() => setStatus(`Selected local upload: ${file.name} · ${Math.max(1, Math.round(file.size / 1024))} KB`))
      .catch((err) => console.warn('[media-draft] save failed', err));
  });

  (async () => {
    try {
      const record = await getDraft();
      if (!record || !record.blob) return;
      if (!record.savedAt || Date.now() - Number(record.savedAt) > TTL) {
        await deleteDraft();
        return;
      }

      const file = record.blob instanceof File
        ? new File([record.blob], record.name || record.blob.name || 'media', {
            type: record.type || record.blob.type || '',
            lastModified: Number(record.lastModified || record.savedAt || Date.now())
          })
        : new File([record.blob], record.name || 'media', {
            type: record.type || '',
            lastModified: Number(record.lastModified || record.savedAt || Date.now())
          });

      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dataset.remaskMediaRestored = '1';
      setStatus(`Restored local upload: ${file.name} · ${Math.max(1, Math.round(file.size / 1024))} KB`);

      // Let existing launch.js recalculate any UI derived from the file input.
      input.dispatchEvent(new Event('change', {bubbles: true}));
    } catch (err) {
      console.warn('[media-draft] restore failed', err);
    }
  })();

  window.__remaskMediaDraft = MARKER;
})();
JS;

file_put_contents($scriptPath, $js);

$php = file_get_contents($phpPath);
if ($php === false) {
    fwrite(STDERR, "[media-draft] cannot read launch.php\n");
    exit(132);
}

// Remove any prior media-draft include before adding the canonical v77 include.
$php = preg_replace(
    '#\s*<script\s+src=["\']scripts/media-draft\.js(?:\?[^"\']*)?["\']\s*></script>\s*#i',
    "\n",
    $php
) ?? $php;

$launchTagPattern = '#(<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>)#';
$replacement = '$1' . "\n" . '<script src="scripts/media-draft.js?v=20260918-media-draft-v77"></script>';
$php = preg_replace($launchTagPattern, $replacement, $php, 1, $count) ?? $php;
if ($count !== 1) {
    fwrite(STDERR, "[media-draft] launch.js tag not found\n");
    exit(133);
}

file_put_contents($phpPath, $php);
fwrite(STDERR, "[media-draft] v77 lightweight file persistence ready\n");
