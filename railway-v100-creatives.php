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
/* creative-meta-builder-v102 */
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
.cr-backdrop{display:none;position:fixed;inset:0;background:rgba(7,9,12,.78);z-index:900;align-items:center;justify-content:center;padding:16px}
.cr-backdrop.open{display:flex}
.cr-modal{width:min(1120px,calc(100vw - 32px));max-height:94vh;display:flex;flex-direction:column;background:#22262e;border:1px solid #363d48;border-radius:11px;box-shadow:0 24px 80px rgba(0,0,0,.55);overflow:hidden}
.cr-modal-head{display:flex;align-items:center;justify-content:space-between;padding:13px 16px;border-bottom:1px solid #343a45}
.cr-modal-head h3{font-size:18px;margin:0;font-weight:700}.cr-close{border:0;background:transparent;color:#9099a8;font-size:24px;line-height:1;padding:4px}
#creativeForm{display:flex;flex-direction:column;min-height:0;flex:1}
.cr-meta-context{display:grid;grid-template-columns:minmax(180px,1fr) minmax(220px,1.25fr) auto minmax(180px,1.2fr);gap:10px;align-items:end;padding:12px 14px;background:#1b1f25;border-bottom:1px solid #343a45}
.cr-meta-context .cr-field label{display:block;margin:0 0 5px;font-size:11px;color:#9aa3b2;font-weight:600}
.cr-meta-context .form-control{height:38px!important;background:#171b21!important;border:1px solid #39414d!important;color:#e8ebf0!important;border-radius:7px!important}
.cr-meta-context-status{font-size:11px;color:#8f99aa;line-height:1.35;padding-bottom:8px}
.cr-audience-estimate{margin-top:14px;border:1px solid #37404c;background:#191e25;border-radius:9px;padding:13px 14px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px 18px;align-items:center}
.cr-audience-estimate-title{font-size:11px;color:#8993a3;text-transform:uppercase;letter-spacing:.04em}
.cr-audience-estimate-value{font-size:24px;font-weight:750;line-height:1.15;color:#eef1f6;margin-top:3px}
.cr-audience-estimate-meta{font-size:11px;color:#7f8999;margin-top:5px;line-height:1.4}
.cr-audience-estimate-state{font-size:11px;color:#9da7b7;text-align:right}
.cr-target-box{border:1px solid #343c48;background:#1a1f26;border-radius:8px;padding:10px;min-height:112px}
.cr-target-box label{display:block;margin:0 0 6px;font-size:11px;color:#9aa3b2;font-weight:650}
.cr-target-input{position:relative}
.cr-target-results{display:none;position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:40;max-height:220px;overflow:auto;background:#171b21;border:1px solid #3b4451;border-radius:7px;box-shadow:0 14px 36px rgba(0,0,0,.35)}
.cr-target-results.open{display:block}
.cr-target-result{padding:8px 10px;cursor:pointer;border-bottom:1px solid #262d36;font-size:12px;color:#dfe4eb}
.cr-target-result:last-child{border-bottom:0}.cr-target-result:hover{background:#252c35}
.cr-target-result small{display:block;color:#7f8999;margin-top:2px}
.cr-target-pills{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.cr-target-pill{display:inline-flex;align-items:center;gap:6px;max-width:100%;border:1px solid #414b59;background:#262d36;border-radius:999px;padding:5px 7px 5px 9px;font-size:11px;color:#dde2e9}
.cr-target-pill button{border:0;background:transparent;color:#929dad;padding:0;line-height:1;cursor:pointer}
.cr-target-empty{padding:9px 10px;color:#7f8999;font-size:11px}
.cr-raw-targeting{grid-column:span 12;border-top:1px solid #343a45;padding-top:8px}
.cr-raw-targeting summary{cursor:pointer;color:#8f99a9;font-size:11px;font-weight:650;margin-bottom:8px}
.cr-tabs{display:flex;gap:4px;padding:9px 12px;border-bottom:1px solid #343a45;background:#20242b;overflow-x:auto}
.cr-tab{border:0;background:transparent;color:#8f98a8;padding:7px 10px;border-radius:6px;font-size:12px;font-weight:700;white-space:nowrap}
.cr-tab.active{background:#313741;color:#fff}
.cr-editor{padding:15px 16px 12px;overflow:auto;min-height:0}
.cr-panel{display:none}.cr-panel.active{display:block}
.cr-form-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px}
.span-12{grid-column:span 12}.span-8{grid-column:span 8}.span-6{grid-column:span 6}.span-4{grid-column:span 4}.span-3{grid-column:span 3}
.cr-field{min-width:0}
.cr-field label,.cr-media-box>label,.cr-instagram-field label{display:block!important;margin:0 0 5px!important;font-size:11px!important;line-height:1.25;color:#9aa3b2;font-weight:600}
.cr-editor .form-control{display:block!important;width:100%!important;height:38px!important;margin:0!important;padding:8px 10px!important;background:#1b1f25!important;border:1px solid #39414d!important;color:#e8ebf0!important;border-radius:7px!important;box-shadow:none!important;font-size:13px!important}
.cr-editor textarea.form-control{height:82px!important;min-height:82px!important;resize:vertical;line-height:1.35}
.cr-editor textarea.cr-json{height:120px!important;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px!important}
.cr-checkrow{display:flex;gap:14px;flex-wrap:wrap;padding:8px 0}.cr-check{display:flex;align-items:center;gap:6px;color:#c6ccd5;font-size:12px}.cr-check input{margin:0}
.cr-section-title{font-size:12px;font-weight:700;color:#d2d7df;margin:4px 0 10px}
.cr-muted{font-size:11px;color:#7f8898;line-height:1.4}
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
.cr-modal-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-top:1px solid #343a45;background:#20242b}
.cr-status{font-size:12px;color:#8993a3;min-height:18px}.cr-status.bad{color:#ff8f8f}.cr-status.ok{color:#69d99b}
.cr-sdk-groups{display:grid;gap:10px}.cr-sdk-group{border:1px solid #343a45;border-radius:8px;background:#1d2128;overflow:hidden}.cr-sdk-group>summary{cursor:pointer;padding:10px 12px;color:#dbe0e8;font-size:12px;font-weight:700;list-style:none;display:flex;justify-content:space-between;align-items:center}.cr-sdk-group>summary::-webkit-details-marker{display:none}.cr-sdk-count{font-size:10px;color:#7f8898;font-weight:600}.cr-sdk-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:0 12px 12px}.cr-sdk-field{min-width:0}.cr-sdk-field label{display:flex!important;justify-content:space-between;gap:8px;margin-bottom:5px!important}.cr-sdk-type{font-size:9px;color:#70798a;font-weight:500}.cr-sdk-field textarea{min-height:72px!important;height:72px!important}.cr-raw-json{margin-top:12px;border-top:1px solid #343a45;padding-top:10px}.cr-raw-json>summary{cursor:pointer;color:#9aa3b2;font-size:12px;font-weight:700;margin-bottom:10px}.cr-sdk-hidden{display:none!important}
.cr-placement-mode{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:14px}
.cr-placement-mode-option{display:flex;gap:9px;align-items:flex-start;border:1px solid #38414d;background:#1a1f26;border-radius:9px;padding:11px 12px;cursor:pointer}
.cr-placement-mode-option input{margin-top:3px}.cr-placement-mode-option span{display:flex;flex-direction:column;gap:3px}.cr-placement-mode-option b{font-size:12px;color:#e3e7ed}.cr-placement-mode-option small{font-size:10px;color:#7f8999;line-height:1.35}
.cr-placement-manual{border-top:1px solid #343a45;padding-top:12px}
.cr-placement-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
.cr-placement-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.cr-placement-card{border:1px solid #343d49;background:#191e25;border-radius:9px;padding:10px;min-width:0}
.cr-placement-card.disabled{opacity:.45}.cr-placement-card-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding-bottom:8px;border-bottom:1px solid #2e3641}
.cr-placement-all{font-size:10px!important;color:#8f99aa!important}
.cr-placement-options{display:flex;flex-wrap:wrap;gap:7px 9px;padding-top:9px}
.cr-placement-options .cr-check{border:1px solid #303945;background:#20262e;border-radius:7px;padding:6px 8px;font-size:11px}
.cr-placement-options .cr-check input{accent-color:#3d79f2}
.cr-placement-options-inline{padding-top:0}
.cr-placement-devices{margin-top:12px;border:1px solid #343d49;background:#191e25;border-radius:9px;padding:10px}
.cr-placement-live{color:#63d69b}.cr-placement-fallback{color:#d4a55d}
.cr-placement-options .cr-check{transition:border-color .16s ease,background .16s ease,transform .16s ease}
.cr-placement-options .cr-check:hover{border-color:#52627a;background:#242c36;transform:translateY(-1px)}
.cr-placement-hover-preview{position:fixed;z-index:2600;width:292px;padding:10px;border:1px solid #3b4655;border-radius:13px;background:#151a20;box-shadow:0 18px 55px rgba(0,0,0,.48);opacity:0;transform:translateY(5px) scale(.985);pointer-events:none;transition:opacity .13s ease,transform .13s ease}
.cr-placement-hover-preview.open{opacity:1;transform:translateY(0) scale(1)}
.cr-placement-preview-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:8px}
.cr-placement-preview-title{font-size:12px;font-weight:800;color:#eef2f7}
.cr-placement-preview-meta{font-size:10px;color:#8d98a9;margin-top:2px}
.cr-placement-preview-live{font-size:9px;font-weight:800;letter-spacing:.08em;color:#78d9a7;border:1px solid #315a47;border-radius:999px;padding:3px 6px}
.cr-placement-demo{height:330px;border:1px solid #333e4c;border-radius:20px;background:#0f1318;overflow:hidden;position:relative}
.cr-placement-demo::before{content:"";position:absolute;top:7px;left:50%;width:48px;height:5px;border-radius:999px;background:#2d3540;transform:translateX(-50%);z-index:5}
.cr-placement-demo-screen{position:absolute;inset:20px 7px 7px;border-radius:14px;overflow:hidden;background:#edf0f4;color:#24272c}
.cr-placement-demo-top{height:30px;display:flex;align-items:center;gap:6px;padding:0 8px;background:#fff;border-bottom:1px solid #d8dce2;font-size:7px;font-weight:800}
.cr-placement-demo-dot{width:13px;height:13px;border-radius:50%;background:#d7dce4}
.cr-placement-demo-feed{position:absolute;inset:30px 0 0;padding:8px;display:flex;flex-direction:column;gap:8px;animation:crPlacementFeed 4.4s ease-in-out infinite}
.cr-placement-demo-card{height:67px;border-radius:8px;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.08);position:relative;overflow:hidden}
.cr-placement-demo-card::before{content:"";position:absolute;left:8px;top:8px;width:46%;height:5px;border-radius:4px;background:#dfe3e9;box-shadow:0 10px 0 #e8ebef}
.cr-placement-demo-ad{min-height:105px;border:2px solid #3b79f4!important;box-shadow:0 0 0 2px rgba(59,121,244,.12)!important;background:#d8e5ff!important;position:relative;overflow:hidden}
.cr-placement-demo-ad::after{content:"AD";position:absolute;right:6px;top:5px;z-index:4;background:#2e6be6;color:#fff;font-size:7px;font-weight:900;line-height:1;padding:4px 5px;border-radius:4px}
.cr-placement-demo-media{position:absolute;left:7px;right:7px;top:28px;bottom:7px;border-radius:6px;overflow:hidden;background:linear-gradient(135deg,#c6d7ff,#8daff9)}
.cr-placement-demo-media img,.cr-placement-demo-media video{width:100%;height:100%;object-fit:cover;display:block}
.cr-placement-demo-media-placeholder{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:800;color:#244a91;letter-spacing:.04em}
.cr-placement-demo.story .cr-placement-demo-screen,.cr-placement-demo.reels .cr-placement-demo-screen,.cr-placement-demo.status .cr-placement-demo-screen{background:#111;color:#fff}
.cr-placement-demo.story .cr-placement-demo-top,.cr-placement-demo.reels .cr-placement-demo-top,.cr-placement-demo.status .cr-placement-demo-top{background:rgba(0,0,0,.35);border:0;color:#fff;position:relative;z-index:4}
.cr-placement-demo.story .cr-placement-demo-feed,.cr-placement-demo.reels .cr-placement-demo-feed,.cr-placement-demo.status .cr-placement-demo-feed{inset:30px 0 0;padding:0;animation:crPlacementVertical 3.8s ease-in-out infinite}
.cr-placement-demo.story .cr-placement-demo-card,.cr-placement-demo.reels .cr-placement-demo-card,.cr-placement-demo.status .cr-placement-demo-card{display:none}
.cr-placement-demo.story .cr-placement-demo-ad,.cr-placement-demo.reels .cr-placement-demo-ad,.cr-placement-demo.status .cr-placement-demo-ad{display:block;height:100%;min-height:0;border:0!important;border-radius:0;box-shadow:none!important;background:#10151d!important}
.cr-placement-demo.story .cr-placement-demo-media,.cr-placement-demo.reels .cr-placement-demo-media,.cr-placement-demo.status .cr-placement-demo-media{inset:0;border-radius:0}
.cr-placement-demo.story .cr-placement-demo-ad::after,.cr-placement-demo.reels .cr-placement-demo-ad::after,.cr-placement-demo.status .cr-placement-demo-ad::after{top:auto;bottom:10px;right:10px}
.cr-placement-demo.column .cr-placement-demo-screen{background:#eef1f4}
.cr-placement-demo.column .cr-placement-demo-feed{right:90px;animation:crPlacementFeed 4.4s ease-in-out infinite}
.cr-placement-demo-column-ad{display:none}
.cr-placement-demo.column .cr-placement-demo-column-ad{display:block;position:absolute;right:6px;top:40px;width:78px;height:104px;border:2px solid #3b79f4;border-radius:7px;background:#fff;overflow:hidden}
.cr-placement-demo.column .cr-placement-demo-column-ad .cr-placement-demo-media{inset:18px 5px 5px}
.cr-placement-demo.column .cr-placement-demo-feed .cr-placement-demo-ad{display:none}
.cr-placement-demo.grid .cr-placement-demo-feed{display:grid;grid-template-columns:repeat(2,1fr);grid-auto-rows:82px;gap:7px;animation:crPlacementGrid 4s ease-in-out infinite}
.cr-placement-demo.grid .cr-placement-demo-card{height:auto}
.cr-placement-demo.grid .cr-placement-demo-ad{min-height:0}
.cr-placement-demo.grid .cr-placement-demo-media{inset:18px 5px 5px}
.cr-placement-demo.messages .cr-placement-demo-feed{animation:none}
.cr-placement-demo.messages .cr-placement-demo-card{height:38px;border-radius:7px}
.cr-placement-demo.messages .cr-placement-demo-ad{min-height:64px}
.cr-placement-demo.video .cr-placement-demo-feed{animation:crPlacementVideo 4s ease-in-out infinite}
.cr-placement-demo.video .cr-placement-demo-ad{min-height:148px}
.cr-placement-preview-foot{font-size:9px;line-height:1.35;color:#7f8998;margin-top:7px}
@keyframes crPlacementFeed{0%,12%{transform:translateY(0)}45%,65%{transform:translateY(-72px)}88%,100%{transform:translateY(0)}}
@keyframes crPlacementVertical{0%,18%{transform:scale(1)}48%,68%{transform:scale(1.035)}100%{transform:scale(1)}}
@keyframes crPlacementGrid{0%,15%{transform:translateY(0)}50%,70%{transform:translateY(-38px)}100%{transform:translateY(0)}}
@keyframes crPlacementVideo{0%,15%{transform:translateY(0)}48%,68%{transform:translateY(-35px)}100%{transform:translateY(0)}}
.cr-foot-actions{display:flex;gap:8px}
@media(max-width:820px){.cr-meta-context{grid-template-columns:1fr}.cr-placement-mode,.cr-placement-grid{grid-template-columns:1fr}.cr-audience-estimate{grid-template-columns:1fr}.cr-audience-estimate-state{text-align:left}.span-8,.span-6,.span-4,.span-3{grid-column:span 12}.cr-media-row{grid-template-columns:1fr}.cr-carousel-row{grid-template-columns:44px 1fr}.cr-carousel-row .form-control{grid-column:span 2}}
</style>
</head>
<body class="app-shell">
<?php include 'menu.php' ?>
<main class="app-main">
<div class="cr-page">
  <div class="cr-head">
    <h1 class="cr-title">Креативы</h1>
    <button id="newCreative" type="button" class="cr-btn cr-primary"><i class="fa-solid fa-plus mr-1"></i> ДОБАВИТЬ СВЯЗКУ</button>
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
    <h3 id="creativeEditorTitle">Новая связка</h3>
    <button id="closeCreative" class="cr-close" type="button">×</button>
  </div>

  <form id="creativeForm">
    <input id="creativeId" type="hidden">

    <div class="cr-tabs">
      <button type="button" class="cr-tab active" data-tab="campaign">Campaign</button>
      <button type="button" class="cr-tab" data-tab="adset">Ad Set</button>
      <button type="button" class="cr-tab" data-tab="audience">Audience</button>
      <button type="button" class="cr-tab" data-tab="placements">Placements</button>
      <button type="button" class="cr-tab" data-tab="creative">Creative</button>
      <button type="button" class="cr-tab" data-tab="tracking">Tracking</button>
      <button type="button" class="cr-tab" data-tab="advanced">Advanced</button>
    </div>

    <div class="cr-editor">
      <section class="cr-panel active" data-panel="campaign">
        <div class="cr-section-title">Campaign</div>
        <div class="cr-form-grid">
          <div class="span-4 cr-field"><label>Название связки</label><input id="presetName" class="form-control"></div>
          <div class="span-4 cr-field"><label>Campaign name</label><input id="mbCampaignName" class="form-control"></div>
          <div class="span-4 cr-field"><label>Objective</label><select id="mbObjective" class="form-control"><option value="">Загрузка из Meta SDK…</option></select></div>
          <div class="span-4 cr-field"><label>Buying type</label><select id="mbBuyingType" class="form-control"><option value="AUCTION">AUCTION</option><option value="RESERVED">RESERVED</option></select></div>
          <div class="span-4 cr-field"><label>Special ad category</label><select id="mbSpecialCategory" class="form-control"><option value="NONE">NONE</option></select></div>
          <div class="span-4 cr-field"><label>Campaign bid strategy</label><select id="mbCampaignBidStrategy" class="form-control"><option value="">Meta default</option></select></div>
          <div class="span-3 cr-field"><label>Daily budget</label><input id="mbCampaignDailyBudget" type="number" min="0" class="form-control"></div>
          <div class="span-3 cr-field"><label>Lifetime budget</label><input id="mbCampaignLifetimeBudget" type="number" min="0" class="form-control"></div>
          <div class="span-3 cr-field"><label>Spend cap</label><input id="mbCampaignSpendCap" type="number" min="0" class="form-control"></div>
          <div class="span-3 cr-field"><label>Status</label><select id="mbCampaignStatus" class="form-control"><option value="PAUSED">PAUSED</option></select></div>
          <div class="span-6 cr-field"><label>Start time</label><input id="mbCampaignStart" type="datetime-local" class="form-control"></div>
          <div class="span-6 cr-field"><label>Stop time</label><input id="mbCampaignStop" type="datetime-local" class="form-control"></div>
        </div>
      </section>

      <section class="cr-panel" data-panel="adset">
        <div class="cr-section-title">Ad Set</div>
        <div class="cr-form-grid">
          <div class="span-4 cr-field"><label>Ad Set name</label><input id="mbAdsetName" class="form-control"></div>
          <div class="span-4 cr-field"><label>Optimization goal</label><select id="mbOptimizationGoal" class="form-control"><option value="">Загрузка из Meta SDK…</option></select></div>
          <div class="span-4 cr-field"><label>Billing event</label><select id="mbBillingEvent" class="form-control"><option value="">Загрузка из Meta SDK…</option></select></div>
          <div class="span-4 cr-field"><label>Bid strategy</label><select id="mbAdsetBidStrategy" class="form-control"><option value="">Meta default</option></select></div>
          <div class="span-4 cr-field"><label>Bid amount</label><input id="mbBidAmount" type="number" min="0" class="form-control"></div>
          <div class="span-4 cr-field"><label>Destination type</label><select id="mbDestinationType" class="form-control"><option value="">Meta default</option></select></div>
          <div class="span-3 cr-field"><label>Daily budget</label><input id="mbAdsetDailyBudget" type="number" min="0" class="form-control"></div>
          <div class="span-3 cr-field"><label>Lifetime budget</label><input id="mbAdsetLifetimeBudget" type="number" min="0" class="form-control"></div>
          <div class="span-3 cr-field"><label>Start time</label><input id="mbAdsetStart" type="datetime-local" class="form-control"></div>
          <div class="span-3 cr-field"><label>End time</label><input id="mbAdsetEnd" type="datetime-local" class="form-control"></div>
          <div class="span-6 cr-field"><label>Conversion event</label><input id="mbConversionEvent" class="form-control" list="mbConversionEventOptions" placeholder="LEAD / PURCHASE / ..."><datalist id="mbConversionEventOptions"></datalist></div>
          <div class="span-6 cr-field"><label>Status</label><select id="mbAdsetStatus" class="form-control"><option value="PAUSED">PAUSED</option></select></div>
          <div class="span-6 cr-field"><label>Attribution spec (JSON)</label><textarea id="mbAttributionSpec" class="form-control cr-json" placeholder='[{"event_type":"CLICK_THROUGH","window_days":7}]'></textarea></div>
          <div class="span-6 cr-field"><label>Promoted object extra (JSON)</label><textarea id="mbPromotedObject" class="form-control cr-json" placeholder='{"application_id":"..."}'></textarea></div>
          <div class="span-12 cr-checkrow"><label class="cr-check"><input id="mbDynamicCreative" type="checkbox"> Dynamic creative</label><label class="cr-check"><input id="mbIncrementalAttribution" type="checkbox"> Incremental attribution</label></div>
        </div>
      </section>

      <section class="cr-panel" data-panel="audience">
        <div class="cr-section-title">Audience / Targeting</div>
        <div class="cr-form-grid">
          <div class="span-3 cr-field"><label>Age min</label><input id="mbAgeMin" type="number" min="13" max="65" class="form-control" value="18"></div>
          <div class="span-3 cr-field"><label>Age max</label><input id="mbAgeMax" type="number" min="13" max="65" class="form-control" value="65"></div>
          <div class="span-3 cr-field"><label>Gender</label><select id="mbGender" class="form-control"><option value="">All</option><option value="1">Male</option><option value="2">Female</option></select></div>
          <div class="span-6 cr-target-box" data-targeting data-remask-languages="1">
            <label>Языки аудитории</label>
            <div class="cr-target-input">
              <input id="mbLanguageSearch" class="form-control" autocomplete="off" placeholder="Начни вводить язык — от 2 символов">
              <div id="mbLanguageResults" class="cr-target-results"></div>
            </div>
            <div id="mbLanguagePills" class="cr-target-pills"></div>
            <input id="mbLocales" type="hidden" value="">
            <div class="cr-hint">Показывать рекламу людям, использующим выбранные языки. Список загружается из Meta.</div>
          </div>
          <div class="span-6 cr-target-box" data-targeting>
            <label>GEO</label>
            <div class="cr-target-input">
              <input id="mbGeoSearch" class="form-control" autocomplete="off" placeholder="Страна, регион или город — от 2 символов">
              <div id="mbGeoResults" class="cr-target-results"></div>
            </div>
            <div id="mbGeoPills" class="cr-target-pills"></div>
          </div>
          <div class="span-6 cr-target-box" data-targeting>
            <label>Excluded GEO</label>
            <div class="cr-target-input">
              <input id="mbExcludedGeoSearch" class="form-control" autocomplete="off" placeholder="Исключить страну, регион или город">
              <div id="mbExcludedGeoResults" class="cr-target-results"></div>
            </div>
            <div id="mbExcludedGeoPills" class="cr-target-pills"></div>
          </div>
          <div class="span-6 cr-target-box" data-targeting>
            <label>Interests</label>
            <div class="cr-target-input">
              <input id="mbInterestSearch" class="form-control" autocomplete="off" placeholder="Начни вводить интерес — от 2 символов">
              <div id="mbInterestResults" class="cr-target-results"></div>
            </div>
            <div id="mbInterestPills" class="cr-target-pills"></div>
          </div>
          <div class="span-6 cr-target-box" data-targeting>
            <label>Behaviors</label>
            <div class="cr-target-input">
              <input id="mbBehaviorSearch" class="form-control" autocomplete="off" placeholder="Начни вводить поведение — от 2 символов">
              <div id="mbBehaviorResults" class="cr-target-results"></div>
            </div>
            <div id="mbBehaviorPills" class="cr-target-pills"></div>
          </div>
          <div class="span-12 cr-muted">Подсказки загружаются из Meta автоматически. FB-профиль/RK в этом разделе выбирать не нужно.</div>
          <details class="cr-raw-targeting">
            <summary>Расширенный Targeting JSON (официальные Meta-поля)</summary>
            <div class="cr-form-grid">
              <div class="span-6 cr-field"><label>Geo locations</label><textarea id="mbGeo" class="form-control cr-json" placeholder='{"countries":["UA"]}'></textarea></div>
              <div class="span-6 cr-field"><label>Excluded geo</label><textarea id="mbExcludedGeo" class="form-control cr-json" placeholder='{"countries":["RU"]}'></textarea></div>
              <div class="span-6 cr-field"><label>Interests</label><textarea id="mbInterests" class="form-control cr-json" placeholder='[{"id":"6003139266461","name":"Business"}]'></textarea></div>
              <div class="span-6 cr-field"><label>Behaviors</label><textarea id="mbBehaviors" class="form-control cr-json" placeholder='[{"id":"...","name":"..."}]'></textarea></div>
              <div class="span-6 cr-field"><label>Flexible spec</label><textarea id="mbFlexibleSpec" class="form-control cr-json"></textarea></div>
              <div class="span-6 cr-field"><label>Exclusions</label><textarea id="mbExclusions" class="form-control cr-json"></textarea></div>
            </div>
          </details>
        </div>
      </section>

      <section class="cr-panel" data-panel="placements">
        <div class="cr-section-title">Placements / Devices</div>

        <div class="cr-placement-mode">
          <label class="cr-placement-mode-option">
            <input type="radio" name="placementMode" value="auto" checked>
            <span><b>Advantage+ placements</b><small>Meta сама выбирает все доступные места показа для текущей воронки.</small></span>
          </label>
          <label class="cr-placement-mode-option">
            <input type="radio" name="placementMode" value="manual">
            <span><b>Manual placements</b><small>Ручной выбор платформ и конкретных мест показа как в Ads Manager.</small></span>
          </label>
        </div>

        <div id="manualPlacements" class="cr-placement-manual" style="display:none">
          <div class="cr-placement-toolbar">
            <div>
              <div class="cr-section-title" style="margin:0">Платформы и места показа</div>
              <div id="placementCapabilitiesStatus" class="cr-hint cr-placement-live">Official Meta SDK placements</div>
            </div>
          </div>

          <div id="placementPlatformGrid" class="cr-placement-grid">
            <div class="cr-placement-card" data-placement-card="facebook">
              <div class="cr-placement-card-head"><label class="cr-check"><input type="checkbox" data-publisher="facebook"> Facebook</label><label class="cr-check cr-placement-all"><input type="checkbox" data-position-all="facebook_positions" checked> Все доступные</label></div>
              <div id="placementFacebookOptions" class="cr-placement-options" data-position-container="facebook_positions"></div>
            </div>
            <div class="cr-placement-card" data-placement-card="instagram">
              <div class="cr-placement-card-head"><label class="cr-check"><input type="checkbox" data-publisher="instagram"> Instagram</label><label class="cr-check cr-placement-all"><input type="checkbox" data-position-all="instagram_positions" checked> Все доступные</label></div>
              <div id="placementInstagramOptions" class="cr-placement-options" data-position-container="instagram_positions"></div>
            </div>
            <div class="cr-placement-card" data-placement-card="messenger">
              <div class="cr-placement-card-head"><label class="cr-check"><input type="checkbox" data-publisher="messenger"> Messenger</label><label class="cr-check cr-placement-all"><input type="checkbox" data-position-all="messenger_positions" checked> Все доступные</label></div>
              <div id="placementMessengerOptions" class="cr-placement-options" data-position-container="messenger_positions"></div>
            </div>
            <div class="cr-placement-card" data-placement-card="audience_network">
              <div class="cr-placement-card-head"><label class="cr-check"><input type="checkbox" data-publisher="audience_network"> Audience Network</label><label class="cr-check cr-placement-all"><input type="checkbox" data-position-all="audience_network_positions" checked> Все доступные</label></div>
              <div id="placementAudienceNetworkOptions" class="cr-placement-options" data-position-container="audience_network_positions"></div>
            </div>
            <div class="cr-placement-card" data-placement-card="threads">
              <div class="cr-placement-card-head"><label class="cr-check"><input type="checkbox" data-publisher="threads"> Threads</label><label class="cr-check cr-placement-all"><input type="checkbox" data-position-all="threads_positions" checked> Все доступные</label></div>
              <div id="placementThreadsOptions" class="cr-placement-options" data-position-container="threads_positions"></div>
            </div>
            <div class="cr-placement-card" data-placement-card="whatsapp">
              <div class="cr-placement-card-head"><label class="cr-check"><input type="checkbox" data-publisher="whatsapp"> WhatsApp</label><label class="cr-check cr-placement-all"><input type="checkbox" data-position-all="whatsapp_positions" checked> Все доступные</label></div>
              <div id="placementWhatsappOptions" class="cr-placement-options" data-position-container="whatsapp_positions"></div>
            </div>
          </div>

          <div id="placementDevices" class="cr-placement-devices">
            <div class="cr-section-title" style="margin:0 0 8px">Devices</div>
            <div id="placementDeviceOptions" class="cr-placement-options cr-placement-options-inline" data-device-container></div>
          </div>

          <div id="placementHoverPreview" class="cr-placement-hover-preview" aria-hidden="true"></div>
        </div>

        <details class="cr-raw-targeting" style="margin-top:12px">
          <summary>Дополнительное device targeting</summary>
          <div class="cr-form-grid">
            <div class="span-6 cr-field"><label>User OS</label><input id="mbUserOs" class="form-control" placeholder="Android,iOS"></div>
            <div class="span-6 cr-field"><label>User devices</label><input id="mbUserDevice" class="form-control" placeholder="Galaxy S24,iPhone"></div>
          </div>
        </details>
      </section>

      <section class="cr-panel" data-panel="creative">
        <div class="cr-section-title">Creative / Ad</div>
        <div class="cr-form-grid">
          <div class="span-4 cr-field"><label>Creative name</label><input id="presetCreativeName" class="form-control"></div>
          <div class="span-4 cr-field"><label>Ad name</label><input id="presetAdName" class="form-control"></div>
          <div class="span-4 cr-field"><label>Ad status</label><select id="mbAdStatus" class="form-control"><option value="PAUSED">PAUSED</option></select></div>
          <div class="span-12 cr-field"><label>Primary text</label><textarea id="presetMessage" class="form-control"></textarea></div>
          <div class="span-4 cr-field"><label>Headline</label><input id="presetHeadline" class="form-control"></div>
          <div class="span-4 cr-field"><label>Description</label><input id="presetDescription" class="form-control"></div>
          <div class="span-4 cr-field"><label>CTA</label><select id="presetCta" class="form-control"><option value="LEARN_MORE">LEARN_MORE</option></select></div>
          <div class="span-8 cr-field"><label>Destination URL</label><input id="presetUrl" class="form-control" placeholder="https://..."></div>
          <div class="span-4 cr-field"><label>URL tags / UTM</label><input id="presetTags" class="form-control"></div>
          <div class="span-4 cr-field"><label>Creative format</label><select id="presetFormat" class="form-control"><option value="SINGLE">Single image / video</option><option value="CAROUSEL">Carousel (2–10 images)</option></select></div>
        </div>

        <div id="singleSection" class="cr-media-box">
          <div class="cr-media-row">
            <div id="singlePreview" class="cr-preview-box"><i class="fa-regular fa-image"></i></div>
            <div class="cr-upload-panel">
              <label class="cr-file-btn">ВЫБРАТЬ IMAGE / VIDEO<input id="presetMedia" type="file" accept="image/*,video/*"></label>
              <div id="singleCurrent" class="cr-hint">Изображение или видео.</div>
            </div>
          </div>
        </div>
        <div id="carouselSection" class="cr-media-box" style="display:none"><label class="cr-file-btn" style="max-width:300px">ВЫБРАТЬ 2–10 ИЗОБРАЖЕНИЙ<input id="presetCarousel" type="file" accept="image/*" multiple></label><div id="carouselHint" class="cr-hint"></div><div id="carouselRows" class="cr-carousel-list"></div></div>
      </section>

      <section class="cr-panel" data-panel="tracking">
        <div class="cr-section-title">Tracking / Creative enhancements</div>
        <div class="cr-form-grid">
          <div class="span-6 cr-field"><label>Conversion domain</label><input id="mbConversionDomain" class="form-control"></div>
          <div class="span-6 cr-field"><label>Ad priority</label><input id="mbAdPriority" type="number" min="0" class="form-control"></div>
          <div class="span-6 cr-field"><label>Tracking specs (JSON)</label><textarea id="mbTrackingSpecs" class="form-control cr-json"></textarea></div>
          <div class="span-6 cr-field"><label>Degrees of freedom / Advantage+ creative (JSON)</label><textarea id="mbDegreesOfFreedom" class="form-control cr-json" placeholder='{"creative_features_spec":{...}}'></textarea></div>
          <div class="span-6 cr-field"><label>Asset feed spec (JSON)</label><textarea id="mbAssetFeedSpec" class="form-control cr-json"></textarea></div>
          <div class="span-6 cr-field"><label>Platform customizations (JSON)</label><textarea id="mbPlatformCustomizations" class="form-control cr-json"></textarea></div>
        </div>
      </section>

      <section class="cr-panel" data-panel="advanced">
        <div class="cr-section-title">Все официальные поля Meta API</div>
        <div class="cr-muted mb-3">Поля ниже строятся из актуальной SDK-схемы ReMask. Простые значения вводятся напрямую, сложные Object / map / list — JSON.</div>
        <div class="cr-toolbar" style="margin:0 0 12px">
          <input id="metaFieldSearch" class="cr-search" placeholder="Поиск поля Meta..." style="max-width:420px">
          <span id="metaSchemaStatus" class="cr-count">Загрузка SDK-схемы…</span>
        </div>
        <div id="metaSdkFields" class="cr-sdk-groups"></div>

        <details class="cr-raw-json">
          <summary>Raw JSON override</summary>
          <div class="cr-muted mb-2">Нужен только если удобнее вставить готовый объект целиком. Значения из обычных контролов имеют приоритет.</div>
          <div class="cr-form-grid">
            <div class="span-6 cr-field"><label>Campaign params (JSON)</label><textarea id="mbAdvancedCampaign" class="form-control cr-json" placeholder="{}"></textarea></div>
            <div class="span-6 cr-field"><label>Ad Set params (JSON)</label><textarea id="mbAdvancedAdset" class="form-control cr-json" placeholder="{}"></textarea></div>
            <div class="span-6 cr-field"><label>Targeting params (JSON)</label><textarea id="mbAdvancedTargeting" class="form-control cr-json" placeholder="{}"></textarea></div>
            <div class="span-6 cr-field"><label>Creative params (JSON)</label><textarea id="mbAdvancedCreative" class="form-control cr-json" placeholder="{}"></textarea></div>
            <div class="span-6 cr-field"><label>Ad params (JSON)</label><textarea id="mbAdvancedAd" class="form-control cr-json" placeholder="{}"></textarea></div>
          </div>
        </details>
      </section>
    </div>

    <div class="cr-modal-foot">
      <div id="creativeStatus" class="cr-status"></div>
      <div class="cr-foot-actions"><button id="cancelCreative" type="button" class="cr-btn">ОТМЕНА</button><button id="saveCreative" type="submit" class="cr-btn cr-primary">СОХРАНИТЬ СВЯЗКУ</button></div>
    </div>
  </form>
</div>
</div>

<script src="scripts/creatives.js?v=20260921-live-targeting-v109" type="module"></script>
<div class="app-footer"><?php include 'copyright.php' ?></div>
</main>
</body>
</html>
