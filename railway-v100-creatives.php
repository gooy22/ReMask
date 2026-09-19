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
.cr-page{max-width:1500px;margin:0 auto;padding:18px 22px 56px;text-align:left;color:#e7e9ee}.cr-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:2px 0 18px}.cr-title{font-size:28px;font-weight:700;margin:0}.cr-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:16px}.cr-search{width:min(440px,100%);height:40px;background:#20242b;border:1px solid #343a45;border-radius:8px;color:#e5e8ee;padding:0 12px}.cr-count{font-size:12px;color:#7f8898}.cr-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(235px,1fr));gap:14px}.cr-card{background:#23272f;border:1px solid #313742;border-radius:10px;overflow:hidden}.cr-preview{position:relative;aspect-ratio:4/3;background:#15181d;overflow:hidden}.cr-preview img,.cr-preview video{width:100%;height:100%;object-fit:cover}.cr-format{position:absolute;left:9px;top:9px;background:rgba(15,17,21,.8);padding:4px 7px;border-radius:5px;font-size:10px;font-weight:700}.cr-body{padding:11px}.cr-name{font-size:14px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.cr-file{font-size:11px;color:#7f8898;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.cr-actions{display:flex;gap:7px;margin-top:11px}.cr-launch{flex:1;background:#248653;border:1px solid #248653;color:#fff;border-radius:7px;padding:7px 9px;font-weight:700;font-size:12px;text-align:center;text-decoration:none!important}.cr-icon,.cr-btn{border-radius:7px;border:1px solid #3b424d;background:#2b3038;color:#dfe4ec}.cr-icon{width:34px;height:34px}.cr-btn{padding:9px 13px}.cr-primary{background:#2d67e8;border-color:#2d67e8;color:#fff;font-weight:700}.cr-empty{grid-column:1/-1;padding:70px 20px;text-align:center;color:#788292}.cr-backdrop{display:none;position:fixed;inset:0;background:rgba(7,9,12,.76);z-index:900;align-items:center;justify-content:center;padding:20px}.cr-backdrop.open{display:flex}.cr-modal{width:min(1120px,100%);max-height:94vh;overflow:auto;background:#242831;border:1px solid #373e49;border-radius:12px;box-shadow:0 24px 80px rgba(0,0,0,.5)}.cr-modal-head{display:flex;align-items:center;justify-content:space-between;padding:15px 18px;border-bottom:1px solid #343a45}.cr-modal-head h3{font-size:18px;margin:0}.cr-close{border:0;background:transparent;color:#9099a8;font-size:24px}.cr-editor{padding:18px}.cr-form-grid{display:grid;grid-template-columns:repeat(12,1fr);gap:12px}.span-12{grid-column:span 12}.span-8{grid-column:span 8}.span-6{grid-column:span 6}.span-4{grid-column:span 4}.cr-editor label{font-size:11px;color:#929bab;margin-bottom:5px}.cr-editor .form-control{background:#1f232a;border:1px solid #373e49;color:#e6e9ef;border-radius:7px}.cr-editor textarea{min-height:100px;resize:vertical}.cr-media-box{margin-top:16px;padding-top:16px;border-top:1px solid #343a45}.cr-preview-box{height:260px;background:#171a1f;border-radius:9px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#747d8b}.cr-preview-box img,.cr-preview-box video{width:100%;height:100%;object-fit:contain}.cr-media-row{display:grid;grid-template-columns:300px 1fr;gap:16px}.cr-file-btn{display:flex;align-items:center;justify-content:center;margin-top:10px;height:38px;border:1px solid #414955;border-radius:7px;background:#2a2f38;color:#dbe0e8;cursor:pointer;font-size:12px;font-weight:700}.cr-file-btn input{display:none}.cr-hint{font-size:11px;color:#808999;margin-top:7px}.cr-carousel-list{margin-top:12px;display:grid;gap:8px}.cr-carousel-row{display:grid;grid-template-columns:52px 180px 1fr 1fr 1fr;gap:8px;align-items:center;background:#1f232a;border:1px solid #343a45;border-radius:8px;padding:8px}.cr-thumb{width:52px;height:52px;background:#15181d;border-radius:6px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#7f8898}.cr-thumb img{width:100%;height:100%;object-fit:cover}.cr-modal-foot{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-top:1px solid #343a45}.cr-status{font-size:12px;color:#8993a3}.cr-status.bad{color:#ff8f8f}.cr-status.ok{color:#69d99b}@media(max-width:820px){.span-8,.span-6,.span-4{grid-column:span 12}.cr-media-row{grid-template-columns:1fr}.cr-carousel-row{grid-template-columns:42px 1fr}.cr-carousel-row .form-control{grid-column:span 2}}
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
        <div class="span-4"><label>Название в библиотеке</label><input id="presetName" class="form-control"></div>
        <div class="span-4"><label>Creative name</label><input id="presetCreativeName" class="form-control"></div>
        <div class="span-4"><label>Ad name</label><input id="presetAdName" class="form-control"></div>

        <div class="span-12"><label>Основной текст</label><textarea id="presetMessage" class="form-control"></textarea></div>

        <div class="span-4"><label>Headline</label><input id="presetHeadline" class="form-control"></div>
        <div class="span-4"><label>Description</label><input id="presetDescription" class="form-control"></div>
        <div class="span-4"><label>CTA</label>
          <select id="presetCta" class="form-control">
            <option value="LEARN_MORE">Подробнее</option>
            <option value="SIGN_UP">Регистрация</option>
            <option value="APPLY_NOW">Подать заявку</option>
            <option value="CONTACT_US">Связаться</option>
            <option value="SHOP_NOW">Купить</option>
            <option value="GET_OFFER">Получить предложение</option>
          </select>
        </div>

        <div class="span-8"><label>Destination URL</label><input id="presetUrl" class="form-control" placeholder="https://..."></div>
        <div class="span-4"><label>URL tags / UTM</label><input id="presetTags" class="form-control" placeholder="utm_source=facebook&..."></div>

        <div class="span-4"><label>Creative format</label>
          <select id="presetFormat" class="form-control">
            <option value="SINGLE">Single image / video</option>
            <option value="CAROUSEL">Carousel (2–10 images)</option>
            <option value="INSTAGRAM_POST">Existing Instagram post / reel</option>
          </select>
        </div>
      </div>

      <div id="singleSection" class="cr-media-box">
        <div class="cr-media-row">
          <div>
            <div id="singlePreview" class="cr-preview-box"><i class="fa-regular fa-image"></i></div>
            <label class="cr-file-btn">ВЫБРАТЬ IMAGE / VIDEO<input id="presetMedia" type="file" accept="image/*,video/*"></label>
            <div id="singleCurrent" class="cr-hint"></div>
          </div>
        </div>
      </div>

      <div id="carouselSection" class="cr-media-box" style="display:none">
        <label class="cr-file-btn" style="max-width:320px">ВЫБРАТЬ 2–10 ИЗОБРАЖЕНИЙ<input id="presetCarousel" type="file" accept="image/*" multiple></label>
        <div id="carouselHint" class="cr-hint"></div>
        <div id="carouselRows" class="cr-carousel-list"></div>
      </div>

      <div id="instagramSection" class="cr-media-box" style="display:none">
        <div style="max-width:460px"><label>Instagram media ID</label><input id="presetInstagramMediaId" class="form-control" inputmode="numeric" placeholder="Existing post / reel media ID"></div>
      </div>
    </div>
    <div class="cr-modal-foot">
      <div id="creativeStatus" class="cr-status"></div>
      <div>
        <button id="cancelCreative" type="button" class="cr-btn mr-2">ОТМЕНА</button>
        <button id="saveCreative" type="submit" class="cr-btn cr-primary">СОХРАНИТЬ</button>
      </div>
    </div>
  </form>
</div>
</div>
<script src="scripts/creatives.js?v=20260919-creative-complete-v100" type="module"></script>
<div class="app-footer"><?php include 'copyright.php' ?></div>
</main>
</body>
</html>
