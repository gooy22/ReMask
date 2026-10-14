/* ReMask workflow presentation. No requests, serializer changes or auto-submits. */
(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const groups = {campaign:'Кампания', adset:'Группа объявлений', audience:'Аудитория', targeting:'Аудитория', placements:'Места показа', creative:'Объявление', tracking:'Отслеживание', advanced:'Дополнительно', ad:'Объявления'};
    const fieldNames = {
        name:'Название', objective:'Цель рекламы', buying_type:'Способ покупки', special_ad_categories:'Особые категории', bid_strategy:'Стратегия ставок',
        daily_budget:'Дневной бюджет', lifetime_budget:'Бюджет на весь срок', spend_cap:'Лимит расходов', status:'Статус', start_time:'Начало показа', stop_time:'Окончание показа', end_time:'Окончание показа',
        optimization_goal:'Оптимизация показа', billing_event:'За что начисляется оплата', bid_amount:'Предел ставки / цены', destination_type:'Место назначения',
        attribution_spec:'Окна атрибуции', promoted_object:'Объект конверсии', is_dynamic_creative:'Динамические креативы', is_incremental_attribution_enabled:'Инкрементальная атрибуция',
        age_min:'Возраст от', age_max:'Возраст до', genders:'Пол', geo_locations:'География', excluded_geo_locations:'Исключённая география', interests:'Интересы', behaviors:'Поведение',
        locales:'Языки', custom_audiences:'Включить свои аудитории', excluded_custom_audiences:'Исключить свои аудитории', flexible_spec:'Комбинации условий', exclusions:'Исключения',
        education_schools:'Учебные заведения', education_majors:'Специальности', education_statuses:'Уровень образования', college_years:'Годы обучения', work_employers:'Работодатели', work_positions:'Должности', relationship_statuses:'Семейное положение', life_events:'События жизни', family_statuses:'Родители', industries:'Отрасли работы', home_ownership:'Владение жильём', home_type:'Тип жилья', home_value:'Стоимость жилья', wireless_carrier:'Сеть и подключение', generation:'Поколение', household_composition:'Состав семьи', office_type:'Тип офиса', location_categories:'Категории мест', excluded_user_device:'Исключённые устройства', app_install_state:'Установка приложения', optimization_sub_event:'Дополнительная оптимизация', applink_treatment:'Открытие ссылки приложения',
        user_os:'Операционные системы', user_device:'Модели устройств', publisher_platforms:'Платформы', device_platforms:'Типы устройств',
        message:'Основной текст', title:'Заголовок', body:'Текст объявления', description:'Описание', link_url:'Ссылка', url_tags:'UTM и параметры ссылки',
        conversion_domain:'Домен конверсии', priority:'Приоритет', tracking_specs:'Правила отслеживания', degrees_of_freedom_spec:'Улучшения креатива', asset_feed_spec:'Набор вариантов креатива', platform_customizations:'Настройки для платформ',
        object_story_spec:'Содержимое объявления', object_story_id:'ID существующего поста', instagram_actor_id:'ID Instagram', source_instagram_media_id:'ID поста Instagram',
        targeting_automation:'Автоматическое расширение аудитории', brand_safety_content_filter_levels:'Безопасность бренда', contextual_bundling_spec:'Контекстные ограничения',
        adlabels:'Метки', execution_options:'Параметры выполнения', ad_schedule:'Расписание показов', pacing_type:'Темп расходования', frequency_control_specs:'Ограничение частоты',
        campaign_id:'ID кампании', adset_id:'ID группы', creative:'Креатив', pixel_id:'ID пикселя', application_id:'ID приложения', product_set_id:'ID набора товаров',
        destination_url:'Ссылка назначения', call_to_action:'Кнопка действия', image_hash:'Хеш изображения', video_id:'ID видео', page_id:'ID страницы'
    };
    const options = {
        OUTCOME_TRAFFIC:'Трафик', OUTCOME_SALES:'Продажи', OUTCOME_LEADS:'Лиды', OUTCOME_ENGAGEMENT:'Вовлечённость', OUTCOME_AWARENESS:'Узнаваемость', OUTCOME_APP_PROMOTION:'Продвижение приложения',
        AUCTION:'Аукцион', RESERVED:'Резервирование', NONE:'Нет', CREDIT:'Кредитование', EMPLOYMENT:'Трудоустройство', HOUSING:'Жильё', ISSUES_ELECTIONS_POLITICS:'Социальные вопросы и политика',
        PAUSED:'На паузе', ACTIVE:'Активно', LEARN_MORE:'Подробнее', SIGN_UP:'Зарегистрироваться', APPLY_NOW:'Подать заявку', CONTACT_US:'Связаться', SHOP_NOW:'Купить', GET_OFFER:'Получить предложение',
        LOWEST_COST_WITHOUT_CAP:'Максимум результатов', LOWEST_COST_WITH_BID_CAP:'Предел ставки', COST_CAP:'Целевая цена результата', LOWEST_COST_WITH_MIN_ROAS:'Минимальная рентабельность',
        LANDING_PAGE_VIEWS:'Просмотры целевой страницы', LINK_CLICKS:'Клики по ссылке', IMPRESSIONS:'Показы', REACH:'Охват', OFFSITE_CONVERSIONS:'Конверсии на сайте', VALUE:'Ценность конверсий',
        WEBSITE:'Сайт', APP:'Приложение', ON_AD:'Внутри объявления', SINGLE:'Одно изображение / видео', CAROUSEL:'Карусель', INSTAGRAM_POST:'Пост / Reels из Instagram',
        true:'Включено', false:'Выключено', ADSET:'На уровне группы', CAMPAIGN:'На уровне кампании',
        AUTO:'Автоматически (Advantage+)', AUTOMATIC:'Автоматически', MANUAL:'Вручную', ARCHIVED:'В архиве', DELETED:'Удалено',
        APP_INSTALLS:'Установки приложения', BRAND_AWARENESS:'Узнаваемость бренда', CONVERSIONS:'Конверсии', EVENT_RESPONSES:'Ответы на мероприятия', LEAD_GENERATION:'Получение лидов', LOCAL_AWARENESS:'Местная аудитория', MESSAGES:'Сообщения', OFFER_CLAIMS:'Получение предложений', PAGE_LIKES:'Отметки «Нравится» страницы', POST_ENGAGEMENT:'Взаимодействия с публикацией', PRODUCT_CATALOG_SALES:'Продажи из каталога', STORE_VISITS:'Посещения магазинов', VIDEO_VIEWS:'Просмотры видео', FINANCIAL_PRODUCTS_SERVICES:'Финансовые продукты и услуги', ONLINE_GAMBLING_AND_GAMING:'Азартные игры и игровые сервисы'
    };
    const labels = {
        presetName:'Название связки', mbCampaignName:'Название кампании', mbObjective:'Цель рекламы', mbBuyingType:'Способ покупки', mbSpecialCategory:'Особая категория', mbCampaignBidStrategy:'Стратегия ставок',
        mbCampaignDailyBudget:'Дневной бюджет', mbCampaignLifetimeBudget:'Бюджет на весь срок', mbCampaignSpendCap:'Лимит расходов', mbCampaignStatus:'Статус кампании', mbCampaignStart:'Начало показа', mbCampaignStop:'Окончание показа',
        mbAdsetName:'Название группы', mbOptimizationGoal:'Оптимизация показа', mbBillingEvent:'За что начисляется оплата', mbAdsetBidStrategy:'Стратегия ставок', mbBidAmount:'Предел ставки / цены', mbDestinationType:'Место назначения',
        mbAdsetDailyBudget:'Дневной бюджет', mbAdsetLifetimeBudget:'Бюджет на весь срок', mbAdsetStart:'Начало показа', mbAdsetEnd:'Окончание показа', mbConversionEvent:'Событие конверсии', mbAdsetStatus:'Статус группы',
        mbAttributionSpec:'Окна атрибуции', mbPromotedObject:'Дополнительные параметры конверсии', mbAgeMin:'Возраст от', mbAgeMax:'Возраст до', mbGender:'Пол', mbGeoSearch:'География', mbExcludedGeoSearch:'Исключить географию', mbInterestSearch:'Интересы', mbBehaviorSearch:'Поведение', mbDynamicCreative:'Динамические креативы', mbIncrementalAttribution:'Инкрементальная атрибуция', presetMedia:'Выбрать изображение или видео', presetCarousel:'Выбрать изображения для карусели',
        presetCreativeName:'Название креатива', presetAdName:'Название объявления', mbAdStatus:'Статус объявления', presetMessage:'Основной текст', presetHeadline:'Заголовок', presetDescription:'Описание', presetCta:'Кнопка действия', presetUrl:'Ссылка назначения', presetTags:'UTM и параметры ссылки', presetFormat:'Формат объявления',
        mbConversionDomain:'Домен конверсии', mbAdPriority:'Приоритет', mbTrackingSpecs:'Правила отслеживания', mbDegreesOfFreedom:'Улучшения креатива', mbAssetFeedSpec:'Варианты креатива', mbPlatformCustomizations:'Настройки платформ',
        profile:'Профили Facebook', adAccount:'Рекламные кабинеты', campaignName:'Название кампании', objective:'Цель рекламы', specialCategory:'Особая категория', adsetName:'Название группы',
        budgetLevel:'Где задаётся бюджет', dailyBudget:'Дневной бюджет', optimizationGoal:'Оптимизация показа', billingEvent:'За что начисляется оплата', conversionLocation:'Место конверсии', bidStrategy:'Стратегия ставок', bidAmount:'Предел ставки / цены', placementMode:'Места показа', startTime:'Начало показа', endTime:'Окончание показа',
        ageMin:'Возраст от', ageMax:'Возраст до', pixel:'Пиксель', conversionEvent:'Событие конверсии', geoQuery:'География', interestQuery:'Интересы', behaviorQuery:'Поведение', customAudiences:'Включить свои аудитории', excludedCustomAudiences:'Исключить свои аудитории',
        page:'Страница Facebook', instagram:'Аккаунт Instagram', creativeName:'Название креатива', adName:'Название объявления', message:'Основной текст', headline:'Заголовок', description:'Описание', destinationUrl:'Ссылка назначения', cta:'Кнопка действия', urlTags:'UTM и параметры ссылки', creativeFormat:'Формат объявления', media:'Изображение или видео', bundleName:'Название настроек', bundleSelect:'Сохранённые настройки'
    };
    const tips = {
        presetName:'Связка сохраняет настройки рекламы и креатив для повторного использования.',
        mbCampaignName:'Название, которое будет видно в Ads Manager. Не меняет название связки.',
        mbCampaignDailyBudget:'В минимальных единицах валюты РК: 500 = 5,00 EUR / USD. Для валют без дробной части масштаб другой.',
        mbCampaignLifetimeBudget:'На весь срок показа, в минимальных единицах валюты РК. Выберите один тип бюджета.',
        mbCampaignSpendCap:'Общий лимит расходов кампании в минимальных единицах валюты РК.',
        mbAdsetDailyBudget:'В минимальных единицах валюты РК. Задайте бюджет на уровне кампании или группы.',
        mbAdsetLifetimeBudget:'На весь срок, в минимальных единицах валюты РК. Не заполняйте вместе с дневным.',
        dailyBudget:'Минимальные единицы валюты выбранного РК: 500 = 5,00 EUR / USD. Масштаб зависит от валюты; при нескольких РК проверьте валюту каждого.',
        mbBidAmount:'Минимальные единицы валюты РК. Применяется к выбранной стратегии ставок.', bidAmount:'Минимальные единицы валюты РК. Доступность зависит от стратегии ставок.',
        presetMessage:'Текст над изображением или видео.', presetHeadline:'Короткий заголовок под креативом.', presetUrl:'Адрес страницы, на которую ведёт объявление.',
        mbCampaignStatus:'Публикация и активация проходят отдельную проверку. Сохранение связки рекламу не запускает.',
        mbAttributionSpec:'Настройка того, какие клики и просмотры учитываются в конверсиях.',
        mbPromotedObject:'Дополнительные параметры объекта конверсии. Пиксель выбранного РК задаётся в автозаливе.'
    };
    const style = document.createElement('style');
    style.textContent = `
    :root{--wf-line:#344151;--wf-muted:#9aa8ba;--wf-accent:#83b2ff}
    .wf-help{display:block;color:var(--wf-muted);font-size:11px;line-height:1.5;margin-top:7px}
    .wf-block{border:1px solid var(--wf-line);border-radius:12px;padding:18px;background:#1b222d;margin:0 0 16px}
    .wf-block>h4{font-size:14px;color:#f1f5fb;font-weight:700;margin:0 0 5px}.wf-block>p{font-size:12px;color:var(--wf-muted);margin:0 0 18px;line-height:1.5}
    .wf-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.wf-fields>.cr-field{grid-column:auto}.wf-fields>.wf-wide{grid-column:1/-1}
    .wf-extra>summary{cursor:pointer;color:#c5d3e6;font-size:13px;padding:12px 0}.wf-extra{border-top:1px solid var(--wf-line)}
    .wf-extra>.cr-form-grid{padding:8px 0 16px}.wf-extra .cr-form-grid{gap:16px}
    #creativeModal.cr-backdrop{z-index:2000}
    #creativeModal .cr-modal{width:min(1180px,calc(100vw - 32px));max-height:calc(100dvh - 32px);border-radius:16px}
    #creativeModal .cr-modal-head{padding:20px 24px}#creativeModal .cr-modal-head h3{font-size:20px}
    #creativeForm{display:grid;grid-template-columns:190px minmax(0,1fr) 235px;grid-template-rows:minmax(0,1fr) auto;flex:1;min-height:0;overflow:hidden}
    #creativeForm>.cr-tabs{grid-column:1;grid-row:1;display:flex;flex-direction:column;padding:16px 12px;overflow:auto;gap:5px;border-right:1px solid var(--wf-line);border-bottom:0;background:#181e28}
    #creativeForm .cr-tab{white-space:normal;text-align:left;padding:12px 14px;font-size:13px;color:#a5b4c8}
    #creativeForm .cr-tab.active{background:#263d61;color:#e9f2ff;box-shadow:inset 3px 0 #7daeff}
    #creativeForm>.cr-editor{grid-column:2;grid-row:1;padding:24px;background:#151b24;overflow:auto}
    #creativeForm>.cr-modal-foot{grid-column:1/-1;grid-row:2;padding:15px 24px}
    #creativeForm .cr-section-title{font-size:19px;margin:0 0 6px;color:#f0f4fa}
    #creativeForm .cr-editor .form-control{height:43px!important;background:#111923!important;border-color:#3d4c61!important;font-size:13px!important}
    #creativeForm .cr-editor textarea.form-control{height:94px!important;min-height:94px!important}
    #creativeForm .cr-field label{font-size:12px!important;color:#c9d4e4!important}
    .wf-panel-description{color:var(--wf-muted);font-size:12px;line-height:1.6;margin-bottom:22px}
    .wf-summary{grid-column:3;grid-row:1;border-left:1px solid var(--wf-line);padding:24px 18px;background:#181e28;overflow:auto;color:#e1e9f6}
    .wf-summary h4{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#9dafc7;margin:0 0 18px}
    .wf-summary dl{margin:0}.wf-summary dt{font-size:11px;color:#91a2ba;margin-top:16px}.wf-summary dd{font-size:13px;margin:5px 0 0;overflow-wrap:anywhere;line-height:1.5}
    .wf-note{border:1px solid #354965;background:#202d42;color:#a9c0df;border-radius:9px;padding:12px;font-size:11px;line-height:1.6;margin-top:24px}
    .wf-ad-copy{border:1px solid #3b485c;border-radius:10px;overflow:hidden;margin-top:18px;background:#111923}.wf-ad-copy p{padding:12px;margin:0;white-space:pre-wrap;font-size:12px;line-height:1.55}.wf-ad-copy strong{display:block;padding:12px;border-top:1px solid #344151;font-size:13px}.wf-ad-copy span{display:block;padding:0 12px 12px;font-size:11px;color:#b6c5dc}
    .launch-wrap.wf-launch{max-width:1280px}.wf-launch>.launch-card{border:1px solid var(--wf-line);border-radius:14px;padding:24px;margin-bottom:20px;background:#1a212c;scroll-margin-top:85px}
    .wf-launch h5{font-size:18px;font-weight:700;color:#eef4ff;margin-bottom:22px}
    main:has(.wf-launch){overflow:visible}
    .wf-launch label{display:block!important;margin:0 0 7px!important;font-size:12px!important;font-weight:600;color:#bccbdd;line-height:1.5}
    .wf-launch label:has(input[type=checkbox],input[type=radio]){display:flex!important;align-items:center;gap:8px}
    .wf-launch .form-control:not(.rm-meta-visual-source):not(.wf-profile-native):not([type=hidden]):not([hidden]){display:block!important;width:100%!important;box-sizing:border-box;min-width:0;min-height:42px;border-radius:8px!important;margin:0!important}
    .wf-launch .row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-left:0;margin-right:0}
    .wf-launch .row>[class*="col-"]{width:auto;max-width:none;min-width:0;flex:none;padding:0;grid-column:auto}
    .wf-launch .row>.col-md-12,.wf-launch .row>.col-12{grid-column:1/-1}
    .wf-launch .row>.wf-account-selector{grid-column:span 2}
    .wf-launch .row+.row{margin-top:20px!important}.wf-launch textarea{min-height:110px}
    .wf-launch small.muted{display:block;font-size:11px;margin-top:6px}.wf-launch .table-responsive{border:1px solid #344151;border-radius:10px;margin-top:15px}
    .wf-launch td,.wf-launch th{padding:12px!important;vertical-align:middle}.wf-launch .wf-section-heading{font-size:13px;color:#87b4f8;border-top:1px solid #344151;padding-top:20px;margin:24px 0 15px}
    .wf-launch>.launch-card{scroll-margin-top:140px}
    .wf-launch .wf-step-nav{position:sticky;top:64px;z-index:80;display:flex;gap:6px;padding:10px;background:#151b24ee;border:1px solid #344151;border-radius:12px;margin:20px 0;backdrop-filter:blur(12px);overflow:auto}
    .wf-step-nav a{flex:1;min-width:max-content;padding:10px 14px;border-radius:7px;font-size:12px;color:#b5c6dd;text-decoration:none}.wf-step-nav a:hover,.wf-step-nav a:focus{background:#263d61;color:#fff}
    .wf-profile-grid{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}.wf-profile-grid label{display:flex;align-items:center;gap:9px;border:1px solid #3a4a60;border-radius:9px;padding:10px 14px;background:#111923;cursor:pointer}
    .wf-profile-grid label:has(input:checked){background:#243e65;border-color:#85b5ff;color:#fff}.wf-profile-grid input{accent-color:#8db9ff;width:16px;height:16px}
    .wf-launch .wf-profile-native{display:none!important}.wf-launch .wf-profile-grid label{margin:0!important}
    #launchMetaAllFields.wf-advanced{max-width:100%;margin:0 0 20px;padding:22px;background:#1a212c;border:1px solid #344151;border-radius:14px}
    #launchMetaAllFields h3{font-size:18px;color:#e6effc}#launchMetaAllFields .rm-meta-note{font-size:12px;line-height:1.6}#launchMetaAllFields .rm-meta-grid{gap:18px;padding:6px 18px 18px}
    #launchMetaAllFields .rm-meta-field label,.cr-sdk-field label{align-items:center!important}.rm-meta-type,.cr-sdk-type{display:none!important}
    .wf-code{font-size:10px;color:#778aa6;margin-top:6px;overflow-wrap:anywhere}.wf-launch .rm-meta-search{max-width:420px}
    .wf-launch .btn,.cr-foot-actions .btn{border-radius:8px;padding:10px 16px;font-weight:600}
    @media(max-width:1050px){#creativeForm{grid-template-columns:170px minmax(0,1fr)}.wf-summary{display:none}}
    @media(max-width:1000px){.wf-launch .row{grid-template-columns:repeat(2,minmax(0,1fr))}.wf-launch .row>.wf-account-selector{grid-column:1/-1}}
    @media(max-width:700px){#creativeModal .cr-modal{width:calc(100vw - 12px);max-height:calc(100dvh - 12px)}#creativeForm{display:flex;flex-direction:column}#creativeForm>.cr-tabs{flex-direction:row;flex-shrink:0;border-right:0;border-bottom:1px solid var(--wf-line);padding:8px}.cr-tab{white-space:nowrap!important}#creativeForm>.cr-editor{padding:16px}.wf-fields{grid-template-columns:1fr}.wf-fields>.wf-wide{grid-column:auto}.cr-form-grid>.cr-field,.cr-form-grid>.cr-target-box{grid-column:span 12}.wf-launch>.launch-card{padding:18px}.wf-launch .row{grid-template-columns:1fr}.wf-launch .row>*{grid-column:1/-1!important}.wf-step-nav a{padding:9px}.cr-modal-foot{flex-wrap:wrap}#launchMetaAllFields .rm-meta-search{min-width:0;width:100%}.cr-carousel-row{grid-template-columns:48px minmax(0,1fr)}}
    @media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
    `;
    document.head.appendChild(style);

    function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }
    function labelFor(el) {
        return document.querySelector('label[for="'+el.id+'"]') || el.closest('label') || el.closest('.cr-field,.cr-target-box')?.querySelector('label') || Array.from(el.parentElement?.children || []).find(n=>n.tagName==='LABEL') || el.closest('[class*="col-"]')?.querySelector('label');
    }
    function translate(el, name, tip) {
        const label = labelFor(el);
        if (label && !label.contains(el)) { setText(label,name); label.htmlFor = el.id; }
        else if(label){const text=Array.from(label.childNodes).find(n=>n.nodeType===3 && n.textContent.trim());if(text && text.textContent.trim()!==name)text.textContent=' '+name+' ';}
        if (el.placeholder && /^(Campaign name|Ad Set name|Creative name|Ad name|Primary text|My betting launch bundle)/.test(el.placeholder)) el.placeholder=name;
        if (tip && !el.parentElement.querySelector('[data-wf-help]')) {
            const help=document.createElement('small'); help.className='wf-help'; help.dataset.wfHelp='1'; help.id=el.id+'Help'; help.textContent=tip;
            el.insertAdjacentElement('afterend',help);
            el.setAttribute('aria-describedby',[el.getAttribute('aria-describedby'),help.id].filter(Boolean).join(' '));
        }
    }
    function translateOptions(root) {
        root.querySelectorAll('select').forEach(select=>{
            for(const option of select.options){
                let name=options[option.value];
                if(!option.value && /Meta default/.test(option.textContent)) name='По умолчанию Meta';
                if(select.id==='specialCategory' && !option.value)name='Без особой категории';
                if(select.id==='mbGender') name=({'':'Все','1':'Мужчины','2':'Женщины'})[option.value];
                if(name && option.textContent!==name) option.textContent=name;
            }
        });
    }
    function fieldGroup(panel, title, note, ids, extra=false) {
        const fields=ids.map(id=>$(id)?.closest('.cr-field')).filter(Boolean);
        if(!fields.length) return;
        const box=document.createElement(extra?'details':'div'); box.className=extra?'wf-extra':'wf-block';
        const heading=document.createElement(extra?'summary':'h4'); heading.textContent=title;box.appendChild(heading);
        if(note){const p=document.createElement('p');p.textContent=note;box.appendChild(p);}
        const grid=document.createElement('div');grid.className=extra?'cr-form-grid':'wf-fields';
        fields.forEach(field=>{if(field.querySelector('textarea')) field.classList.add('wf-wide');grid.appendChild(field);});box.appendChild(grid);
        panel.appendChild(box);
    }
    function setupCreative() {
        const form=$('creativeForm'); if(!form || form.dataset.wfReady) return;form.dataset.wfReady='1';
        $('creativeModal')?.querySelector('.cr-modal')?.setAttribute('role','dialog');
        $('creativeModal')?.querySelector('.cr-modal')?.setAttribute('aria-modal','true');
        $('creativeModal')?.querySelector('.cr-modal')?.setAttribute('aria-labelledby','creativeEditorTitle');
        const descriptions={campaign:'Задайте цель и бюджет. Эти настройки сохранятся в связке.',adset:'Выберите, какой результат нужно получить и как распределять бюджет.',audience:'Выберите людей, которым будет показана реклама.',placements:'Выберите автоматические места показа или настройте платформы вручную.',creative:'Добавьте медиа, текст и ссылку. Названия ниже будут видны в Ads Manager.',tracking:'UTM задаются в объявлении. Здесь находятся дополнительные параметры отслеживания.',advanced:'Редкие параметры доступны по поиску. Заполняйте только настройки, которые нужны вашей кампании.'};
        form.querySelectorAll('.cr-tab').forEach(tab=>{setText(tab,groups[tab.dataset.tab]);tab.setAttribute('aria-controls','wf-panel-'+tab.dataset.tab);});
        form.querySelectorAll('.cr-panel').forEach(panel=>{
            panel.id='wf-panel-'+panel.dataset.panel;setText(panel.querySelector('.cr-section-title'),groups[panel.dataset.panel]);
            const p=document.createElement('div');p.className='wf-panel-description';p.textContent=descriptions[panel.dataset.panel];panel.querySelector('.cr-section-title')?.after(p);
        });
        const campaign=form.querySelector('[data-panel="campaign"]'),adset=form.querySelector('[data-panel="adset"]');
        fieldGroup(campaign,'Основные настройки','Названия и цель рекламы',['presetName','mbCampaignName','mbObjective']);
        fieldGroup(campaign,'Бюджет и состояние','Оставьте один тип бюджета. Валюта определяется рекламным кабинетом.',['mbCampaignDailyBudget','mbCampaignLifetimeBudget','mbCampaignStatus']);
        fieldGroup(campaign,'Дополнительные условия','',['mbBuyingType','mbSpecialCategory','mbCampaignBidStrategy','mbCampaignSpendCap','mbCampaignStart','mbCampaignStop'],true);
        fieldGroup(adset,'Результат и оптимизация','Настройка группы объявлений',['mbAdsetName','mbOptimizationGoal','mbBillingEvent','mbConversionEvent','mbDestinationType','mbAdsetStatus']);
        fieldGroup(adset,'Бюджет группы','Заполняйте, если бюджет не задан на уровне кампании.',['mbAdsetDailyBudget','mbAdsetLifetimeBudget','mbAdsetBidStrategy','mbBidAmount']);
        fieldGroup(adset,'Расписание и атрибуция','',['mbAdsetStart','mbAdsetEnd','mbAttributionSpec','mbPromotedObject'],true);
        // Empty original grids retain non-field controls such as dynamic-creative checkboxes.
        [campaign,adset].forEach(panel=>{const grid=panel.querySelector(':scope > .cr-form-grid');if(grid){if(!grid.children.length)grid.remove();else panel.appendChild(grid);}});
        const aside=document.createElement('aside');aside.className='wf-summary';aside.innerHTML='<h4>Ваша связка</h4><dl><dt>Цель</dt><dd data-wf-summary="objective"></dd><dt>Бюджет</dt><dd data-wf-summary="budget"></dd><dt>Аудитория</dt><dd data-wf-summary="audience"></dd><dt>Формат</dt><dd data-wf-summary="format"></dd></dl><div class="wf-ad-copy"><p data-wf-summary="message"></p><strong data-wf-summary="headline"></strong><span data-wf-summary="cta"></span></div><div class="wf-note">Сохранение связки не публикует рекламу. РК, страницу и пиксель выбирают в автозаливе.</div>';
        form.appendChild(aside);
        form.addEventListener('input',updateCreativeSummary);form.addEventListener('change',updateCreativeSummary);
        form.addEventListener('click',event=>{if(event.target.closest('.cr-tab'))requestAnimationFrame(updateCreativeSummary);});
        new MutationObserver(()=>{if($('creativeModal').classList.contains('open')){form.querySelectorAll('.wf-extra').forEach(details=>{details.open=Array.from(details.querySelectorAll('input,textarea,select')).some(el=>el.value && (el.tagName!=='SELECT' || !['NONE','AUCTION'].includes(el.value)));});updateCreativeSummary();}}).observe($('creativeModal'),{attributes:true,attributeFilter:['class']});
        updateCreativeSummary();
    }
    function updateCreativeSummary() {
        const selected=id=>{const e=$(id);return e?.selectedOptions?.[0]?.textContent || 'Не задано';};
        const value=id=>$(id)?.value?.trim() || '';
        const budgets=[['mbCampaignDailyBudget','Кампания / день'],['mbCampaignLifetimeBudget','Кампания / весь срок'],['mbAdsetDailyBudget','Группа / день'],['mbAdsetLifetimeBudget','Группа / весь срок']].filter(([id])=>value(id)).map(([id,name])=>name+': '+value(id)+' мин. ед.');
        const summary={objective:selected('mbObjective'),budget:budgets.join(' · ')||'Бюджет не задан',audience:value('mbAgeMin')+'–'+value('mbAgeMax')+' лет · '+selected('mbGender'),format:selected('presetFormat'),message:value('presetMessage')||'Здесь будет текст объявления',headline:value('presetHeadline')||'Заголовок объявления',cta:selected('presetCta')};
        Object.entries(summary).forEach(([key,text])=>setText(document.querySelector('[data-wf-summary="'+key+'"]'),text));
        document.querySelectorAll('.cr-tab').forEach(tab=>tab.setAttribute('aria-current',tab.classList.contains('active')?'step':'false'));
    }
    function setupProfilePicker() {
        const select=$('profile'); if(!select?.multiple) return;
        let grid=$('wfProfilePicker');if(!grid){grid=document.createElement('div');grid.id='wfProfilePicker';grid.className='wf-profile-grid';grid.setAttribute('role','group');grid.setAttribute('aria-label','Профили Facebook');select.after(grid);}
        const signature=Array.from(select.options).map(o=>o.value+'|'+o.textContent+'|'+o.disabled).join(';');
        if(grid.dataset.signature!==signature){grid.dataset.signature=signature;grid.replaceChildren();for(const option of select.options){if(!option.value)continue;const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=option.value;input.disabled=option.disabled;input.addEventListener('change',()=>{option.selected=input.checked;select.dispatchEvent(new Event('change',{bubbles:true}));});label.append(input,document.createTextNode(option.textContent));grid.appendChild(label);}select.classList.add('wf-profile-native');}
        grid.querySelectorAll('input').forEach(input=>{const option=Array.from(select.options).find(o=>o.value===input.value);input.checked=Boolean(option?.selected);input.disabled=select.disabled||Boolean(option?.disabled);});
    }
    function setupLaunch() {
        const root=document.querySelector('.launch-wrap');if(!root || root.dataset.wfReady)return;root.dataset.wfReady='1';root.classList.add('wf-launch');
        const entries=[['profile','1. Профили и рекламные кабинеты'],['campaignName','2. Кампания'],['adsetName','3. Бюджет и аудитория'],['creativeName','4. Объявление'],['reviewLaunch','5. Проверка и запуск']];
        const nav=document.createElement('nav');nav.className='wf-step-nav';nav.setAttribute('aria-label','Этапы подготовки рекламы');
        for(const [id,title] of entries){const card=$(id)?.closest('.launch-card');if(!card)continue;if(!card.id)card.id='wf-launch-'+id;setText(card.querySelector('h5'),title);const a=document.createElement('a');a.href='#'+card.id;a.textContent=title;nav.appendChild(a);}
        root.querySelector('.launch-card')?.before(nav);
        [['bundleSelect','Сохранённые настройки'],['loadFunding','Состояние оплаты'],['recentJobSelect','Задачи и результаты']].forEach(([id,title])=>setText($(id)?.closest('.launch-card')?.querySelector('h5'),title));
        const adset=$('adsetName')?.closest('.launch-card');
        [['adsetName','Оптимизация и бюджет'],['ageMin','Аудитория и конверсии'],['geoQuery','География и интересы'],['customAudiences','Свои аудитории']].forEach(([id,title])=>{const row=$(id)?.closest('.row');if(row && adset?.contains(row)){const h=document.createElement('h6');h.className='wf-section-heading';h.textContent=title;row.before(h);}});
        const extra=$('launchMetaAllFields');if(extra){extra.classList.add('wf-advanced');root.querySelector('#wf-launch-reviewLaunch')?.before(extra);if(!root.contains(extra))root.appendChild(extra);setText(extra.querySelector('h3'),'Дополнительные параметры рекламы');setText(extra.querySelector('.rm-meta-note'),'Редкие настройки сгруппированы по назначению. Они сохраняются в плане и задаче. Поиск понимает название и код поля.');}
        const buttons={rkSelectAll:'Выбрать все',rkClear:'Снять выбор',bundleLoad:'Загрузить',bundleSave:'Сохранить',bundleDelete:'Удалить',dryRunPlan:'Предварительный план',launchButton:'Создать рекламу',loadTargetBindings:'Обновить привязки',loadTargetAudiences:'Загрузить аудитории',loadTargetMedia:'Загрузить медиа',refreshInsights:'Обновить статистику',activateJobAds:'Активировать объявления',pauseJobAds:'Приостановить объявления',continueJob:'Продолжить очередь',retryFailed:'Повторить неудачные',forceRetryReview:'Разобрать неизвестный результат',openRecentJob:'Открыть задачу'};
        for(const [id,text] of Object.entries(buttons)){const button=$(id);if(button){if(id==='launchButton'){for(const node of Array.from(button.childNodes)){if(node.nodeType===3)node.textContent=' '+text;}button.setAttribute('aria-label',text);}else setText(button,text);}}
        if($('rkFilter'))$('rkFilter').placeholder='Найти РК, профиль или валюту';
        if($('dailyBudget')){const note=$('dailyBudget').parentElement.querySelector('span.muted');if(note)note.remove();}
        setupProfilePicker();$('profile')?.addEventListener('change',setupProfilePicker);
        $('adAccount')?.closest('[class*="col-"]')?.classList.add('wf-account-selector');
    }
    function enhanceDynamic() {
        Object.entries(labels).forEach(([id,label])=>{
            const el=$(id);
            if(el)translate(el,id==='dailyBudget' && $('budgetPeriod')?.value==='LIFETIME'
                ? 'Бюджет на весь срок' : label,tips[id]);
        });
        document.querySelectorAll('.cr-sdk-field,.rm-meta-field').forEach(wrap=>{
            const input=wrap.querySelector('[data-meta-field],[data-launch-meta-field]');if(!input)return;
            const field=input.dataset.metaField||input.dataset.launchMetaField;
            const label=wrap.querySelector('label'),span=label?.querySelector('span');
            if(span)setText(span,fieldNames[field]||field.replaceAll('_',' '));
            if(label)label.title=field;
            if(!wrap.querySelector('.wf-code')){const code=document.createElement('div');code.className='wf-code';code.textContent=field;wrap.appendChild(code);}
            if(!wrap.dataset.wfSearchBase)wrap.dataset.wfSearchBase=wrap.dataset.search||field;
            wrap.dataset.search=(wrap.dataset.wfSearchBase+' '+(fieldNames[field]||'')).trim().toLowerCase();
        });
        document.querySelectorAll('[data-launch-meta-group] > summary > span:first-child,.cr-sdk-group > summary > span:first-child').forEach(span=>{const key=span.parentElement.parentElement.dataset.launchMetaGroup||span.parentElement.parentElement.dataset.sdkGroup;if(groups[key])setText(span,groups[key]);});
        translateOptions(document);
        const captions={
            'MASS META LAUNCH':'ЗАПУСК РЕКЛАМЫ', 'Run preflight first.':'Выберите профили и загрузите РК.', 'Run preflight first':'Сначала загрузите РК',
            'Choose one or more Facebook profiles and run preflight.':'Отметьте профили выше и нажмите «Загрузить сохранённые РК».',
            'API usage: no data yet.':'Данных об использовании API пока нет.', 'FB profile':'Профиль FB', 'Currency':'Валюта',
            'Funding source':'Способ оплаты', 'Type':'Тип', 'Account':'Аккаунт', 'Spend cap':'Лимит расходов', 'Status':'Статус',
            'Select RK and check funding.':'Выберите РК для проверки оплаты.', 'No review yet.':'План ещё не подготовлен.', 'No launch yet.':'Реклама ещё не создана.',
            'No active job.':'Нет активной задачи.', 'No job.':'Задач пока нет.', 'Recent jobs':'Недавние задачи', 'Select bundle':'Выберите сохранённые настройки',
            'No pixel':'Без пикселя', 'Not required':'Не требуется', 'Select one RK':'Выберите один РК', 'No connected Instagram':'Нет связанного Instagram',
            'No existing Meta asset selected.':'Существующее медиа Meta не выбрано.',
            'For one RK, audiences can be selected here. Multi-RK Launch uses the Per-RK bindings table above.':'Для одного РК выберите аудитории здесь. Для нескольких РК используются отдельные привязки к каждому кабинету.',
            'Bundle stores Campaign, Ad Set, targeting, Page/IG/Pixel selection, creative text, CTA, URL and UTM. Media file must be selected for each launch.':'Набор сохраняет настройки рекламы, медиа и порядок карточек. Для бюджета на весь срок укажите окончание показа.'
        };
        const launch=document.querySelector('.launch-wrap');
        if(launch){const walker=document.createTreeWalker(launch,NodeFilter.SHOW_TEXT);let node;while((node=walker.nextNode())){if(node.parentElement.closest('textarea,code,article'))continue;const key=node.textContent.trim();let caption=captions[key];if(/^Selected FB: \d+$/.test(key))caption=key.replace('Selected FB:','Выбрано профилей:');else if(/^Selected: \d+$/.test(key))caption=key.replace('Selected:','Выбрано РК:');if(caption && key!==caption)node.textContent=caption;}}
        for(const [id,placeholder] of [['geoQuery','Страна, регион или город'],['interestQuery','Выберите интерес или найдите по названию'],['behaviorQuery','Выберите поведение или найдите по названию']])if($(id))$(id).placeholder=placeholder;
        setupProfilePicker();
        updateCreativeSummary();
    }
    function init() {
        if(!$('creativeForm')&&!document.querySelector('.launch-wrap'))return;
        setupCreative();setupLaunch();enhanceDynamic();
        let queued=false;
        const observer=new MutationObserver(()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;observer.disconnect();enhanceDynamic();observer.observe(document.body,{childList:true,subtree:true});});});
        observer.observe(document.body,{childList:true,subtree:true});
    }
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
