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
        locales:'Языки', custom_audiences:'Включить свои аудитории', excluded_custom_audiences:'Исключить свои аудитории', flexible_spec:'Комбинации условий', exclusions:'Исключить интересы и поведение',
        education_schools:'Учебные заведения', education_majors:'Специальности', education_statuses:'Уровень образования', college_years:'Годы обучения', work_employers:'Работодатели', work_positions:'Должности', relationship_statuses:'Семейное положение', life_events:'События жизни', family_statuses:'Родители', industries:'Отрасли работы', home_ownership:'Владение жильём', home_type:'Тип жилья', home_value:'Стоимость жилья', wireless_carrier:'Сеть и подключение', generation:'Поколение', household_composition:'Состав семьи', office_type:'Тип офиса', location_categories:'Категории мест', excluded_user_device:'Исключённые устройства', app_install_state:'Установка приложения', optimization_sub_event:'Дополнительная оптимизация', applink_treatment:'Открытие ссылки приложения', purchase_intent:'Намерение покупки', user_adclusters:'Категории аудитории Meta', hashtag_interactions:'Взаимодействия с хэштегами', ethnic_affinity:'Этническая принадлежность', interested_in:'Интересы в отношении', income:'Доход домохозяйства', net_worth:'Чистая стоимость активов', politics:'Политическая тематика', moms:'Родители',
        user_os:'Операционные системы', user_device:'Модели устройств', publisher_platforms:'Платформы', device_platforms:'Типы устройств',
        facebook_positions:'Места показа Facebook', instagram_positions:'Места показа Instagram', messenger_positions:'Места показа Messenger', audience_network_positions:'Места показа Audience Network', threads_positions:'Места показа Threads', whatsapp_positions:'Места показа WhatsApp', oculus_positions:'Места показа Meta Quest', streaming_services_positions:'Места показа в потоковых сервисах',
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
        mbUserOs:'Операционные системы',mbUserDevice:'Модели устройств',
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
    #creativeForm{display:grid;grid-template-columns:190px minmax(0,1fr) 280px;grid-template-rows:minmax(0,1fr) auto;flex:1;min-height:0;overflow:hidden}
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
    .wf-summary{grid-column:3;grid-row:1;border-left:1px solid var(--wf-line);padding:24px 18px;background:#181e28;overflow:auto;color:#e1e9f6;min-width:0}
    .wf-summary,#creativeForm>.cr-editor{scrollbar-color:#425064 #181e28;scrollbar-width:thin}
    .wf-summary h4{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:#9dafc7;margin:0 0 18px}
    .wf-summary dl{margin:0}.wf-summary dt{font-size:11px;color:#91a2ba;margin-top:16px}.wf-summary dd{font-size:13px;margin:5px 0 0;overflow-wrap:anywhere;line-height:1.5}
    .wf-note{border:1px solid #354965;background:#202d42;color:#a9c0df;border-radius:9px;padding:12px;font-size:11px;line-height:1.6;margin-top:24px}
    .wf-ad-copy{border:1px solid #3b485c;border-radius:10px;overflow:hidden;margin-top:18px;background:#111923}.wf-ad-copy p{padding:12px;margin:0;white-space:pre-wrap;font-size:12px;line-height:1.55}.wf-ad-copy strong{display:block;padding:12px;border-top:1px solid #344151;font-size:13px}.wf-ad-copy span{display:block;padding:0 12px 12px;font-size:11px;color:#b6c5dc}
    .launch-wrap.wf-launch{max-width:1280px}.wf-launch>.launch-card{border:1px solid var(--wf-line);border-radius:14px;padding:24px;margin-bottom:20px;background:#1a212c;scroll-margin-top:85px}
    .wf-launch h5{font-size:18px;font-weight:700;color:#eef4ff;margin-bottom:22px}
    main:has(.wf-launch){overflow:visible}
    .wf-launch label{display:block!important;margin:0 0 7px!important;font-size:12px!important;font-weight:600;color:#bccbdd;line-height:1.5}
    .wf-launch label:has(input[type=checkbox],input[type=radio]){display:flex!important;align-items:center;gap:8px}
    .wf-launch .form-control:not(.rm-meta-visual-source):not(.rm-module-source):not(.wf-profile-native):not([type=hidden]):not([hidden]){display:block!important;width:100%!important;box-sizing:border-box;min-width:0;min-height:42px;border-radius:8px!important;margin:0!important}
    .wf-launch .row{display:grid;grid-template-columns:repeat(2,minmax(280px,1fr));gap:22px;margin-left:0;margin-right:0}
    .wf-launch .row>[class*="col-"]{width:auto;max-width:none;min-width:0;flex:none;padding:0;grid-column:auto}
    .wf-launch .row>.col-md-12,.wf-launch .row>.col-12{grid-column:1/-1}
    .wf-launch .row>.wf-account-selector{grid-column:span 2}
    .wf-launch .row+.row{margin-top:20px!important}.wf-launch textarea{min-height:110px}
    .wf-launch small.muted{display:block;font-size:11px;margin-top:6px}.wf-launch .table-responsive{border:1px solid #344151;border-radius:10px;margin-top:15px}
    .wf-launch td,.wf-launch th{padding:12px!important;vertical-align:middle}.wf-launch .wf-section-heading{font-size:13px;color:#87b4f8;border-top:1px solid #344151;padding-top:20px;margin:24px 0 15px}
    .wf-launch>.launch-card{scroll-margin-top:140px}
    .wf-launch .wf-step-nav{position:sticky;top:64px;z-index:80;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px;padding:8px;background:#151b24e8;border:1px solid #344151;border-radius:16px;margin:18px 0;backdrop-filter:blur(14px)}
    .wf-step-nav a{display:flex;align-items:center;gap:10px;min-width:0;padding:10px 12px;border:1px solid transparent;border-radius:11px;font-size:12px;color:#aebdd1;text-decoration:none;transition:background .16s ease,border-color .16s ease,color .16s ease}
    .wf-step-nav a:hover,.wf-step-nav a:focus-visible{background:#222e3d;color:#f3f7fc}
    .wf-step-nav a.is-current{background:linear-gradient(135deg,#1b3a61,#1d2e44);border-color:#385d87;color:#f4f8ff}
    .wf-step-index{display:grid;place-items:center;flex:0 0 26px;height:26px;border:1px solid #44536a;border-radius:50%;font-size:11px;color:#c4d2e4}
    .wf-step-nav a.is-current .wf-step-index{background:#2573d5;border-color:#4794ee;color:#fff}
    .wf-step-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .wf-profile-grid{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}.wf-profile-grid label{display:flex;align-items:center;gap:9px;border:1px solid #3a4a60;border-radius:9px;padding:10px 14px;background:#111923;cursor:pointer}
    .wf-profile-grid label:has(input:checked){background:#243e65;border-color:#85b5ff;color:#fff}.wf-profile-grid input{accent-color:#8db9ff;width:16px;height:16px}
    .wf-launch .wf-profile-native{display:none!important}.wf-launch .wf-profile-grid label{margin:0!important}
    #launchMetaAllFields.wf-advanced{max-width:100%;margin:0 0 20px;padding:22px;background:#1a212c;border:1px solid #344151;border-radius:14px}
    #launchMetaAllFields h3{font-size:18px;color:#e6effc}#launchMetaAllFields .rm-meta-note{font-size:12px;line-height:1.6}#launchMetaAllFields .rm-meta-grid{gap:18px;padding:6px 18px 18px}
    #launchMetaAllFields .rm-meta-field label,.cr-sdk-field label{align-items:center!important}.rm-meta-type,.cr-sdk-type{display:none!important}
    .wf-code{font-size:10px;color:#778aa6;margin-top:6px;overflow-wrap:anywhere}.wf-launch .rm-meta-search{max-width:420px}
    .wf-launch .btn,.cr-foot-actions .btn{border-radius:8px;padding:10px 16px;font-weight:600}
    @media(max-width:700px){.wf-launch>.launch-card>.d-flex{flex-wrap:wrap;gap:8px}}
    @media(max-width:1050px){#creativeForm{grid-template-columns:150px minmax(0,1fr) 250px}.wf-summary{padding:18px 12px}.wf-launch .row{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:880px){.wf-launch .wf-step-nav{grid-template-columns:repeat(5,minmax(92px,1fr));overflow:auto}.wf-launch .row{grid-template-columns:repeat(2,minmax(0,1fr))}}
    
    @media(max-width:900px){#creativeForm{grid-template-columns:150px minmax(0,1fr);grid-template-rows:170px minmax(0,1fr) auto}#creativeForm>.wf-summary{grid-column:1/-1;grid-row:1;border-left:0;border-bottom:1px solid var(--wf-line);padding:12px 16px;min-height:0}#creativeForm>.wf-summary>:not(.rm-ad-estimate){display:none}#creativeForm>.cr-tabs,#creativeForm>.cr-editor{grid-row:2}#creativeForm>.cr-modal-foot{grid-row:3}}
    @media(max-width:1000px){.wf-launch .row{grid-template-columns:repeat(2,minmax(0,1fr))}.wf-launch .row>.wf-account-selector{grid-column:1/-1}}
    @media(max-width:700px){#creativeModal .cr-modal{width:calc(100vw - 12px);max-height:calc(100dvh - 12px)}#creativeForm{display:flex;flex-direction:column}#creativeForm>.cr-tabs{flex-direction:row;flex-shrink:0;border-right:0;border-bottom:1px solid var(--wf-line);padding:8px}.cr-tab{white-space:nowrap!important}#creativeForm>.cr-editor{padding:16px}.wf-fields{grid-template-columns:1fr}.wf-fields>.wf-wide{grid-column:auto}.cr-form-grid>.cr-field,.cr-form-grid>.cr-target-box{grid-column:span 12}.wf-launch>.launch-card{padding:18px}.wf-launch .row{grid-template-columns:1fr}.wf-launch .row>*{grid-column:1/-1!important}.wf-step-nav a{padding:9px}.cr-modal-foot{flex-wrap:wrap}#launchMetaAllFields .rm-meta-search{min-width:0;width:100%}.cr-carousel-row{grid-template-columns:48px minmax(0,1fr)}}
    @media(max-width:700px){#creativeForm{display:grid;grid-template-columns:minmax(0,1fr);grid-template-rows:auto 155px minmax(0,1fr) auto}#creativeForm>.cr-tabs{grid-column:1;grid-row:1}#creativeForm>.wf-summary{grid-column:1;grid-row:2}#creativeForm>.cr-editor{grid-column:1;grid-row:3;min-height:0}#creativeForm>.cr-modal-foot{grid-column:1;grid-row:4}}
    @media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
    `;
    style.textContent += '\n'+".wf-launch.rm-estimate-layout{width:100%;max-width:1480px!important;grid-template-columns:minmax(0,1fr) 304px!important;gap:24px!important;align-items:start}\n.wf-launch .launch-main,.wf-launch .rm-estimate-rail{min-width:0}\n.wf-launch .rm-estimate-rail{position:sticky;top:88px;align-self:start;display:grid;gap:16px}\n.wf-launch>.launch-card{padding:26px;border:1px solid #2f3d50;border-radius:20px;background:linear-gradient(160deg,#19222f 0%,#151d28 100%);box-shadow:0 14px 34px rgba(3,8,16,.18);margin-bottom:22px}\n.wf-launch h5{font-size:19px;letter-spacing:-.02em;margin-bottom:24px}\n.wf-launch .row{gap:22px 20px}\n.wf-launch label{font-size:12px!important;color:#c1cede!important;margin-bottom:8px!important}\n.wf-launch .form-control:not(.rm-meta-visual-source):not(.rm-module-source):not(.wf-profile-native):not([type=hidden]):not([hidden]){min-height:46px!important;border-radius:11px!important;border-color:#3a4b61!important;background:#111923!important;color:#eef3fb!important;padding:11px 13px!important;transition:border-color .16s ease,box-shadow .16s ease}\n.wf-launch .form-control:focus{border-color:#6e9eea!important;box-shadow:0 0 0 3px rgba(83,137,218,.18)!important}\n.wf-launch textarea.form-control{min-height:112px!important}\n.wf-launch .wf-step-nav{top:70px;gap:8px;padding:9px;border-color:#33445a;border-radius:17px;background:rgba(17,24,34,.88);box-shadow:0 10px 28px rgba(3,8,16,.2)}\n.wf-step-nav a{padding:10px 11px;border-radius:12px}\n.wf-step-index{width:28px;height:28px;flex-basis:28px}\n.wf-launch .wf-section-heading{border:0;padding:0;margin:30px 0 2px;color:#9db7da;font-size:12px;font-weight:750;letter-spacing:.08em;text-transform:uppercase}\n .wf-launch .wf-profile-grid{gap:10px}\n.wf-launch .wf-profile-grid label{flex:1 1 230px;min-width:0}\n.wf-launch .wf-profile-grid label{min-height:44px;padding:10px 14px;border-radius:12px;background:#151e2a}\n.wf-launch .wf-profile-grid label:has(input:checked){background:linear-gradient(135deg,#203c60,#202c40);border-color:#6592cc}\n.wf-target-groups{margin:30px 0 4px}\n.wf-target-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:0 0 14px}\n.wf-target-head h6{margin:0;color:#e7eef9;font-size:15px;font-weight:750}\n.wf-target-head p{margin:5px 0 0;color:#9cabc0;font-size:12px;line-height:1.55}\n.wf-target-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}\n.wf-target-panel{min-width:0;border:1px solid #35465b;border-radius:16px;background:linear-gradient(145deg,rgba(31,43,58,.9),rgba(23,32,44,.9));overflow:hidden}\n.wf-target-panel>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 17px;cursor:pointer;list-style:none}\n.wf-target-panel>summary::-webkit-details-marker{display:none}\n.wf-target-panel>summary::after,.wf-live-summary>summary::after{content:'';flex:0 0 8px;width:8px;height:8px;border-right:1.5px solid #91a8c6;border-bottom:1.5px solid #91a8c6;transform:rotate(45deg);transition:transform .16s ease}\n.wf-target-panel[open]>summary::after,.wf-live-summary[open]>summary::after{transform:rotate(225deg)}\n.wf-target-panel>summary:focus-visible,.wf-live-summary>summary:focus-visible{outline:2px solid #75a8ee;outline-offset:-3px}\n.wf-target-panel[open]>summary{border-bottom:1px solid #344357}\n.wf-target-name{color:#eef3fc;font-size:13px;font-weight:700}\n.wf-target-count{flex:0 0 auto;color:#9eb2cd;font-size:11px}\n.wf-target-note{margin:0;padding:13px 17px 0;color:#a5b4c8;font-size:11px;line-height:1.55}\n.wf-target-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:14px 15px 16px}\n.wf-target-field{min-width:0;padding:13px;border:1px solid rgba(104,126,153,.24);border-radius:13px;background:rgba(12,19,28,.38)}\n.wf-target-field>label{display:block!important;margin:0 0 8px!important;color:#d4dfed!important;font-size:12px!important;font-weight:650}\n.wf-target-field .rm-module-control{min-width:0}\n.wf-target-field .rm-module-results{max-height:210px;overflow:auto;background:#111a25}\n.wf-target-empty{margin-top:12px;padding:12px 14px;border:1px dashed #43536a;border-radius:12px;color:#a9b7ca;font-size:12px;line-height:1.55}\n.wf-target-source{display:none!important}\n.wf-live-summary{border:1px solid #35465a;border-radius:18px;background:linear-gradient(165deg,#1c2735,#171f2b);box-shadow:0 12px 30px rgba(3,8,16,.18);overflow:hidden}\n.wf-live-summary>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:17px 18px;cursor:pointer;list-style:none}\n.wf-live-summary>summary::-webkit-details-marker{display:none}\n.wf-live-summary-title{display:block;color:#edf3fc;font-size:14px;font-weight:750}\n.wf-live-summary-caption{display:block;margin-top:4px;color:#95a6bc;font-size:11px}\n.wf-live-status{flex:0 0 auto;border:1px solid rgba(92,166,128,.4);border-radius:999px;padding:6px 9px;background:rgba(37,105,76,.2);color:#9fe0bd;font-size:10px;font-weight:700}\n.wf-live-summary-body{display:grid;gap:0;padding:0 18px 8px}\n.wf-live-row{padding:12px 0;border-top:1px solid rgba(114,134,159,.18)}\n.wf-live-row-label{display:block;margin-bottom:5px;color:#91a4bd;font-size:10px;font-weight:750;letter-spacing:.07em;text-transform:uppercase}\n.wf-live-row-value{display:block;color:#e0e8f4;font-size:12px;line-height:1.5;overflow-wrap:anywhere}\n.wf-live-summary-foot{padding:10px 18px 16px;color:#95a6bc;font-size:11px;line-height:1.5}\n.wf-mobile-actions{display:none}\n#launchMetaAllFields.wf-advanced{margin:8px 0 24px;padding:24px;border:1px solid #33445a;border-radius:18px;background:linear-gradient(160deg,#19222f,#151c27);box-shadow:0 12px 28px rgba(3,8,16,.14)}\n#launchMetaAllFields h3{font-size:18px;letter-spacing:-.02em}\n#launchMetaAllFields .rm-meta-note{max-width:760px;color:#a2b2c8;font-size:12px;line-height:1.65}\n#launchMetaAllFields .rm-meta-groups{gap:12px}\n#launchMetaAllFields details{border-color:#34455b;border-radius:14px;background:rgba(12,19,28,.32)}\n#launchMetaAllFields summary{padding:15px 16px}\n#launchMetaAllFields .rm-meta-grid{gap:14px;padding:2px 15px 16px}\n@media(max-width:1120px){.wf-launch.rm-estimate-layout{grid-template-columns:minmax(0,1fr)!important;gap:18px!important}.wf-launch .rm-estimate-rail{position:static;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start}.wf-launch .wf-step-nav{top:62px}}\n@media(max-width:900px){.wf-launch .wf-step-nav{grid-template-columns:repeat(5,minmax(96px,1fr));overflow:auto}.wf-launch .wf-step-nav a{scroll-snap-align:start}.wf-launch .rm-estimate-rail{grid-template-columns:minmax(0,1fr)}}\n@media(max-width:720px){\nbody.wf-autoupload-open{padding-bottom:calc(84px + env(safe-area-inset-bottom))}\n.wf-launch.rm-estimate-layout{display:grid;grid-template-columns:minmax(0,1fr)!important;gap:16px!important}\n.wf-launch>.launch-card{padding:19px 17px;border-radius:17px;margin-bottom:16px}\n.wf-launch h5{font-size:17px;margin-bottom:19px}\n.wf-launch .row{grid-template-columns:minmax(0,1fr);gap:17px}\n.wf-launch .row>*{grid-column:1/-1!important}\n.wf-launch .wf-profile-grid label{flex-basis:100%}\n.wf-launch .wf-step-nav{position:sticky;top:56px;display:flex;gap:7px;margin:12px 0 16px;padding:7px;border-radius:14px;overflow-x:auto;overscroll-behavior-x:contain;scroll-snap-type:x mandatory}\n.wf-launch .wf-step-nav a{flex:0 0 auto;min-width:106px;padding:9px 10px;scroll-snap-align:start}\n.wf-step-index{width:25px;height:25px;flex-basis:25px}\n.wf-target-list,.wf-target-fields{grid-template-columns:minmax(0,1fr)}\n.wf-target-panel>summary{padding:14px}\n.wf-target-fields{padding:12px}\n.wf-target-head{align-items:flex-start;flex-direction:column;gap:4px}\n.wf-launch .rm-estimate-rail{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}\n.wf-live-summary{border-radius:15px}\n.wf-live-summary>summary{padding:15px}\n.wf-live-summary:not([open]) .wf-live-summary-body,.wf-live-summary:not([open]) .wf-live-summary-foot{display:none}\n.wf-mobile-actions{position:fixed;z-index:500;left:0;right:0;bottom:0;display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;align-items:center;gap:8px;padding:10px 12px calc(10px + env(safe-area-inset-bottom));border-top:1px solid #34445a;background:rgba(15,22,32,.94);box-shadow:0 -12px 30px rgba(0,0,0,.24);backdrop-filter:blur(18px)}\n.wf-mobile-actions button{min-height:42px;border:1px solid #3a4b61;border-radius:12px;padding:9px 12px;background:#1b2634;color:#e8eef8;font:inherit;font-size:12px;font-weight:600;line-height:1.1;white-space:nowrap}\n.wf-mobile-actions button:disabled{opacity:.45;cursor:not-allowed}\n.wf-mobile-actions .wf-mobile-next{border-color:#6795d2;background:linear-gradient(135deg,#315f98,#28456c);color:#fff}\n.wf-mobile-step{min-width:0;color:#b9c7d9;font-size:11px;line-height:1.3;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.wf-mobile-step strong{display:block;color:#eef4fc;font-size:12px;margin-bottom:3px}\n#launchMetaAllFields.wf-advanced{padding:18px 15px;border-radius:16px}\n#launchMetaAllFields .rm-meta-search{min-width:0;width:100%;max-width:none}\n#launchMetaAllFields .rm-meta-grid{grid-template-columns:minmax(0,1fr);padding:0 10px 12px}\n#creativeModal .cr-modal{width:calc(100vw - 10px);max-height:calc(100dvh - 10px)}\n#creativeForm{display:grid;grid-template-columns:minmax(0,1fr);grid-template-rows:auto 145px minmax(0,1fr) auto}\n#creativeForm>.cr-tabs{grid-column:1;grid-row:1;flex-direction:row;overflow:auto;border-right:0;border-bottom:1px solid var(--wf-line);padding:8px}\n#creativeForm>.wf-summary{grid-column:1;grid-row:2;border-left:0;border-bottom:1px solid var(--wf-line);padding:11px 15px;min-height:0}\n#creativeForm>.wf-summary>:not(.rm-ad-estimate){display:none}\n#creativeForm>.cr-editor{grid-column:1;grid-row:3;padding:16px}\n#creativeForm>.cr-modal-foot{grid-column:1;grid-row:4;flex-wrap:wrap;padding:12px}\n.cr-tab{white-space:nowrap!important}\n.wf-fields{grid-template-columns:minmax(0,1fr)}\n.wf-fields>.wf-wide{grid-column:auto}\n.cr-form-grid>.cr-field,.cr-form-grid>.cr-target-box{grid-column:span 12}\n.cr-carousel-row{grid-template-columns:48px minmax(0,1fr)}\n}";
    document.head.appendChild(style);

    function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }
    function labelFor(el) {
        return document.querySelector('label[for="'+el.id+'"]') || el.closest('label') || el.closest('.cr-field,.cr-target-box')?.querySelector('label') || Array.from(el.parentElement?.children || []).find(n=>n.tagName==='LABEL') || el.closest('[class*="col-"]')?.querySelector('label');
    }
    function translate(el, name, tip) {
        const label = labelFor(el);
        if (label && !label.contains(el)) { setText(label,name); label.htmlFor = $(el.id+'_choices')?.id || el.id; }
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
        const estimate=$('remaskAdvertisingEstimate');if(estimate)aside.prepend(estimate);
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
    const wfTargetGroups = [
        {key:'demographics',title:'Демография',note:'Возраст, пол, образование, семейный статус и социальные параметры.',fields:new Set(['age_min','age_max','genders','education_statuses','education_schools','education_majors','college_years','relationship_statuses','family_statuses','household_composition','ethnic_affinity','generation','interested_in','income','net_worth','politics'])},
        {key:'devices',title:'Устройства и подключение',note:'Платформы, модели устройств, операционные системы и тип подключения.',fields:new Set(['device_platforms','user_device','excluded_user_device','user_os','wireless_carrier','app_install_state'])},
        {key:'placements',title:'Платформы и места показа',note:'Поверхности размещения и доступные ограничения для выбранной цели.',fields:new Set(['publisher_platforms','facebook_positions','instagram_positions','messenger_positions','audience_network_positions','threads_positions','whatsapp_positions','oculus_positions','streaming_services_positions','brand_safety_content_filter_levels','contextual_bundling_spec'])},
        {key:'geography',title:'География и язык',note:'Страны и регионы — поиск по каталогу; города, радиусы и другие гео-типы — в редакторе структуры.',fields:new Set(['geo_locations','locales'])},
        {key:'exclusions',title:'Исключения',note:'Исключённые страны, регионы и аудитории; интересы и поведение — поиском.',fields:new Set(['excluded_geo_locations','excluded_custom_audiences','exclusions'])},
        {key:'detailed',title:'Интересы и категории Meta',note:'Интересы, поведение, свои аудитории, работа, события жизни, жильё и другие категории.',fields:new Set(['interests','behaviors','custom_audiences','flexible_spec','purchase_intent','user_adclusters','hashtag_interactions','work_employers','work_positions','industries','office_type','life_events','moms','home_ownership','home_type','home_value','location_categories'])}
    ];
    function wfTargetValues(source) {
        const raw=String(source?.value||'').trim();if(!raw)return [];
        try{const parsed=JSON.parse(raw);if(Array.isArray(parsed))return parsed;if(parsed&&typeof parsed==='object'){if(['exclusions','excluded_geo_locations'].includes(source?.dataset?.launchMetaField))return Object.values(parsed).flatMap(value=>Array.isArray(value)?value:[]);return Object.values(parsed);}if(parsed!==null&&parsed!==undefined)return [parsed];}
        catch{return raw.split(',').map(value=>value.trim()).filter(Boolean);}
        return [];
    }
    function wfTargetGroupFor(field) {
        return wfTargetGroups.find(group=>group.fields.has(field))||wfTargetGroups.find(group=>group.key==='detailed');
    }
    function updateAudienceTargetingSummary() {
        const root=$('wfTargetingOptions');if(!root)return;
        for(const panel of root.querySelectorAll('[data-wf-target-group]')){
            const fields=[...panel.querySelectorAll('[data-wf-target-field]')];
            let selected=0;
            for(const row of fields){
                const field=row.dataset.wfTargetField;
                const source=document.querySelector('[data-launch-meta-group="targeting"][data-launch-meta-field="'+field+'"]');
                selected+=wfTargetValues(source).length;
            }
            const count=panel.querySelector('[data-wf-target-count]');
            if(count)setText(count,fields.length?(selected?fields.length+' полей · '+selected+' значений':'Поля доступны · не выбрано'):'Нет параметров для этого аккаунта');
        }
    }
    function setupAudienceTargeting() {
        const adsetCard=$('adsetName')?.closest('.launch-card');if(!adsetCard)return;
        let root=$('wfTargetingOptions');
        if(!root){
            root=document.createElement('section');root.id='wfTargetingOptions';root.className='wf-target-groups';
            const head=document.createElement('div');head.className='wf-target-head';
            const copy=document.createElement('div'),title=document.createElement('h6'),description=document.createElement('p');
            title.textContent='Дополнительные настройки аудитории';
            description.textContent='Выбирайте доступные параметры Meta через поиск; точные значения сохраняются в настройки аудитории.';
            copy.append(title,description);head.appendChild(copy);
            const list=document.createElement('div');list.className='wf-target-list';
            for(const group of wfTargetGroups){
                const panel=document.createElement('details');panel.className='wf-target-panel';panel.dataset.wfTargetGroup=group.key;
                const summary=document.createElement('summary'),name=document.createElement('span'),count=document.createElement('span');
                name.className='wf-target-name';name.textContent=group.title;
                count.className='wf-target-count';count.dataset.wfTargetCount='1';count.textContent='Загружаю варианты Meta…';
                summary.append(name,count);
                const note=document.createElement('p');note.className='wf-target-note';note.textContent=group.note;
                const fields=document.createElement('div');fields.className='wf-target-fields';
                panel.append(summary,note,fields);list.appendChild(panel);
            }
            const empty=document.createElement('div');empty.className='wf-target-empty';empty.dataset.wfTargetEmpty='1';
            empty.textContent='Каталог прямого выбора Meta сейчас недоступен. Другие доступные параметры остаются в разделе «Дополнительные настройки рекламы».';
            root.append(head,list,empty);
            const anchors=['ageMin','geoQuery','interestQuery','behaviorQuery','languageQuery','customAudiences','excludedCustomAudiences']
                .map(id=>$(id)?.closest('.row')||$(id)?.closest('[data-remaskLanguageTargeting]'))
                .filter(row=>row&&adsetCard.contains(row));
            anchors.sort((a,b)=>a===b?0:(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1));
            const anchor=anchors[anchors.length-1];
            if(anchor)anchor.after(root);else adsetCard.appendChild(root);
        }
        const rowsByGroup=new Map(wfTargetGroups.map(group=>[group.key,root.querySelector('[data-wf-target-group="'+group.key+'"] .wf-target-fields')]));
        let available=0,sourceCount=0;
        for(const source of document.querySelectorAll('[data-launch-meta-group="targeting"][data-launch-meta-field]')){
            sourceCount++;
            const field=source.dataset.launchMetaField||'';
            const group=wfTargetGroupFor(field);
            const fields=rowsByGroup.get(group.key);
            const wrapper=source.closest('.rm-meta-field,.cr-sdk-field');
            const picker=wrapper?.querySelector('.rm-module-control');
            const structure=wrapper?.querySelector('.rm-module-structure');
            const nativeSelect=!picker&&source instanceof HTMLSelectElement?source:null;
            const control=picker||nativeSelect||structure;
            if(!control||!fields)continue;
            let row=[...fields.querySelectorAll('[data-wf-target-field]')].find(item=>item.dataset.wfTargetField===field);
            if(!row){
                row=document.createElement('div');row.className='wf-target-field';row.dataset.wfTargetField=field;
                const caption=document.createElement('label'),search=picker?.querySelector('input')||nativeSelect;
                caption.textContent=fieldNames[field]||field.replaceAll('_',' ');
                if(search){caption.htmlFor=search.id;search.setAttribute('aria-label',caption.textContent);}
                row.appendChild(caption);fields.appendChild(row);
            }
            if(structure){
                const fieldTitle=fieldNames[field]||field.replaceAll('_',' '),controls=structure.querySelectorAll('button');
                controls[0]?.setAttribute('aria-label','Настроить '+fieldTitle);
                controls[1]?.setAttribute('aria-label','Очистить '+fieldTitle);
            }
            if(picker&&picker.parentElement!==row)row.appendChild(picker);
            if(nativeSelect){nativeSelect.classList.add('wf-target-native-control');if(nativeSelect.parentElement!==row)row.appendChild(nativeSelect);}
            if(structure&&structure.parentElement!==row)row.appendChild(structure);
            wrapper.classList.add('wf-target-source');available++;
        }
        let visibleGroups=0;
        for(const panel of root.querySelectorAll('[data-wf-target-group]')){
            const count=panel.querySelectorAll('[data-wf-target-field]').length;
            panel.hidden=count===0;if(count)visibleGroups++;
        }
        const empty=root.querySelector('[data-wf-target-empty]');
        if(empty){
            empty.hidden=available>0||sourceCount===0;
            if(sourceCount===0)setText(empty,'Загружаю доступные параметры аудитории Meta…');
        }
        root.hidden=sourceCount===0&&available===0;
        root.dataset.wfReady=visibleGroups?'1':'0';
        updateAudienceTargetingSummary();
    }
    function selectedLabels(id) {
        const element=$(id);if(!element)return [];
        if(element instanceof HTMLSelectElement)return [...element.selectedOptions].filter(option=>option.value).map(option=>option.textContent.trim()).filter(Boolean);
        return String(element.value||'').trim()?[String(element.value).trim()]:[];
    }
    function selectedPillLabels(id) {
        const container=$(id);if(!container)return [];
        return [...container.children].map(item=>item.textContent.trim().replace(/\s*[×✕x]\s*$/,'')).filter(Boolean);
    }
    function moduleFieldLabels(group,field) {
        const source=document.querySelector('[data-launch-meta-group="'+group+'"][data-launch-meta-field="'+field+'"]');
        const row=[...document.querySelectorAll('[data-wf-target-field]')].find(item=>item.dataset.wfTargetField===field);
        const pills=row?.querySelector('.rm-module-pills')||source?.closest('.rm-meta-field,.cr-sdk-field')?.querySelector('.rm-module-pills');
        return pills?[...pills.children].map(item=>item.textContent.trim().replace(/\s*[×✕x]\s*$/,'')).filter(Boolean):[];
    }
    function updateLiveSummary() {
        const panel=$('wfLiveSummary');if(!panel)return;
        const profiles=selectedLabels('profile'),accounts=selectedLabels('adAccount');
        const objective=selectedLabels('objective')[0]||'Цель не выбрана';
        const campaignName=String($('campaignName')?.value||'').trim()||'Название не задано';
        const budget=String($('dailyBudget')?.value||'').trim();
        const budgetPeriod=selectedLabels('budgetPeriod')[0]||'';
        const budgetLevel=selectedLabels('budgetLevel')[0]||'';
        const geo=selectedPillLabels('selectedGeo'),interests=selectedPillLabels('selectedInterests'),behaviors=selectedPillLabels('selectedBehaviors'),languages=selectedPillLabels('selectedLanguages');
        const ageMin=String($('ageMin')?.value||'').trim(),ageMax=String($('ageMax')?.value||'').trim();
        const gender=moduleFieldLabels('targeting','genders');
        const platformCount=document.querySelectorAll('.publisher-platform:checked').length;
        const targetCounts={device_platforms:0,user_device:0,user_os:0,excluded_user_device:0,app_install_state:0};
        for(const field of Object.keys(targetCounts)){
            const source=document.querySelector('[data-launch-meta-group="targeting"][data-launch-meta-field="'+field+'"]');
            targetCounts[field]=wfTargetValues(source).length;
        }
        const deviceCount=Object.values(targetCounts).reduce((a,b)=>a+b,0);
        const positionCount=[...document.querySelectorAll('[data-launch-meta-group="targeting"][data-launch-meta-field]')]
            .filter(source=>(source.dataset.launchMetaField||'').endsWith('_positions'))
            .reduce((count,source)=>count+wfTargetValues(source).length,0);
        const page=selectedLabels('page')[0]||'Страница не выбрана';
        const instagram=selectedLabels('instagram')[0]||'Не выбран';
        const format=selectedLabels('creativeFormat')[0]||'Формат не выбран';
        const media=$('media'),mediaText=media?.files?.length?media.files.length+' файл(а)':'Медиа не выбрано';
        const optimization=selectedLabels('optimizationGoal')[0]||'Оптимизация не выбрана';
        const conversionEvent=selectedLabels('conversionEvent')[0]||'';
        const pixel=selectedLabels('pixel')[0]||'';
        const startTime=String($('startTime')?.value||'').trim(),endTime=String($('endTime')?.value||'').trim();
        const message=String($('message')?.value||'').trim(),headline=String($('headline')?.value||'').trim();
        const cta=selectedLabels('cta')[0]||'Кнопка не выбрана';
        const destination=String($('destinationUrl')?.value||'').trim()||'Ссылка не задана';
        const copyPreview=message?(message.length>72?message.slice(0,72)+'…':message):'Текст объявления не задан';
        const budgetText=budget?[budgetLevel,budget+(budgetPeriod?' · '+budgetPeriod.toLowerCase():'')].filter(Boolean).join(': '):'Бюджет не задан';
        const exclusionSources=['excluded_geo_locations','exclusions'].map(field=>document.querySelector('[data-launch-meta-group="targeting"][data-launch-meta-field="'+field+'"]'));
        let exclusionCount=0;
        for(const exclusionsSource of exclusionSources){
            try{
                const rules=JSON.parse(String(exclusionsSource?.value||'{}'));
                if(rules&&typeof rules==='object'&&!Array.isArray(rules))exclusionCount+=Object.values(rules).reduce((count,value)=>count+(Array.isArray(value)?value.length:0),0);
            }catch{}
        }
        const audienceParts=[
            ageMin||ageMax?((ageMin||'—')+'–'+(ageMax||'—')+' лет'):'Возраст по умолчанию',
            gender.length?gender.join(', '):'Любой пол',
            geo.length?geo.length+' гео':'География не выбрана',
            interests.length?interests.length+' интересов':'без выбранных интересов',
            behaviors.length?behaviors.length+' поведенческих категорий':'',
            exclusionCount?exclusionCount+' исключений':''
        ].filter(Boolean);
        const placement=selectedLabels('placementMode')[0]||'Режим не выбран';
        const rows={
            resources:(profiles.length?profiles.length+' профилей':'Профили не выбраны')+' · '+(accounts.length?accounts.slice(0,2).join(', ')+(accounts.length>2?' +'+(accounts.length-2):''):'РК не выбран'),
            campaign:campaignName+' · '+objective,
            budget:budgetText,
            schedule:(startTime||endTime)?(startTime||'Сразу')+' — '+(endTime||'Без даты окончания'):'Расписание по умолчанию',
            audience:audienceParts.join(' · ')+(languages.length?' · '+languages.length+' языков':''),
            optimization:optimization+(conversionEvent?' · '+conversionEvent:'')+(pixel?' · '+pixel:''),
            delivery:placement+' · '+(platformCount?platformCount+' платформ':'платформы автоматически')+(positionCount?' · '+positionCount+' позиций':'')+(deviceCount?' · '+deviceCount+' параметров устройств':''),
            creative:page+' · Instagram: '+instagram+' · '+format+' · '+mediaText,
            copy:copyPreview+' · '+headline+' · '+cta+' · '+destination
        };
        for(const [key,value] of Object.entries(rows)){
            const node=panel.querySelector('[data-wf-summary-value="'+key+'"]');if(node)setText(node,value);
        }
        const foot=panel.querySelector('[data-wf-summary-foot]');
        if(foot)setText(foot,'Черновик сохранится на паузе. В форме доступны параметры аудитории, плейсменты, устройства, креатив и остальные поля Meta из каталога.');
    }
    function setupLiveSummary(root) {
        let panel=$('wfLiveSummary');if(panel)return panel;
        const layout=root.matches('.rm-estimate-layout')?root:root.closest('.rm-estimate-layout');
        const rail=layout?.querySelector('.rm-estimate-rail')||root.querySelector('.rm-estimate-rail');
        panel=document.createElement('details');panel.id='wfLiveSummary';panel.className='wf-live-summary';
        panel.open=!(window.matchMedia&&window.matchMedia('(max-width: 720px)').matches);
        const summary=document.createElement('summary'),heading=document.createElement('span'),title=document.createElement('span'),caption=document.createElement('span'),status=document.createElement('span');
        heading.className='wf-live-summary-heading';title.className='wf-live-summary-title';title.textContent='Сводка автозалива';
        caption.className='wf-live-summary-caption';caption.textContent='Обновляется при изменении параметров';
        heading.append(title,caption);status.className='wf-live-status';status.textContent='Черновик · на паузе';summary.append(heading,status);
        const body=document.createElement('div');body.className='wf-live-summary-body';
        const rows=[['resources','Ресурсы'],['campaign','Кампания'],['budget','Бюджет'],['schedule','График'],['audience','Аудитория'],['optimization','Оптимизация'],['delivery','Показ'],['creative','Страница и медиа'],['copy','Текст и переход']];
        for(const [key,label] of rows){
            const row=document.createElement('div');row.className='wf-live-row';
            const name=document.createElement('span'),value=document.createElement('span');
            name.className='wf-live-row-label';name.textContent=label;value.className='wf-live-row-value';value.dataset.wfSummaryValue=key;
            row.append(name,value);body.appendChild(row);
        }
        const foot=document.createElement('div');foot.className='wf-live-summary-foot';foot.dataset.wfSummaryFoot='1';
        panel.append(summary,body,foot);
        if(rail)rail.prepend(panel);else root.querySelector('.wf-step-nav')?.after(panel);
        updateLiveSummary();return panel;
    }
    function setupLaunch() {
        const root=document.querySelector('.launch-wrap');if(!root||root.dataset.wfReady)return;
        root.dataset.wfReady='1';root.classList.add('wf-launch');document.body.classList.add('wf-autoupload-open');
        const entries=[['profile','Ресурсы','Профили и рекламные кабинеты'],['campaignName','Кампания','Кампания'],['adsetName','Аудитория','Бюджет и аудитория'],['creativeName','Объявление','Объявление'],['reviewLaunch','Черновик','Сохранение черновика']];
        const nav=document.createElement('nav');nav.className='wf-step-nav';nav.setAttribute('aria-label','Этапы автозалива');
        const stepCards=[];
        entries.forEach(([id,title,cardTitle])=>{
            const card=$(id)?.closest('.launch-card');if(!card)return;
            if(!card.id)card.id='wf-launch-'+id;
            setText(card.querySelector('h5'),cardTitle||title);
            const a=document.createElement('a');a.href='#'+card.id;a.setAttribute('aria-label',(stepCards.length+1)+'. '+title);
            const number=document.createElement('span');number.className='wf-step-index';number.textContent=String(stepCards.length+1);
            const label=document.createElement('span');label.className='wf-step-label';label.textContent=title;
            a.append(number,label);nav.appendChild(a);stepCards.push({link:a,card});
        });
        root.querySelector('.launch-card')?.before(nav);
        let activeStep=0;
        let mobileBar=document.getElementById('wfMobileActions');
        if(!mobileBar){
            mobileBar=document.createElement('div');mobileBar.id='wfMobileActions';mobileBar.className='wf-mobile-actions';mobileBar.setAttribute('aria-label','Навигация по этапам автозалива');
            const back=document.createElement('button');back.type='button';back.className='wf-mobile-back';back.textContent='Назад';back.setAttribute('aria-label','Предыдущий этап');
            const status=document.createElement('div');status.className='wf-mobile-step';status.setAttribute('aria-live','polite');
            const summaryButton=document.createElement('button');summaryButton.type='button';summaryButton.className='wf-mobile-summary';summaryButton.textContent='Сводка';
            const next=document.createElement('button');next.type='button';next.className='wf-mobile-next';next.textContent='Далее';next.setAttribute('aria-label','Следующий этап');
            mobileBar.append(back,status,summaryButton,next);document.body.appendChild(mobileBar);
            back.addEventListener('click',()=>{if(activeStep>0)stepTo(activeStep-1);});
            next.addEventListener('click',()=>{if(activeStep<stepCards.length-1)stepTo(activeStep+1);else stepTo(0);});
            summaryButton.addEventListener('click',()=>{
                const panel=$('wfLiveSummary');if(!panel)return;
                panel.open=true;panel.scrollIntoView({behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});
            });
        }
        const mobileStatus=mobileBar.querySelector('.wf-mobile-step'),backButton=mobileBar.querySelector('.wf-mobile-back'),nextButton=mobileBar.querySelector('.wf-mobile-next');
        function setCurrentStep(index){
            if(!stepCards.length)return;
            activeStep=Math.max(0,Math.min(index,stepCards.length-1));
            stepCards.forEach((item,i)=>{
                const active=i===activeStep;item.link.classList.toggle('is-current',active);
                if(active)item.link.setAttribute('aria-current','location');else item.link.removeAttribute('aria-current');
            });
            if(mobileStatus){
                const label=stepCards[activeStep].link.querySelector('.wf-step-label')?.textContent||'Этап';
                mobileStatus.replaceChildren();
                const title=document.createElement('strong');title.textContent=label;
                mobileStatus.append(title,document.createTextNode('Этап '+(activeStep+1)+' из '+stepCards.length));
            }
            if(backButton)backButton.disabled=activeStep===0;
            if(nextButton)nextButton.textContent=activeStep===stepCards.length-1?'К началу':'Далее';
        }
        function stepTo(index){
            const item=stepCards[index];if(!item)return;
            setCurrentStep(index);
            item.card.scrollIntoView({behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
        }
        stepCards.forEach((item,index)=>item.link.addEventListener('click',()=>setCurrentStep(index)));
        setCurrentStep(0);
        if('IntersectionObserver'in window){
            const observer=new IntersectionObserver(rows=>{
                const current=rows.filter(row=>row.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];
                if(!current)return;
                const index=stepCards.findIndex(item=>item.card===current.target);if(index>=0)setCurrentStep(index);
            },{rootMargin:'-20% 0px -65% 0px',threshold:[0.05,0.2,0.45]});
            stepCards.forEach(item=>observer.observe(item.card));
        }
        [['bundleSelect','Сохранённые настройки'],['loadFunding','Состояние оплаты'],['recentJobSelect','Задачи и результаты']].forEach(([id,title])=>setText($(id)?.closest('.launch-card')?.querySelector('h5'),title));
        const adset=$('adsetName')?.closest('.launch-card');
        [['adsetName','Бюджет и стратегия'],['ageMin','Возраст и пол'],['geoQuery','География'],['interestQuery','Интересы и поведение'],['customAudiences','Свои аудитории и исключения'],['placementMode','Места показа'],['startTime','Расписание']].forEach(([id,title])=>{
            const row=$(id)?.closest('.row');if(row&&adset?.contains(row)&&!row.previousElementSibling?.classList.contains('wf-section-heading')){
                const heading=document.createElement('h6');heading.className='wf-section-heading';heading.textContent=title;row.before(heading);
            }
        });
        const extra=$('launchMetaAllFields');
        if(extra){
            extra.classList.add('wf-advanced');root.querySelector('#wf-launch-reviewLaunch')?.before(extra);if(!root.contains(extra))root.appendChild(extra);
            setText(extra.querySelector('h3'),'Дополнительные настройки рекламы');
            setText(extra.querySelector('.rm-meta-note'),'Поля, доступные в текущей схеме Meta, сгруппированы по объектам. Поиск понимает название и API-код; значения сохраняются в Review и Job.');
        }
        const buttons={rkSelectAll:'Выбрать все',rkClear:'Снять выбор',bundleLoad:'Загрузить',bundleSave:'Сохранить',bundleDelete:'Удалить',dryRunPlan:'Предварительный план',launchButton:'Создать рекламу',loadTargetBindings:'Обновить привязки',loadTargetAudiences:'Загрузить аудитории',loadTargetMedia:'Загрузить медиа',refreshInsights:'Обновить статистику',activateJobAds:'Активировать объявления',pauseJobAds:'Приостановить объявления',continueJob:'Продолжить очередь',retryFailed:'Повторить неудачные',forceRetryReview:'Разобрать неизвестный результат',openRecentJob:'Открыть задачу'};
        for(const [id,text] of Object.entries(buttons)){
            const button=$(id);if(!button)continue;
            if(id==='launchButton'){for(const node of Array.from(button.childNodes)){if(node.nodeType===3)node.textContent=' '+text;}button.setAttribute('aria-label',text);}
            else setText(button,text);
        }
        if($('rkFilter'))$('rkFilter').placeholder='Найти РК, профиль или валюту';
        if($('dailyBudget'))$('dailyBudget').parentElement.querySelector('span.muted')?.remove();
        setupProfilePicker();$('profile')?.addEventListener('change',setupProfilePicker);
        $('adAccount')?.closest('[class*="col-"]')?.classList.add('wf-account-selector');
        setupLiveSummary(root);
        if(root.dataset.wfSummaryBound!=='1'){
            root.dataset.wfSummaryBound='1';
            ['input','change'].forEach(type=>root.addEventListener(type,()=>{updateLiveSummary();updateAudienceTargetingSummary();},true));
        }
        updateLiveSummary();
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
        setupAudienceTargeting();
        updateAudienceTargetingSummary();
        updateLiveSummary();
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
