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
/* creative-complete-v100 / creative-ui-v101 */
.cr-page{max-width:1460px;margin:0 auto;padding:18px 22px 56px;text-align:left;color:#e7e9ee}
.cr-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:2px 0 16px}
.cr-title{font-size:26px;font-weight:700;margin:0}
.cr-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.cr-search{width:min(420px,100%);height:38px;background:#1d2128;border:1px solid #343a45;border-radius:7px;color:#e5e8ee;padding:0 12px}
.cr-count{font-size:12px;color:#7f8898}
.cr-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:13px}
.cr-card{background:#23272f;border:1px solid #313742;border-radius:9px;overflow:hidden}
.cr-preview{position:relative;aspect-ratio:4/3;background:#15181d;overflow:hidden}
.cr-preview img,.cr-preview video{width:100%;height:100%;object-fit:cover}
.cr-format{position:absolute;left:8px;top:8px;background:rgba(15,17,21,.82);padding:4px 7px;border-radius:5px;font-size:10px;font-weight:700}
.cr-body{padding:10px}.cr-name{font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cr-file{font-size:11px;color:#7f8898;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cr-actions{display:flex;gap:6px;margin-top:10px}
.cr-launch{flex:1;background:#248653;border:1px solid #248653;color:#fff;border-radius:7px;padding:7px 9px;font-weight:700;font-size:12px;text-align:center;text-decoration:none!important}
.cr-icon,.cr-btn{border-radius:7px;border:1px solid #3b424d;background:#2b3038;color:#dfe4ec}
.cr-icon{width:34px;height:34px}.cr-btn{padding:9px 13px}.cr-primary{background:#2d67e8;border-color:#2d67e8;color:#fff;font-weight:700}
.cr-empty{grid-column:1/-1;padding:60px 20px;text-align:center;color:#788292}

.cr-backdrop{display:none;position:fixed;inset:0;background:rgba(7,9,12,.78);z-index:900;align-items:center;justify-content:center;padding:18px}
.cr-backdrop.open{display:flex}
.cr-modal{width:min(940px,calc(100vw - 36px));max-height:92vh;display:flex;flex-direction:column;background:#22262e;border:1px solid #363d48;border-radius:11px;box-shadow:0 24px 80px rgba(0,0,0,.55);overflow:hidden}
.cr-modal-head{display:flex;align-items:center;justify-content:space-between;padding:13px 16px;border-bottom:1px solid #343a45;flex:0 0 auto}
.cr-modal-head h3{font-size:18px;margin:0;font-weight:700}.cr-close{border:0;background:transparent;color:#9099a8;font-size:24px;line-height:1;padding:4px}
#creativeForm{display:flex;flex-direction:column;min-height:0}
.cr-editor{padding:15px 16px 12px;overflow:auto}
.cr-form-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px}
.span-12{grid-column:span 12}.span-8{grid-column:span 8}.span-6{grid-column:span 6}.span-4{grid-column:span 4}.span-3{grid-column:span 3}
.cr-field{min-width:0}
.cr-field label,.cr-media-box>label,.cr-instagram-field label{display:block!important;margin:0 0 5px!important;font-size:11px!important;line-height:1.25;color:#9aa3b2;font-weight:600}
.cr-editor .form-control{display:block!important;width:100%!important;height:38px!important;margin:0!important;padding:8px 10px!important;background:#1b1f25!important;border:1px solid #39414d!important;color:#e8ebf0!important;border-radius:7px!important;box-shadow:none!important;font-size:13px!important}
.cr-editor textarea.form-control{height:82px!important;min-height:82px!important;resize:vertical;line-height:1.35}
.cr-editor select.form-control{padding-right:30px!important}
.cr-section-title{font-size:12px;font-weight:700;color:#c6ccd5;margin:14px 0 8px}
.cr-separator{height:1px;background:#343a45;margin:14px 0}

.cr-media-box{margin-top:14px;padding-top:14px;border-top:1px solid #343a45}
.cr-media-row{display:grid;grid-template-columns:230px minmax(0,1fr);gap:14px;align-items:start}
.cr-preview-box{height:150px;background:#171a1f;border:1px solid #303640;border-radius:8px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#747d8b}
.cr-preview-box img,.cr-preview-box video{width:100%;height:100%;object-fit:contain}
.cr-upload-panel{display:flex;flex-direction:column;gap:8px;min-height:150px;justify-content:center}
.cr-file-btn{display:flex;align-items:center;justify-content:center;height:38px;margin:0;border:1px solid #414955;border-radius:7px;background:#2a2f38;color:#dbe0e8;cursor:pointer;font-size:12px;font-weight:700}
.cr-file-btn:hover{background:#313741}.cr-file-btn input{display:none}
.cr-hint{font-size:11px;color:#808999;line-height:1.35}
.cr-carousel-list{margin-top:10px;display:grid;gap:8px}
.cr-carousel-row{display:grid;grid-template-columns:48px 150px minmax(130px,1fr) minmax(130px,1fr) minmax(160px,1.15fr);gap:8px;align-items:center;background:#1b1f25;border:1px solid #343a45;border-radius:8px;padding:7px}
.cr-carousel-row .form-control{height:34px!important;font-size:12px!important;padding:6px 8px!important}
.cr-thumb{width:48px;height:48px;background:#15181d;border-radius:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#7f8898}
.cr-thumb img{width:100%;height:100%;object-fit:cover}
.cr-instagram-field{max-width:420px}

.cr-modal-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-top:1px solid #343a45;background:#20242b;flex:0 0 auto}
.cr-status{font-size:12px;color:#8993a3;min-height:18px}.cr-status.bad{color:#ff8f8f}.cr-status.ok{color:#69d99b}
.cr-foot-actions{display:flex;gap:8px}
@media(max-width:820px){
  .span-8,.span-6,.span-4,.span-3{grid-column:span 12}
  .cr-modal{width:min(680px,calc(100vw - 18px))}
  .cr-media-row{grid-template-columns:1fr}
  .cr-preview-box{height:180px}
  .cr-carousel-row{grid-template-columns:44px 1fr}
  .cr-carousel-row .form-control{grid-column:span 2}
}
</style>
</head>
<body class="app-shell">
<?php include 'menu.php' ?>
<main class="app-main">
<div class="cr-page">
  <div class="cr-head">
    <h1 class="cr-title">Креативы</h1>
    <button id="newCreative" type="button" class="cr-btn cr-primary"><i class="fa-solid fa-plus mr-1"></i> ДОБАВИТЬ КРЕО</button>
  </div>
  <div class="cr-toolbar">
    <input id="creativeSearch" class="cr-search" placeholder="Поиск">
    <span id="creativeCount" class="cr-count"></span>
    <button id="refreshCreatives" type="button" class="cr-icon" title="Обновить"><i class="fa-solid fa-rotate"></i></button>
  </div>
  <div id="creativeGrid" class="cr-grid"><div class="cr-empty">Загрузка…</div></div>
</div>

<div id="creativeModal" class="cr-backdrop" aria-hidden="true">
<div class="cr-modal">
  <div class="cr-modal-head">
    <h3 id="creativeEditorTitle">Новое крео</h3>
    <button id="closeCreative" class="cr-close" type="button">×</button>
  </div>

  <form id="creativeForm">
    <input id="creativeId" type="hidden">
    <div class="cr-editor">
      <div class="cr-form-grid">
        <div class="span-4 cr-field">
          <label for="presetName">Название в библиотеке</label>
          <input id="presetName" class="form-control">
        </div>
        <div class="span-4 cr-field">
          <label for="presetCreativeName">Creative name</label>
          <input id="presetCreativeName" class="form-control">
        </div>
        <div class="span-4 cr-field">
          <label for="presetAdName">Ad name</label>
          <input id="presetAdName" class="form-control">
        </div>

        <div class="span-12 cr-field">
          <label for="presetMessage">Основной текст</label>
          <textarea id="presetMessage" class="form-control"></textarea>
        </div>

        <div class="span-4 cr-field">
          <label for="presetHeadline">Headline</label>
          <input id="presetHeadline" class="form-control">
        </div>
        <div class="span-4 cr-field">
          <label for="presetDescription">Description</label>
          <input id="presetDescription" class="form-control">
        </div>
        <div class="span-4 cr-field">
          <label for="presetCta">CTA</label>
          <select id="presetCta" class="form-control">
            <option value="LEARN_MORE">Подробнее</option>
            <option value="SIGN_UP">Регистрация</option>
            <option value="APPLY_NOW">Подать заявку</option>
            <option value="CONTACT_US">Связаться</option>
            <option value="SHOP_NOW">Купить</option>
            <option value="GET_OFFER">Получить предложение</option>
          </select>
        </div>

        <div class="span-8 cr-field">
          <label for="presetUrl">Destination URL</label>
          <input id="presetUrl" class="form-control" placeholder="https://...">
        </div>
        <div class="span-4 cr-field">
          <label for="presetTags">URL tags / UTM</label>
          <input id="presetTags" class="form-control" placeholder="utm_source=facebook&...">
        </div>

        <div class="span-4 cr-field">
          <label for="presetFormat">Creative format</label>
          <select id="presetFormat" class="form-control">
            <option value="SINGLE">Single image / video</option>
            <option value="CAROUSEL">Carousel (2–10 images)</option>
            <option value="INSTAGRAM_POST">Existing Instagram post / reel</option>
          </select>
        </div>
      </div>

      <div id="singleSection" class="cr-media-box">
        <div class="cr-media-row">
          <div id="singlePreview" class="cr-preview-box"><i class="fa-regular fa-image"></i></div>
          <div class="cr-upload-panel">
            <label class="cr-file-btn">ВЫБРАТЬ IMAGE / VIDEO<input id="presetMedia" type="file" accept="image/*,video/*"></label>
            <div id="singleCurrent" class="cr-hint">Изображение или видео для этого крео.</div>
          </div>
        </div>
      </div>

      <div id="carouselSection" class="cr-media-box" style="display:none">
        <label class="cr-file-btn" style="max-width:300px">ВЫБРАТЬ 2–10 ИЗОБРАЖЕНИЙ<input id="presetCarousel" type="file" accept="image/*" multiple></label>
        <div id="carouselHint" class="cr-hint"></div>
        <div id="carouselRows" class="cr-carousel-list"></div>
      </div>

      <div id="instagramSection" class="cr-media-box" style="display:none">
        <div class="cr-instagram-field">
          <label for="presetInstagramMediaId">Instagram media ID</label>
          <input id="presetInstagramMediaId" class="form-control" inputmode="numeric" placeholder="Existing post / reel media ID">
        </div>
      </div>
    </div>

    <div class="cr-modal-foot">
      <div id="creativeStatus" class="cr-status"></div>
      <div class="cr-foot-actions">
        <button id="cancelCreative" type="button" class="cr-btn">ОТМЕНА</button>
        <button id="saveCreative" type="submit" class="cr-btn cr-primary">СОХРАНИТЬ</button>
      </div>
    </div>
  </form>
</div>
</div>

<script src="scripts/creatives.js?v=20260919-creative-ui-v101" type="module"></script>
<div class="app-footer"><?php include 'copyright.php' ?></div>
</main>
</body>
</html>
