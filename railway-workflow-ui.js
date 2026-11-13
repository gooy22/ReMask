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
    style.textContent += '\n'+".wf-launch.rm-estimate-layout{width:100%;max-width:1480px!important;grid-template-columns:minmax(0,1fr) 304px!important;gap:24px!important;align-items:start}\n.wf-launch .launch-main,.wf-launch .rm-estimate-rail{min-width:0}\n.wf-launch .rm-estimate-rail{position:sticky;top:88px;align-self:start;display:grid;gap:16px}\n.wf-launch>.launch-card{padding:26px;border:1px solid #2f3d50;border-radius:20px;background:linear-gradient(160deg,#19222f 0%,#151d28 100%);box-shadow:0 14px 34px rgba(3,8,16,.18);margin-bottom:22px}\n.wf-launch h5{font-size:19px;letter-spacing:-.02em;margin-bottom:24px}\n.wf-launch .row{gap:22px 20px}\n.wf-launch label{font-size:12px!important;color:#c1cede!important;margin-bottom:8px!important}\n.wf-launch .form-control:not(.rm-meta-visual-source):not(.rm-module-source):not(.wf-profile-native):not([type=hidden]):not([hidden]){min-height:46px!important;border-radius:11px!important;border-color:#3a4b61!important;background:#111923!important;color:#eef3fb!important;padding:11px 13px!important;transition:border-color .16s ease,box-shadow .16s ease}\n.wf-launch .form-control:focus{border-color:#6e9eea!important;box-shadow:0 0 0 3px rgba(83,137,218,.18)!important}\n.wf-launch textarea.form-control{min-height:112px!important}\n.wf-launch .wf-step-nav{top:70px;gap:8px;padding:9px;border-color:#33445a;border-radius:17px;background:rgba(17,24,34,.88);box-shadow:0 10px 28px rgba(3,8,16,.2)}\n.wf-step-nav a{padding:10px 11px;border-radius:12px}\n.wf-step-index{width:28px;height:28px;flex-basis:28px}\n.wf-launch .wf-section-heading{border:0;padding:0;margin:30px 0 2px;color:#9db7da;font-size:12px;font-weight:750;letter-spacing:.08em;text-transform:uppercase}\n .wf-launch .wf-profile-grid{gap:10px}\n.wf-launch .wf-profile-grid label{flex:1 1 230px;min-width:0}\n.wf-launch .wf-profile-grid label{min-height:44px;padding:10px 14px;border-radius:12px;background:#151e2a}\n.wf-launch .wf-profile-grid label:has(input:checked){background:linear-gradient(135deg,#203c60,#202c40);border-color:#6592cc}\n.wf-target-groups{margin:30px 0 4px}\n.wf-target-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:0 0 14px}\n.wf-target-head h6{margin:0;color:#e7eef9;font-size:15px;font-weight:750}\n.wf-target-head p{margin:5px 0 0;color:#9cabc0;font-size:12px;line-height:1.55}\n.wf-target-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}\n.wf-target-panel{min-width:0;border:1px solid #35465b;border-radius:16px;background:linear-gradient(145deg,rgba(31,43,58,.9),rgba(23,32,44,.9));overflow:hidden}\n.wf-target-panel>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 17px;cursor:pointer;list-style:none}\n.wf-target-panel>summary::-webkit-details-marker{display:none}\n.wf-target-panel>summary::after,.wf-live-summary>summary::after{content:'';flex:0 0 8px;width:8px;height:8px;border-right:1.5px solid #91a8c6;border-bottom:1.5px solid #91a8c6;transform:rotate(45deg);transition:transform .16s ease}\n.wf-target-panel[open]>summary::after,.wf-live-summary[open]>summary::after{transform:rotate(225deg)}\n.wf-target-panel>summary:focus-visible,.wf-live-summary>summary:focus-visible{outline:2px solid #75a8ee;outline-offset:-3px}\n.wf-target-panel[open]>summary{border-bottom:1px solid #344357}\n.wf-target-name{color:#eef3fc;font-size:13px;font-weight:700}\n.wf-target-count{flex:0 0 auto;color:#9eb2cd;font-size:11px}\n.wf-target-note{margin:0;padding:13px 17px 0;color:#a5b4c8;font-size:11px;line-height:1.55}\n.wf-target-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:14px 15px 16px}\n.wf-target-field{min-width:0;padding:13px;border:1px solid rgba(104,126,153,.24);border-radius:13px;background:rgba(12,19,28,.38)}\n.wf-target-field>label{display:block!important;margin:0 0 8px!important;color:#d4dfed!important;font-size:12px!important;font-weight:650}\n.wf-target-field .rm-module-control{min-width:0}\n.wf-target-field .rm-module-results{max-height:210px;overflow:auto;background:#111a25}\n.wf-target-empty{margin-top:12px;padding:12px 14px;border:1px dashed #43536a;border-radius:12px;color:#a9b7ca;font-size:12px;line-height:1.55}\n.wf-target-source{display:none!important}\n.wf-live-summary{border:1px solid #35465a;border-radius:18px;background:linear-gradient(165deg,#1c2735,#171f2b);box-shadow:0 12px 30px rgba(3,8,16,.18);overflow:hidden}\n.wf-live-summary>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:17px 18px;cursor:pointer;list-style:none}\n.wf-live-summary>summary::-webkit-details-marker{display:none}\n.wf-live-summary-title{display:block;color:#edf3fc;font-size:14px;font-weight:750}\n.wf-live-summary-caption{display:block;margin-top:4px;color:#95a6bc;font-size:11px}\n.wf-live-status{flex:0 0 auto;border:1px solid rgba(92,166,128,.4);border-radius:999px;padding:6px 9px;background:rgba(37,105,76,.2);color:#9fe0bd;font-size:10px;font-weight:700}\n.wf-live-summary-body{display:grid;gap:0;padding:0 18px 8px}\n.wf-live-row{padding:12px 0;border-top:1px solid rgba(114,134,159,.18)}\n.wf-live-row-label{display:block;margin-bottom:5px;color:#91a4bd;font-size:10px;font-weight:750;letter-spacing:.07em;text-transform:uppercase}\n.wf-live-row-value{display:block;color:#e0e8f4;font-size:12px;line-height:1.5;overflow-wrap:anywhere}\n.wf-live-summary-foot{padding:10px 18px 16px;color:#95a6bc;font-size:11px;line-height:1.5}\n.wf-mobile-actions{display:none}\n#launchMetaAllFields.wf-advanced{margin:8px 0 24px;padding:24px;border:1px solid #33445a;border-radius:18px;background:linear-gradient(160deg,#19222f,#151c27);box-shadow:0 12px 28px rgba(3,8,16,.14)}\n#launchMetaAllFields h3{font-size:18px;letter-spacing:-.02em}\n#launchMetaAllFields .rm-meta-note{max-width:760px;color:#a2b2c8;font-size:12px;line-height:1.65}\n#launchMetaAllFields .rm-meta-groups{gap:12px}\n#launchMetaAllFields details{border-color:#34455b;border-radius:14px;background:rgba(12,19,28,.32)}\n#launchMetaAllFields summary{padding:15px 16px}\n#launchMetaAllFields .rm-meta-grid{gap:14px;padding:2px 15px 16px}\n@media(max-width:1120px){.wf-launch.rm-estimate-layout{grid-template-columns:minmax(0,1fr)!important;gap:18px!important}.wf-launch .rm-estimate-rail{position:static;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start}.wf-launch .wf-step-nav{top:62px}}\n@media(max-width:900px){.wf-launch .wf-step-nav{grid-template-columns:repeat(5,minmax(96px,1fr));overflow:auto}.wf-launch .wf-step-nav a{scroll-snap-align:start}.wf-launch .rm-estimate-rail{grid-template-columns:minmax(0,1fr)}}\n@media(max-width:720px){\n.wf-launch .wf-step-nav{display:none!important}\n.wf-launch .wf-step-card:not(.is-current-step){display:none!important}\nbody.wf-autoupload-open{padding-bottom:calc(84px + env(safe-area-inset-bottom))}\n.wf-launch.rm-estimate-layout{display:grid;grid-template-columns:minmax(0,1fr)!important;gap:16px!important}\n.wf-launch>.launch-card{padding:19px 17px;border-radius:17px;margin-bottom:16px}\n.wf-launch h5{font-size:17px;margin-bottom:19px}\n.wf-launch .row{grid-template-columns:minmax(0,1fr);gap:17px}\n.wf-launch .row>*{grid-column:1/-1!important}\n.wf-launch .wf-profile-grid label{flex-basis:100%}\n.wf-launch .wf-step-nav{position:sticky;top:56px;display:flex;gap:7px;margin:12px 0 16px;padding:7px;border-radius:14px;overflow-x:auto;overscroll-behavior-x:contain;scroll-snap-type:x mandatory}\n.wf-launch .wf-step-nav a{flex:0 0 auto;min-width:106px;padding:9px 10px;scroll-snap-align:start}\n.wf-step-index{width:25px;height:25px;flex-basis:25px}\n.wf-target-list,.wf-target-fields{grid-template-columns:minmax(0,1fr)}\n.wf-target-panel>summary{padding:14px}\n.wf-target-fields{padding:12px}\n.wf-target-head{align-items:flex-start;flex-direction:column;gap:4px}\n.wf-launch .rm-estimate-rail{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}\n.wf-live-summary{border-radius:15px}\n.wf-live-summary>summary{padding:15px}\n.wf-live-summary:not([open]) .wf-live-summary-body,.wf-live-summary:not([open]) .wf-live-summary-foot{display:none}\n.wf-mobile-actions{position:fixed;z-index:500;left:0;right:0;bottom:0;display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;align-items:center;gap:8px;padding:10px 12px calc(10px + env(safe-area-inset-bottom));border-top:1px solid #34445a;background:rgba(15,22,32,.94);box-shadow:0 -12px 30px rgba(0,0,0,.24);backdrop-filter:blur(18px)}\n.wf-mobile-actions button{min-height:42px;border:1px solid #3a4b61;border-radius:12px;padding:9px 12px;background:#1b2634;color:#e8eef8;font:inherit;font-size:12px;font-weight:600;line-height:1.1;white-space:nowrap}\n.wf-mobile-actions button:disabled{opacity:.45;cursor:not-allowed}\n.wf-mobile-actions .wf-mobile-next{border-color:#6795d2;background:linear-gradient(135deg,#315f98,#28456c);color:#fff}\n.wf-mobile-step{min-width:0;color:#b9c7d9;font-size:11px;line-height:1.3;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.wf-mobile-step strong{display:block;color:#eef4fc;font-size:12px;margin-bottom:3px}\n#launchMetaAllFields.wf-advanced{padding:18px 15px;border-radius:16px}\n#launchMetaAllFields .rm-meta-search{min-width:0;width:100%;max-width:none}\n#launchMetaAllFields .rm-meta-grid{grid-template-columns:minmax(0,1fr);padding:0 10px 12px}\n#creativeModal .cr-modal{width:calc(100vw - 10px);max-height:calc(100dvh - 10px)}\n#creativeForm{display:grid;grid-template-columns:minmax(0,1fr);grid-template-rows:auto 145px minmax(0,1fr) auto}\n#creativeForm>.cr-tabs{grid-column:1;grid-row:1;flex-direction:row;overflow:auto;border-right:0;border-bottom:1px solid var(--wf-line);padding:8px}\n#creativeForm>.wf-summary{grid-column:1;grid-row:2;border-left:0;border-bottom:1px solid var(--wf-line);padding:11px 15px;min-height:0}\n#creativeForm>.wf-summary>:not(.rm-ad-estimate){display:none}\n#creativeForm>.cr-editor{grid-column:1;grid-row:3;padding:16px}\n#creativeForm>.cr-modal-foot{grid-column:1;grid-row:4;flex-wrap:wrap;padding:12px}\n.cr-tab{white-space:nowrap!important}\n.wf-fields{grid-template-columns:minmax(0,1fr)}\n.wf-fields>.wf-wide{grid-column:auto}\n.cr-form-grid>.cr-field,.cr-form-grid>.cr-target-box{grid-column:span 12}\n.cr-carousel-row{grid-template-columns:48px minmax(0,1fr)}\n}";
    style.textContent += '\n'+".wf-launch{display:grid!important;grid-template-columns:minmax(0,1fr) minmax(280px,320px);column-gap:20px;row-gap:0;align-items:start;max-width:1480px!important}\n.wf-launch>.app-page-head,.wf-launch>.wf-step-nav{grid-column:1/-1;min-width:0}\n.wf-launch>.wf-step-nav{grid-template-columns:repeat(5,minmax(0,1fr))}\n.wf-launch>.wf-live-summary{grid-column:2;grid-row:3;position:sticky;top:82px;align-self:start;max-height:calc(100vh - 100px);overflow:auto;margin:0 0 16px}\n.wf-launch>.launch-card,.wf-launch>#launchMetaAllFields{grid-column:1;min-width:0}\n.wf-launch .wf-step-card:not(.is-current-step){display:none!important}\n.wf-launch .wf-step-card.is-current-step{display:block}\n.wf-live-summary>summary{position:sticky;top:0;z-index:2;align-items:flex-start;flex-direction:column;padding:16px;background:linear-gradient(165deg,#1c2735,#171f2b)}\n.wf-live-summary-heading{min-width:0}\n.wf-live-summary-body{padding:0 15px 8px}\n.wf-live-row{padding:10px 0}\n.wf-live-row-label{font-size:9px;margin-bottom:4px}\n.wf-live-row-value{font-size:12px;line-height:1.45}\n.wf-live-summary-foot{padding:10px 15px 14px}\n.wf-card-disclosure{border:1px solid #35465b;border-radius:14px;background:linear-gradient(145deg,rgba(31,43,58,.7),rgba(23,32,44,.7));overflow:hidden}\n.wf-card-disclosure>summary{display:flex;align-items:center;min-height:52px;padding:12px 16px;cursor:pointer;list-style:none}\n.wf-card-disclosure>summary::-webkit-details-marker{display:none}\n.wf-card-disclosure>summary::after,.wf-audience-group>summary::after,.wf-audience-subgroup>summary::after{content:'';margin-left:auto;flex:0 0 8px;width:8px;height:8px;border-right:1.5px solid #91a8c6;border-bottom:1.5px solid #91a8c6;transform:rotate(45deg);transition:transform .16s ease}\n.wf-card-disclosure[open]>summary::after,.wf-audience-group[open]>summary::after,.wf-audience-subgroup[open]>summary::after{transform:rotate(225deg)}\n.wf-card-disclosure>summary h5{margin:0!important;font-size:14px!important}\n.wf-card-disclosure-body{padding:0 16px 16px}\n.wf-audience-group{margin:14px 0;border:1px solid #35465b;border-radius:15px;background:linear-gradient(145deg,rgba(31,43,58,.7),rgba(23,32,44,.7));overflow:hidden}\n.wf-audience-group>summary,.wf-audience-subgroup>summary{display:flex;align-items:center;min-height:50px;padding:12px 16px;color:#e7eef9;font-size:13px;font-weight:700;cursor:pointer;list-style:none}\n.wf-audience-group>summary::-webkit-details-marker,.wf-audience-subgroup>summary::-webkit-details-marker{display:none}\n.wf-audience-group[open]>summary,.wf-audience-subgroup[open]>summary{border-bottom:1px solid #344357}\n.wf-audience-group-body{padding:0 15px 14px}\n.wf-audience-subgroup{margin:14px 0 0;border:1px solid #35465b;border-radius:14px;background:rgba(12,19,28,.3);overflow:hidden}\n.wf-audience-subgroup-body{padding:12px 14px}\n#launchMetaAllFields.wf-advanced{padding:0;border:0;background:transparent;box-shadow:none;margin:14px 0 22px}\n#launchMetaAllFields.wf-advanced>details{border:1px solid #35465b;border-radius:15px;background:linear-gradient(145deg,rgba(31,43,58,.7),rgba(23,32,44,.7));overflow:hidden}\n#launchMetaAllFields.wf-advanced>details>summary{padding:14px 16px;color:#e7eef9;font-weight:700;cursor:pointer}\n#launchMetaAllFields.wf-advanced>details>.wf-meta-disclosure-body{padding:0 16px 16px}\n.wf-mobile-actions{position:fixed;z-index:500;left:0;right:0;bottom:0;display:grid!important;grid-template-columns:auto auto minmax(0,1fr) auto auto auto;align-items:center;gap:9px;padding:10px max(20px,calc((100vw - 1480px)/2)) calc(10px + env(safe-area-inset-bottom));border-top:1px solid #34445a;background:rgba(15,22,32,.96);box-shadow:0 -12px 30px rgba(0,0,0,.24);backdrop-filter:blur(18px)}\n.wf-mobile-actions button{min-height:44px;border:1px solid #3a4b61;border-radius:12px;padding:9px 13px;background:#1b2634;color:#e8eef8;font:inherit;font-size:12px;font-weight:650;line-height:1.1;white-space:nowrap}\n.wf-mobile-actions button:disabled{opacity:.45;cursor:not-allowed}\n.wf-workflow-status{border:1px solid #3f76b6;border-radius:10px;padding:9px 12px;background:#1b3c64;color:#d7e9ff;font-size:12px;font-weight:700;white-space:nowrap}\n.wf-workflow-step{min-width:0;color:#b9c7d9;font-size:12px;line-height:1.3;text-align:center}\n.wf-workflow-step strong{display:inline;color:#eef4fc;font-size:12px;margin-right:8px}\n.wf-mobile-back{display:none}\n.wf-mobile-summary{background:#151e2a!important}\n#wfSaveTemplateButton{border-color:#61748e;background:transparent;color:#e3ebf5}\n.wf-mobile-actions .wf-mobile-next,.wf-mobile-actions #reviewLaunch,.wf-mobile-actions #launchButton{border-color:#6795d2;background:linear-gradient(135deg,#2879ee,#1264d8);color:#fff;font-weight:750;box-shadow:0 5px 15px rgba(20,100,216,.18)}\n.wf-mobile-actions #reviewLaunch,.wf-mobile-actions #launchButton{margin:0}\n.wf-mobile-actions [hidden]{display:none!important}\nbody.wf-autoupload-open{padding-bottom:calc(82px + env(safe-area-inset-bottom))}\n@media(max-width:1120px){.wf-launch{grid-template-columns:minmax(0,1fr);column-gap:0}.wf-launch>.wf-live-summary{grid-column:1;grid-row:auto;position:static;max-height:none;margin:0 0 12px}.wf-launch>.wf-live-summary[open]{max-height:44vh}.wf-launch>.wf-step-nav{grid-template-columns:repeat(5,minmax(96px,1fr));overflow:auto}}\n@media(max-width:900px){.wf-launch>.wf-step-nav{display:flex;gap:7px;overflow-x:auto;scroll-snap-type:x mandatory}.wf-launch>.wf-step-nav a{flex:0 0 auto;min-width:124px;scroll-snap-align:start}}\n@media(min-width:721px){.wf-mobile-actions .wf-mobile-back{display:none}}\n@media(max-width:720px){.wf-launch{grid-template-columns:minmax(0,1fr);column-gap:0}.wf-launch>.wf-live-summary{grid-column:1;grid-row:auto;max-height:none;margin:0 0 8px}.wf-launch>.wf-live-summary[open]{max-height:45vh}.wf-launch>.wf-step-nav{display:none!important}.wf-mobile-actions{grid-template-columns:auto minmax(0,1fr) auto auto auto;gap:6px;padding:9px 10px calc(9px + env(safe-area-inset-bottom))}.wf-workflow-status{display:none}.wf-mobile-actions .wf-mobile-back{display:block;padding:8px 10px;font-size:11px}.wf-mobile-actions button{min-height:42px;padding:8px 9px;font-size:10px}.wf-workflow-step{font-size:10px}.wf-workflow-step strong{display:block;margin:0 0 2px;font-size:11px}.wf-card-disclosure>summary{min-height:48px}.wf-audience-group{margin:11px 0}.wf-audience-group-body{padding:0 10px 12px}.wf-target-list,.wf-target-fields{grid-template-columns:minmax(0,1fr)}}\n@media(max-width:380px){.wf-mobile-actions{grid-template-columns:auto minmax(0,1fr) auto auto}.wf-mobile-summary{display:none!important}}";

    style.textContent += `
/* ReMask AutoUpload: product-level visual system */
.wf-launch{
  --wf-canvas:#0d1420;--wf-surface:#141e2c;--wf-surface-raised:#1a2636;
  --wf-border:rgba(150,177,210,.16);--wf-border-strong:rgba(155,182,216,.26);
  --wf-text:#f3f7fc;--wf-muted:#9aabc0;--wf-blue:#3b91ff;--wf-violet:#8065ff;
  position:relative;width:100%;max-width:1540px!important;
  grid-template-columns:minmax(0,1fr) minmax(278px,320px)!important;
  column-gap:24px!important;row-gap:16px!important;color:var(--wf-text);
}
.wf-launch>.app-page-head,.wf-launch>.wf-step-nav{grid-column:1/-1;min-width:0}
.wf-launch>.app-page-head{margin-bottom:2px}
.wf-launch>.wf-step-nav{
  position:sticky;top:60px;z-index:30;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));
  gap:8px;margin:0 0 2px;padding:8px;border:1px solid var(--wf-border);
  border-radius:18px;background:rgba(15,23,35,.88);backdrop-filter:blur(16px);
  box-shadow:0 14px 36px rgba(3,9,18,.25)
}
.wf-step-nav a{
  position:relative;display:flex;align-items:center;gap:10px;min-width:0;min-height:50px;
  padding:8px 12px;border:1px solid transparent;border-radius:13px;color:#91a3ba;
  text-decoration:none;transition:background .18s,border-color .18s,color .18s,transform .18s
}
.wf-step-nav a:hover{color:#e7f0fb;background:rgba(78,104,139,.12)}
.wf-step-nav a:not(:last-child)::after{content:"";position:absolute;z-index:-1;right:-9px;top:50%;width:10px;height:1px;background:#43536a}
.wf-step-index{
  display:grid;place-items:center;width:32px;height:32px;flex:0 0 32px;
  border:1px solid #46556b;border-radius:50%;background:#151e2b;color:#a8b8cc;
  font-size:12px;font-weight:750;transition:all .18s
}
.wf-step-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;font-weight:650}
.wf-step-nav a.is-current{
  color:#f7fbff;border-color:rgba(71,151,255,.88);
  background:linear-gradient(115deg,rgba(43,108,201,.34),rgba(94,76,185,.17));
  box-shadow:inset 0 0 0 1px rgba(93,165,255,.12),0 8px 22px rgba(23,96,183,.14)
}
.wf-step-nav a.is-current .wf-step-index{
  border-color:transparent;background:linear-gradient(135deg,#44a3ff,#526dff);
  color:white;box-shadow:0 0 0 5px rgba(55,139,248,.12)
}
.wf-step-nav a.is-complete .wf-step-index{border-color:rgba(91,195,151,.42);background:rgba(50,147,112,.16);color:#8ce0b8}
.wf-step-nav a.is-complete .wf-step-index::after{content:"✓";position:absolute}
.wf-step-nav a.is-complete .wf-step-index{font-size:0}
.wf-step-nav a.is-current .wf-step-index{font-size:12px}
.wf-launch>.launch-card.wf-step-card{
  min-width:0;margin:0!important;padding:26px!important;border:1px solid var(--wf-border)!important;
  border-radius:20px!important;background:
    radial-gradient(ellipse at 0 0,rgba(58,116,197,.08),transparent 55%),
    linear-gradient(160deg,rgba(23,33,48,.98),rgba(17,26,39,.98))!important;
  box-shadow:0 16px 40px rgba(3,9,18,.22)!important;scroll-margin-top:126px
}
.wf-launch>.launch-card.wf-step-card>h5{
  display:flex;align-items:center;gap:12px;margin:0 0 22px!important;padding:0 0 18px;
  border-bottom:1px solid var(--wf-border);color:var(--wf-text)!important;
  font-size:21px!important;font-weight:720;letter-spacing:-.035em
}
.wf-launch>.launch-card.wf-step-card>h5::before{
  content:"";width:4px;height:25px;border-radius:4px;
  background:linear-gradient(180deg,#50b6ff,#7866ff);box-shadow:0 0 18px rgba(69,150,255,.38)
}
.wf-launch .wf-section-heading{
  display:flex;align-items:center;gap:10px;margin:20px 0 14px!important;
  padding:0;color:#c7d5e7!important;font-size:11px!important;font-weight:800!important;
  letter-spacing:.13em;text-transform:uppercase
}
.wf-launch .wf-section-heading::after{content:"";height:1px;flex:1;background:linear-gradient(90deg,var(--wf-border-strong),transparent)}
.wf-launch .row{align-items:start;gap:18px 16px!important;margin-left:0!important;margin-right:0!important}
.wf-launch .row>[class*="col-"]{min-width:0}
.wf-launch label{
  margin:0 0 8px!important;color:#b8c7d9!important;font-size:12px!important;
  font-weight:650!important;letter-spacing:.005em
}
.wf-launch .form-control:not(.rm-meta-visual-source):not(.rm-module-source):not(.wf-profile-native):not([type=hidden]):not([hidden]){
  min-height:47px!important;padding:11px 14px!important;border:1px solid rgba(151,177,207,.22)!important;
  border-radius:12px!important;background:rgba(9,16,26,.72)!important;color:#eef5ff!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.025);transition:border-color .17s,box-shadow .17s,background .17s
}
.wf-launch .form-control:hover:not(:disabled){border-color:rgba(119,165,224,.48)!important;background:#101b2a!important}
.wf-launch .form-control:focus{
  outline:0!important;border-color:#54a5ff!important;background:#101b2a!important;
  box-shadow:0 0 0 4px rgba(58,146,255,.15)!important
}
.wf-launch .form-control:disabled{color:#728198!important;background:rgba(18,25,36,.66)!important}
.wf-launch select.form-control{
  appearance:none!important;padding-right:42px!important;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%239aabc0' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E")!important;
  background-repeat:no-repeat!important;background-position:right 14px center!important
}
.wf-launch input[type=checkbox],.wf-launch input[type=radio]{accent-color:#4b9bff}
.wf-launch .wf-help,.wf-launch small.muted{display:block;margin-top:7px!important;color:#8294aa!important;font-size:11px!important;line-height:1.55}
.wf-launch .wf-audience-group,.wf-launch .wf-audience-subgroup{
  margin:14px 0!important;border:1px solid var(--wf-border)!important;border-radius:16px!important;
  background:linear-gradient(145deg,rgba(29,43,61,.64),rgba(18,27,40,.7))!important;overflow:hidden
}
.wf-launch .wf-audience-group>summary,.wf-launch .wf-audience-subgroup>summary{
  display:flex;align-items:center;justify-content:space-between;min-height:54px;
  padding:14px 17px!important;color:#e1eaf5!important;font-size:13px!important;font-weight:700;
  cursor:pointer;list-style:none;transition:background .16s
}
.wf-launch .wf-audience-group>summary::-webkit-details-marker,.wf-launch .wf-audience-subgroup>summary::-webkit-details-marker{display:none}
.wf-launch .wf-audience-group>summary:hover,.wf-launch .wf-audience-subgroup>summary:hover{background:rgba(103,144,198,.09)}
.wf-launch .wf-audience-group>summary::after,.wf-launch .wf-audience-subgroup>summary::after{
  content:"";width:8px;height:8px;border-right:1.5px solid #9eb0c7;border-bottom:1.5px solid #9eb0c7;
  transform:rotate(45deg);transition:transform .18s
}
.wf-launch .wf-audience-group[open]>summary::after,.wf-launch .wf-audience-subgroup[open]>summary::after{transform:rotate(225deg)}
.wf-audience-group-body,.wf-audience-subgroup-body{padding:3px 17px 18px!important}
.wf-launch .wf-target-panel,.wf-launch .wf-target-card,.wf-launch .wf-targeting-block{
  border:1px solid var(--wf-border)!important;border-radius:14px!important;background:rgba(11,19,29,.45)!important
}
.wf-launch .wf-target-head{color:#b8c7d9}
.wf-launch .wf-target-chip,.wf-launch .rm-module-pill,.wf-launch .rm-module-pills>*{
  border:1px solid rgba(83,155,242,.34)!important;border-radius:999px!important;
  background:rgba(51,119,201,.19)!important;color:#d9ebff!important
}
.wf-launch .remask-targeting-autocomplete-panel,.wf-launch .rm-module-options,.wf-launch .rm-meta-options{
  border:1px solid var(--wf-border-strong)!important;border-radius:14px!important;
  background:#111b29!important;box-shadow:0 18px 44px rgba(1,6,13,.55)!important;overflow:hidden
}
.wf-launch .remask-targeting-autocomplete-panel>*:hover,.wf-launch .rm-module-options>*:hover,.wf-launch .rm-meta-options>*:hover{
  background:rgba(64,130,210,.18)!important
}
.wf-launch .wf-profile-grid,.wf-launch .wf-profile-picker,.wf-launch .wf-account-list{
  border:1px solid var(--wf-border)!important;border-radius:14px!important;background:rgba(8,15,24,.52)!important
}
.wf-launch .table-responsive{border:1px solid var(--wf-border)!important;border-radius:14px!important;background:rgba(8,15,24,.4)}
.wf-launch table{border-collapse:separate;border-spacing:0}
.wf-launch th{color:#9eb0c5!important;font-size:10px!important;font-weight:800!important;letter-spacing:.09em;text-transform:uppercase}
.wf-launch td,.wf-launch th{border-color:rgba(143,169,201,.11)!important}
.wf-launch button:not(.wf-mobile-back):not(.wf-mobile-summary){
  min-height:40px;border-radius:11px!important;font-weight:650!important;transition:transform .16s,border-color .16s,background .16s,box-shadow .16s
}
.wf-launch button:not(:disabled):hover{transform:translateY(-1px)}
.wf-launch .btn-primary,.wf-launch .btn-info,.wf-launch .btn-success{
  border:1px solid rgba(104,176,255,.46)!important;
  background:linear-gradient(130deg,#367fe6,#3560d9)!important;color:#fff!important;
  box-shadow:0 8px 20px rgba(41,105,207,.2)
}
.wf-launch>.wf-live-summary,.wf-launch .rm-estimate-rail>.wf-live-summary{
  grid-column:2;grid-row:3;position:sticky;top:86px;align-self:start;
  max-height:calc(100vh - 176px);overflow:auto;margin:0!important;
  border:1px solid rgba(107,157,222,.22);border-radius:20px;
  background:
    radial-gradient(ellipse at 100% 0,rgba(79,113,230,.15),transparent 58%),
    linear-gradient(155deg,#182638,#121c2a);
  box-shadow:0 18px 42px rgba(2,8,16,.3);scrollbar-width:thin
}
.wf-live-summary>summary{
  position:sticky;top:0;z-index:2;display:flex;align-items:flex-start;justify-content:space-between;
  gap:12px;padding:20px 20px 17px!important;background:rgba(21,33,49,.96)!important;
  border-bottom:1px solid var(--wf-border);list-style:none;cursor:pointer
}
.wf-live-summary>summary::-webkit-details-marker{display:none}
.wf-live-summary-heading{min-width:0}
.wf-live-summary-title{color:#f3f7fc!important;font-size:15px!important;font-weight:760!important;letter-spacing:-.02em}
.wf-live-summary-caption{margin-top:5px;color:#8fa3bc!important;font-size:11px!important}
.wf-live-status{
  flex:0 0 auto;border:1px solid rgba(86,202,151,.32)!important;border-radius:999px!important;
  padding:7px 10px!important;background:rgba(33,134,96,.17)!important;color:#9ce2c0!important;
  font-size:9px!important;font-weight:800!important;letter-spacing:.07em
}
.wf-live-summary-body{display:flex!important;flex-direction:column;gap:0!important;padding:6px 18px 4px!important}
.wf-live-row{padding:11px 2px!important;border-top:1px solid rgba(142,169,204,.12)!important}
.wf-live-row-label{margin-bottom:4px!important;color:#8298b3!important;font-size:9px!important;letter-spacing:.1em!important}
.wf-live-row-value{color:#e0eaf6!important;font-size:12px!important;line-height:1.45!important}
.wf-live-row[data-wf-summary-key="budget"]{
  margin:8px 0;padding:13px 14px!important;border:1px solid rgba(67,145,243,.25)!important;
  border-radius:13px;background:rgba(42,104,184,.11)
}
.wf-live-row[data-wf-summary-key="budget"] .wf-live-row-label{color:#90baff!important}
.wf-live-row[data-wf-summary-key="budget"] .wf-live-row-value{font-size:14px!important;font-weight:760}
.wf-summary-more{margin:9px 0 4px;border:1px solid rgba(142,169,204,.14);border-radius:12px;background:rgba(8,15,24,.25)}
.wf-summary-more>summary{padding:12px 13px;color:#b6c8dd;font-size:11px;font-weight:700;cursor:pointer;list-style:none}
.wf-summary-more>summary::-webkit-details-marker{display:none}
.wf-summary-more>summary::after{content:"+";float:right;color:#81adf5;font-size:16px;line-height:12px}
.wf-summary-more[open]>summary::after{content:"−"}
.wf-summary-more-body{padding:0 13px 8px}
.wf-live-summary-foot{padding:12px 20px 16px!important;border-top:1px solid rgba(142,169,204,.12);color:#8fa2b8!important;font-size:10px!important;line-height:1.5}
.wf-mobile-actions{
  left:200px!important;right:0!important;bottom:0;display:grid!important;z-index:500;
  grid-template-columns:auto auto minmax(0,1fr) auto auto auto;gap:9px;
  padding:10px max(24px,calc((100vw - 1500px)/2)) calc(10px + env(safe-area-inset-bottom)) max(24px,calc((100vw - 1500px)/2));
  border-top:1px solid rgba(143,169,201,.2);background:rgba(12,19,29,.94);
  box-shadow:0 -14px 36px rgba(0,0,0,.28);backdrop-filter:blur(22px)
}
.wf-mobile-actions button{min-height:44px!important;border-radius:12px!important;padding:9px 14px!important;font-size:12px!important}
.wf-workflow-status{border:1px solid rgba(86,159,244,.32)!important;background:rgba(38,96,165,.22)!important;color:#b7d5ff!important}
.wf-mobile-step{color:#c0cfe0!important}
.wf-mobile-actions .wf-mobile-next,.wf-mobile-actions #reviewLaunch,.wf-mobile-actions #launchButton{
  border-color:rgba(120,183,255,.56)!important;background:linear-gradient(125deg,#398df4,#605ee7)!important;
  color:#fff!important;box-shadow:0 7px 18px rgba(46,110,215,.25)
}
#wfSaveTemplateButton{border:1px solid rgba(171,190,215,.28)!important;background:rgba(255,255,255,.035)!important;color:#dce7f4!important}
.wf-launch .wf-card-disclosure{border:1px solid var(--wf-border)!important;border-radius:15px!important;background:rgba(10,17,27,.38)!important}
.wf-launch .wf-card-disclosure>summary{min-height:52px;padding:14px 17px!important}
.wf-launch .wf-card-disclosure-body{padding:2px 17px 16px!important}
#launchMetaAllFields.wf-advanced{margin:15px 0 4px!important;padding:0!important;border:0!important;background:transparent!important;box-shadow:none!important}
#launchMetaAllFields.wf-advanced>details{border:1px solid var(--wf-border)!important;border-radius:16px!important;background:linear-gradient(145deg,rgba(27,40,57,.78),rgba(16,25,37,.86))!important;overflow:hidden}
#launchMetaAllFields.wf-advanced>details>summary{padding:16px 18px!important;color:#dce9f8!important;font-size:13px;font-weight:700}
#launchMetaAllFields .rm-meta-note{color:#8fa2b8!important;line-height:1.6}
#launchMetaAllFields .rm-meta-search{border-radius:12px!important}
.wf-launch .wf-step-card:not(.is-current-step){display:none!important}
.wf-launch .wf-step-card.is-current-step{display:block!important}
.wf-launch .wf-step-card[aria-hidden="true"]{display:none!important}
body.wf-autoupload-open{padding-bottom:calc(92px + env(safe-area-inset-bottom))}
@media(max-width:1120px){
  .wf-launch{grid-template-columns:minmax(0,1fr)!important;column-gap:0!important;row-gap:14px!important}
  .wf-launch>.wf-live-summary,.wf-launch .rm-estimate-rail>.wf-live-summary{
    grid-column:1;grid-row:auto;position:static;max-height:350px;margin:0 0 0!important
  }
  .wf-launch>.wf-step-nav{grid-template-columns:repeat(5,minmax(110px,1fr));overflow:auto}
}
@media(max-width:720px){
  .wf-launch>.wf-step-nav{display:none!important}
  .wf-launch>.launch-card.wf-step-card{padding:20px 17px!important;border-radius:17px!important}
  .wf-launch>.launch-card.wf-step-card>h5{font-size:18px!important;padding-bottom:15px;margin-bottom:18px!important}
  .wf-launch .row{grid-template-columns:minmax(0,1fr)!important;gap:15px!important}
  .wf-launch .row>*{grid-column:1/-1!important}
  .wf-launch .wf-step-nav{display:flex;position:sticky;top:56px;grid-template-columns:none;gap:7px;margin:10px 0;padding:6px;border-radius:13px}
  .wf-launch .rm-estimate-rail{grid-template-columns:minmax(0,1fr)!important}
  .wf-live-summary>summary{padding:14px 15px!important}
  .wf-live-summary-body{padding:4px 14px!important}
  .wf-launch .wf-audience-group>summary{min-height:49px;padding:12px 14px!important}
  .wf-audience-group-body{padding:2px 13px 15px!important}
  .wf-mobile-actions{left:0!important;right:0!important;grid-template-columns:auto minmax(0,1fr) auto auto auto;gap:6px;padding:8px 9px calc(8px + env(safe-area-inset-bottom))}
  .wf-mobile-actions button{min-height:42px!important;padding:8px 9px!important;font-size:10px!important}
  .wf-workflow-status{display:none!important}
  .wf-workflow-step strong{display:block;margin:0 0 2px}
  .wf-launch .wf-card-disclosure>summary{padding:12px 14px!important}
}
@media(max-width:390px){
  .wf-mobile-actions{grid-template-columns:auto minmax(0,1fr) auto auto}
  .wf-mobile-summary{display:none!important}
  .wf-launch>.launch-card.wf-step-card{padding:18px 14px!important}
}
@media(prefers-reduced-motion:reduce){
  .wf-step-nav a,.wf-step-index,.wf-launch .form-control{transition:none!important}
}
`;

    style.textContent += `
/* Direct choice controls for high-impact campaign settings */
.wf-choice-source{position:absolute!important;width:1px!important;height:1px!important;min-height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;clip-path:inset(50%)!important;white-space:nowrap!important;opacity:0!important;pointer-events:none!important}
.wf-choice-control{display:grid;gap:9px;margin-top:9px}
.wf-choice-cards{grid-template-columns:repeat(2,minmax(0,1fr))}
.wf-choice-pills{display:flex;flex-wrap:wrap;gap:8px}
.wf-choice-segments{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));padding:4px;border:1px solid var(--wf-border-strong);border-radius:13px;background:rgba(7,13,22,.58)}
.wf-choice-option{
  position:relative;display:flex;align-items:flex-start;gap:12px;min-width:0;min-height:56px;
  padding:13px 14px;border:1px solid rgba(146,171,201,.17)!important;border-radius:13px!important;
  background:rgba(12,20,31,.68)!important;color:#dbe6f4!important;text-align:left;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.025);cursor:pointer
}
.wf-choice-option:hover{border-color:rgba(94,159,239,.48)!important;background:rgba(31,52,77,.75)!important}
.wf-choice-option.is-selected{border-color:rgba(77,159,255,.86)!important;background:linear-gradient(135deg,rgba(45,112,199,.33),rgba(77,83,176,.22))!important;color:#f5f9ff!important;box-shadow:inset 0 0 0 1px rgba(85,167,255,.14),0 5px 16px rgba(25,96,175,.12)}
.wf-choice-icon{display:grid;place-items:center;width:31px;height:31px;flex:0 0 31px;border:1px solid rgba(144,171,205,.2);border-radius:10px;background:rgba(255,255,255,.035);color:#99b6d9;font-size:16px;font-weight:750}
.wf-choice-option.is-selected .wf-choice-icon{border-color:rgba(108,180,255,.4);background:rgba(69,139,221,.2);color:#b8dcff}
.wf-choice-copy{display:grid;gap:4px;min-width:0}
.wf-choice-copy strong{font-size:12px;font-weight:700;line-height:1.35}
.wf-choice-copy small{color:#93a6be;font-size:10px;font-weight:450;line-height:1.4}
.wf-choice-check{display:none;position:absolute;top:9px;right:10px;width:17px;height:17px;border-radius:50%;background:linear-gradient(135deg,#48aaff,#5872ff);color:#fff}
.wf-choice-option.is-selected .wf-choice-check{display:grid;place-items:center}
.wf-choice-option.is-selected .wf-choice-check::after{content:"✓";font-size:10px;font-weight:800}
.wf-choice-pills .wf-choice-option{min-height:38px;padding:9px 13px;align-items:center;border-radius:999px!important}
.wf-choice-pills .wf-choice-icon,.wf-choice-pills .wf-choice-check{display:none}
.wf-choice-pills .wf-choice-copy strong{font-size:11px}
.wf-choice-segments .wf-choice-option{min-height:48px;align-items:center;justify-content:center;padding:10px;border:1px solid transparent!important;background:transparent!important;text-align:center}
.wf-choice-segments .wf-choice-option.is-selected{border-color:rgba(88,158,245,.4)!important;border-radius:10px!important;background:rgba(48,107,177,.25)!important}
.wf-choice-segments .wf-choice-icon{display:none}
.wf-choice-segments .wf-choice-check{display:none!important}
.wf-campaign-builder{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:20px 18px!important}
.wf-campaign-builder>.wf-campaign-name-field,.wf-campaign-builder>.wf-campaign-objective-field,.wf-campaign-builder>.wf-campaign-category-field{grid-column:1/-1!important}
.wf-campaign-objective-field .wf-choice-cards{grid-template-columns:repeat(3,minmax(0,1fr))}
.wf-campaign-category-field .wf-choice-pills{gap:7px}
.wf-campaign-builder .wf-choice-option{min-height:86px}
.wf-campaign-category-field .wf-choice-option{min-height:38px}
.wf-choice-option:focus-visible{outline:3px solid rgba(79,160,255,.4)!important;outline-offset:2px}
@media(max-width:720px){
 .wf-choice-cards{grid-template-columns:repeat(2,minmax(0,1fr))}
 .wf-campaign-builder{grid-template-columns:minmax(0,1fr)!important}
 .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:repeat(2,minmax(0,1fr))}
 .wf-choice-copy small{font-size:9px}
}
@media(max-width:390px){
 .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:minmax(0,1fr)}
 .wf-choice-option{padding:11px}
}
`;
    style.textContent += `
/* AutoUpload Studio: fresh workspace composition */
.wf-launch{
  --wf-canvas:#0b1220;--wf-surface:#121d2d;--wf-surface-raised:#19283a;
  --wf-border:rgba(151,177,211,.14);--wf-border-strong:rgba(151,177,211,.25);
  --wf-text:#f3f7fc;--wf-muted:#96a8bf;--wf-blue:#4aa5ff;--wf-violet:#856dff;
  position:relative;isolation:isolate;
  padding-bottom:calc(14px + env(safe-area-inset-bottom));
  background:
    radial-gradient(ellipse at 12% 0%,rgba(50,104,190,.10),transparent 34%),
    radial-gradient(ellipse at 90% 12%,rgba(101,75,201,.10),transparent 28%);
}
.wf-launch>.app-page-head{
  position:relative;display:flex;align-items:flex-end;justify-content:space-between;gap:22px;
  min-height:128px;margin:0!important;padding:25px 28px 22px!important;
  overflow:hidden;border:1px solid rgba(135,170,216,.17)!important;border-radius:22px!important;
  background:
    radial-gradient(circle at 88% 18%,rgba(91,126,255,.20),transparent 22%),
    radial-gradient(circle at 100% 100%,rgba(37,145,239,.15),transparent 35%),
    linear-gradient(115deg,#152337 0%,#151e31 56%,#1c2039 100%)!important;
  box-shadow:0 18px 48px rgba(2,8,18,.22)!important;
}
.wf-launch>.app-page-head::after{
  content:"";position:absolute;right:5%;top:-104px;width:260px;height:260px;border-radius:50%;
  border:1px solid rgba(135,173,255,.19);box-shadow:0 0 0 22px rgba(120,149,255,.035),0 0 0 46px rgba(120,149,255,.025);
  pointer-events:none
}
.wf-launch>.app-page-head h1,.wf-launch>.app-page-head h2{position:relative;z-index:1;font-size:30px!important;line-height:1.12!important;letter-spacing:-.045em!important}
.wf-launch>.app-page-head p,.wf-launch>.app-page-head .muted{max-width:640px;color:#a8b8cd!important;font-size:13px!important;line-height:1.55}
.wf-launch>.app-page-head .badge,.wf-launch>.app-page-head .status-pill{position:relative;z-index:1;border:1px solid rgba(101,160,247,.34)!important;border-radius:999px!important;background:rgba(40,103,184,.20)!important;color:#c8e2ff!important}
.wf-launch>.wf-step-nav{
  position:relative;top:auto!important;z-index:2;gap:5px;margin:0!important;padding:5px!important;
  border:0!important;border-bottom:1px solid rgba(140,170,210,.18)!important;border-radius:0!important;
  background:transparent!important;box-shadow:none!important;backdrop-filter:none!important
}
.wf-step-nav a{min-height:46px;padding:7px 10px;border-radius:11px;color:#8598b1}
.wf-step-nav a.is-current{
  border-color:rgba(94,160,255,.28);background:linear-gradient(100deg,rgba(50,132,232,.20),rgba(107,88,222,.12));
  box-shadow:inset 0 0 0 1px rgba(134,174,255,.05)
}
.wf-step-nav a.is-current .wf-step-index{box-shadow:0 0 0 4px rgba(75,148,255,.11)}
.wf-launch>.launch-card.wf-step-card{
  position:relative;margin-top:4px!important;padding:26px 28px!important;border-color:rgba(148,176,212,.15)!important;
  border-radius:22px!important;background:
    radial-gradient(ellipse at 0 0,rgba(58,124,211,.09),transparent 45%),
    linear-gradient(155deg,rgba(20,31,47,.98),rgba(15,24,37,.98))!important;
  box-shadow:0 22px 60px rgba(2,8,18,.26)!important
}
.wf-launch>.launch-card.wf-step-card>h5{
  margin:0 0 22px!important;padding:0 0 17px;border-bottom:1px solid rgba(146,174,211,.14);
  font-size:22px!important;letter-spacing:-.04em
}
.wf-launch>.launch-card.wf-step-card>h5::before{width:5px;height:28px;background:linear-gradient(180deg,#61c4ff,#8171ff);box-shadow:0 0 22px rgba(74,155,255,.5)}
.wf-launch .wf-section-heading{margin:24px 0 13px!important;color:#92a9c7!important;font-size:10px!important;letter-spacing:.15em}
.wf-launch .wf-section-heading::after{background:linear-gradient(90deg,rgba(119,158,208,.25),transparent)}
.wf-launch .row{gap:17px 20px!important}
.wf-launch label{color:#c4d1e2!important;font-size:12px!important;letter-spacing:.015em}
.wf-launch .form-control:not(.rm-meta-visual-source):not(.rm-module-source):not(.wf-profile-native):not([type=hidden]):not([hidden]){
  min-height:48px!important;border-color:rgba(142,172,211,.20)!important;border-radius:12px!important;
  background:rgba(7,14,24,.52)!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.025)
}
.wf-launch .form-control:focus{border-color:#5faeff!important;box-shadow:0 0 0 4px rgba(72,150,244,.14)!important}
.wf-launch .wf-choice-option{
  min-height:68px;padding:13px 15px;border-color:rgba(141,171,210,.16)!important;border-radius:14px!important;
  background:linear-gradient(150deg,rgba(24,37,56,.88),rgba(13,22,35,.90))!important
}
.wf-launch .wf-choice-option:hover{border-color:rgba(93,165,255,.46)!important;background:linear-gradient(145deg,rgba(34,59,91,.94),rgba(20,31,49,.94))!important}
.wf-launch .wf-choice-option.is-selected{
  border-color:rgba(76,163,255,.78)!important;background:
    radial-gradient(ellipse at 0 0,rgba(59,144,242,.22),transparent 70%),
    linear-gradient(145deg,rgba(25,58,97,.96),rgba(24,35,65,.97))!important;
  box-shadow:inset 0 0 0 1px rgba(112,180,255,.18),0 8px 22px rgba(35,106,192,.13)
}
.wf-launch .wf-choice-icon{display:grid;place-items:center;width:32px;height:32px;flex:0 0 32px;border:1px solid rgba(111,167,234,.2);border-radius:10px;background:rgba(75,136,213,.13);color:#a9d0ff;font-size:15px}
.wf-launch .wf-choice-copy strong{font-size:13px!important;color:#eff6ff}
.wf-launch .wf-choice-copy small{font-size:10px!important;color:#95a9c2}
.wf-launch .wf-choice-check{border-color:rgba(164,185,211,.28)}
.wf-launch .wf-choice-option.is-selected .wf-choice-check{border-color:#83c0ff;background:#4a9cf4;box-shadow:0 0 0 3px rgba(76,157,245,.14)}
.wf-launch .wf-audience-group,.wf-launch .wf-audience-subgroup{
  margin:12px 0!important;border-color:rgba(144,174,213,.15)!important;border-radius:15px!important;
  background:linear-gradient(145deg,rgba(23,36,54,.72),rgba(12,21,34,.8))!important
}
.wf-launch .wf-audience-group>summary,.wf-launch .wf-audience-subgroup>summary{
  min-height:51px;padding:13px 16px!important;font-size:12px!important
}
.wf-launch .wf-target-panel,.wf-launch .wf-target-card,.wf-launch .wf-targeting-block{
  border-color:rgba(144,174,213,.16)!important;border-radius:13px!important;background:rgba(7,14,24,.40)!important
}
.wf-launch .wf-target-chip,.wf-launch .rm-module-pill,.wf-launch .rm-module-pills>*{
  border-color:rgba(113,169,244,.30)!important;background:rgba(51,113,194,.16)!important;color:#dcecff!important
}
.wf-launch>.wf-live-summary,.wf-launch .rm-estimate-rail>.wf-live-summary{
  position:sticky;top:78px;max-height:none!important;overflow:visible!important;margin:4px 0 0!important;
  padding:0!important;border:1px solid rgba(141,171,212,.16)!important;border-radius:21px!important;
  background:linear-gradient(155deg,rgba(23,36,55,.97),rgba(14,23,36,.98))!important;
  box-shadow:0 18px 48px rgba(2,8,18,.25)!important
}
.wf-live-summary>summary{
  position:relative;display:grid!important;grid-template-columns:minmax(0,1fr) auto;align-items:start!important;
  gap:10px 12px;padding:18px!important;border:0!important;border-radius:21px!important;background:transparent!important
}
.wf-live-summary-heading{display:grid;gap:4px}
.wf-live-summary-title{font-size:14px!important}
.wf-live-summary-caption{color:#8fa4bf!important}
.wf-live-status{padding:6px 8px!important;font-size:8px!important;letter-spacing:.04em}
.wf-summary-quickfacts{grid-column:1/-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;width:100%;margin-top:3px}
.wf-summary-quickfact{display:grid;gap:5px;min-width:0;padding:10px 11px;border:1px solid rgba(147,176,212,.12);border-radius:12px;background:rgba(7,14,24,.36)}
.wf-summary-quickfact>span{color:#8198b5;font-size:9px;font-weight:750;letter-spacing:.09em;text-transform:uppercase}
.wf-summary-quickfact>strong{overflow:hidden;color:#dce8f7;font-size:11px;font-weight:650;line-height:1.4;text-overflow:ellipsis}
.wf-summary-more{border-color:rgba(141,171,212,.14);background:rgba(7,14,24,.24)}
.wf-live-summary-body{padding:0 16px 8px!important}
.wf-live-row{padding:10px 2px!important}
.wf-live-summary-foot{padding:11px 17px 14px!important}
.wf-mobile-actions{
  left:200px!important;right:0!important;grid-template-columns:auto minmax(0,1fr) auto auto auto;
  gap:9px;padding:10px 22px calc(10px + env(safe-area-inset-bottom))!important;
  border-top:1px solid rgba(151,177,211,.15);background:rgba(10,17,28,.90)!important;
  box-shadow:0 -18px 46px rgba(0,0,0,.28)!important;backdrop-filter:blur(24px)!important
}
.wf-mobile-actions .wf-mobile-next,.wf-mobile-actions #reviewLaunch,.wf-mobile-actions #launchButton{
  border:1px solid rgba(146,191,255,.4)!important;background:linear-gradient(120deg,#398ff0,#765be7)!important
}
.wf-launch .btn-primary,.wf-launch .btn-info,.wf-launch .btn-success{background:linear-gradient(120deg,#398ff0,#765be7)!important}
@media(max-width:1120px){
  .wf-launch{grid-template-columns:minmax(0,1fr)!important}
  .wf-launch>.wf-live-summary,.wf-launch .rm-estimate-rail>.wf-live-summary{position:relative;top:auto;grid-column:1;grid-row:3}
}
@media(max-width:720px){
  .wf-launch>.app-page-head{display:block;min-height:auto;padding:21px 18px 18px!important;border-radius:18px!important}
  .wf-launch>.app-page-head h1,.wf-launch>.app-page-head h2{font-size:25px!important}
  .wf-launch>.app-page-head::after{right:-80px;top:-118px;width:220px;height:220px}
  .wf-launch>.launch-card.wf-step-card{padding:20px 17px!important;border-radius:18px!important}
  .wf-launch>.launch-card.wf-step-card>h5{font-size:19px!important;margin-bottom:18px!important}
  .wf-launch .row{gap:14px!important}
  .wf-launch .wf-choice-cards{grid-template-columns:repeat(2,minmax(0,1fr))}
  .wf-launch .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:repeat(2,minmax(0,1fr))}
  .wf-launch>.wf-live-summary,.wf-launch .rm-estimate-rail>.wf-live-summary{position:relative;top:auto;max-height:none;grid-row:auto}
  .wf-live-summary>summary{padding:14px!important}
  .wf-summary-quickfact{padding:9px}
  .wf-launch>.wf-step-nav{display:none!important}
  .wf-mobile-actions{left:0!important;right:0!important;grid-template-columns:auto minmax(0,1fr) auto auto!important;gap:6px;padding:8px 9px calc(8px + env(safe-area-inset-bottom))!important}
  .wf-mobile-actions .wf-mobile-step{font-size:10px}
}
@media(max-width:390px){
  .wf-launch .wf-choice-cards,.wf-launch .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:minmax(0,1fr)}
  .wf-mobile-actions{grid-template-columns:auto minmax(0,1fr) auto!important}
}
@media(prefers-reduced-motion:reduce){.wf-launch *{scroll-behavior:auto!important;transition:none!important}}
`;
    document.head.appendChild(style);

    /* The previous layered V2 palette was removed; AutoUpload uses one studio theme below. */



    /* REMASK_AUTUPLOAD_STUDIO_COMPOSITION_V3 */
    const studioV3Style=document.createElement("style");
    studioV3Style.textContent="/* AutoUpload Studio V8: compact graphite-and-lilac workspace, with Meta-style progressive disclosure. */\nbody.wf-autoupload-open{padding-bottom:calc(92px + env(safe-area-inset-bottom));color-scheme:light}\nbody.wf-autoupload-open main:has(.wf-launch){background:#f3f4f6!important;color:#262a32!important}\n.rm-estimate-layout.wf-studio-estimate-layout{width:100%!important;max-width:none!important;margin:0 auto!important;padding:22px 28px 112px!important;background:#f3f4f6!important;box-sizing:border-box}\n.wf-launch.wf-studio-v3{\n  --studio-ink:#262a32;--studio-muted:#69717d;--studio-border:#dfe2e8;--studio-border-strong:#cbd0d8;\n  --studio-surface:#fff;--studio-surface-2:#f8f9fb;--studio-accent:#8057df;--studio-accent-strong:#7046d0;\n  --studio-accent-soft:rgba(128,87,223,.09);--studio-positive:#168765;--studio-shadow:0 8px 24px rgba(25,31,42,.06);\n  --wf-line:var(--studio-border);--wf-muted:var(--studio-muted);--wf-accent:var(--studio-accent);\n  display:grid!important;grid-template-columns:minmax(0,1fr) 292px!important;grid-auto-flow:row;\n  column-gap:28px!important;row-gap:14px!important;width:100%!important;max-width:1540px!important;\n  margin:0 auto!important;color:var(--studio-ink)!important;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif!important\n}\n.wf-launch.wf-studio-v3>.app-page-head,.wf-launch.wf-studio-v3>.wf-step-nav,.wf-launch.wf-studio-v3>.wf-step-progress{grid-column:1/-1!important;min-width:0}\n.wf-launch.wf-studio-v3>.app-page-head{\n  display:block!important;position:relative;isolation:isolate;min-height:0!important;padding:2px 0 18px!important;margin:0!important;\n  border:0!important;border-bottom:1px solid var(--studio-border)!important;border-radius:0!important;background:transparent!important;\n  box-shadow:none!important;overflow:visible!important\n}\n.wf-launch.wf-studio-v3>.app-page-head:before{display:none!important}\n.wf-studio-kicker{display:block;margin:0 0 6px;color:#7651c9!important;font-size:10px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}\n.wf-studio-badge{\n  position:absolute;top:4px;right:0;display:inline-flex;align-items:center;gap:7px;padding:6px 9px;\n  border:1px solid #dce7e2!important;border-radius:999px;background:#f5faf7!important;color:#426a5c!important;\n  font-size:10px;font-weight:750;letter-spacing:.02em;text-transform:none\n}\n.wf-studio-badge i{width:7px;height:7px;border-radius:50%;background:#26a67a!important;box-shadow:0 0 0 3px rgba(38,166,122,.12)!important}\n.wf-launch.wf-studio-v3>.app-page-head h1,.wf-launch.wf-studio-v3>.app-page-head h2{\n  margin:0 0 5px!important;color:var(--studio-ink)!important;font-size:26px!important;line-height:1.2!important;letter-spacing:-.035em!important;font-weight:720!important\n}\n.wf-launch.wf-studio-v3>.app-page-head p{max-width:700px;margin:0!important;color:var(--studio-muted)!important;font-size:12px!important;line-height:1.5!important}\n.wf-launch.wf-studio-v3>.wf-step-nav{\n  position:relative!important;top:auto!important;display:grid!important;grid-template-columns:repeat(5,minmax(0,1fr))!important;gap:0!important;\n  margin:0!important;padding:0!important;border:0!important;border-bottom:1px solid var(--studio-border)!important;border-radius:0!important;\n  background:transparent!important;overflow:visible!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3>.wf-step-nav a{\n  position:relative;display:flex!important;align-items:center;gap:9px;min-width:0;min-height:44px;padding:7px 10px!important;\n  border:0!important;border-bottom:2px solid transparent!important;border-radius:0!important;background:transparent!important;\n  color:#6f7783!important;text-decoration:none!important;box-shadow:none!important;transform:none!important;transition:color .15s ease,border-color .15s ease!important\n}\n.wf-launch.wf-studio-v3>.wf-step-nav a:hover,.wf-launch.wf-studio-v3>.wf-step-nav a:focus-visible{\n  background:rgba(128,87,223,.045)!important;color:#4f3c78!important;outline-color:var(--studio-accent)!important\n}\n.wf-launch.wf-studio-v3>.wf-step-nav a.is-current{\n  border-bottom-color:var(--studio-accent)!important;background:transparent!important;color:#302643!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3 .wf-step-index{\n  display:grid;place-items:center;width:24px;height:24px;flex:0 0 24px;border:1px solid #d6d9df!important;border-radius:50%;\n  background:#fff!important;color:#737a85!important;font-size:11px;font-weight:700\n}\n.wf-launch.wf-studio-v3>.wf-step-nav a.is-current .wf-step-index{\n  border-color:var(--studio-accent)!important;background:var(--studio-accent)!important;color:#fff!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3>.wf-step-nav a.is-complete .wf-step-index{\n  border-color:#bfe3d5!important;background:#eef8f3!important;color:#168765!important\n}\n.wf-launch.wf-studio-v3 .wf-step-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-weight:650}\n.wf-launch.wf-studio-v3>.wf-step-progress{height:2px!important;margin:-1px 0 2px!important;border-radius:99px;background:#e6e8ed!important;overflow:hidden}\n.wf-launch.wf-studio-v3 .wf-step-progress>span{background:var(--studio-accent)!important;box-shadow:none!important}\n.wf-launch.wf-studio-v3>.wf-live-summary{\n  grid-column:2!important;grid-row:4!important;position:sticky!important;top:18px!important;align-self:start!important;\n  max-height:calc(100dvh - 36px)!important;overflow:auto!important;margin:0!important;padding:0!important;\n  border:1px solid var(--studio-border)!important;border-radius:14px!important;background:#fff!important;\n  box-shadow:var(--studio-shadow)!important;color:var(--studio-ink)!important\n}\n.wf-launch.wf-studio-v3>.launch-card.wf-step-card,.wf-launch.wf-studio-v3>#launchMetaAllFields{\n  grid-column:1!important;min-width:0;margin:0!important\n}\n.wf-launch.wf-studio-v3>.launch-card.wf-step-card{\n  padding:0!important;border:0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important;\n  color:var(--studio-ink)!important;scroll-margin-top:90px\n}\n.wf-launch.wf-studio-v3>.launch-card.wf-step-card[hidden],.wf-launch.wf-studio-v3>.wf-step-card[hidden]{display:none!important}\n.wf-launch.wf-studio-v3 .wf-step-card:not(.is-current-step){display:none!important}\n.wf-launch.wf-studio-v3 .wf-step-card.is-current-step{display:block!important}\n.wf-launch.wf-studio-v3>.launch-card.wf-step-card>h5{\n  display:flex;align-items:center;gap:10px;margin:0 0 7px!important;padding:2px 0 9px!important;\n  border-bottom:1px solid var(--studio-border)!important;color:var(--studio-ink)!important;font-size:22px!important;\n  font-weight:720!important;letter-spacing:-.03em!important\n}\n.wf-launch.wf-studio-v3>.launch-card.wf-step-card>h5:before{content:none!important}\n.wf-launch.wf-studio-v3 .wf-step-context{margin:0 0 16px!important;color:var(--studio-muted)!important;font-size:12px!important;line-height:1.5!important}\n.wf-launch.wf-studio-v3 .wf-section-heading{\n  display:flex;align-items:center;gap:9px;margin:17px 0 9px!important;padding:0!important;border:0!important;\n  color:#737b87!important;font-size:10px!important;font-weight:750!important;letter-spacing:.09em;text-transform:uppercase\n}\n.wf-launch.wf-studio-v3 .wf-section-heading:after{content:\"\";height:1px;flex:1;background:#e3e6eb}\n.wf-launch.wf-studio-v3 .row{align-items:start!important;gap:12px 16px!important;margin-top:0!important}\n.wf-launch.wf-studio-v3 label{\n  display:block!important;margin:0 0 5px!important;color:#525a66!important;font-size:11px!important;font-weight:650!important;line-height:1.4!important\n}\n.wf-launch.wf-studio-v3 label:has(input[type=checkbox],input[type=radio]){display:flex!important;align-items:center;gap:8px}\n.wf-launch.wf-studio-v3 .form-control:not(.rm-meta-visual-source):not(.rm-module-source):not(.wf-profile-native):not([type=hidden]):not([hidden]){\n  min-height:39px!important;height:auto!important;padding:8px 10px!important;border:1px solid #d7dbe2!important;border-radius:8px!important;\n  background:#fff!important;color:#242832!important;box-shadow:none!important;font-size:12px!important\n}\n.wf-launch.wf-studio-v3 textarea.form-control:not(.rm-module-source){min-height:92px!important}\n.wf-launch.wf-studio-v3 .form-control::placeholder{color:#a3a9b3!important;opacity:1}\n.wf-launch.wf-studio-v3 .form-control:focus{\n  border-color:#9a7be5!important;outline:0!important;box-shadow:0 0 0 3px rgba(128,87,223,.12)!important\n}\n.wf-launch.wf-studio-v3 .form-control:disabled,.wf-launch.wf-studio-v3 .form-control[readonly]{\n  background:#f2f3f5!important;color:#9298a2!important\n}\n.wf-launch.wf-studio-v3 .wf-help,.wf-launch.wf-studio-v3 small,.wf-launch.wf-studio-v3 .muted{\n  color:#7c838e!important;font-size:10px!important;line-height:1.45!important\n}\n.wf-launch.wf-studio-v3 .wf-profile-grid{display:grid!important;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px!important;margin:8px 0 12px!important}\n.wf-launch.wf-studio-v3 .wf-profile-grid label{\n  min-height:40px;padding:8px 10px!important;border:1px solid #dfe2e8!important;border-radius:9px!important;background:#fff!important;\n  color:#353a44!important;box-shadow:none!important;cursor:pointer\n}\n.wf-launch.wf-studio-v3 .wf-profile-grid label:has(input:checked){\n  border-color:#a68ae9!important;background:#f5f0ff!important;color:#4a347c!important\n}\n.wf-launch.wf-studio-v3 .wf-profile-grid input{accent-color:var(--studio-accent)!important;width:15px;height:15px}\n.wf-launch.wf-studio-v3 .wf-profile-native{display:none!important}\n.wf-launch.wf-studio-v3 #wfResourceActions{display:flex;justify-content:flex-end;gap:8px;margin:10px 0 0}\n.wf-launch.wf-studio-v3 #wfResourceActions button{min-height:38px!important;border-radius:8px!important;padding:8px 12px!important}\n.wf-launch.wf-studio-v3 #wfResourceActions .wf-resource-primary{\n  border:1px solid var(--studio-accent)!important;background:var(--studio-accent)!important;color:#fff!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3 #wfResourceActions .wf-resource-secondary{\n  border:1px solid var(--studio-border)!important;background:#fff!important;color:#444a54!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3 .wf-empty-state{\n  min-height:0!important;padding:20px!important;border:1px dashed #cbd0d8!important;border-radius:10px!important;\n  background:#fff!important;color:#5d6570!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3 .wf-empty-state>a{color:#7046d0!important}\n.wf-launch.wf-studio-v3 .wf-campaign-builder{display:grid!important;grid-template-columns:repeat(12,minmax(0,1fr))!important;gap:15px 16px!important}\n.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-name-field{grid-column:1/-1!important}\n.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-objective-field{grid-column:1/-1!important}\n.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-category-field{grid-column:1/-1!important}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:8px!important}\n.wf-launch.wf-studio-v3 .wf-choice-option{\n  display:flex;align-items:center;min-width:0;min-height:44px!important;padding:9px 11px!important;gap:9px!important;\n  border:1px solid #dfe2e8!important;border-radius:8px!important;background:#fff!important;color:#363b45!important;\n  box-shadow:none!important;text-align:left;cursor:pointer\n}\n.wf-launch.wf-studio-v3 .wf-choice-option:hover{border-color:#b7a3e9!important;background:#faf8ff!important}\n.wf-launch.wf-studio-v3 .wf-choice-option.is-selected{\n  border-color:#9e7cdf!important;background:#f4efff!important;color:#503680!important;box-shadow:inset 0 0 0 1px rgba(128,87,223,.1)!important\n}\n.wf-launch.wf-studio-v3 .wf-choice-icon{\n  width:27px;height:27px;flex:0 0 27px;border:1px solid #e4def1;border-radius:7px;background:#f7f4fc;color:#7651c9;font-size:14px\n}\n.wf-launch.wf-studio-v3 .wf-choice-option.is-selected .wf-choice-icon{border-color:#d7c7f5;background:#eee6ff;color:#7046d0}\n.wf-launch.wf-studio-v3 .wf-choice-copy strong{font-size:12px;color:inherit}\n.wf-launch.wf-studio-v3 .wf-choice-copy small{font-size:10px;color:#737b87;line-height:1.35}\n.wf-launch.wf-studio-v3 .wf-choice-segments{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px;padding:3px;border:1px solid #dfe2e8;border-radius:9px;background:#f3f4f6}\n.wf-launch.wf-studio-v3 .wf-choice-segments .wf-choice-option{justify-content:center;min-height:34px!important;padding:6px 9px!important;border:0!important;background:transparent!important}\n.wf-launch.wf-studio-v3 .wf-choice-segments .wf-choice-option.is-selected{background:#fff!important;color:#4e367c!important;box-shadow:0 1px 3px rgba(25,31,42,.1)!important}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills{display:flex;gap:6px;flex-wrap:wrap}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option{min-height:34px!important;padding:6px 10px!important}\n.wf-launch.wf-studio-v3 .wf-target-accordion,.wf-launch.wf-studio-v3 .wf-audience-inline{\n  display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 16px;min-width:0\n}\n.wf-launch.wf-studio-v3 .wf-audience-inline{margin:8px 0 2px!important;padding:0!important;border:0!important;border-radius:0!important;background:transparent!important}\n.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field:has(#geoQuery){grid-column:1/-1}\n.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field:has(#languageQuery){grid-column:1/-1}\n.wf-launch.wf-studio-v3 .wf-audience-field{min-width:0}\n.wf-launch.wf-studio-v3 .wf-audience-field .form-control{width:100%}\n.wf-launch.wf-studio-v3 .wf-audience-group,.wf-launch.wf-studio-v3 .wf-audience-delivery,.wf-launch.wf-studio-v3 .wf-audience-subgroup,.wf-launch.wf-studio-v3 .wf-audience-estimate{\n  display:block;margin:0!important;padding:0!important;border:0!important;border-top:1px solid #e2e5ea!important;border-radius:0!important;\n  background:transparent!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3 .wf-audience-group>summary,.wf-launch.wf-studio-v3 .wf-audience-delivery>summary,.wf-launch.wf-studio-v3 .wf-audience-subgroup>summary,.wf-launch.wf-studio-v3 .wf-audience-estimate>summary{\n  display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:42px;padding:8px 0!important;\n  color:#30343c!important;font-size:12px!important;font-weight:650!important;cursor:pointer;list-style:none\n}\n.wf-launch.wf-studio-v3 .wf-audience-group>summary::-webkit-details-marker,.wf-launch.wf-studio-v3 .wf-audience-delivery>summary::-webkit-details-marker{display:none}\n.wf-launch.wf-studio-v3 .wf-audience-group>summary:after,.wf-launch.wf-studio-v3 .wf-audience-delivery>summary:after,.wf-launch.wf-studio-v3 .wf-audience-subgroup>summary:after{\n  content:\"\";width:7px;height:7px;flex:0 0 7px;border-right:1.5px solid #8a9099;border-bottom:1.5px solid #8a9099;transform:rotate(45deg)\n}\n.wf-launch.wf-studio-v3 .wf-audience-group[open]>summary:after,.wf-launch.wf-studio-v3 .wf-audience-delivery[open]>summary:after,.wf-launch.wf-studio-v3 .wf-audience-subgroup[open]>summary:after{transform:rotate(225deg)}\n.wf-launch.wf-studio-v3 .wf-audience-group-body,.wf-launch.wf-studio-v3 .wf-audience-delivery-body{padding:4px 0 12px!important}\n.wf-launch.wf-studio-v3 .wf-compact-field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 16px;min-width:0}\n.wf-launch.wf-studio-v3 .wf-compact-field{min-width:0;width:100%!important;max-width:none!important;flex:none!important;padding:0!important;margin:0!important}\n.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field:nth-child(3){grid-column:auto}\n.wf-launch.wf-studio-v3 .wf-audience-subgroup-body{padding:4px 0 10px!important}\n.wf-launch.wf-studio-v3 .wf-resource-utilities,.wf-launch.wf-studio-v3 .wf-resource-utility,.wf-launch.wf-studio-v3 .wf-target-panel{\n  border:0!important;border-top:1px solid #e2e5ea!important;border-radius:0!important;background:transparent!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3 .wf-resource-utilities>summary,.wf-launch.wf-studio-v3 .wf-resource-utility>summary,.wf-launch.wf-studio-v3 .wf-target-panel>summary{\n  display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:42px;padding:8px 0!important;color:#353a44!important;background:transparent!important;list-style:none\n}\n.wf-launch.wf-studio-v3 .wf-resource-utilities>summary::-webkit-details-marker,.wf-launch.wf-studio-v3 .wf-resource-utility>summary::-webkit-details-marker,.wf-launch.wf-studio-v3 .wf-target-panel>summary::-webkit-details-marker{display:none}\n.wf-launch.wf-studio-v3 .wf-resource-utilities-body,.wf-launch.wf-studio-v3 .wf-resource-utility-content{display:grid;gap:8px;padding:8px 0 12px!important}\n.wf-launch.wf-studio-v3 .wf-target-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 16px;padding:12px 0!important}\n.wf-launch.wf-studio-v3 .wf-target-field{min-width:0;padding:0!important;border:0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important}\n.wf-launch.wf-studio-v3 .wf-target-field>label{color:#535b66!important;font-size:11px!important}\n.wf-launch.wf-studio-v3 .wf-target-empty{margin:8px 0!important;padding:12px!important;border:1px dashed #cbd0d8!important;border-radius:8px!important;background:#fff!important;color:#6d7480!important}\n.wf-launch.wf-studio-v3 .wf-target-note{padding:8px 0 0!important;color:#737b87!important}\n.wf-launch.wf-studio-v3 .wf-target-accordion .wf-target-panel{grid-column:1/-1}\n.wf-launch.wf-studio-v3 .rm-module-results,.wf-launch.wf-studio-v3 .wf-target-field .rm-module-results{\n  max-height:220px;overflow:auto;border:1px solid #dfe2e8!important;border-radius:8px!important;background:#fff!important;\n  box-shadow:0 8px 20px rgba(25,31,42,.08)\n}\n.wf-launch.wf-studio-v3 .rm-module-choice{padding:8px 10px!important;color:#333943!important}\n.wf-launch.wf-studio-v3 .rm-module-choice:hover{background:#f5f1fc!important}\n.wf-launch.wf-studio-v3 .rm-module-pill{border:1px solid #d9c9f5!important;border-radius:999px!important;background:#f3edff!important;color:#503680!important}\n.wf-launch.wf-studio-v3 .rm-module-hint{color:#707782!important}\n.wf-launch.wf-studio-v3 #launchMetaAllFields.wf-advanced{\n  max-width:none!important;margin:12px 0 0!important;padding:0!important;border:0!important;border-top:1px solid #e2e5ea!important;\n  border-radius:0!important;background:transparent!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3 #launchMetaAllFields h3{margin:0!important;padding:14px 0!important;color:#30343c!important;font-size:12px!important;letter-spacing:0!important}\n.wf-launch.wf-studio-v3 #launchMetaAllFields .rm-meta-note{max-width:760px;color:#737b87!important;font-size:11px!important}\n.wf-launch.wf-studio-v3 #launchMetaAllFields .rm-meta-groups{display:block!important;gap:0!important}\n.wf-launch.wf-studio-v3 #launchMetaAllFields details{\n  border:0!important;border-top:1px solid #e2e5ea!important;border-radius:0!important;background:transparent!important\n}\n.wf-launch.wf-studio-v3 #launchMetaAllFields summary{padding:10px 0!important;color:#353a44!important}\n.wf-launch.wf-studio-v3 #launchMetaAllFields .rm-meta-grid{gap:12px 16px!important;padding:8px 0 14px!important}\n.wf-launch.wf-studio-v3 .wf-meta-tabs{display:flex;gap:4px;overflow:auto;border-bottom:1px solid #e1e4e9}\n.wf-launch.wf-studio-v3 .wf-meta-tab{\n  flex:0 0 auto;padding:9px 11px;border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;color:#68707c;font-size:11px;font-weight:650;cursor:pointer\n}\n.wf-launch.wf-studio-v3 .wf-meta-tab[aria-selected=true]{border-bottom-color:var(--studio-accent);color:#4e367c}\n.wf-launch.wf-studio-v3 .wf-live-summary>summary{\n  display:flex;align-items:flex-start;justify-content:space-between;gap:9px;padding:14px 15px!important;\n  cursor:pointer;list-style:none;background:#fff!important\n}\n.wf-launch.wf-studio-v3 .wf-live-summary>summary::-webkit-details-marker{display:none}\n.wf-launch.wf-studio-v3 .wf-live-summary>summary:after{content:none!important}\n.wf-launch.wf-studio-v3 .wf-live-summary-title{color:#292d35!important;font-size:13px!important;font-weight:700!important}\n.wf-launch.wf-studio-v3 .wf-live-summary-caption{color:#7d848e!important;font-size:10px!important}\n.wf-launch.wf-studio-v3 .wf-live-status{border:1px solid #cce8dc!important;border-radius:999px!important;background:#f0f9f5!important;color:#147656!important;font-size:9px!important}\n.wf-launch.wf-studio-v3 .wf-live-summary-body{display:grid;gap:0;padding:0 15px 8px!important}\n.wf-launch.wf-studio-v3 .wf-live-row{display:grid;grid-template-columns:66px minmax(0,1fr);align-items:center;gap:8px;padding:9px 0!important;border-top:1px solid #eef0f3!important}\n.wf-launch.wf-studio-v3 .wf-live-row-label{margin:0!important;color:#79808b!important;font-size:8px!important;line-height:1.3;white-space:nowrap}\n.wf-launch.wf-studio-v3 .wf-live-row-value{color:#373c45!important;font-size:10px!important;line-height:1.4}\n.wf-launch.wf-studio-v3 .wf-live-summary-foot{padding:8px 15px 13px!important;border-top:1px solid #eef0f3;color:#737b87!important;font-size:10px!important}\n.wf-launch.wf-studio-v3 .wf-mobile-actions{\n  display:grid!important;grid-template-columns:minmax(0,1fr) auto auto auto;align-items:center;gap:8px;\n  position:fixed!important;z-index:1200;left:var(--app-sidebar-width,220px);right:0;bottom:0;\n  padding:10px 20px calc(10px + env(safe-area-inset-bottom))!important;border-top:1px solid #dfe2e8!important;\n  background:rgba(255,255,255,.96)!important;backdrop-filter:blur(16px);box-shadow:0 -5px 18px rgba(25,31,42,.06)\n}\n.wf-launch.wf-studio-v3 .wf-workflow-status,body.wf-autoupload-open #wfMobileActions .wf-workflow-status{border:1px solid #e0e3e8!important;background:#f7f8fa!important;color:#666e79!important;box-shadow:none!important}\n.wf-launch.wf-studio-v3 .wf-mobile-actions button,.wf-launch.wf-studio-v3 .wf-mobile-actions .btn{\n  min-height:36px!important;padding:7px 11px!important;border:1px solid #dfe2e8!important;border-radius:8px!important;\n  background:#fff!important;color:#414751!important;box-shadow:none!important;font-size:11px!important;font-weight:650!important\n}\n.wf-launch.wf-studio-v3 .wf-mobile-actions .wf-mobile-next,.wf-launch.wf-studio-v3 .wf-mobile-actions #reviewLaunch,.wf-launch.wf-studio-v3 .wf-mobile-actions #launchButton{\n  border-color:var(--studio-accent)!important;background:var(--studio-accent)!important;color:#fff!important\n}\n.wf-launch.wf-studio-v3 .wf-mobile-actions .wf-mobile-next:hover,.wf-launch.wf-studio-v3 .wf-mobile-actions #reviewLaunch:hover,.wf-launch.wf-studio-v3 .wf-mobile-actions #launchButton:hover{\n  background:var(--studio-accent-strong)!important\n}\n.wf-launch.wf-studio-v3 .wf-resource-utility-card{min-width:0;margin:0!important;padding:0!important;border:0!important;background:transparent!important;box-shadow:none!important}\nbody.wf-autoupload-open .app-shell .app-sidebar .app-nav a.active{\n  border-left:2px solid #9c7be4!important;border-radius:0 7px 7px 0!important;background:rgba(170,143,232,.13)!important;color:#eee6ff!important;box-shadow:none!important\n}\nbody.wf-autoupload-open .app-shell .app-connection{border-color:#35404d!important;background:#1c2530!important;color:#b8c3d0!important}\nbody.wf-autoupload-open .app-shell .app-connection i{color:#45c9a2!important}\n@media(max-width:1120px){\n  .wf-launch.wf-studio-v3{grid-template-columns:minmax(0,1fr)!important;gap:14px!important}\n  .wf-launch.wf-studio-v3>.wf-live-summary,.wf-launch.wf-studio-v3 .rm-estimate-rail>.wf-live-summary{\n    grid-column:1!important;grid-row:auto!important;position:static!important;max-height:none!important;margin:0!important\n  }\n  .wf-launch.wf-studio-v3>.wf-step-nav{grid-template-columns:repeat(5,minmax(100px,1fr))!important;overflow:auto!important}\n}\n@media(max-width:720px){\n  .rm-estimate-layout.wf-studio-estimate-layout{padding:16px 14px 132px!important}\n  .wf-launch.wf-studio-v3{row-gap:10px!important}\n  .wf-launch.wf-studio-v3>.app-page-head{padding:0 0 13px!important}\n  .wf-launch.wf-studio-v3>.app-page-head h1,.wf-launch.wf-studio-v3>.app-page-head h2{font-size:22px!important}\n  .wf-studio-badge{position:static;margin:8px 0 0}\n  .wf-launch.wf-studio-v3>.wf-step-nav{display:flex!important;gap:0!important;overflow-x:auto!important;overscroll-behavior-x:contain;scroll-snap-type:x mandatory}\n  .wf-launch.wf-studio-v3>.wf-step-nav a{flex:0 0 auto;min-width:112px;scroll-snap-align:start;padding:6px 8px!important}\n  .wf-launch.wf-studio-v3>.wf-live-summary{grid-column:1;grid-row:auto;max-height:none!important}\n  .wf-launch.wf-studio-v3>.launch-card.wf-step-card>h5{font-size:19px!important}\n  .wf-launch.wf-studio-v3 .row{grid-template-columns:minmax(0,1fr)!important;gap:11px!important}\n  .wf-launch.wf-studio-v3 .wf-audience-inline,.wf-launch.wf-studio-v3 .wf-target-fields,.wf-launch.wf-studio-v3 .wf-compact-field-grid{grid-template-columns:minmax(0,1fr)!important}\n  .wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:repeat(2,minmax(0,1fr))!important}\n  .wf-launch.wf-studio-v3 .wf-mobile-actions{\n    left:0;grid-template-columns:minmax(0,1fr) auto auto;grid-template-areas:\"state summary next\" \"step back save\";\n    row-gap:6px;padding:8px 12px calc(8px + env(safe-area-inset-bottom))!important\n  }\n  .wf-launch.wf-studio-v3 .wf-workflow-status{grid-area:state;min-width:0}\n  .wf-launch.wf-studio-v3 .wf-mobile-step{grid-area:step;text-align:left}\n  .wf-launch.wf-studio-v3 .wf-mobile-summary{grid-area:summary}\n  .wf-launch.wf-studio-v3 .wf-mobile-back{grid-area:back}\n  .wf-launch.wf-studio-v3 .wf-mobile-next,.wf-launch.wf-studio-v3 .wf-mobile-actions #reviewLaunch,.wf-launch.wf-studio-v3 .wf-mobile-actions #launchButton{grid-area:next}\n  .wf-launch.wf-studio-v3 #wfSaveTemplateButton{grid-area:save}\n}\n@media(max-width:420px){\n  .wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:minmax(0,1fr)!important}\n  .wf-launch.wf-studio-v3 .wf-live-row{grid-template-columns:56px minmax(0,1fr)}\n}\n@media(prefers-reduced-motion:reduce){.wf-launch.wf-studio-v3 *{transition:none!important;scroll-behavior:auto!important}}";
    document.head.appendChild(studioV3Style);

    /* AutoUpload final layout corrections: keep the estimate node inside its audience disclosure. */
    studioV3Style.textContent += "\n/* AutoUpload V9: resource empty states, stage alignment and shared accent */\n"+
      ".wf-launch.wf-studio-v3>.app-page-head:after{display:none!important}\n"+
      ".wf-launch.wf-studio-v3>.app-page-head .app-page-actions{display:flex!important;flex-wrap:wrap;gap:6px!important;margin-top:12px!important}\n"+
      ".wf-launch.wf-studio-v3>.app-page-head .app-pill{border:1px solid #e4dcf4!important;border-radius:999px!important;background:#f4f0fb!important;color:#5b4388!important}\n"+
      ".wf-launch.wf-studio-v3>.app-page-head .app-pill.blue{border-color:#e4dcf4!important;background:#f4f0fb!important;color:#5b4388!important}\n"+
      ".wf-launch.wf-studio-v3>.wf-live-summary{grid-column:1/-1!important;grid-row:auto!important;align-self:stretch!important;position:static!important;max-height:none!important}\n"+
      ".wf-launch.wf-studio-v3>.rm-estimate-rail:empty{display:none!important}\n"+
      ".wf-launch.wf-studio-v3 .wf-profile-empty,.wf-launch.wf-studio-v3 .wf-account-empty{grid-column:1/-1!important;display:grid!important;grid-template-columns:36px minmax(0,1fr) auto!important;align-items:center;gap:10px 12px!important;width:100%;min-width:0}\n"+
      ".wf-launch.wf-studio-v3 .wf-empty-mark{display:grid!important;place-items:center;width:34px;height:34px;border:1px solid #e4def1;border-radius:9px;background:#f7f4fc;color:#7651c9;font-size:10px;font-weight:750;letter-spacing:.04em}\n"+
      ".wf-launch.wf-studio-v3 .wf-empty-copy{display:grid!important;gap:3px!important;min-width:0}\n"+
      ".wf-launch.wf-studio-v3 .wf-empty-copy strong{display:block!important;color:#30343c!important;font-size:12px!important;line-height:1.35}\n"+
      ".wf-launch.wf-studio-v3 .wf-empty-copy small{display:block!important;color:#737b87!important;font-size:10px!important;line-height:1.45}\n"+
      ".wf-launch.wf-studio-v3 .wf-profile-empty>a{justify-self:end;white-space:nowrap;font-size:11px;font-weight:650}\n"+
      ".wf-launch.wf-studio-v3 .wf-account-grid{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px!important;margin:8px 0 12px!important;min-width:0}\n"+
      ".wf-launch.wf-studio-v3 .wf-account-choice{display:flex!important;align-items:center;gap:10px;min-width:0;min-height:48px;padding:9px 11px!important;border:1px solid #dfe2e8!important;border-radius:9px!important;background:#fff!important;color:#353a44!important;cursor:pointer}\n"+
      ".wf-launch.wf-studio-v3 .wf-account-choice:has(input:checked){border-color:#a68ae9!important;background:#f5f0ff!important;color:#4a347c!important}\n"+
      ".wf-launch.wf-studio-v3 .wf-account-choice input{flex:0 0 15px;width:15px;height:15px;accent-color:#8057df!important}\n"+
      ".wf-launch.wf-studio-v3 .wf-account-copy{display:grid!important;gap:3px;min-width:0}\n"+
      ".wf-launch.wf-studio-v3 .wf-account-copy strong{overflow:hidden;color:inherit!important;font-size:11px!important;text-overflow:ellipsis;white-space:nowrap}\n"+
      ".wf-launch.wf-studio-v3 .wf-account-copy small{color:#737b87!important;font-size:10px!important}\n"+
      ".wf-launch.wf-studio-v3 .wf-profile-native,.wf-launch.wf-studio-v3 .wf-account-native{display:none!important}\n"+
      ".wf-launch.wf-studio-v3 .table-responsive.wf-empty-inventory{display:none!important}\n"+
      ".wf-launch.wf-studio-v3>.wf-live-summary .wf-live-row[data-wf-summary-key=budget]{margin:0!important;padding:9px 0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important}\n"+
      ".wf-launch.wf-studio-v3>.wf-live-summary .wf-live-row[data-wf-summary-key=budget] .wf-live-row-value{color:#373c45!important;font-size:11px!important;font-weight:700!important}\n"+
      ".wf-launch.wf-studio-v3>.wf-live-summary .wf-summary-more{margin:0!important;border:0!important;border-top:1px solid #eef0f3!important;border-radius:0!important;background:transparent!important}\n"+
      ".wf-launch.wf-studio-v3>.wf-live-summary .wf-summary-more>summary{padding:11px 0!important;color:#4d535e!important;font-size:11px!important}\n"+
      ".wf-launch.wf-studio-v3>.wf-live-summary .wf-summary-more>summary:after{color:#8057df!important}\n"+
      "body.app-shell.wf-autoupload-open .app-sidebar .app-nav a.active,body.app-shell.wf-autoupload-open .app-sidebar .app-nav a[aria-current=page]{border-left:2px solid #9c7be4!important;border-radius:0 7px 7px 0!important;background:rgba(170,143,232,.13)!important;color:#eee6ff!important;box-shadow:none!important}\n"+
      "body.app-shell.wf-autoupload-open #wfMobileActions{left:var(--app-sidebar-w,202px)!important;right:0!important;border-top:1px solid #dfe2e8!important;background:rgba(255,255,255,.97)!important;box-shadow:0 -5px 18px rgba(25,31,42,.06)!important}\n"+
      "body.app-shell.wf-autoupload-open #wfMobileActions button{border:1px solid #dfe2e8!important;border-radius:8px!important;background:#fff!important;color:#414751!important;box-shadow:none!important}\n"+
      "body.app-shell.wf-autoupload-open #wfMobileActions .wf-mobile-next,body.app-shell.wf-autoupload-open #wfMobileActions #reviewLaunch,body.app-shell.wf-autoupload-open #wfMobileActions #launchButton{border-color:#8057df!important;background:#8057df!important;color:#fff!important;box-shadow:none!important}\n"+
      "@media(max-width:1120px){.wf-launch.wf-studio-v3>.wf-live-summary{grid-column:1!important;grid-row:auto!important;position:static!important;max-height:none!important}.wf-launch.wf-studio-v3 .wf-account-grid{grid-template-columns:minmax(0,1fr)!important}}\n"+
      "@media(max-width:720px){.wf-launch.wf-studio-v3 .wf-profile-empty,.wf-launch.wf-studio-v3 .wf-account-empty{grid-template-columns:34px minmax(0,1fr)!important}.wf-launch.wf-studio-v3 .wf-profile-empty>a{grid-column:2;justify-self:start}.wf-launch.wf-studio-v3>.wf-live-summary{grid-column:1!important;grid-row:auto!important}body.app-shell.wf-autoupload-open #wfMobileActions{left:0!important}}";
    studioV3Style.textContent += "\n.wf-launch.wf-studio-v3 .row>.wf-profile-selector,.wf-launch.wf-studio-v3 .row>.wf-account-selector{grid-column:1/-1!important}\n.wf-launch.wf-studio-v3 #adAccount{display:none!important}\n.wf-launch.wf-studio-v3 .rk-picker-wrap.wf-empty-inventory{display:none!important}\n.wf-launch.wf-studio-v3 .rk-picker-wrap{max-height:300px!important;overflow:auto!important;border:1px solid #dfe2e8!important;border-radius:9px!important;background:#fff!important;scrollbar-color:#c9cdd5 #f7f8fa;scrollbar-width:thin}\n.wf-launch.wf-studio-v3 .rk-picker-table{width:100%;margin:0!important;border-collapse:separate;border-spacing:0;background:#fff!important;color:#373c45!important}\n.wf-launch.wf-studio-v3 .rk-picker-table thead th{position:sticky;top:0;z-index:2;padding:9px 10px!important;border-bottom:1px solid #e2e5ea!important;background:#f4f5f7!important;color:#737b87!important;font-size:9px!important;letter-spacing:.06em;text-transform:uppercase}\n.wf-launch.wf-studio-v3 .rk-picker-table tbody td{padding:9px 10px!important;border-top:1px solid #eef0f3!important;background:transparent!important;color:#444a54!important;font-size:11px!important}\n.wf-launch.wf-studio-v3 .rk-picker-table .rk-picker-row{cursor:pointer;transition:background .12s ease}\n.wf-launch.wf-studio-v3 .rk-picker-table .rk-picker-row:hover{background:#faf8ff!important}\n.wf-launch.wf-studio-v3 .rk-picker-table .rk-picker-row.is-selected{background:#f5f0ff!important}\n.wf-launch.wf-studio-v3>.wf-live-summary .wf-live-row[data-wf-summary-key=budget]{border:0!important;border-top:1px solid #eef0f3!important}\n@media(max-width:720px){.wf-launch.wf-studio-v3 .rk-picker-table{min-width:560px}}\n";

    function setupAudienceWorkspace(){
        const card=$("adsetName")?.closest(".launch-card");
        if(!card)return;
        card.classList.add("wf-audience-flow");
        let sections=Array.from(card.querySelectorAll(":scope > details.wf-audience-group"));
        const find=part=>sections.find(section=>String(section.querySelector(":scope > summary")?.textContent||"").toLowerCase().includes(part));
        const age=find("возраст");
        const geo=find("география");
        const placements=find("места показа");
        let inline=card.querySelector(":scope > .wf-audience-inline");
        if(geo&&!inline){
            inline=document.createElement("section");
            inline.className="wf-audience-inline";
            inline.setAttribute("aria-label","География и детальный таргетинг");
            const body=geo.querySelector(":scope > .wf-audience-group-body");
            for(const node of Array.from(body?.childNodes||[])){
                if(node.nodeType===1&&node.matches(".row")){
                    for(const child of Array.from(node.childNodes))inline.appendChild(child);
                    node.remove();
                }else inline.appendChild(node);
            }
            geo.before(inline);
            geo.remove();
        }
        if(age){
            const summary=age.querySelector(":scope > summary");
            if(summary)summary.textContent="Возраст";
            if(age.dataset.wfFlowReady!=="1"){
                age.open=true;
                age.dataset.wfFlowReady="1";
            }
        }
        if(inline){
            inline.dataset.wfAudienceBuilder="1";
            for(const id of ["geoQuery","interestQuery","behaviorQuery","languageQuery"]){
                const control=$(id);
                if(!control)continue;
                const wrapper=control.closest("[class*='col-']")||control.closest(".cr-field,.form-group")||control.parentElement;
                if(wrapper&&card.contains(wrapper))wrapper.classList.add("wf-audience-field");
            }
        }
        let delivery=card.querySelector(":scope > details.wf-audience-delivery");
        if(!delivery){
            delivery=document.createElement("details");
            delivery.className="wf-audience-delivery";
            delivery.dataset.wfDeliveryReady="1";
            const summary=document.createElement("summary");
            summary.textContent="Доставка, оптимизация и конверсии";
            const body=document.createElement("div");
            body.className="wf-audience-delivery-body";
            const fields=document.createElement("div");
            fields.className="wf-compact-field-grid";
            fields.dataset.wfDeliveryFields="1";
            const placementsBody=document.createElement("div");
            placementsBody.className="wf-audience-delivery-placements";
            body.append(fields,placementsBody);
            delivery.append(summary,body);
            delivery.open=false;
            card.appendChild(delivery);
        }
        const fields=delivery.querySelector("[data-wf-delivery-fields]");
        const placementsHost=delivery.querySelector(".wf-audience-delivery-placements");
        const fieldRoot=id=>{
            const control=$(id);
            if(!control||!card.contains(control))return null;
            const column=control.closest("[class*='col-']");
            return column&&card.contains(column)?column:(control.closest(".cr-field,.form-group")||control.parentElement);
        };
        const pruneOldLayout=nodes=>{
            for(const parent of nodes){
                if(!parent?.isConnected||parent===card)continue;
                if(!parent.matches(".row,[class*='col-']"))continue;
                if(!parent.querySelector("input,select,textarea,button,[data-wf-target-field]")&&!String(parent.textContent||"").trim())parent.remove();
            }
        };
        for(const id of ["optimizationGoal","billingEvent","pixel","conversionEvent"]){
            const wrapper=fieldRoot(id);
            if(!wrapper||!fields||fields.contains(wrapper))continue;
            const stale=[];
            for(let parent=wrapper.parentElement;parent&&parent!==card;parent=parent.parentElement){
                if(parent.matches(".row,[class*='col-']"))stale.push(parent);
            }
            wrapper.classList.add("wf-compact-field");
            wrapper.dataset.wfCompactField=id;
            fields.appendChild(wrapper);
            pruneOldLayout(stale);
        }
        if(placements&&placementsHost&&!placementsHost.contains(placements)){
            const body=placements.querySelector(":scope > .wf-audience-group-body");
            for(const node of Array.from(body?.childNodes||[]))placementsHost.appendChild(node);
            placements.remove();
        }
        const targetingRoot=card.querySelector("#wfTargetingOptions");
        const targeting=targetingRoot?.closest("details.wf-target-catalog-disclosure")||targetingRoot;
        const estimate=card.querySelector(".wf-audience-estimate");
        const ordered=[age,inline,targeting,estimate,delivery].filter(Boolean);
        for(const section of ordered)card.appendChild(section);
    }

    function activateStudioTargetGroup(root,key){
        const list=root?.querySelector(':scope > .wf-target-list');if(!root||!list)return;
        const panels=Array.from(list.querySelectorAll(':scope > [data-wf-target-group]'))
            .filter(panel=>panel.dataset.wfStudioPromoted!=='1'&&panel.querySelectorAll('[data-wf-target-field]').length>0);
        if(!panels.length)return;
        const active=panels.find(panel=>panel.dataset.wfTargetGroup===key)||panels.find(panel=>panel.dataset.wfTargetGroup==='demographics')||panels[0];
        const activeKey=active.dataset.wfTargetGroup;
        root.dataset.wfActiveTargetGroup=activeKey;
        for(const panel of panels){
            const selected=panel===active,groupKey=panel.dataset.wfTargetGroup;
            panel.hidden=!selected;
            panel.setAttribute('role','tabpanel');
            panel.id=panel.id||'wfTargetPanel-'+groupKey;
            panel.setAttribute('aria-labelledby','wfTargetTab-'+groupKey);
        }
        for(const tab of root.querySelectorAll(':scope > .wf-target-tabs [data-wf-target-tab]')){
            const selected=tab.dataset.wfTargetTab===activeKey;
            tab.setAttribute('aria-selected',String(selected));
            tab.tabIndex=selected?0:-1;
        }
    }
    function setupStudioTargetTabs(){
        const root=$('wfTargetingOptions');
        const list=root?.querySelector(':scope > .wf-target-list');
        if(!root||!list)return;
        root.classList.add('wf-target-tabs-mode');
        root.classList.remove('wf-target-accordion');
        let tabs=root.querySelector(':scope > .wf-target-tabs');
        if(!tabs){
            tabs=document.createElement('div');
            tabs.className='wf-target-tabs';
            tabs.setAttribute('role','tablist');
            tabs.setAttribute('aria-label','Категории настроек аудитории');
            root.insertBefore(tabs,list);
        }
        const panels=Array.from(list.querySelectorAll(':scope > [data-wf-target-group]'));
        for(const panel of panels){
            const key=panel.dataset.wfTargetGroup;
            const fullName=panel.querySelector('[data-wf-target-name]')?.textContent?.trim()||key;
            const name=({demographics:'Демография',devices:'Устройства',placements:'Показы',geography:'Гео и язык',exclusions:'Исключения',detailed:'Детальный таргетинг'})[key]||fullName;
            const count=panel.querySelectorAll('[data-wf-target-field]').length;
            let tab=tabs.querySelector('[data-wf-target-tab="'+key+'"]');
            if(!tab){
                tab=document.createElement('button');
                tab.type='button';
                tab.className='wf-target-tab';
                tab.id='wfTargetTab-'+key;
                tab.dataset.wfTargetTab=key;
                tab.setAttribute('role','tab');
                tab.setAttribute('aria-controls','wfTargetPanel-'+key);
                const label=document.createElement('span');label.className='wf-target-tab-label';
                const badge=document.createElement('span');badge.className='wf-target-tab-count';badge.dataset.wfTargetTabCount=key;badge.hidden=true;
                tab.append(label,badge);tabs.appendChild(tab);
            }
            const label=tab.querySelector('.wf-target-tab-label');
            if(label)label.textContent=name;
            tab.setAttribute('aria-label',fullName);tab.title=fullName;
            tab.hidden=count===0;
            panel.id=panel.id||'wfTargetPanel-'+key;
            panel.hidden=count===0;
            panel.setAttribute('role','tabpanel');
            panel.setAttribute('aria-labelledby',tab.id);
        }
        if(tabs.dataset.wfTargetTabsBound!=='1'){
            tabs.dataset.wfTargetTabsBound='1';
            tabs.addEventListener('click',event=>{
                const tab=event.target instanceof Element?event.target.closest('[data-wf-target-tab]'):null;
                if(tab&&tabs.contains(tab))activateStudioTargetGroup(root,tab.dataset.wfTargetTab);
            });
            tabs.addEventListener('keydown',event=>{
                const tab=event.target instanceof Element?event.target.closest('[data-wf-target-tab]'):null;
                if(!tab||!tabs.contains(tab))return;
                const available=Array.from(tabs.querySelectorAll('[data-wf-target-tab]')).filter(item=>!item.hidden);
                const index=available.indexOf(tab);
                let next=-1;
                if(event.key==='ArrowRight')next=(index+1)%available.length;
                else if(event.key==='ArrowLeft')next=(index-1+available.length)%available.length;
                else if(event.key==='Home')next=0;
                else if(event.key==='End')next=available.length-1;
                if(next>=0&&available[next]){
                    event.preventDefault();
                    activateStudioTargetGroup(root,available[next].dataset.wfTargetTab);
                    available[next].focus();
                }
            });
        }
        const available=panels.filter(panel=>panel.querySelectorAll('[data-wf-target-field]').length>0);
        const empty=root.querySelector('.wf-target-empty');
        tabs.hidden=available.length===0;
        if(empty)empty.hidden=available.length>0;
        if(!available.length){
            root.dataset.wfActiveTargetGroup='';
            if(empty&&!String(empty.textContent||'').trim())empty.textContent='Каталог Meta появится после выбора профиля и рекламного кабинета.';
            return;
        }
        activateStudioTargetGroup(root,root.dataset.wfActiveTargetGroup);
    }
        function setupMetaGroupTabs(){
        const root=$("launchMetaSdkFields");
        if(!root)return;
        const groups=Array.from(root.querySelectorAll(":scope > details[data-launch-meta-group]"));
        if(!groups.length)return;
        let tabs=root.querySelector(":scope > .wf-meta-tabs");
        if(!tabs){
            tabs=document.createElement("div");
            tabs.className="wf-meta-tabs";
            tabs.setAttribute("role","tablist");
            tabs.setAttribute("aria-label","Объекты Meta");
            root.prepend(tabs);
        }
        const signature=groups.map(group=>group.dataset.launchMetaGroup).join("|");
        if(tabs.dataset.wfSignature!==signature){
            tabs.replaceChildren();
            tabs.dataset.wfSignature=signature;
            const titles={campaign:"Кампания",adset:"Группа",targeting:"Аудитория",creative:"Креатив",ad:"Объявление"};
            for(const group of groups){
                const key=group.dataset.launchMetaGroup;
                const button=document.createElement("button");
                button.type="button";
                button.className="wf-meta-tab";
                button.dataset.wfMetaTab=key;
                button.setAttribute("role","tab");
                button.setAttribute("aria-controls","wfMetaGroup_"+key);
                button.textContent=titles[key]||key;
                button.addEventListener("click",()=>selectMetaGroup(key));
                tabs.appendChild(button);
                group.id="wfMetaGroup_"+key;
            }
        }
        const search=$("launchMetaFieldSearch");
        if(search&&!search.dataset.wfMetaSearchBound){
            search.dataset.wfMetaSearchBound="1";
            search.addEventListener("input",()=>{
                const query=String(search.value||"").trim();
                if(!query)return;
                const match=groups.find(group=>!group.classList.contains("rm-meta-hidden"));
                if(match)selectMetaGroup(match.dataset.launchMetaGroup);
            });
        }
        const active=groups.some(group=>group.dataset.launchMetaGroup===root.dataset.wfStudioMetaActive)
            ?root.dataset.wfStudioMetaActive
            :(groups.some(group=>group.dataset.launchMetaGroup==="targeting")?"targeting":groups[0].dataset.launchMetaGroup);
        selectMetaGroup(active);
        function selectMetaGroup(key){
            root.dataset.wfStudioMetaActive=key;
            groups.forEach(group=>{
                const selected=group.dataset.launchMetaGroup===key;
                group.hidden=!selected;
                group.open=selected;
            });
            tabs.querySelectorAll("[data-wf-meta-tab]").forEach(button=>{
                const selected=button.dataset.wfMetaTab===key;
                button.setAttribute("aria-selected",String(selected));
                button.tabIndex=selected?0:-1;
            });
        }
    }

    function applyStudioV3(){
        const root=document.querySelector(".launch-wrap.wf-launch");
        if(!root)return;
        root.classList.add("wf-studio-v3");
        root.style.setProperty("grid-template-columns","minmax(0,1fr)","important");
        root.style.setProperty("column-gap","0px","important");
        const header=root.querySelector(":scope > .app-page-head");
        if(header){
            const title=header.querySelector("h1,h2");
            const intro=header.querySelector("p");
            if(title)setText(title,"Автозалив рекламы");
            if(intro)setText(intro,"Соберите кампанию и объявление в одном рабочем процессе. Черновик остаётся на паузе.");
            if(!header.querySelector(".wf-studio-kicker")){
                const kicker=document.createElement("span");
                kicker.className="wf-studio-kicker";
                kicker.textContent="REMASK / CAMPAIGN STUDIO";
                header.prepend(kicker);
            }
            if(!header.querySelector(".wf-studio-badge")){
                const badge=document.createElement("span");
                badge.className="wf-studio-badge";
                const dot=document.createElement("i");
                dot.setAttribute("aria-hidden","true");
                badge.append(dot,document.createTextNode("Черновик · PAUSED"));
                header.appendChild(badge);
            }
        }
        const summary=$("wfLiveSummary");
        if(summary&&!summary.dataset.wfStudioOpenSet){
            summary.open=false;
            summary.dataset.wfStudioOpenSet="1";
        }
        const resourceCard=$("wf-launch-resources");
        const table=resourceCard?.querySelector("table");
        if(table&&/выберите профили|choose profiles|сначала загрузите/i.test(table.textContent||"")){
            (table.closest(".rk-picker-wrap,.table-responsive,.table-wrapper")||table).classList.add("wf-empty-inventory");
        }
        setupAudienceSections();
        setupAudienceWorkspace();
        setupAudienceTargeting();
        setupStudioTargetTabs();
        mountAudienceEstimate(root);
        setupMetaGroupTabs();
        const schemaRoot=$("launchMetaSdkFields");
        if(schemaRoot&&!schemaRoot.dataset.wfStudioSyncBound){
            schemaRoot.dataset.wfStudioSyncBound="1";
            let queued=false;
            new MutationObserver(()=>{
                if(queued)return;
                queued=true;
                requestAnimationFrame(()=>{
                    queued=false;
                    window.remaskModuleControls?.scan?.();
                    setupAudienceSections();
                    setupAudienceWorkspace();
                    setupAudienceTargeting();
                    setupStudioTargetTabs();
                    mountAudienceEstimate(root);
                    setupMetaGroupTabs();
                });
            }).observe(schemaRoot,{childList:true,subtree:true});
        }
    }

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
    function ensureMetaChoicePrompts() {
        for (const [id, message] of [
            ['optimizationGoal','Выберите рекламный кабинет'],
            ['billingEvent','Выберите рекламный кабинет']
        ]) {
            const select=$(id);
            if (!(select instanceof HTMLSelectElement)) continue;
            const prompt=select.querySelector('option[data-wf-empty-prompt]');
            const hasChoice=Array.from(select.options).some(option=>option.value&&!option.disabled);
            if (hasChoice) {
                prompt?.remove();
                select.classList.remove('wf-empty-choice');
                continue;
            }
            if (!prompt) {
                const option=document.createElement('option');
                option.value='';
                option.dataset.wfEmptyPrompt='1';
                option.textContent=message;
                select.prepend(option);
                option.selected=true;
            }
            select.classList.add('wf-empty-choice');
        }
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
        let grid=$('wfProfilePicker');
        if(!grid){grid=document.createElement('div');grid.id='wfProfilePicker';grid.className='wf-profile-grid';grid.setAttribute('role','group');grid.setAttribute('aria-label','Профили Facebook');select.after(grid);}
        const options=Array.from(select.options).filter(option=>option.value);
        const signature=Array.from(select.options).map(o=>o.value+'|'+o.textContent+'|'+o.disabled).join(';');
        if(grid.dataset.signature!==signature){
            grid.dataset.signature=signature;grid.replaceChildren();
            if(!options.length){
                const empty=document.createElement('div');empty.className='wf-empty-state wf-profile-empty';
                const mark=document.createElement('span');mark.className='wf-empty-mark';mark.textContent='01';
                const copy=document.createElement('span');copy.className='wf-empty-copy';
                const title=document.createElement('strong');title.textContent='Сохранённых профилей пока нет';
                const note=document.createElement('small');note.textContent='Добавленные профили появятся здесь для выбора.';
                copy.append(title,note);
                const link=document.createElement('a');link.href='accounts.php';link.textContent='Открыть аккаунты';
                empty.append(mark,copy,link);grid.appendChild(empty);
            }else{
                for(const option of options){
                    const label=document.createElement('label'),input=document.createElement('input');
                    input.type='checkbox';input.value=option.value;input.disabled=option.disabled;
                    input.setAttribute('aria-label',option.textContent.trim());
                    input.addEventListener('change',()=>{option.selected=input.checked;select.dispatchEvent(new Event('change',{bubbles:true}));});
                    const name=document.createElement('span');name.className='wf-profile-name';name.textContent=option.textContent.trim();
                    label.append(input,name);grid.appendChild(label);
                }
            }
            select.classList.add('wf-profile-native');
        }
        grid.querySelectorAll('input').forEach(input=>{const option=Array.from(select.options).find(o=>o.value===input.value);input.checked=Boolean(option?.selected);input.disabled=select.disabled||Boolean(option?.disabled);});
    }
    function setupAccountPicker() {
        const select=$('adAccount');if(!(select instanceof HTMLSelectElement))return;
        let grid=$('wfAdAccountPicker');
        if(!grid){grid=document.createElement('div');grid.id='wfAdAccountPicker';grid.className='wf-account-grid';grid.setAttribute('role','group');grid.setAttribute('aria-label','Рекламные кабинеты');select.after(grid);}
        const options=Array.from(select.options).filter(option=>option.value);
        const signature=Array.from(select.options).map(o=>o.value+'|'+o.textContent+'|'+o.disabled+'|'+(o.dataset.currency||'')).join(';');
        if(grid.dataset.signature!==signature){
            grid.dataset.signature=signature;grid.replaceChildren();
            if(!options.length){
                const empty=document.createElement('div');empty.className='wf-empty-state wf-account-empty';
                const mark=document.createElement('span');mark.className='wf-empty-mark';mark.textContent='RK';
                const copy=document.createElement('span');copy.className='wf-empty-copy';
                const title=document.createElement('strong');title.textContent=selectedLabels('profile').length?'Кабинеты ещё не загружены':'Сначала выберите профиль';
                const note=document.createElement('small');note.textContent=selectedLabels('profile').length?'Нажмите «Обновить данные Meta», чтобы получить доступный список.':'После выбора профиля здесь появятся сохранённые рекламные кабинеты.';
                copy.append(title,note);empty.append(mark,copy);grid.appendChild(empty);
            }else{
                for(const option of options){
                    const label=document.createElement('label');label.className='wf-account-choice';label.dataset.search=(option.textContent+' '+option.value+' '+(option.dataset.currency||'')).toLocaleLowerCase();
                    const input=document.createElement('input');input.type=select.multiple?'checkbox':'radio';input.name=select.multiple?'':'wfAdAccountChoice';input.value=option.value;input.disabled=option.disabled;input.setAttribute('aria-label',option.textContent.trim());
                    input.addEventListener('change',()=>{if(select.multiple)option.selected=input.checked;else if(input.checked)select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));});
                    const copy=document.createElement('span');copy.className='wf-account-copy';
                    const title=document.createElement('strong');title.textContent=option.textContent.trim()||option.value;
                    const meta=document.createElement('small');meta.textContent=[option.dataset.currency,option.value].filter(Boolean).join(' · ');
                    copy.append(title,meta);label.append(input,copy);grid.appendChild(label);
                }
            }
            select.classList.add('wf-account-native');
        }
        const sync=()=>grid.querySelectorAll('input').forEach(input=>{const option=Array.from(select.options).find(o=>o.value===input.value);input.checked=Boolean(option?.selected);input.disabled=select.disabled||Boolean(option?.disabled);});
        sync();
        if(!select.dataset.wfAccountPickerBound){select.dataset.wfAccountPickerBound='1';select.addEventListener('change',sync);}
        if(!select.dataset.wfAccountPickerObserved){select.dataset.wfAccountPickerObserved='1';new MutationObserver(()=>setupAccountPicker()).observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','label','value','data-currency']});}
        const filter=$('rkFilter');
        if(filter&&!filter.dataset.wfAccountFilterBound){filter.dataset.wfAccountFilterBound='1';filter.addEventListener('input',()=>{const query=filter.value.trim().toLocaleLowerCase();grid.querySelectorAll('.wf-account-choice').forEach(item=>{item.hidden=!!query&&!item.dataset.search.includes(query);});});}
        if(filter){const query=filter.value.trim().toLocaleLowerCase();grid.querySelectorAll('.wf-account-choice').forEach(item=>{item.hidden=!!query&&!item.dataset.search.includes(query);});}
        const picker=select.closest('.launch-card')?.querySelector('.rk-picker-wrap');
        const noAccounts=options.length===0;
        const pickerPlaceholder=Boolean(picker?.querySelector('#rkPickerRows td[colspan]'));
        if(picker)picker.classList.toggle('wf-empty-inventory',noAccounts&&pickerPlaceholder);
        if(filter)filter.hidden=noAccounts;
        for(const id of ['rkSelectAll','rkClear']){const button=$(id);if(button)button.hidden=noAccounts;}
    }
        const wfTargetGroups = [
        {key:'demographics',title:'Демография',note:'Возраст, пол, образование, семейный статус и социальные параметры.',fields:new Set(['age_min','age_max','genders','education_statuses','education_schools','education_majors','college_years','relationship_statuses','family_statuses','household_composition','ethnic_affinity','generation','interested_in','income','net_worth','politics'])},
        {key:'devices',title:'Устройства и подключение',note:'Платформы, модели устройств, операционные системы и тип подключения.',fields:new Set(['device_platforms','user_device','excluded_user_device','user_os','wireless_carrier','app_install_state'])},
        {key:'placements',title:'Платформы и места показа',note:'Поверхности размещения и доступные ограничения для выбранной цели.',fields:new Set(['publisher_platforms','facebook_positions','instagram_positions','messenger_positions','audience_network_positions','threads_positions','whatsapp_positions','oculus_positions','streaming_services_positions','brand_safety_content_filter_levels','contextual_bundling_spec'])},
        {key:'geography',title:'География и язык',note:'Страны и регионы — быстрый каталог; города ищутся в выбранном РК, радиус задаётся для каждого города. Другие гео-типы доступны в редакторе структуры.',fields:new Set(['geo_locations','locales'])},
        {key:'exclusions',title:'Исключения',note:'Исключённые страны, регионы и аудитории; интересы и поведение — поиском.',fields:new Set(['excluded_geo_locations','excluded_custom_audiences','exclusions'])},
        {key:'detailed',title:'Интересы и категории Meta',note:'Интересы, поведение, свои аудитории, работа, события жизни, жильё и другие категории.',fields:new Set(['interests','behaviors','custom_audiences','flexible_spec','purchase_intent','user_adclusters','hashtag_interactions','work_employers','work_positions','industries','office_type','life_events','moms','home_ownership','home_type','home_value','location_categories'])}
    ];
    function wfTargetValues(source) {
        const raw=String(source?.value||'').trim();if(!raw)return [];
        try{const parsed=JSON.parse(raw);if(Array.isArray(parsed))return parsed;if(parsed&&typeof parsed==='object'){if(['exclusions','excluded_geo_locations','geo_locations'].includes(source?.dataset?.launchMetaField))return Object.values(parsed).flatMap(value=>Array.isArray(value)?value:[]);return Object.values(parsed);}if(parsed!==null&&parsed!==undefined)return [parsed];}
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
            const tabCount=root.querySelector('[data-wf-target-tab-count="'+panel.dataset.wfTargetGroup+'"]');
            if(tabCount){tabCount.textContent=selected?String(selected):'';tabCount.hidden=selected===0;}
        }
    }
    function setupAudienceMetaDisclosure(){
        const card=$('adsetName')?.closest('.launch-card');
        const meta=$('launchMetaAllFields');
        if(!card||!meta||!card.contains(meta))return;
        let disclosure=card.querySelector(':scope > details.wf-meta-extra-disclosure');
        if(!disclosure){
            disclosure=document.createElement('details');
            disclosure.className='wf-meta-extra-disclosure';
            disclosure.open=false;
            const summary=document.createElement('summary');
            summary.className='wf-meta-extra-summary';
            const label=document.createElement('span');label.textContent='Дополнительные поля Meta';
            const hint=document.createElement('span');hint.className='wf-meta-extra-hint';hint.textContent='Полная схема API';
            summary.append(label,hint);
            const body=document.createElement('div');body.className='wf-meta-extra-body';
            disclosure.append(summary,body);
            card.appendChild(disclosure);
        }
        const body=disclosure.querySelector(':scope > .wf-meta-extra-body');
        if(body&&meta.parentElement!==body)body.appendChild(meta);
        const heading=meta.querySelector('.rm-meta-head h3');
        if(heading)heading.textContent='Поля схемы Meta';
        const note=meta.querySelector('.rm-meta-head .rm-meta-note');
        if(note)note.textContent='Параметры, которых нет в основных настройках. Значения участвуют в проверке и сборке черновика.';
        const estimate=card.querySelector(':scope > .wf-audience-estimate');
        const target=card.querySelector(':scope > details.wf-target-catalog-disclosure')
            ||card.querySelector(':scope > #wfTargetingOptions');
        const anchor=estimate||target||card.querySelector(':scope > .wf-audience-inline');
        if(anchor&&anchor.nextElementSibling!==disclosure)anchor.after(disclosure);
    }

    function ensureTargetingDisclosure(card, root) {
        let disclosure=root.closest('details.wf-target-catalog-disclosure')
            ||card.querySelector(':scope > details.wf-target-catalog-disclosure');
        if(!disclosure){
            disclosure=document.createElement('details');
            disclosure.className='wf-target-catalog-disclosure';
            const summary=document.createElement('summary');summary.className='wf-target-catalog-summary';
            const title=document.createElement('span');title.textContent='Расширенные настройки Meta';
            const hint=document.createElement('span');hint.className='wf-target-catalog-hint';
            summary.append(title,hint);
            const body=document.createElement('div');body.className='wf-target-catalog-body';
            disclosure.append(summary,body);
            if(root.parentElement)root.before(disclosure);else card.appendChild(disclosure);
            body.appendChild(root);
        }else{
            const body=disclosure.querySelector(':scope > .wf-target-catalog-body');
            if(body&&root.parentElement!==body)body.appendChild(root);
        }
        const fields=Array.from(root.querySelectorAll('[data-wf-target-field]')).filter(field=>!field.hidden);
        const groups=Array.from(root.querySelectorAll('[data-wf-target-group]')).filter(group=>!group.hidden);
        const hint=disclosure.querySelector('.wf-target-catalog-hint');
        const hintText=fields.length?fields.length+' полей · '+groups.length+' категорий':'Устройства · размещения · демография';
        if(hint&&hint.textContent!==hintText)hint.textContent=hintText;
        return disclosure;
    }

    function setupAdStagePolish() {
        const card=$('creativeName')?.closest('.launch-card');if(!card)return;
        const addSectionLabel=(fieldId,key,label)=>{
            const row=$(fieldId)?.closest('.row');
            if(!row||row.dataset.wfAdSection)return;
            const heading=document.createElement('div');heading.className='wf-ad-section-title';
            heading.dataset.wfAdSection=key;heading.textContent=label;
            row.before(heading);row.dataset.wfAdSection=key;
        };
        addSectionLabel('page','identity','Идентичность и названия');
        addSectionLabel('message','content','Текст и действие');
        addSectionLabel('creativeFormat','media','Формат и медиа');
        const columns=['description','urlTags'].map(id=>$(id)?.closest('[class*="col-"]')).filter(column=>column&&card.contains(column));
        if(!columns.length)return;
        let disclosure=card.querySelector(':scope > details.wf-ad-advanced-disclosure');
        if(!disclosure){
            disclosure=document.createElement('details');disclosure.className='wf-ad-advanced-disclosure';
            const summary=document.createElement('summary');
            const title=document.createElement('span');title.textContent='Дополнительные параметры';
            const hint=document.createElement('span');hint.className='wf-ad-advanced-hint';hint.textContent='Описание · UTM';
            summary.append(title,hint);
            const body=document.createElement('div');body.className='wf-ad-advanced-body';
            disclosure.append(summary,body);
            const anchor=$('creativeFormat')?.closest('.row');
            if(anchor&&anchor.parentElement===card)anchor.after(disclosure);else card.appendChild(disclosure);
        }
        const body=disclosure.querySelector(':scope > .wf-ad-advanced-body');
        for(const column of columns)if(body&&column.parentElement!==body)body.appendChild(column);
    }

    function setupPreviewPlacementLabels() {
        const select=$('remaskPreviewFormat');if(!select)return;
        const prefixes=[['AUDIENCE NETWORK','Audience Network'],['FACEBOOK','Facebook'],['INSTAGRAM','Instagram'],
            ['MESSENGER','Messenger'],['WHATSAPP','WhatsApp'],['THREADS','Threads'],['OCULUS','VR'],['BIZ DISCO','Meta'],['GROUPS','Группы']];
        const platformFor=value=>{
            const text=String(value||'').toUpperCase().replaceAll('_',' ').trim();
            return prefixes.find(([key])=>text.startsWith(key))?.[1]||'Meta';
        };
        const optionLabel=value=>{
            const text=String(value||'').toUpperCase().replaceAll('_',' ').trim();if(!text)return '';
            const words=text.split(/\s+/),has=word=>words.includes(word),platform=platformFor(text);
            const surface=[[['REELS'],'Reels'],[['STORY','STORIES'],'Истории'],[['FEED'],'Лента'],
                [['EXPLORE'],'Интересное'],[['SEARCH','SERP'],'Поиск'],[['MARKETPLACE'],'Marketplace'],
                [['GROUP','GROUPS'],'Группы'],[['PROFILE'],'Профиль'],[['WATCH'],'Watch'],
                [['INBOX'],'Входящие'],[['CHANNEL'],'Канал'],[['STATUS'],'Статус'],
                [['INSTANT','ARTICLE'],'Instant Articles'],[['RIGHT','COLUMN'],'Правая колонка']].find(([tokens])=>tokens.some(has))?.[1];
            const media=[[['INSTREAM'],'In-stream'],[['REWARDED'],'Rewarded video'],[['BANNER'],'Баннер'],
                [['INTERSTITIAL'],'Полноэкранный формат'],[['NATIVE'],'Нативный формат'],[['VIDEO'],'Видео'],
                [['MEDIUM','RECTANGLE'],'Прямоугольный формат']].find(([tokens])=>tokens.some(has))?.[1];
            const device=has('CTV')||has('TV')?'TV':has('DESKTOP')?'Компьютер'
                :has('MOBILE')||has('MSITE')?'Мобильное':has('IOS')?'iOS':has('ANDROID')?'Android':has('WEB')?'Web':'';
            const parts=[platform,surface,media,device].filter(Boolean);
            return [...new Set(parts)].join(' · ')||text.toLowerCase().replace(/\b\w/g,char=>char.toUpperCase());
        };
        const selected=select.value,options=Array.from(select.options);
        if(select.dataset.wfPlacementGrouped!=='1'){
            const groups=new Map(),loose=[];
            for(const option of options){
                if(!option.value){loose.push(option);continue;}
                const platform=platformFor(option.value),label=optionLabel(option.value);
                if(option.textContent!==label)option.textContent=label;
                let group=groups.get(platform);
                if(!group){group=document.createElement('optgroup');group.label=platform;groups.set(platform,group);}
                group.appendChild(option);
            }
            select.replaceChildren(...loose,...groups.values());
            select.dataset.wfPlacementGrouped='1';
        }else{
            const groups=new Map(Array.from(select.children).filter(child=>child.tagName==='OPTGROUP').map(group=>[group.label,group]));
            for(const option of options){
                if(!option.value){if(option.parentElement!==select)select.insertBefore(option,select.firstChild);continue;}
                const label=optionLabel(option.value);
                if(option.textContent!==label)option.textContent=label;
                const platform=platformFor(option.value);
                let group=groups.get(platform);
                if(!group){group=document.createElement('optgroup');group.label=platform;groups.set(platform,group);select.appendChild(group);}
                if(option.parentElement!==group)group.appendChild(option);
            }
        }
        if(selected)select.value=selected;
        const label=document.querySelector('label[for="remaskPreviewFormat"]')||select.parentElement?.querySelector('label');
        if(label&&label.textContent.trim()!=='Формат предпросмотра')label.textContent='Формат предпросмотра';
        const stage=select.closest('.launch-card');let node=select.parentElement,preview=null;
        while(node&&node!==stage){
            const match=getComputedStyle(node).backgroundColor.match(/rgba?\((\d+)[, ]+\s*(\d+)[, ]+\s*(\d+)/);
            if(match){const [r,g,b]=match.slice(1).map(Number);if((r*299+g*587+b*114)/1000<100){preview=node;break;}}
            node=node.parentElement;
        }
        preview?.classList.add('wf-preview-surface');
    }

    function setupAudienceTargeting() {
        const adsetCard=$('adsetName')?.closest('.launch-card');if(!adsetCard)return;
        let root=$('wfTargetingOptions');
        if(!root){
            root=document.createElement('section');root.id='wfTargetingOptions';root.className='wf-target-groups';
            const head=document.createElement('div');head.className='wf-target-head';
            const copy=document.createElement('div'),title=document.createElement('h6'),description=document.createElement('p');
            title.textContent='Настройки Meta';
            description.textContent='Выберите категорию. Внутри останутся только поля, относящиеся к ней.';
            copy.append(title,description);head.appendChild(copy);
            const list=document.createElement('div');list.className='wf-target-list';
            for(const group of wfTargetGroups){
                    const panel=document.createElement('section');panel.className='wf-target-panel';panel.dataset.wfTargetGroup=group.key;panel.id='wfTargetPanel-'+group.key;
                const heading=document.createElement('div');heading.className='wf-target-panel-heading';
                const name=document.createElement('span'),count=document.createElement('span');
                name.className='wf-target-name';name.dataset.wfTargetName='1';name.textContent=group.title;
                count.className='wf-target-count';count.dataset.wfTargetCount='1';count.textContent='Загружаю варианты Meta…';
                heading.append(name,count);
                const note=document.createElement('p');note.className='wf-target-note';note.textContent=group.note;
                const fields=document.createElement('div');fields.className='wf-target-fields';
                panel.append(heading,note,fields);list.appendChild(panel);
            }
            const empty=document.createElement('div');empty.className='wf-target-empty';empty.dataset.wfTargetEmpty='1';
            empty.textContent='Каталог прямого выбора Meta сейчас недоступен. Другие доступные параметры остаются в разделе «Дополнительные настройки рекламы».';
            root.append(head,list,empty);
        }
        let targetView=root.closest('details.wf-target-catalog-disclosure')||root;
        const audienceSurface=adsetCard.querySelector(':scope > .wf-audience-inline');
        if(audienceSurface){
            if(audienceSurface.nextElementSibling!==targetView)audienceSurface.after(targetView);
            const estimate=adsetCard.querySelector('.wf-audience-estimate');
            if(estimate&&targetView.nextElementSibling!==estimate)targetView.after(estimate);
        }else{
            const anchors=['ageMin','geoQuery','interestQuery','behaviorQuery','languageQuery']
                .map(id=>$(id)?.closest('.row')||$(id)?.closest('[data-remaskLanguageTargeting]'))
                .filter(row=>row&&adsetCard.contains(row));
            const audienceBox=$('singleAudienceBox');
            const audienceAnchor=audienceBox?.closest('.row')||audienceBox;
            if(audienceAnchor&&adsetCard.contains(audienceAnchor))anchors.push(audienceAnchor);
            anchors.sort((a,b)=>a===b?0:(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1));
            const anchor=anchors[anchors.length-1];
            if(anchor){if(anchor.nextElementSibling!==targetView)anchor.after(targetView);}
            else if(targetView.parentElement!==adsetCard)adsetCard.appendChild(targetView);
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
            wrapper?.classList.add('wf-target-source');available++;
        }
        let visibleGroups=0;
        for(const panel of root.querySelectorAll('[data-wf-target-group]')){
            const count=panel.querySelectorAll('[data-wf-target-field]').length;
            panel.hidden=count===0;if(count)visibleGroups++;
        }
        const empty=root.querySelector('[data-wf-target-empty]');
        if(empty){
            empty.hidden=available>0;
            if(sourceCount===0)setText(empty,'Выберите профиль и рекламный кабинет на шаге «Ресурсы», затем загрузите данные Meta. Здесь появятся прямые настройки географии, интересов, поведения, языков, размещений, устройств и операционных систем.');
        }
        ensureTargetingDisclosure(adsetCard,root);
        root.hidden=false;
        root.dataset.wfReady=visibleGroups?'1':'0';
        updateAudienceTargetingSummary();
        setupStudioTargetTabs();
        setupAudienceMetaDisclosure();
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
        const geo=selectedPillLabels('selectedGeo');
        const geoSource=document.querySelector('[data-launch-meta-group="targeting"][data-launch-meta-field="geo_locations"]');
        const moduleGeoCount=wfTargetValues(geoSource).length;
        const geoSummary=[geo.length?geo.length+' быстрых гео':'',moduleGeoCount?moduleGeoCount+' гео Meta':''].filter(Boolean).join(' + ')||'География не выбрана';
        const interests=selectedPillLabels('selectedInterests'),behaviors=selectedPillLabels('selectedBehaviors'),languages=selectedPillLabels('selectedLanguages');
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
            geoSummary,
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
        const quickValues={campaign:(campaignName==='Название не задано'?'':campaignName+' · ')+objective,budget:budgetText,resources:profiles.length+' профилей · '+accounts.length+' РК',audience:audienceParts[0]+' · '+(geo.length+moduleGeoCount)+' гео · '+(interests.length+behaviors.length)+' интересов/поведения'};
        for(const [key,value] of Object.entries(quickValues)){const node=panel.querySelector('[data-wf-summary-quick="'+key+'"]');if(node)setText(node,value);}
        const foot=panel.querySelector('[data-wf-summary-foot]');
        if(foot)setText(foot,'Объекты сохраняются на паузе. Публикация выключена.');
    }
    function setupLiveSummary(root) {
        let panel=$('wfLiveSummary');
        const place=()=>{
            const progress=root.querySelector('.wf-step-progress');
            const nav=root.querySelector('.wf-step-nav');
            if(progress?.parentElement===root)progress.after(panel);
            else if(nav?.parentElement===root)nav.after(panel);
            else root.appendChild(panel);
        };
        if(panel){place();return panel;}
        panel=document.createElement('details');panel.id='wfLiveSummary';panel.className='wf-live-summary';panel.setAttribute('aria-label','Сводка параметров запуска');
        panel.open=false;
        const summary=document.createElement('summary'),heading=document.createElement('span'),title=document.createElement('span'),caption=document.createElement('span'),status=document.createElement('span');
        heading.className='wf-live-summary-heading';title.className='wf-live-summary-title';title.textContent='Сводка запуска';
        caption.className='wf-live-summary-caption';caption.textContent='Текущие параметры черновика';
        heading.append(title,caption);status.className='wf-live-status';status.textContent='PAUSED · без публикации';const quick=document.createElement('div');quick.className='wf-summary-quickfacts';for(const [key,label] of [['campaign','Кампания'],['budget','Бюджет'],['resources','Ресурсы'],['audience','Аудитория']]){const cell=document.createElement('div'),name=document.createElement('span'),value=document.createElement('strong');cell.className='wf-summary-quickfact';name.textContent=label;value.dataset.wfSummaryQuick=key;cell.append(name,value);quick.appendChild(cell);}summary.append(heading,status,quick);
        const body=document.createElement('div');body.className='wf-live-summary-body';
        const values=new Map();
        const rows=[['resources','Ресурсы'],['campaign','Кампания'],['budget','Бюджет'],['audience','Аудитория'],['delivery','Показ'],['schedule','График'],['optimization','Оптимизация'],['creative','Страница и медиа'],['copy','Текст и переход']];
        for(const [key,label] of rows){
            const row=document.createElement('div');row.className='wf-live-row';row.dataset.wfSummaryKey=key;
            const name=document.createElement('span'),value=document.createElement('span');
            name.className='wf-live-row-label';name.textContent=label;value.className='wf-live-row-value';value.dataset.wfSummaryValue=key;
            row.append(name,value);values.set(key,row);
        }
        for(const key of ['resources','campaign','budget','audience','delivery'])body.appendChild(values.get(key));
        const more=document.createElement('details');more.className='wf-summary-more';
        const moreTitle=document.createElement('summary');moreTitle.textContent='Ещё параметры черновика';
        const moreBody=document.createElement('div');moreBody.className='wf-summary-more-body';
        for(const key of ['schedule','optimization','creative','copy'])moreBody.appendChild(values.get(key));
        more.append(moreTitle,moreBody);body.appendChild(more);
        const foot=document.createElement('div');foot.className='wf-live-summary-foot';foot.dataset.wfSummaryFoot='1';
        foot.textContent='Объекты сохраняются на паузе. Публикация выключена.';
        panel.append(summary,body,foot);
        place();
        updateLiveSummary();return panel;
    }
    function setupAudienceSections() {
        const card=$('adsetName')?.closest('.launch-card');if(!card)return;
        const configs=[
            {start:'места показа',stop:'возраст и пол',title:'Места показа и график',open:false},
            {start:'возраст и пол',stop:'география',title:'Возраст',open:true},
            {start:'география',stop:null,title:'География и детальный таргетинг',open:true}
        ];
        for(const config of configs){
            const heading=[...card.querySelectorAll(':scope > .wf-section-heading')].find(node=>node.textContent.trim().toLowerCase().includes(config.start));
            if(!heading)continue;
            const details=document.createElement('details');details.className='wf-audience-group';details.open=config.open;
            const summary=document.createElement('summary');summary.textContent=config.title;
            const body=document.createElement('div');body.className='wf-audience-group-body';
            details.append(summary,body);heading.before(details);
            let node=heading.nextSibling;
            while(node){
                if(config.stop&&node.nodeType===1&&node.matches('.wf-section-heading')&&node.textContent.trim().toLowerCase().includes(config.stop))break;
                const next=node.nextSibling;body.appendChild(node);node=next;
            }
            heading.remove();
        }
        const box=$('singleAudienceBox');
        if(box&&box.dataset.wfDisclosure!=='1'){
            const heading=box.querySelector(':scope > h6')||box.querySelector('h6');
            const details=document.createElement('details');details.className='wf-audience-subgroup';details.open=false;
            const summary=document.createElement('summary');summary.textContent='Свои аудитории и исключения';
            const body=document.createElement('div');body.className='wf-audience-subgroup-body';
            for(const child of Array.from(box.childNodes))if(child!==heading)body.appendChild(child);
            details.append(summary,body);box.replaceChildren(details);box.dataset.wfDisclosure='1';
        }
    }
    function mountAudienceEstimate(root) {
        const estimate=$('remaskAdvertisingEstimate');
        const audienceCard=$('adsetName')?.closest('.launch-card');
        if(!estimate||!audienceCard||estimate.dataset.wfStudioMounted==='1')return;
        const disclosure=document.createElement('details');
        disclosure.className='wf-audience-estimate';
        disclosure.dataset.wfEstimateDisclosure='1';
        const summary=document.createElement('summary');
        summary.textContent='Прогноз охвата Meta';
        const body=document.createElement('div');
        body.className='wf-audience-estimate-body';
        body.appendChild(estimate);
        disclosure.append(summary,body);
        estimate.classList.add('wf-audience-estimate-content');
        estimate.dataset.wfStudioMounted='1';
        const targetingRoot=$('wfTargetingOptions');
        const targeting=targetingRoot?.closest('details.wf-target-catalog-disclosure')||targetingRoot;
        const advanced=$('launchMetaAllFields');
        if(targeting&&audienceCard.contains(targeting))targeting.after(disclosure);
        else if(advanced&&audienceCard.contains(advanced))advanced.before(disclosure);
        else audienceCard.appendChild(disclosure);
        const rail=root?.querySelector(':scope > .rm-estimate-rail');
        const summaryPanel=$('wfLiveSummary');
        if(rail&&summaryPanel&&rail.contains(summaryPanel)){
            const progress=root.querySelector(':scope > .wf-step-progress');
            if(progress)progress.after(summaryPanel);else root.appendChild(summaryPanel);
        }
        if(rail&&rail.childElementCount===0)rail.remove();
        const layout=root?.matches('.rm-estimate-layout')?root:root?.closest('.rm-estimate-layout');
        layout?.classList.add('wf-studio-estimate-layout');
    }
    function setupLaunch() {
        const root=document.querySelector('.launch-wrap');if(!root||root.dataset.wfReady)return;
        root.dataset.wfReady='1';root.classList.add('wf-launch','wf-studio-v2');document.body.classList.add('wf-autoupload-open');
        const pageTitle=root.querySelector(':scope > .app-page-head h1,:scope > .app-page-head h2');
        const pageIntro=root.querySelector(':scope > .app-page-head p');
        if(pageTitle)setText(pageTitle,'Студия запуска');
        if(pageIntro)setText(pageIntro,'Соберите черновик по шагам. Объявление сохраняется на паузе; публикация не выполняется.');
        const compactCard=(id,label)=>{
            const node=$(id),card=node?(node.classList.contains('launch-card')?node:node.closest('.launch-card')):null;
            if(!card||card.dataset.wfDisclosure==='1')return;
            const heading=card.querySelector(':scope > h5');
            const details=document.createElement('details');details.className='wf-card-disclosure';
            const summary=document.createElement('summary');summary.className='wf-card-disclosure-summary';
            if(heading){heading.classList.add('wf-disclosure-heading');summary.appendChild(heading);}else summary.textContent=label;
            const body=document.createElement('div');body.className='wf-card-disclosure-body';
            for(const child of Array.from(card.childNodes))if(child!==heading)body.appendChild(child);
            details.append(summary,body);card.replaceChildren(details);card.dataset.wfDisclosure='1';
        };
        compactCard('jobsCard','Задачи и результаты');
        const entries=[
            {key:'resources',title:'Ресурсы',ids:['profile'],cardTitle:'Профили и рекламные кабинеты',description:'Выберите профили и кабинеты для черновика.'},
            {key:'campaign',title:'Кампания',ids:['campaignName'],cardTitle:'Кампания',description:'Укажите имя кампании, цель и особую категорию.'},
            {key:'audience',title:'Аудитория',ids:['adsetName'],cardTitle:'Бюджет и аудитория',description:'Задайте бюджет, аудиторию и стратегию доставки.'},
            {key:'ad',title:'Объявление',ids:['creativeName'],cardTitle:'Объявление',description:'Добавьте медиа, текст, ссылку и оформление объявления.'},
            {key:'review',title:'Проверка',ids:['reviewLaunch','launchButton','jobsCard'],cardTitle:'Проверка черновика',description:'Проверьте итоговые настройки перед созданием PAUSED-черновика.'}
        ];
        const cardFor=id=>{
            const node=$(id);if(!node)return null;
            if(id==='launchMetaAllFields')return node;
            return node.classList.contains('launch-card')?node:node.closest('.launch-card');
        };
        const resourceMain=cardFor('profile');
        if(resourceMain&&!resourceMain.querySelector('.wf-resource-utilities')){
            const utilityCards=[...new Set(['targetBindingsCard','bundleSelect','loadFunding'].map(cardFor).filter(card=>card&&card!==resourceMain))];
            if(utilityCards.length){
                const utilities=document.createElement('details');utilities.className='wf-resource-utilities';
                const summary=document.createElement('summary'),title=document.createElement('strong'),count=document.createElement('span');
                title.textContent='Дополнительные инструменты';count.textContent='Связки · привязки · оплата';summary.append(title,count);
                const body=document.createElement('div');body.className='wf-resource-utilities-body';
                for(const card of utilityCards){
                    const heading=card.querySelector(':scope > h5'),label=heading?.textContent.trim()||'Настройки';
                    const details=document.createElement('details');details.className='wf-resource-utility';
                    const disclosure=document.createElement('summary');disclosure.textContent=label;
                    const content=document.createElement('div');content.className='wf-resource-utility-content';
                    for(const child of Array.from(card.childNodes))if(child!==heading)content.appendChild(child);
                    details.append(disclosure,content);card.replaceChildren(details);card.classList.add('wf-resource-utility-card');body.appendChild(card);
                }
                utilities.append(summary,body);resourceMain.appendChild(utilities);
            }
        }
        const nav=document.createElement('nav');nav.className='wf-step-nav';nav.setAttribute('aria-label','Этапы автозалива');
        const stepCards=[];
        entries.forEach(entry=>{
            const cards=[...new Set(entry.ids.map(cardFor).filter(Boolean))];
            if(!cards.length)return;
            cards.forEach((card,i)=>{
                card.classList.add('wf-step-card');
                if(!card.id)card.id='wf-launch-'+entry.key+(i?'-'+i:'');
                card.setAttribute('aria-hidden','true');
            });
            if(entry.cardTitle)setText(cards[0].querySelector('h5'),entry.cardTitle);
            if(entry.description&&!cards[0].querySelector(':scope > .wf-step-context')){
                const context=document.createElement('p');context.className='wf-step-context';context.textContent=entry.description;
                cards[0].querySelector(':scope > h5')?.after(context);
            }
            const link=document.createElement('a');link.href='#'+cards[0].id;link.setAttribute('aria-label',(stepCards.length+1)+'. '+entry.title);
            const number=document.createElement('span');number.className='wf-step-index';number.textContent=String(stepCards.length+1);
            const label=document.createElement('span');label.className='wf-step-label';label.textContent=entry.title;
            link.append(number,label);nav.appendChild(link);stepCards.push({...entry,link,cards,card:cards[0]});
        });
        root.querySelector('.launch-card')?.before(nav);
        const progressTrack=document.createElement('div');progressTrack.className='wf-step-progress';progressTrack.setAttribute('aria-hidden','true');
        const progressFill=document.createElement('span');progressTrack.appendChild(progressFill);nav.after(progressTrack);
        let activeStep=0;
        const reviewIndex=stepCards.findIndex(item=>item.key==='review');
        let mobileBar=document.getElementById('wfMobileActions');
        if(!mobileBar){
            mobileBar=document.createElement('div');mobileBar.id='wfMobileActions';mobileBar.className='wf-mobile-actions';mobileBar.setAttribute('aria-label','Действия автозалива');
            const state=document.createElement('span');state.className='wf-workflow-status';state.textContent='Черновик · PAUSED';
            const back=document.createElement('button');back.type='button';back.className='wf-mobile-back';back.textContent='Назад';back.setAttribute('aria-label','Предыдущий этап');
            const status=document.createElement('div');status.className='wf-mobile-step wf-workflow-step';status.setAttribute('aria-live','polite');
            const summaryButton=document.createElement('button');summaryButton.type='button';summaryButton.className='wf-mobile-summary';summaryButton.textContent='Сводка';
            const saveButton=document.createElement('button');saveButton.type='button';saveButton.id='wfSaveTemplateButton';saveButton.textContent='Сохранить шаблон';
            const next=document.createElement('button');next.type='button';next.className='wf-mobile-next';next.textContent='Далее';next.setAttribute('aria-label','Следующий этап');
            mobileBar.append(state,back,status,summaryButton,saveButton,next);document.body.appendChild(mobileBar);
            back.addEventListener('click',()=>{if(activeStep>0)stepTo(activeStep-1);});
            next.addEventListener('click',()=>{if(activeStep<reviewIndex-1)stepTo(activeStep+1);else if(reviewIndex>=0){stepTo(reviewIndex);const review=$('reviewLaunch');if(review&&!review.disabled)review.click();}});
            summaryButton.addEventListener('click',()=>{
                const panel=$('wfLiveSummary');if(!panel)return;
                panel.open=true;panel.scrollIntoView({behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});
            });
            saveButton.addEventListener('click',()=>{
                const name=$('bundleName');
                if(!String(name?.value||'').trim()){
                    stepTo(0);const disclosure=name?.closest('details');if(disclosure)disclosure.open=true;
                    name?.scrollIntoView({behavior:'smooth',block:'center'});name?.focus({preventScroll:true});return;
                }
                $('bundleSave')?.click();
            });
        }
        const mobileStatus=mobileBar.querySelector('.wf-workflow-step'),backButton=mobileBar.querySelector('.wf-mobile-back'),nextButton=mobileBar.querySelector('.wf-mobile-next');
        const reviewButton=$('reviewLaunch'),createButton=$('launchButton');
        if(reviewButton){reviewButton.classList.add('wf-toolbar-action');mobileBar.appendChild(reviewButton);}
        if(createButton){createButton.classList.add('wf-toolbar-action');mobileBar.appendChild(createButton);}
        function syncToolbarActions(){
            const isReview=activeStep===reviewIndex,createReady=!!(createButton&&!createButton.disabled);
            if(nextButton){
                nextButton.hidden=isReview;
                nextButton.textContent=activeStep===reviewIndex-1?'Проверить черновик':'Далее';
                nextButton.setAttribute('aria-label',nextButton.textContent);
            }
            if(reviewButton)reviewButton.hidden=!isReview||createReady;
            if(createButton)createButton.hidden=!isReview||!createReady;
        }
        function isStepComplete(index){
            if(index===0)return selectedLabels('profile').length>0&&selectedLabels('adAccount').length>0;
            if(index===1)return !!String($('campaignName')?.value||'').trim()&&!!selectedLabels('objective').length;
            if(index===2)return !!String($('adsetName')?.value||'').trim()&&Number($('dailyBudget')?.value||0)>0&&!!selectedLabels('optimizationGoal').length&&!!selectedLabels('billingEvent').length;
            if(index===3){const media=$('media');return !!selectedLabels('page').length&&!!String($('adName')?.value||'').trim()&&!!(media?.files?.length||selectedLabels('creativeLibrarySelect').length);}
            return false;
        }
        function syncCompletedStates(){stepCards.forEach((item,i)=>item.link.classList.toggle('is-complete',i!==activeStep&&isStepComplete(i)));}
        function setCurrentStep(index){
            if(!stepCards.length)return;
            activeStep=Math.max(0,Math.min(index,stepCards.length-1));
            root.dataset.wfActiveStep=stepCards[activeStep]?.key||'resources';
            root.style.setProperty('--wf-step-progress',((activeStep+1)/stepCards.length*100)+'%');
            stepCards.forEach((item,i)=>{
                const active=i===activeStep;item.link.classList.toggle('is-current',active);item.link.classList.toggle('is-complete',!active&&isStepComplete(i));
                item.cards.forEach(card=>{
                    card.classList.toggle('is-current-step',active);
                    card.hidden=!active;
                    card.setAttribute('aria-hidden',active?'false':'true');
                });
                if(active)item.link.setAttribute('aria-current','step');else item.link.removeAttribute('aria-current');
            });
            if(mobileStatus){
                const item=stepCards[activeStep];mobileStatus.replaceChildren();
                const title=document.createElement('strong');title.textContent=item.title;
                mobileStatus.append(title,document.createTextNode('Этап '+(activeStep+1)+' из '+stepCards.length));
            }
            if(backButton)backButton.disabled=activeStep===0;
            syncToolbarActions();
        }
        function stepTo(index){
            const item=stepCards[index];if(!item)return;
            setCurrentStep(index);
            const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
            item.card.scrollIntoView({behavior:reduced?'auto':'smooth',block:'start'});
            if(window.matchMedia?.('(max-width:720px)').matches){item.card.tabIndex=-1;item.card.focus({preventScroll:true});}
        }
        stepCards.forEach((item,index)=>item.link.addEventListener('click',event=>{event.preventDefault();stepTo(index);}));
        setCurrentStep(0);
        ['input','change'].forEach(type=>root.addEventListener(type,syncCompletedStates,true));
        root._wfSyncToolbar=syncToolbarActions;
        if(createButton&&'MutationObserver'in window){
            const actionObserver=new MutationObserver(syncToolbarActions);
            actionObserver.observe(createButton,{attributes:true,attributeFilter:['disabled']});
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
            extra.classList.add('wf-advanced','wf-meta-embedded');
            if(adset&&!adset.contains(extra))adset.appendChild(extra);else if(!root.contains(extra))root.appendChild(extra);
            const extraHeading=extra.querySelector('h3');setText(extraHeading,'Все дополнительные поля Meta');
            setText(extra.querySelector('.rm-meta-note'),'Поля сгруппированы по объектам Meta. Поиск работает по названию и API-коду; значения сохраняются в настройках черновика.');
            if(extra.dataset.wfDisclosure!=='1'){
                const details=document.createElement('details');details.className='wf-meta-disclosure';
                const summary=document.createElement('summary');if(extraHeading)summary.appendChild(extraHeading);else summary.textContent='Все дополнительные поля Meta';
                const body=document.createElement('div');body.className='wf-meta-disclosure-body';
                for(const child of Array.from(extra.childNodes))if(child!==extraHeading)body.appendChild(child);
                details.append(summary,body);extra.replaceChildren(details);extra.dataset.wfDisclosure='1';
            }
        }
        const buttons={rkSelectAll:'Выбрать все',rkClear:'Снять выбор',bundleLoad:'Загрузить',bundleSave:'Сохранить',bundleDelete:'Удалить',dryRunPlan:'Предварительный план',preflight:'Проверить профиль',syncMeta:'Обновить данные Meta',reviewLaunch:'Проверить черновик',launchButton:'Создать PAUSED черновик',loadTargetBindings:'Обновить привязки',loadTargetAudiences:'Загрузить аудитории',loadTargetMedia:'Загрузить медиа',refreshInsights:'Обновить статистику',activateJobAds:'Активировать объявления',pauseJobAds:'Приостановить объявления',continueJob:'Продолжить очередь',retryFailed:'Повторить неудачные',forceRetryReview:'Разобрать неизвестный результат',openRecentJob:'Открыть задачу'};
        for(const [id,text] of Object.entries(buttons)){
            const button=$(id);if(!button)continue;
            if(id==='launchButton'){for(const node of Array.from(button.childNodes)){if(node.nodeType===3)node.textContent=' '+text;}button.setAttribute('aria-label',text);}
            else setText(button,text);
        }
        if($('rkFilter'))$('rkFilter').placeholder='Найти РК, профиль или валюту';
        if($('dailyBudget'))$('dailyBudget').parentElement.querySelector('span.muted')?.remove();
        setupProfilePicker();setupAccountPicker();
        if($('profile')&&!$('profile').dataset.wfAccountRefreshBound){$('profile').dataset.wfAccountRefreshBound='1';$('profile').addEventListener('change',()=>{setupProfilePicker();setupAccountPicker();});}
        const accountActions=document.createElement('div');
        if($('preflight')&&$('syncMeta')&&!$('wfResourceActions')){
            accountActions.id='wfResourceActions';accountActions.className='wf-resource-actions';accountActions.setAttribute('aria-label','Действия с ресурсами');
            const accountButtons=[$('preflight'),$('syncMeta')].filter(Boolean);
            const formerRows=[...new Set(accountButtons.map(button=>button.closest('.row')).filter(Boolean))];
            for(const button of accountButtons){button.classList.add(button.id==='syncMeta'?'wf-resource-primary':'wf-resource-secondary');accountActions.appendChild(button);}
            const anchor=$('wfAdAccountPicker')||$('adAccount');if(anchor)anchor.insertAdjacentElement('afterend',accountActions);
            setText($('preflight'),'Проверить профиль');setText($('syncMeta'),'Обновить данные Meta');
            for(const row of formerRows){if(!row.querySelector('button,input,select,textarea,a'))row.remove();}
        }
        $('profile')?.closest('[class*="col-"]')?.classList.add('wf-profile-selector');
        $('adAccount')?.closest('[class*="col-"]')?.classList.add('wf-account-selector');
        const liveSummary=setupLiveSummary(root);
        const progressTrackNode=root.querySelector('.wf-step-progress');
        if(liveSummary&&progressTrackNode?.parentElement===root)progressTrackNode.after(liveSummary);
        const estimateLayout=root.matches('.rm-estimate-layout')?root:root.closest('.rm-estimate-layout');
        estimateLayout?.classList.add('wf-studio-estimate-layout');
        if(root.dataset.wfSummaryBound!=='1'){
            root.dataset.wfSummaryBound='1';
            ['input','change'].forEach(type=>root.addEventListener(type,()=>{updateLiveSummary();updateAudienceTargetingSummary();},true));
        }
        updateLiveSummary();
    }
function setupVisualChoices() {
        const configs={
            objective:{kind:'cards',icon:{OUTCOME_TRAFFIC:'↗',OUTCOME_SALES:'◆',OUTCOME_LEADS:'◎',OUTCOME_ENGAGEMENT:'♡',OUTCOME_AWARENESS:'◌',OUTCOME_APP_PROMOTION:'▣'},description:{
                OUTCOME_TRAFFIC:'Переходы и просмотры',OUTCOME_SALES:'Покупки и конверсии',OUTCOME_LEADS:'Заявки и контакты',
                OUTCOME_ENGAGEMENT:'Сообщения и реакции',OUTCOME_AWARENESS:'Охват и запоминаемость',OUTCOME_APP_PROMOTION:'Установки и действия в приложении'
            }},
            specialCategory:{kind:'pills',description:{CREDIT:'Финансовые продукты',EMPLOYMENT:'Вакансии и работа',HOUSING:'Недвижимость',ISSUES_ELECTIONS_POLITICS:'Политика и общественные вопросы'}},
            budgetLevel:{kind:'segments',description:{ADSET:'Лимит задаётся для этой группы',CAMPAIGN:'Общий лимит для кампании'}},
            placementMode:{kind:'cards',icon:{AUTO:'✦',MANUAL:'☷'},description:{AUTO:'Meta подбирает доступные места автоматически',MANUAL:'Выбрать платформы и места вручную'}}
        };
        const campaignNameField=$('campaignName')?.closest('[class*="col-"]');
        if(campaignNameField){
            campaignNameField.classList.add('wf-campaign-name-field');
            campaignNameField.closest('.row')?.classList.add('wf-campaign-builder');
        }
        for(const [id,config] of Object.entries(configs)){
            const source=$(id);if(!source||source.tagName!=='SELECT')continue;
            source.classList.add('wf-choice-source');source.setAttribute('aria-hidden','true');source.tabIndex=-1;
            let control=source.previousElementSibling;
            if(!control?.classList.contains('wf-choice-control')||control.dataset.wfChoiceFor!==id){
                control=document.createElement('div');control.className='wf-choice-control wf-choice-'+config.kind;control.dataset.wfChoiceFor=id;
                source.before(control);
            }
            if(!source.dataset.wfChoiceBound){
                source.dataset.wfChoiceBound='1';
                source.addEventListener('change',()=>control.querySelectorAll('[data-wf-choice-value]').forEach(button=>{
                    const selected=button.dataset.wfChoiceValue===source.value;button.classList.toggle('is-selected',selected);
                    button.setAttribute('aria-checked',String(selected));button.setAttribute('aria-pressed',String(selected));
                }));
            }
            const signature=[...source.options].map(option=>[option.value,option.textContent.trim(),option.disabled?'1':'0'].join('~')).join('|');
            if(control.dataset.wfChoiceSignature!==signature){
                control.replaceChildren();control.dataset.wfChoiceSignature=signature;
                [...source.options].forEach((option,index)=>{
                    const button=document.createElement('button');button.type='button';button.className='wf-choice-option';
                    button.dataset.wfChoiceValue=option.value;button.disabled=option.disabled;button.setAttribute('role','radio');
                    const icon=document.createElement('span');icon.className='wf-choice-icon';icon.setAttribute('aria-hidden','true');
                    icon.textContent=config.icon?.[option.value]||(config.kind==='cards'?String(index+1).padStart(2,'0'):'');
                    const copy=document.createElement('span');copy.className='wf-choice-copy';
                    const title=document.createElement('strong');title.textContent=option.textContent.trim();copy.appendChild(title);
                    const detail=config.description?.[option.value];
                    if(detail){const hint=document.createElement('small');hint.textContent=detail;copy.appendChild(hint);}
                    const check=document.createElement('span');check.className='wf-choice-check';check.setAttribute('aria-hidden','true');
                    button.append(icon,copy,check);
                    button.addEventListener('click',()=>{
                        if(option.disabled)return;
                        source.value=option.value;
                        source.dispatchEvent(new Event('input',{bubbles:true}));
                        source.dispatchEvent(new Event('change',{bubbles:true}));
                    });
                    control.appendChild(button);
                });
                control.setAttribute('role','radiogroup');
                const field=source.closest('[class*="col-"]');
                if(id==='campaignName')field?.classList.add('wf-campaign-name-field');
                if(id==='objective')field?.classList.add('wf-campaign-objective-field');
                if(id==='specialCategory')field?.classList.add('wf-campaign-category-field');
                if(id==='objective'||id==='specialCategory')field?.closest('.row')?.classList.add('wf-campaign-builder');
            }
            control.querySelectorAll('[data-wf-choice-value]').forEach(button=>{
                const selected=button.dataset.wfChoiceValue===source.value;button.classList.toggle('is-selected',selected);
                button.setAttribute('aria-checked',String(selected));button.setAttribute('aria-pressed',String(selected));
            });
        }
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
        ensureMetaChoicePrompts();
        setupVisualChoices();
        const captions={
            'MASS META LAUNCH':'ЗАПУСК РЕКЛАМЫ', 'Run preflight first.':'Выберите профили и загрузите РК.', 'Run preflight first':'Сначала загрузите РК',
            'Choose one or more Facebook profiles and run preflight.':'Выберите профиль и нажмите «Обновить данные Meta», чтобы загрузить сохранённые РК.',
            'API usage: no data yet.':'Данных об использовании API пока нет.', 'FB profile':'Профиль FB', 'Currency':'Валюта',
            'Funding source':'Способ оплаты', 'Type':'Тип', 'Account':'Аккаунт', 'Spend cap':'Лимит расходов', 'Status':'Статус',
            'Select RK and check funding.':'Выберите РК для проверки оплаты.', 'No review yet.':'План ещё не подготовлен.', 'No launch yet.':'Реклама ещё не создана.',
            'RK':'РК', 'Infrastructure readiness':'Готовность инфраструктуры', 'Page / IG':'Страница / Instagram',
            'Pixel':'Пиксель', 'Funding':'Оплата', 'Warnings / Issues':'Предупреждения',
            'INFRASTRUCTURE READINESS':'Готовность инфраструктуры', 'PAGE / IG':'Страница / Instagram',
            'PIXEL':'Пиксель', 'FUNDING':'Оплата', 'WARNINGS / ISSUES':'Предупреждения',
            'First-stage safety:':'Режим PAUSED:', 'Campaign, Ad Set and Ad will be created as':'Кампания, группа и объявление сохранятся со статусом',
            'No active job.':'Нет активной задачи.', 'No job.':'Задач пока нет.', 'Recent jobs':'Недавние задачи', 'Select bundle':'Выберите сохранённые настройки',
            'No pixel':'Без пикселя', 'Not required':'Не требуется', 'Select one RK':'Выберите один РК', 'No connected Instagram':'Нет связанного Instagram',
            'No existing Meta asset selected.':'Существующее медиа Meta не выбрано.',
            'For one RK, audiences can be selected here. Multi-RK Launch uses the Per-RK bindings table above.':'Для одного РК выберите аудитории здесь. Для нескольких РК используются отдельные привязки к каждому кабинету.',
            'Bundle stores Campaign, Ad Set, targeting, Page/IG/Pixel selection, creative text, CTA, URL and UTM. Media file must be selected for each launch.':'Набор сохраняет настройки рекламы, медиа и порядок карточек. Для бюджета на весь срок укажите окончание показа.'
        };
        const launch=document.querySelector('.launch-wrap');
        if(launch){const walker=document.createTreeWalker(launch,NodeFilter.SHOW_TEXT);let node;while((node=walker.nextNode())){if(node.parentElement.closest('textarea,code,article'))continue;const key=node.textContent.trim();let caption=captions[key];if(/^Selected FB: \d+$/.test(key))caption=key.replace('Selected FB:','Выбрано профилей:');else if(/^Selected: \d+$/.test(key))caption=key.replace('Selected:','Выбрано РК:');if(caption && key!==caption)node.textContent=caption;
                if(key==='Choose one or more Facebook profiles and run preflight.')node.parentElement?.closest('.alert,.alert-info,.alert-warning,.muted,.small')?.classList.add('wf-legacy-resource-guidance');
                if(key==='API usage: no data yet.')node.parentElement?.classList.add('wf-empty-api-status');}}
        for(const [id,placeholder] of [['geoQuery','Страна, регион или город'],['interestQuery','Выберите интерес или найдите по названию'],['behaviorQuery','Выберите поведение или найдите по названию']])if($(id))$(id).placeholder=placeholder;
        setupProfilePicker();
        window.remaskModuleControls?.scan?.();
        setupAudienceTargeting();
        setupAudienceSections();
        setupAudienceWorkspace();
        setupStudioTargetTabs();
        setupMetaGroupTabs();
        mountAudienceEstimate(launch);
        setupAdStagePolish();
        setupPreviewPlacementLabels();
        updateAudienceTargetingSummary();
        updateLiveSummary();
        updateCreativeSummary();
        applyStudioV3();
    }
    style.textContent += '\n.wf-launch.wf-studio-v3 .wf-campaign-builder{grid-template-columns:minmax(0,1fr)!important;gap:18px!important}.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-name-field,.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-objective-field,.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-category-field{grid-column:1/-1!important}.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:repeat(3,minmax(0,1fr))!important}.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:9px!important}.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option{min-height:58px!important;padding:10px 12px!important}.wf-launch.wf-studio-v3 .wf-summary-quickfacts{display:none!important}.wf-launch.wf-studio-v3 .wf-live-summary-body{gap:0;padding:0 16px 8px}.wf-launch.wf-studio-v3 .wf-live-row{display:grid;grid-template-columns:68px minmax(0,1fr);align-items:center;gap:8px;padding:9px 0!important}.wf-launch.wf-studio-v3 .wf-live-row-label{font-size:8px!important;line-height:1.3;white-space:nowrap}.wf-launch.wf-studio-v3 .wf-live-row-value{font-size:11px!important;line-height:1.4}.wf-launch.wf-studio-v3 .form-control.wf-empty-choice{color:#97a9c2!important}\n@media(max-width:720px){.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:repeat(2,minmax(0,1fr))!important}.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills{grid-template-columns:repeat(2,minmax(0,1fr))!important}body.wf-autoupload-open{padding-bottom:calc(132px + env(safe-area-inset-bottom))}.wf-mobile-actions{grid-template-columns:minmax(0,1fr) auto auto!important;grid-template-areas:"state summary save" "step back next";row-gap:8px}.wf-workflow-status{grid-area:state;min-width:0}.wf-mobile-step{grid-area:step;text-align:left}.wf-mobile-summary{grid-area:summary}.wf-mobile-back{grid-area:back}.wf-mobile-next,#reviewLaunch,#launchButton{grid-area:next}#wfSaveTemplateButton{grid-area:save}}';
    studioV3Style.textContent += "\n/* AutoUpload Studio v4: a calm, full-width editing surface with compact review. */\n.wf-launch.wf-studio-v3{\n  --studio-ink:#252832;--studio-muted:#69717e;--studio-border:#e0e3e9;--studio-border-strong:#cbd0d9;\n  --studio-surface:#fff;--studio-surface-2:#f7f8fa;--studio-accent:#7655dc;--studio-accent-strong:#6544cc;\n  --studio-accent-soft:#f3efff;--studio-positive:#168765;--studio-shadow:0 4px 16px rgba(25,31,42,.045);\n  grid-template-columns:minmax(0,1fr)!important;column-gap:0!important;row-gap:12px!important;\n  max-width:1280px!important;padding:22px 30px 112px!important;background:#f5f6f8!important;\n}\n.wf-launch.wf-studio-v3>.app-page-head,.wf-launch.wf-studio-v3>.wf-step-nav,.wf-launch.wf-studio-v3>.wf-step-progress,\n.wf-launch.wf-studio-v3>.wf-live-summary,.wf-launch.wf-studio-v3>.launch-card.wf-step-card,.wf-launch.wf-studio-v3>#launchMetaAllFields{\n  grid-column:1/-1!important;min-width:0!important;width:100%!important;\n}\n.wf-launch.wf-studio-v3>.wf-live-summary{\n  grid-row:auto!important;position:static!important;align-self:stretch!important;max-height:none!important;overflow:visible!important;\n  margin:0!important;padding:0!important;border:0!important;border-top:1px solid #dfe2e8!important;border-bottom:1px solid #dfe2e8!important;\n  border-radius:0!important;background:transparent!important;box-shadow:none!important;color:var(--studio-ink)!important;\n}\n.wf-launch.wf-studio-v3>.wf-live-summary>summary{\n  display:grid!important;grid-template-columns:minmax(145px,190px) auto minmax(0,1fr) 10px!important;\n  align-items:center!important;gap:12px!important;min-width:0;padding:8px 0!important;\n  border:0!important;border-radius:0!important;background:transparent!important;cursor:pointer;\n}\n.wf-launch.wf-studio-v3 .wf-live-summary-heading{display:grid;gap:1px;min-width:0}\n.wf-launch.wf-studio-v3 .wf-live-summary-title{color:#292d35!important;font-size:12px!important;font-weight:720!important}\n.wf-launch.wf-studio-v3 .wf-live-summary-caption{color:#7d848e!important;font-size:9px!important}\n.wf-launch.wf-studio-v3 .wf-live-status{justify-self:start;padding:5px 8px!important;border:1px solid #cce8dc!important;border-radius:999px!important;background:#f0f9f5!important;color:#147656!important;font-size:8px!important;white-space:nowrap}\n.wf-launch.wf-studio-v3 .wf-summary-quickfacts{\n  display:grid!important;grid-template-columns:repeat(4,minmax(0,1fr))!important;gap:0!important;width:100%!important;margin:0!important;\n}\n.wf-launch.wf-studio-v3 .wf-summary-quickfact{\n  display:grid;gap:3px;min-width:0;padding:2px 11px!important;border:0!important;border-left:1px solid #e5e7ec!important;\n  border-radius:0!important;background:transparent!important;\n}\n.wf-launch.wf-studio-v3 .wf-summary-quickfact>span{color:#7e8490!important;font-size:8px!important;font-weight:750!important;letter-spacing:.06em;text-transform:uppercase}\n.wf-launch.wf-studio-v3 .wf-summary-quickfact>strong{overflow:hidden;color:#353a44!important;font-size:10px!important;font-weight:650!important;line-height:1.35;text-overflow:ellipsis;white-space:nowrap}\n.wf-launch.wf-studio-v3>.wf-live-summary>summary:after{\n  content:\"\";width:6px;height:6px;border-right:1.5px solid #7d848e;border-bottom:1.5px solid #7d848e;transform:rotate(45deg);\n}\n.wf-launch.wf-studio-v3>.wf-live-summary[open]>summary{border-bottom:1px solid #e5e7ec!important}\n.wf-launch.wf-studio-v3>.wf-live-summary[open]>summary:after{transform:rotate(225deg)}\n.wf-launch.wf-studio-v3 .wf-live-summary-body{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 24px!important;padding:4px 0 8px!important}\n.wf-launch.wf-studio-v3 .wf-live-row{display:grid;grid-template-columns:90px minmax(0,1fr);align-items:center;gap:10px;padding:8px 0!important;border-top:1px solid #eef0f3!important}\n.wf-launch.wf-studio-v3 .wf-live-row-label{color:#7b828d!important;font-size:9px!important;line-height:1.3;white-space:nowrap}\n.wf-launch.wf-studio-v3 .wf-live-row-value{color:#373c45!important;font-size:11px!important;line-height:1.4}\n.wf-launch.wf-studio-v3 .wf-summary-more,.wf-launch.wf-studio-v3 .wf-live-summary-foot{grid-column:1/-1!important}\n.wf-launch.wf-studio-v3 .wf-live-summary-foot{padding:8px 0 10px!important;border-top:1px solid #eef0f3;color:#737b87!important;font-size:10px!important}\n.wf-launch.wf-studio-v3>.launch-card.wf-step-card{width:100%!important;padding:0!important;border:0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important}\n.wf-launch.wf-studio-v3>.launch-card.wf-step-card>h5{margin:0 0 6px!important;padding:0 0 10px!important;border-bottom:1px solid #e0e3e9!important;font-size:22px!important}\n.wf-launch.wf-studio-v3 .wf-step-context{margin-bottom:14px!important}\n.wf-launch.wf-studio-v3 .wf-campaign-builder{display:grid!important;grid-template-columns:minmax(0,1fr)!important;gap:14px!important}\n.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-name-field,\n.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-objective-field,\n.wf-launch.wf-studio-v3 .wf-campaign-builder>.wf-campaign-category-field{\n  grid-column:1/-1!important;width:100%!important;max-width:none!important;min-width:0!important;flex:none!important;padding:0!important;margin:0!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-name-field input{width:100%!important;max-width:none!important;min-height:42px!important}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:0 12px!important;border-top:1px solid #e6e8ed}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-option{\n  min-width:0;min-height:48px!important;padding:8px 10px!important;border:0!important;border-bottom:1px solid #e6e8ed!important;\n  border-left:2px solid transparent!important;border-radius:0!important;background:transparent!important;box-shadow:none!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-option:hover{background:#f8f6ff!important}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-option.is-selected{\n  border-left-color:var(--studio-accent)!important;background:var(--studio-accent-soft)!important;color:#4e367c!important;box-shadow:none!important\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-icon{border:0!important;background:transparent!important;color:var(--studio-accent)!important}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills{display:flex!important;flex-wrap:wrap;gap:7px!important}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option{\n  min-height:34px!important;padding:6px 11px!important;border:1px solid #e0e3e9!important;border-radius:999px!important;background:#fff!important;\n  box-shadow:none!important;font-size:11px!important\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option.is-selected{border-color:#b6a5eb!important;background:#f3efff!important;color:#52378b!important}\n.wf-launch.wf-studio-v3 .wf-audience-inline{\n  display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:12px 20px!important;width:100%!important;margin:10px 0 4px!important;\n}\n.wf-launch.wf-studio-v3 .wf-audience-inline>.wf-audience-field{\n  box-sizing:border-box!important;display:block!important;min-width:0!important;width:100%!important;max-width:none!important;flex:1 1 auto!important;\n  margin:0!important;padding:0!important;grid-column:auto!important\n}\n.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field :is(input:not([type=hidden]),select,textarea,[role=combobox],.rm-module-search){\n  box-sizing:border-box!important;width:100%!important;max-width:none!important;min-width:0!important;min-height:40px!important;\n  padding:8px 11px!important;border:1px solid #d8dce4!important;border-radius:9px!important;background:#fff!important;\n  color:#252832!important;box-shadow:none!important;font-size:12px!important;line-height:1.4!important\n}\n.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field :is(input:not([type=hidden]),select,textarea,[role=combobox],.rm-module-search):focus{\n  border-color:#967de2!important;outline:0!important;box-shadow:0 0 0 3px rgba(118,85,220,.12)!important\n}\n.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field :is(input,select,textarea)::placeholder{color:#9ca2ad!important;opacity:1}\n.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field label{margin-bottom:5px!important}\n.wf-launch.wf-studio-v3 .wf-section-heading{margin-top:15px!important}\n.wf-launch.wf-studio-v3 .wf-target-accordion,.wf-launch.wf-studio-v3 .wf-audience-delivery{margin-top:10px!important}\n.wf-launch.wf-studio-v3 .wf-target-accordion .wf-target-fields{grid-template-columns:repeat(2,minmax(0,1fr))!important}\n.wf-launch.wf-studio-v3 .wf-choice-segments{max-width:520px}\n.wf-launch.wf-studio-v3 .form-control.wf-empty-choice{color:#777e89!important}\nbody.app-shell.wf-autoupload-open .app-connection{border-color:#343b46!important;background:#242a33!important;color:#c2c8d1!important}\nbody.app-shell.wf-autoupload-open .app-connection i{color:#48c69c!important}\nbody.wf-autoupload-open #wfMobileActions{\n  grid-template-columns:auto minmax(0,1fr) auto auto auto!important;gap:9px!important;padding:10px 24px calc(10px + env(safe-area-inset-bottom))!important;\n  border-top:1px solid #dfe2e8!important;background:rgba(255,255,255,.96)!important;box-shadow:0 -5px 18px rgba(25,31,42,.06)!important;backdrop-filter:blur(16px)\n}\nbody.wf-autoupload-open #wfMobileActions .wf-mobile-step{color:#5e6672!important;font-size:11px!important;font-weight:600!important}\nbody.wf-autoupload-open #wfMobileActions .wf-workflow-status{color:#59616d!important}\nbody.wf-autoupload-open #wfMobileActions button,body.wf-autoupload-open #wfMobileActions .btn{min-height:38px!important;border-radius:9px!important}\nbody.wf-autoupload-open #wfMobileActions .wf-mobile-next,\nbody.wf-autoupload-open #wfMobileActions #reviewLaunch,\nbody.wf-autoupload-open #wfMobileActions #launchButton{border-color:var(--studio-accent)!important;background:var(--studio-accent)!important;color:#fff!important}\n@media(max-width:900px){\n  .wf-launch.wf-studio-v3{max-width:100%!important;padding:18px 22px 128px!important}\n  .wf-launch.wf-studio-v3>.wf-live-summary>summary{grid-template-columns:minmax(120px,170px) auto minmax(0,1fr) 10px!important;gap:8px!important}\n  .wf-launch.wf-studio-v3 .wf-summary-quickfact{padding:2px 7px!important}\n  .wf-launch.wf-studio-v3 .wf-audience-inline{gap:10px 14px!important}\n  body.wf-autoupload-open #wfMobileActions{left:0!important}\n}\n@media(max-width:640px){\n  .wf-launch.wf-studio-v3{padding:15px 14px 132px!important;row-gap:10px!important}\n  .wf-launch.wf-studio-v3>.wf-live-summary>summary{grid-template-columns:minmax(0,1fr) auto 10px!important;gap:7px!important}\n  .wf-launch.wf-studio-v3 .wf-live-summary-caption{display:none!important}\n  .wf-launch.wf-studio-v3 .wf-summary-quickfacts{grid-column:1/-1!important;grid-row:2!important;grid-template-columns:repeat(2,minmax(0,1fr))!important}\n  .wf-launch.wf-studio-v3 .wf-summary-quickfact{padding:5px 8px!important;border-left:0!important;border-top:1px solid #e5e7ec!important}\n  .wf-launch.wf-studio-v3 .wf-live-summary-body{grid-template-columns:minmax(0,1fr)!important}\n  .wf-launch.wf-studio-v3 .wf-audience-inline{grid-template-columns:minmax(0,1fr)!important}\n  .wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards{grid-template-columns:repeat(2,minmax(0,1fr))!important}\n  .wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important}\n  .wf-launch.wf-studio-v3 .wf-step-label{font-size:10px!important}\n  body.wf-autoupload-open #wfMobileActions{grid-template-columns:minmax(0,1fr) auto auto!important;grid-template-areas:\"state summary save\" \"step back next\"!important;row-gap:8px!important;padding:9px 12px calc(9px + env(safe-area-inset-bottom))!important}\n  body.wf-autoupload-open #wfMobileActions .wf-workflow-status{grid-area:state}\n  body.wf-autoupload-open #wfMobileActions .wf-mobile-step{grid-area:step;text-align:left}\n  body.wf-autoupload-open #wfMobileActions .wf-mobile-summary{grid-area:summary}\n  body.wf-autoupload-open #wfMobileActions .wf-mobile-back{grid-area:back}\n  body.wf-autoupload-open #wfMobileActions .wf-mobile-next{grid-area:next}\n  body.wf-autoupload-open #wfMobileActions #wfSaveTemplateButton{grid-area:save}\n}\n";
    studioV3Style.textContent += "\n/* AutoUpload Studio V10: one working column, restrained choices and a cool neutral palette. */\n.wf-launch.wf-studio-v3 {\n  --studio-ink:#262a32;\n  --studio-muted:#68717d;\n  --studio-border:#dfe3e9;\n  --studio-accent:#7655dc;\n  --studio-accent-strong:#6544cc;\n  --studio-accent-soft:#f3efff;\n  grid-template-columns:minmax(0,1fr)!important;\n  column-gap:0!important;\n  background:#f5f6f8!important;\n}\n.wf-launch.wf-studio-v3>.wf-live-summary {\n  grid-column:1/-1!important;\n  grid-row:auto!important;\n  position:static!important;\n  align-self:stretch!important;\n  width:100%!important;\n  max-height:none!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards {\n  display:grid!important;\n  grid-template-columns:repeat(3,minmax(0,1fr))!important;\n  gap:0 12px!important;\n  border-top:1px solid var(--studio-border)!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-option {\n  position:relative;\n  display:flex!important;\n  align-items:center!important;\n  min-width:0!important;\n  min-height:44px!important;\n  padding:6px 9px!important;\n  border:0!important;\n  border-bottom:1px solid var(--studio-border)!important;\n  border-left:2px solid transparent!important;\n  border-radius:0!important;\n  background:transparent!important;\n  box-shadow:none!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-option::before {\n  content:\"\";\n  width:14px;\n  height:14px;\n  flex:0 0 14px;\n  margin-right:9px;\n  border:1.5px solid #b9bec8;\n  border-radius:50%;\n  background:#fff;\n  box-sizing:border-box;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-option.is-selected {\n  border-left-color:var(--studio-accent)!important;\n  background:var(--studio-accent-soft)!important;\n  color:#493674!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-option.is-selected::before {\n  border:4px solid var(--studio-accent);\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-icon,\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-check {\n  display:none!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-copy {\n  display:grid!important;\n  gap:2px!important;\n  min-width:0;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-copy strong {\n  overflow:hidden;\n  font-size:12px!important;\n  text-overflow:ellipsis;\n  white-space:nowrap;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-copy small {\n  overflow:hidden;\n  color:var(--studio-muted)!important;\n  font-size:10px!important;\n  text-overflow:ellipsis;\n  white-space:nowrap;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills {\n  display:grid!important;\n  grid-template-columns:repeat(3,minmax(0,1fr))!important;\n  gap:7px!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option {\n  justify-content:flex-start!important;\n  min-width:0!important;\n  min-height:40px!important;\n  padding:7px 10px!important;\n  border:1px solid var(--studio-border)!important;\n  border-radius:8px!important;\n  background:#fff!important;\n  box-shadow:none!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option.is-selected {\n  border-color:#b4a2e9!important;\n  background:#f4f0fc!important;\n  color:#493674!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option small {\n  display:none!important;\n}\n.wf-launch.wf-studio-v3 .wf-target-fields {\n  display:grid!important;\n  grid-template-columns:repeat(2,minmax(0,1fr))!important;\n  gap:10px 16px!important;\n}\n.wf-launch.wf-studio-v3 .wf-target-field {\n  box-sizing:border-box!important;\n  min-width:0!important;\n  width:100%!important;\n}\n@media(max-width:720px) {\n  .wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards,\n  .wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills,\n  .wf-launch.wf-studio-v3 .wf-target-fields {\n    grid-template-columns:repeat(2,minmax(0,1fr))!important;\n  }\n}\n@media(max-width:420px) {\n  .wf-launch.wf-studio-v3 .wf-campaign-objective-field .wf-choice-cards,\n  .wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills,\n  .wf-launch.wf-studio-v3 .wf-target-fields {\n    grid-template-columns:minmax(0,1fr)!important;\n  }\n}\n";

    studioV3Style.textContent += "\n/* AutoUpload Studio V11: use one compact radio language for special categories. */\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills {\n  display:grid!important;\n  grid-template-columns:repeat(3,minmax(0,1fr))!important;\n  gap:0 12px!important;\n  border-top:1px solid var(--studio-border)!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option {\n  position:relative;\n  display:flex!important;\n  align-items:center!important;\n  min-width:0!important;\n  min-height:40px!important;\n  padding:6px 9px!important;\n  border:0!important;\n  border-bottom:1px solid var(--studio-border)!important;\n  border-left:2px solid transparent!important;\n  border-radius:0!important;\n  background:transparent!important;\n  box-shadow:none!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option::before {\n  content:\"\";\n  width:14px;\n  height:14px;\n  flex:0 0 14px;\n  margin-right:9px;\n  border:1.5px solid #b9bec8;\n  border-radius:50%;\n  background:#fff;\n  box-sizing:border-box;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option.is-selected {\n  border-left-color:var(--studio-accent)!important;\n  background:var(--studio-accent-soft)!important;\n  color:#493674!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-option.is-selected::before {\n  border:4px solid var(--studio-accent);\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-icon,\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-check {\n  display:none!important;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-copy {\n  min-width:0;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-copy strong {\n  overflow:hidden;\n  font-size:11px!important;\n  text-overflow:ellipsis;\n  white-space:nowrap;\n}\n.wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-copy small {\n  display:none!important;\n}\n@media(max-width:720px) {\n  .wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills {\n    grid-template-columns:repeat(2,minmax(0,1fr))!important;\n  }\n}\n@media(max-width:420px) {\n  .wf-launch.wf-studio-v3 .wf-campaign-category-field .wf-choice-pills {\n    grid-template-columns:minmax(0,1fr)!important;\n  }\n}\n";

    studioV3Style.textContent += `
/* AutoUpload Studio V12: cool graphite, clean targeting controls, and category navigation. */
body.wf-autoupload-open main:has(.wf-launch){background:#f4f6f9!important;color:#182230!important}
.wf-launch.wf-studio-v3{
  --studio-ink:#182230;
  --studio-muted:#667386;
  --studio-border:#dce3ec;
  --studio-border-strong:#cbd5e1;
  --studio-surface:#fff;
  --studio-surface-2:#f8fafc;
  --studio-accent:#7155dc;
  --studio-accent-strong:#5e43c8;
  --studio-accent-soft:#f3f0ff;
  --studio-positive:#168765;
  background:#f4f6f9!important;
  color:var(--studio-ink)!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline{
  grid-template-columns:repeat(2,minmax(0,1fr))!important;
  gap:12px 18px!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline>.wf-audience-field{
  border:0!important;border-bottom:0!important;background:transparent!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field .rm-module-control{
  display:grid!important;grid-template-columns:minmax(0,1fr)!important;gap:4px!important;
  box-sizing:border-box!important;width:100%!important;max-width:none!important;min-width:0!important;min-height:0!important;
  margin:0!important;padding:0!important;border:0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field :is(input:not([type=hidden]),select,textarea,[role=combobox],.rm-module-search){
  box-sizing:border-box!important;width:100%!important;max-width:none!important;min-width:0!important;min-height:40px!important;
  padding:8px 11px!important;border:1px solid #d8dee8!important;border-radius:9px!important;background:#fff!important;
  color:#182230!important;box-shadow:none!important;font-size:12px!important;line-height:1.4!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field :is(input:not([type=hidden]),select,textarea,[role=combobox],.rm-module-search):focus{
  border-color:#9179e3!important;outline:0!important;box-shadow:0 0 0 3px rgba(113,85,220,.13)!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field .rm-module-pills:empty,
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field .rm-module-results:empty,
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field .rm-module-hint:empty{display:none!important}
.wf-launch.wf-studio-v3 .rm-module-results:not(:empty){
  border:1px solid #dce3ec!important;border-radius:9px!important;background:#fff!important;
  color:#182230!important;box-shadow:0 10px 24px rgba(26,35,48,.10)!important;
}
.wf-launch.wf-studio-v3 .rm-module-choice{padding:8px 10px!important;color:#303b4b!important}
.wf-launch.wf-studio-v3 .rm-module-choice:hover{background:#f3f0ff!important;color:#483478!important}
.wf-launch.wf-studio-v3 .rm-module-pill,
.wf-launch.wf-studio-v3 .rm-module-pills>*{
  border:1px solid #dfd7f6!important;border-radius:999px!important;background:#f5f2ff!important;color:#514078!important;
}
.wf-launch.wf-studio-v3 .rm-module-hint{padding:3px 0!important;color:#697689!important;opacity:1!important;font-size:10px!important}
.wf-launch.wf-studio-v3 .rm-module-exclusion-group,
.wf-launch.wf-studio-v3 .rm-module-geo-exclusion-group,
.wf-launch.wf-studio-v3 .rm-module-geo-inclusion-group,
.wf-launch.wf-studio-v3 .rm-module-city-target{
  min-width:0!important;border:1px solid #dce3ec!important;border-radius:10px!important;background:#fff!important;color:#263243!important;
  box-shadow:none!important;
}
.wf-launch.wf-studio-v3 .rm-module-exclusion-label,
.wf-launch.wf-studio-v3 .rm-module-geo-exclusion-label,
.wf-launch.wf-studio-v3 .rm-module-geo-inclusion-label,
.wf-launch.wf-studio-v3 .rm-module-city-radius-label{color:#58677a!important}
.wf-launch.wf-studio-v3 .rm-module-city-remove,
.wf-launch.wf-studio-v3 .rm-module-structure .btn{
  min-height:32px!important;padding:6px 10px!important;border:1px solid #dce3ec!important;border-radius:8px!important;
  background:#fff!important;color:#364255!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 .rm-module-structure{gap:6px!important}
.wf-launch.wf-studio-v3 .rm-module-structure>.rm-module-hint{flex-basis:auto!important}
.wf-launch.wf-studio-v3 .wf-target-groups{width:100%!important;min-width:0!important;margin:16px 0 0!important}
.wf-launch.wf-studio-v3 .wf-target-head{margin:0 0 8px!important}
.wf-launch.wf-studio-v3 .wf-target-head h6{margin:0!important;color:#263243!important;font-size:14px!important;font-weight:700!important;letter-spacing:-.01em}
.wf-launch.wf-studio-v3 .wf-target-head p{margin:3px 0 0!important;color:#6b7788!important;font-size:11px!important;line-height:1.45!important}
.wf-launch.wf-studio-v3 .wf-target-tabs{
  display:flex!important;align-items:stretch!important;gap:4px!important;width:100%!important;min-width:0!important;
  overflow-x:auto!important;overscroll-behavior-x:contain;scrollbar-width:thin;
  border-bottom:1px solid #dce3ec!important;
}
.wf-launch.wf-studio-v3 .wf-target-tabs[hidden]{display:none!important}
.wf-launch.wf-studio-v3 .wf-target-tab{
  appearance:none!important;display:inline-flex!important;align-items:center!important;justify-content:center!important;gap:7px!important;
  flex:0 0 auto!important;min-height:39px!important;padding:8px 10px!important;margin:0!important;
  border:0!important;border-bottom:2px solid transparent!important;border-radius:0!important;
  background:transparent!important;color:#667386!important;box-shadow:none!important;transform:none!important;
  font-size:11px!important;font-weight:650!important;white-space:nowrap!important;cursor:pointer!important;
}
.wf-launch.wf-studio-v3 .wf-target-tab:hover{background:#f0eef8!important;color:#45366d!important}
.wf-launch.wf-studio-v3 .wf-target-tab[aria-selected="true"]{
  border-bottom-color:var(--studio-accent)!important;background:transparent!important;color:#342857!important;
}
.wf-launch.wf-studio-v3 .wf-target-tab:focus-visible{outline:2px solid rgba(113,85,220,.42)!important;outline-offset:-3px!important}
.wf-launch.wf-studio-v3 .wf-target-tab-count{
  display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 5px;
  border-radius:9px;background:#eeebf7;color:#5a4a82;font-size:9px;font-weight:750;line-height:1;
}
.wf-launch.wf-studio-v3 .wf-target-tab-count[hidden]{display:none!important}
.wf-launch.wf-studio-v3 .wf-target-list{display:block!important;width:100%!important;min-width:0!important;margin:0!important;padding:0!important}
.wf-launch.wf-studio-v3 .wf-target-panel{
  display:block!important;width:100%!important;min-width:0!important;margin:0!important;padding:12px 0 0!important;
  border:0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important;overflow:visible!important;
}
.wf-launch.wf-studio-v3 .wf-target-panel[hidden]{display:none!important}
.wf-launch.wf-studio-v3 .wf-target-panel-heading{display:none!important}
.wf-launch.wf-studio-v3 .wf-target-note{margin:0 0 10px!important;padding:0!important;color:#6b7788!important;font-size:10px!important;line-height:1.45!important}
.wf-launch.wf-studio-v3 .wf-target-fields{
  display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:12px 16px!important;width:100%!important;padding:0!important;
}
.wf-launch.wf-studio-v3 .wf-target-field{min-width:0!important;width:100%!important;padding:0!important}
.wf-launch.wf-studio-v3 .wf-target-field>label{margin:0 0 5px!important;color:#515e70!important;font-size:10px!important;font-weight:650!important}
.wf-launch.wf-studio-v3 .wf-target-field .rm-module-control{width:100%!important;min-width:0!important;padding:0!important;border:0!important;background:transparent!important;box-shadow:none!important}
.wf-launch.wf-studio-v3 .wf-target-field .rm-module-results:empty,
.wf-launch.wf-studio-v3 .wf-target-field .rm-module-pills:empty,
.wf-launch.wf-studio-v3 .wf-target-field .rm-module-hint:empty{display:none!important}
.wf-launch.wf-studio-v3 .wf-target-empty{
  margin:9px 0 0!important;padding:10px 0!important;border:0!important;border-top:1px solid #dce3ec!important;border-radius:0!important;
  background:transparent!important;color:#667386!important;font-size:11px!important;
}
.wf-launch.wf-studio-v3 .wf-audience-group,
.wf-launch.wf-studio-v3 .wf-audience-subgroup,
.wf-launch.wf-studio-v3 .wf-audience-delivery,
.wf-launch.wf-studio-v3 .wf-audience-estimate{
  margin:0!important;padding:0!important;border:0!important;border-top:1px solid #dce3ec!important;border-radius:0!important;
  background:transparent!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 .wf-audience-group>summary,
.wf-launch.wf-studio-v3 .wf-audience-subgroup>summary,
.wf-launch.wf-studio-v3 .wf-audience-delivery>summary,
.wf-launch.wf-studio-v3 .wf-audience-estimate>summary{
  min-height:42px!important;padding:9px 0!important;border:0!important;border-bottom:1px solid transparent!important;
  border-radius:0!important;background:transparent!important;color:#263243!important;font-size:12px!important;font-weight:650!important;
}
.wf-launch.wf-studio-v3 .wf-audience-group[open]>summary,
.wf-launch.wf-studio-v3 .wf-audience-subgroup[open]>summary,
.wf-launch.wf-studio-v3 .wf-audience-delivery[open]>summary,
.wf-launch.wf-studio-v3 .wf-audience-estimate[open]>summary{
  border-bottom-color:#dce3ec!important;
}
.wf-launch.wf-studio-v3 .wf-audience-group>summary::after,
.wf-launch.wf-studio-v3 .wf-audience-subgroup>summary::after,
.wf-launch.wf-studio-v3 .wf-audience-delivery>summary::after,
.wf-launch.wf-studio-v3 .wf-audience-estimate>summary::after{border-color:#8290a2!important}
@media(max-width:900px){
  .wf-launch.wf-studio-v3 .wf-target-fields{grid-template-columns:repeat(2,minmax(0,1fr))!important}
}
@media(max-width:640px){
  .wf-launch.wf-studio-v3 .wf-audience-inline{grid-template-columns:minmax(0,1fr)!important}
  .wf-launch.wf-studio-v3 .wf-target-tabs{gap:0!important}
  .wf-launch.wf-studio-v3 .wf-target-tab{min-height:38px!important;padding:8px 9px!important}
  .wf-launch.wf-studio-v3 .wf-target-fields{grid-template-columns:minmax(0,1fr)!important;gap:10px!important}
}
`;
    studioV3Style.textContent += `
/* AutoUpload Studio V13: quiet resource feedback. */
.wf-launch.wf-studio-v3 .wf-legacy-resource-guidance{
  margin:8px 0!important;padding:8px 10px!important;border:1px solid #dce3ec!important;border-radius:8px!important;
  background:#f8fafc!important;color:#5d6b7e!important;box-shadow:none!important;font-size:11px!important;line-height:1.45!important;
}
.wf-launch.wf-studio-v3:has(#wfProfilePicker .wf-profile-empty) .wf-legacy-resource-guidance{display:none!important}
.wf-launch.wf-studio-v3 .wf-empty-api-status{
  margin:4px 0 0!important;padding:0!important;border:0!important;border-radius:0!important;background:transparent!important;
  color:#8591a2!important;box-shadow:none!important;font-size:10px!important;line-height:1.4!important;
}
`;
    studioV3Style.textContent += `
/* AutoUpload Studio V14: neutral surface, short category navigation and consistent controls. */
body.wf-autoupload-open main:has(.wf-launch){background:#f7f8fb!important;color:#1d2735!important}
.wf-launch.wf-studio-v3{
  --studio-ink:#1d2735;--studio-muted:#687587;--studio-border:#e1e6ee;
  --studio-border-strong:#cbd4e0;--studio-surface:#fff;--studio-surface-2:#f8fafc;
  --studio-accent:#6d4ed7;--studio-accent-strong:#5b3bc6;--studio-accent-soft:#f4f0ff;
  background:#f7f8fb!important;color:var(--studio-ink)!important;
}
.wf-launch.wf-studio-v3>.launch-card.wf-step-card{
  background:#fff!important;border-color:#e1e6ee!important;box-shadow:0 8px 24px rgba(28,38,52,.045)!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline{
  grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:10px 18px!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline>.wf-audience-field,
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field,
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field .rm-module-control{
  min-width:0!important;border:0!important;border-bottom:0!important;border-radius:0!important;
  background:transparent!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field::before,
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field::after,
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field .rm-module-control::before,
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field .rm-module-control::after{
  content:none!important;display:none!important;border:0!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field :is(input:not([type=hidden]),select,textarea,[role=combobox],.rm-module-search),
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field .rm-module-control :is(input,select,textarea,[role=combobox]),
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-target-field .rm-module-control :is(input,select,textarea,[role=combobox]){
  box-sizing:border-box!important;min-height:40px!important;padding:8px 11px!important;
  border:1px solid #cfd7e2!important;border-bottom:1px solid #cfd7e2!important;border-radius:9px!important;
  background:#fff!important;color:#1d2735!important;box-shadow:none!important;outline:0!important;
  font-size:12px!important;line-height:1.4!important;
}
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field :is(input,select,textarea,[role=combobox],.rm-module-search):focus,
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-target-field .rm-module-control :is(input,select,textarea,[role=combobox]):focus{
  border-color:#9079df!important;box-shadow:0 0 0 3px rgba(109,78,215,.12)!important;
}
.wf-launch.wf-studio-v3 .wf-audience-inline> *:has(.wf-audience-subgroup),
.wf-launch.wf-studio-v3 .wf-audience-inline> #singleAudienceBox{
  grid-column:1/-1!important;min-width:0!important;
}
.wf-launch.wf-studio-v3 .wf-target-head h6{font-size:13px!important;letter-spacing:0!important}
.wf-launch.wf-studio-v3 .wf-target-head p{font-size:11px!important;max-width:68ch!important}
.wf-launch.wf-studio-v3 .wf-target-tabs{
  gap:2px!important;border-bottom:1px solid #e1e6ee!important;scrollbar-width:none!important;
}
.wf-launch.wf-studio-v3 .wf-target-tab{
  min-height:36px!important;padding:7px 9px!important;font-size:11px!important;
  color:#677487!important;
}
.wf-launch.wf-studio-v3 .wf-target-tab:hover{background:#f7f5fc!important;color:#463b65!important}
.wf-launch.wf-studio-v3 .wf-target-tab[aria-selected="true"]{
  border-bottom-color:#6d4ed7!important;background:transparent!important;color:#3e3261!important;
}
.wf-launch.wf-studio-v3 .wf-target-tab-count{background:#f1eef8!important;color:#5c4b82!important}
.wf-launch.wf-studio-v3 .wf-target-fields{
  grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:11px 16px!important;
}
.wf-launch.wf-studio-v3 .wf-target-field>label{font-size:10px!important;color:#536174!important}
.wf-launch.wf-studio-v3 .wf-meta-extra-disclosure{
  width:100%!important;margin:10px 0 0!important;padding:0!important;
  border:0!important;border-top:1px solid #e1e6ee!important;border-radius:0!important;
  background:transparent!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 .wf-meta-extra-summary{
  display:flex!important;align-items:center!important;justify-content:space-between!important;gap:12px!important;
  min-height:42px!important;padding:8px 0!important;list-style:none!important;cursor:pointer!important;
  color:#273345!important;font-size:12px!important;font-weight:650!important;
}
.wf-launch.wf-studio-v3 .wf-meta-extra-summary::-webkit-details-marker{display:none!important}
.wf-launch.wf-studio-v3 .wf-meta-extra-hint{color:#7a8798!important;font-size:10px!important;font-weight:500!important}
.wf-launch.wf-studio-v3 .wf-meta-extra-body{padding:0 0 12px!important}
.wf-launch.wf-studio-v3 #launchMetaAllFields{
  margin:0!important;padding:12px 0 0!important;border:0!important;border-radius:0!important;
  background:transparent!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 #launchMetaAllFields .rm-meta-head{align-items:center!important;gap:12px!important}
.wf-launch.wf-studio-v3 #launchMetaAllFields .rm-meta-head h3{color:#273345!important;font-size:13px!important}
.wf-launch.wf-studio-v3 #launchMetaAllFields .rm-meta-note{max-width:70ch!important;color:#7a8798!important}
body.wf-autoupload-open div[style*="z-index:14000"]{background:rgba(24,34,48,.26)!important}
body.wf-autoupload-open section[role="dialog"][aria-modal="true"]{
  background:#fff!important;color:#1d2735!important;border:1px solid #dce3ec!important;border-radius:16px!important;
  box-shadow:0 24px 70px rgba(24,34,48,.2)!important;
}
body.wf-autoupload-open section[role="dialog"][aria-modal="true"] h3{color:#1d2735!important}
body.wf-autoupload-open section[role="dialog"][aria-modal="true"] div[style*="border:1px solid #334155"]{
  border-color:#e1e6ee!important;background:#f8fafc!important;color:#273345!important;
}
body.wf-autoupload-open section[role="dialog"][aria-modal="true"] :is(input,select,textarea,.form-control){
  border:1px solid #d2dbe6!important;border-radius:8px!important;background:#fff!important;color:#1d2735!important;box-shadow:none!important;
}
body.wf-autoupload-open section[role="dialog"][aria-modal="true"] .btn-secondary{
  border:1px solid #dce3ec!important;border-radius:8px!important;background:#f8fafc!important;color:#344154!important;
}
body.wf-autoupload-open section[role="dialog"][aria-modal="true"] [role="alert"]{color:#b42318!important}
@media(max-width:640px){
  .wf-launch.wf-studio-v3 .wf-target-fields{grid-template-columns:minmax(0,1fr)!important}
  .wf-launch.wf-studio-v3 .wf-target-tab{padding:7px 8px!important;font-size:10px!important}
  .wf-launch.wf-studio-v3 .wf-meta-extra-summary{align-items:flex-start!important;flex-direction:column!important;gap:2px!important}
}
`;
        studioV3Style.textContent += `
/* AutoUpload V15: neutral shell, compact hierarchy, readable review and ad controls. */
body.app-shell.wf-autoupload-open{
  --app-bg:#f3f5f6;--app-sidebar:#171d21;--app-topbar:#171d21;
  --app-panel:#fff;--app-panel-2:#f8fafb;--app-panel-3:#eef2f3;
  --app-border:#dfe5e7;--app-border-strong:#c8d2d5;
  --app-text:#1c272e;--app-muted:#68777e;--app-muted-2:#7d8a90;
  --app-blue:#0c8179;--app-blue-2:#0b6d67;--app-green:#0d806e;--app-green-bg:#e8f4f0;
  --app-warn:#8c681d;--app-warn-bg:#fff6df;--app-danger:#ad4d56;--app-danger-bg:#faeeee;
  background:#f3f5f6!important;color:#1c272e!important;
}
body.app-shell.wf-autoupload-open .app-sidebar,
body.app-shell.wf-autoupload-open .app-topbar{background:#171d21!important;border-color:#293337!important}
body.app-shell.wf-autoupload-open .app-brand-mark{background:#0e837c!important;box-shadow:none!important}
body.app-shell.wf-autoupload-open .app-nav a.active{position:relative;background:#202b2e!important;color:#e9fbf8!important}
body.app-shell.wf-autoupload-open .app-nav a.active::before{content:"";position:absolute;inset:7px auto 7px 0;width:2px;border-radius:2px;background:#36b8a8}
body.app-shell.wf-autoupload-open .app-avatar{background:#25413d!important;color:#ddf8f2!important}
body.app-shell.wf-autoupload-open .app-connection{background:#20292c!important;border-color:#344145!important;color:#c1d8d4!important}
body.app-shell.wf-autoupload-open .app-main{background:#f3f5f6!important;color:#1c272e!important}
body.app-shell.wf-autoupload-open .app-page-head .app-title{color:#1c272e!important}
body.app-shell.wf-autoupload-open .app-page-head .app-subtitle{color:#68777e!important}
body.app-shell.wf-autoupload-open .app-page-head .app-eyebrow{color:#637a78!important}
body.app-shell.wf-autoupload-open .launch-wrap,
body.app-shell.wf-autoupload-open .wf-launch.wf-studio-v3{color:#1c272e!important;background:transparent!important}
body.app-shell.wf-autoupload-open .launch-wrap .launch-card,
body.app-shell.wf-autoupload-open .launch-wrap .launch-card.wf-step-card{
  background:#fff!important;border:1px solid #dfe5e7!important;border-radius:15px!important;
  box-shadow:0 7px 24px rgba(27,43,46,.055)!important;
}
body.app-shell.wf-autoupload-open .launch-wrap .launch-card h4,
body.app-shell.wf-autoupload-open .launch-wrap .launch-card h5,
body.app-shell.wf-autoupload-open .launch-wrap .launch-card h6{color:#1c272e!important}
body.app-shell.wf-autoupload-open .launch-wrap :is(input:not([type=checkbox]):not([type=radio]),select,textarea,.form-control){
  background:#fff!important;color:#1c272e!important;border:1px solid #d4dddf!important;border-radius:9px!important;box-shadow:none!important;
}
body.app-shell.wf-autoupload-open .launch-wrap :is(input,select,textarea,.form-control):focus{
  border-color:#15978d!important;box-shadow:0 0 0 3px rgba(21,151,141,.13)!important;
}
body.app-shell.wf-autoupload-open .launch-wrap :is(input,select,textarea,.form-control):disabled{
  background:#f2f5f5!important;color:#7b888d!important;border-color:#e1e6e7!important;
}
body.app-shell.wf-autoupload-open .launch-wrap label{color:#435159!important}
body.app-shell.wf-autoupload-open .launch-wrap option{background:#fff!important;color:#1c272e!important}
body.app-shell.wf-autoupload-open .launch-wrap .btn-primary,
body.app-shell.wf-autoupload-open .launch-wrap .btn-info,
body.app-shell.wf-autoupload-open .launch-wrap .btn-success{background:#0d8179!important;border-color:#0d8179!important;color:#fff!important}
body.app-shell.wf-autoupload-open .launch-wrap .btn-primary:hover,
body.app-shell.wf-autoupload-open .launch-wrap .btn-info:hover,
body.app-shell.wf-autoupload-open .launch-wrap .btn-success:hover{background:#0a7069!important;border-color:#0a7069!important}
body.app-shell.wf-autoupload-open .launch-wrap .btn-secondary,
body.app-shell.wf-autoupload-open .launch-wrap .btn-outline-secondary{background:#fff!important;border:1px solid #d4dddf!important;color:#34434a!important}
body.app-shell.wf-autoupload-open .launch-wrap .btn-dark{background:#2b3438!important;border-color:#2b3438!important;color:#fff!important}
.wf-launch.wf-studio-v3>.wf-step-nav a[aria-current="step"]{border-bottom-color:#0d8179!important;color:#1c272e!important}
.wf-launch.wf-studio-v3>.wf-step-nav a[aria-current="step"] .wf-step-index{background:#0d8179!important;border-color:#0d8179!important;color:#fff!important}
.wf-launch.wf-studio-v3 .wf-step-progress span{background:#1da69a!important}
.wf-launch.wf-studio-v3 .wf-choice-option{
  min-height:52px!important;padding:10px 12px!important;border:1px solid #dfe5e7!important;
  border-radius:10px!important;background:#f8fafb!important;color:#27363d!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 .wf-choice-option:hover{border-color:#b8cdca!important;background:#f2f8f7!important;color:#1c3e3b!important}
.wf-launch.wf-studio-v3 .wf-choice-option.is-selected{
  border-color:#168e85!important;background:#eaf6f3!important;color:#164d49!important;
  box-shadow:inset 0 0 0 1px rgba(22,142,133,.12)!important;
}
.wf-launch.wf-studio-v3 .wf-choice-icon{border-color:#d8e3e2!important;background:#edf3f2!important;color:#52706c!important}
.wf-launch.wf-studio-v3 .wf-choice-option.is-selected .wf-choice-icon{border-color:#b5ded7!important;background:#d9f0eb!important;color:#08766e!important}
.wf-launch.wf-studio-v3 .wf-choice-check{background:#0d8179!important}
.wf-launch.wf-studio-v3 .wf-choice-copy small{color:#68777e!important}
.wf-launch.wf-studio-v3 .wf-choice-segments{border-color:#dfe5e7!important;background:#f3f6f6!important}
.wf-launch.wf-studio-v3 .wf-choice-segments .wf-choice-option{background:transparent!important}
.wf-launch.wf-studio-v3 .wf-choice-segments .wf-choice-option.is-selected{background:#fff!important}
.wf-launch.wf-studio-v3 .wf-audience-inline .wf-audience-field,
.wf-launch.wf-studio-v3 .wf-target-field{min-width:0!important}
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field :is(input,select,textarea,[role=combobox],.rm-module-search),
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-target-field .rm-module-control :is(input,select,textarea,[role=combobox]){
  border:1px solid #d4dddf!important;border-bottom:1px solid #d4dddf!important;border-radius:9px!important;background:#fff!important;color:#1c272e!important;
}
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field::before,
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field::after,
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field .rm-module-control::before,
.wf-launch.wf-studio-v3 #wf-launch-audience .wf-audience-inline .wf-audience-field .rm-module-control::after{
  content:none!important;display:none!important;border:0!important;box-shadow:none!important;
}
body.app-shell.wf-autoupload-open .launch-wrap .search-results:empty{
  display:none!important;min-height:0!important;height:0!important;margin:0!important;padding:0!important;border:0!important;background:transparent!important;box-shadow:none!important;
}
body.app-shell.wf-autoupload-open .launch-wrap .search-results:not(:empty){
  max-height:220px!important;overflow:auto!important;background:#fff!important;border:1px solid #dfe5e7!important;border-radius:9px!important;box-shadow:0 8px 20px rgba(27,43,46,.08)!important;
}
.wf-target-catalog-disclosure,.wf-ad-advanced-disclosure{
  margin:14px 0 0!important;border:1px solid #dfe5e7!important;border-radius:11px!important;background:#f8fafb!important;box-shadow:none!important;overflow:hidden!important;
}
.wf-target-catalog-summary,.wf-ad-advanced-disclosure>summary{
  display:flex!important;align-items:center!important;justify-content:space-between!important;gap:12px!important;
  min-height:44px!important;padding:11px 13px!important;list-style:none!important;cursor:pointer!important;color:#26363d!important;font-size:12px!important;font-weight:650!important;
}
.wf-target-catalog-summary::-webkit-details-marker,.wf-ad-advanced-disclosure>summary::-webkit-details-marker{display:none!important}
.wf-target-catalog-hint,.wf-ad-advanced-hint{color:#738187!important;font-size:10px!important;font-weight:500!important}
.wf-target-catalog-body{padding:0 13px 13px!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions{
  margin:0!important;padding:13px 0 0!important;border:0!important;border-radius:0!important;background:transparent!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-head h6{color:#1c272e!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-head p{color:#68777e!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-tabs{border-color:#dfe5e7!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-tab{color:#64737a!important;background:transparent!important;border-color:transparent!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-tab[aria-selected="true"]{color:#0b6f68!important;border-bottom-color:#0d8179!important;background:transparent!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-tab-count{background:#eef3f3!important;color:#56686d!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-panel{border:1px solid #e1e7e8!important;border-radius:10px!important;background:#fff!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-panel-heading{color:#2b393f!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-note{color:#718087!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-field>label{color:#536168!important}
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-field input,
.wf-launch.wf-studio-v3 #wfTargetingOptions .wf-target-field select{background:#fff!important;color:#1c272e!important;border:1px solid #d4dddf!important}
.wf-launch.wf-studio-v3 #wf-launch-ad .wf-ad-section-title{
  margin:18px 0 8px!important;padding-top:13px!important;border-top:1px solid #e8edee!important;
  color:#64737a!important;font-size:10px!important;font-weight:700!important;letter-spacing:.06em!important;text-transform:uppercase!important;
}
.wf-launch.wf-studio-v3 #wf-launch-ad .wf-ad-section-title[data-wf-ad-section="identity"]{margin-top:5px!important;border-top:0!important;padding-top:0!important}
.wf-launch.wf-studio-v3 #wf-launch-ad .row:has(#headline){
  display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;gap:12px!important;margin:8px 0 0!important;
}
.wf-launch.wf-studio-v3 #wf-launch-ad .row:has(#headline)>[class*="col-"]{width:auto!important;max-width:none!important;flex:none!important;padding:0!important}
.wf-launch.wf-studio-v3 #wf-launch-ad .row:has(#creativeFormat){
  display:grid!important;grid-template-columns:minmax(190px,.8fr) minmax(280px,1.2fr)!important;gap:12px!important;margin:8px 0 0!important;
}
.wf-launch.wf-studio-v3 #wf-launch-ad .row:has(#creativeFormat)>[class*="col-"]{width:auto!important;max-width:none!important;flex:none!important;padding:0!important}
.wf-ad-advanced-body{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:12px!important;padding:0 13px 13px!important}
.wf-ad-advanced-body>[class*="col-"]{width:auto!important;max-width:none!important;flex:none!important;padding:0!important}
.wf-launch.wf-studio-v3 .wf-preview-surface{
  margin-top:14px!important;padding:15px!important;border:1px solid #dfe5e7!important;border-radius:12px!important;background:#f8fafb!important;color:#1c272e!important;box-shadow:none!important;
}
.wf-launch.wf-studio-v3 .wf-preview-surface :is(h1,h2,h3,h4,h5,label,p,small,span){color:#405159!important}
.wf-launch.wf-studio-v3 .wf-preview-surface :is(select,input,textarea){background:#fff!important;color:#1c272e!important;border:1px solid #d4dddf!important}
.wf-launch.wf-studio-v3 #wf-launch-review .job-table{
  overflow:hidden!important;border:1px solid #dfe5e7!important;border-radius:10px!important;background:#fff!important;color:#35434a!important;border-collapse:separate!important;border-spacing:0!important;
}
.wf-launch.wf-studio-v3 #wf-launch-review .job-table thead th,
.wf-launch.wf-studio-v3 #wf-launch-review .job-table thead td{
  background:#f0f4f4!important;color:#57686e!important;border-bottom:1px solid #dfe5e7!important;font-size:9px!important;letter-spacing:.04em!important;
}
.wf-launch.wf-studio-v3 #wf-launch-review .job-table tbody td{background:#fff!important;color:#3c4a50!important;border-bottom:1px solid #e8edee!important}
.wf-launch.wf-studio-v3 #wf-launch-review .job-table tbody tr:hover td{background:#f8fbfa!important}
body.app-shell.wf-autoupload-open .launch-wrap .status-box,
body.app-shell.wf-autoupload-open .launch-wrap #launchResult,
body.app-shell.wf-autoupload-open .launch-wrap #reviewStatus{
  min-height:44px!important;padding:12px 13px!important;border:1px solid #dfe5e7!important;border-radius:10px!important;background:#f8fafb!important;color:#66767c!important;
}
.wf-launch.wf-studio-v3 #wf-launch-review .wf-card-disclosure{border:1px solid #dfe5e7!important;border-radius:11px!important;background:#f8fafb!important}
.wf-launch.wf-studio-v3 #wf-launch-review .wf-card-disclosure>summary{min-height:44px!important;padding:10px 13px!important;background:transparent!important;color:#26363d!important}
.wf-launch.wf-studio-v3 #wf-launch-review .wf-card-disclosure[open]>.wf-card-disclosure-body{padding:12px 13px!important;border-top:1px solid #e4e9ea!important;background:#fff!important}
@media(max-width:720px){
  .wf-launch.wf-studio-v3 #wf-launch-ad .row:has(#headline){grid-template-columns:repeat(2,minmax(0,1fr))!important}
  .wf-launch.wf-studio-v3 #wf-launch-ad .row:has(#creativeFormat){grid-template-columns:minmax(0,1fr)!important}
  .wf-ad-advanced-body{grid-template-columns:minmax(0,1fr)!important}
}
`;
        function safeEnhance(name, task) {
        try { return task(); }
        catch (error) { console.error('[AutoUpload] '+name+' failed', error); return null; }
    }
    function init() {
        if(!$('creativeForm')&&!document.querySelector('.launch-wrap'))return;
        safeEnhance('creative setup',setupCreative);
        safeEnhance('workflow setup',setupLaunch);
        safeEnhance('Meta module controls',()=>window.remaskModuleControls?.scan?.());
        safeEnhance('audience sections',setupAudienceSections);
        safeEnhance('audience workspace',setupAudienceWorkspace);
        safeEnhance('direct audience controls',setupAudienceTargeting);
        safeEnhance('targeting tabs',setupStudioTargetTabs);
        safeEnhance('Meta object tabs',setupMetaGroupTabs);
        safeEnhance('studio layout',applyStudioV3);
        safeEnhance('dynamic controls',enhanceDynamic);
        let queued=false;
        const observer=new MutationObserver(()=>{
            if(queued)return;
            queued=true;
            requestAnimationFrame(()=>{
                queued=false;
                observer.disconnect();
                safeEnhance('dynamic refresh',enhanceDynamic);
                observer.observe(document.body,{childList:true,subtree:true});
            });
        });
        observer.observe(document.body,{childList:true,subtree:true});
    }
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
