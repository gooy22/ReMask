const paymentCardResumeKey='remask_payment_card_batch_v1';
function paymentCardResumeStorage(){
  try{if(typeof localStorage!=='undefined')return localStorage;}catch(e){}
  try{if(typeof sessionStorage!=='undefined')return sessionStorage;}catch(e){}
  return null;
}
function paymentCardResumeRead(){
  try{
    const storage=paymentCardResumeStorage();
    let raw=storage?.getItem(paymentCardResumeKey)||'';
    if(!raw&&typeof sessionStorage!=='undefined'){
      raw=sessionStorage.getItem(paymentCardResumeKey)||'';
      if(raw&&storage&&storage!==sessionStorage)storage.setItem(paymentCardResumeKey,raw);
    }
    const saved=JSON.parse(raw||'null');
    if(!saved||saved.version!==1||!Array.isArray(saved.rows)||!Number.isFinite(saved.updated)||Date.now()-saved.updated>7*86400000)return null;
    const rows=saved.rows.filter(r=>typeof r.profile==='string'&&r.profile.length<=160&&/^act_\d{5,30}$/.test(r.id));
    return rows.length?{rows,cardId:typeof saved.cardId==='string'?saved.cardId:''}:null;
  }catch(e){return null;}
}
function paymentCardResumeWrite(rows,cardId){
  // Explicit allowlist: never persist CVV, PAN, billing fields or retry tokens.
  const targets=rows.map(r=>({profile:r.profile,id:'act_'+String(r.id).replace(/^act_/,''),
    ...paymentAssetHint(r)}));
  try{paymentCardResumeStorage()?.setItem(paymentCardResumeKey,JSON.stringify({version:1,updated:Date.now(),rows:targets,cardId:cardId||''}));}catch(e){}
}
function paymentCardResumeRemove(){
  try{localStorage.removeItem(paymentCardResumeKey);}catch(e){}
  try{sessionStorage.removeItem(paymentCardResumeKey);}catch(e){}
}
function remaskClearPaymentSecrets(){
  ['paymentCardNumber','paymentCardCvv'].forEach(id=>{const el=$(id);if(el)el.value='';});
}

function paymentCardMessage(result){
  const names={number:'номер карты',cvv:'CVV',holder:'имя владельца',expiry:'срок действия',month:'месяц',year:'год',country:'страна',currency:'валюта',timezone:'часовой пояс',address:'платёжный адрес',city:'город',region:'область / штат',postal_code:'индекс',email_or_phone:'email или телефон владельца',unknown_required_field:'дополнительное поле Meta'};
  const messages={
    CARD_FORM_READY:'Форма Meta доступна для выбранного РК.',
    CARD_HTTP_FORM_CONFIRMED:'Форма карты доступна. Карта ещё не отправлена.',
    CARD_SCREEN_QUERY_REJECTED:'Meta не подтвердила запрос формы карты. Карта не отправлена.',
    CARD_SCREEN_SCOPE_UNVERIFIED:'Meta не подтвердила форму именно выбранного РК. Карта не отправлена.',
    CARD_SCREEN_OPTIONS_INCONCLUSIVE:'Meta подтвердила РК, но не подтвердила доступность добавления карты.',
    CARD_BIN_UNSUPPORTED:'Meta не поддерживает эту карту для выбранных настроек оплаты. Карта не отправлена.',
    CARD_PTT_KEY_RESPONSE_UNCONFIRMED:'Meta не вернула подтверждённый ключ шифрования. Сохранение карты не отправлено.',
    CARD_PTT_KEY_REJECTED:'Meta отклонила получение ключа шифрования. Сохранение карты не отправлено.',
    CARD_PTT_KEY_MUTATION_MISMATCH:'Ответ ключа Meta относится к другому запросу. Сохранение карты не отправлено.',
    CARD_PTT_DEVELOPMENT_KEY_REJECTED:'Meta вернула ключ разработки. Сохранение карты не отправлено.',
    CARD_PTT_TRUST_CHAIN_INVALID:'Не удалось проверить сертификаты ключа Meta. Сохранение карты не отправлено.',
    CARD_BIN_QUERY_REJECTED:'Meta не подтвердила требования к этой карте. Карта не отправлена.',
    CARD_BIN_REQUIREMENTS_INCONCLUSIVE:'Ответ Meta о требованиях карты неполный. Карта не отправлена.',
    CARD_REQUIRED_FIELDS_MISSING:'Meta требует дополнительные данные владельца карты. Карта не отправлена.',
    CARD_RECURRING_CONSENT_REQUIRED:'Для этой карты Meta требует согласие на регулярные платежи. Согласие не проставлено; карта не отправлена.',
    CARD_TOKENIZATION_CONSENT_REQUIRED:'Meta требует согласие на токенизацию карты. Согласие не проставлено; карта не отправлена.',
    CARD_COUNTRY_POLICY_INCONCLUSIVE:'Meta не подтвердила правила страны выбранного РК. Карта не отправлена.',
    CARD_COUNTRY_UPDATE_SCOPE_UNVERIFIED:'Meta не подтвердила разрешение изменить страну именно этого РК. Сохранение карты не отправлено.',
    CARD_COUNTRY_UPDATE_OPTIONS_UNCONFIRMED:'Meta не подтвердила доступность выбранной страны и текущие настройки РК. Сохранение карты не отправлено.',
    CARD_COUNTRY_UPDATE_RESULT_UNCONFIRMED:'Meta не подтвердила ответ изменения страны. Сначала проверьте настройки РК; сохранение карты не отправлено.',
    CARD_COUNTRY_UPDATE_VERIFY_PENDING:'Изменение страны отправлено; независимая проверка ещё не подтвердила настройки того же РК. Сохранение карты не отправлено.',
    CARD_TAX_COUNTRY_VALIDATION_REQUIRED:'Meta требует отдельную проверку страны РК. Карта не отправлена.',
    CARD_TAX_COUNTRY_QUERY_UNCONFIRMED:'Meta не подтвердила ответ проверки страны выбранного РК. Карта не отправлена.',
    CARD_TAX_COUNTRY_STEPUP_REQUIRED:'Meta требует подтверждение страны владельцем рекламного кабинета. Карта не отправлена.',
    CARD_BIN_COUNTRY_CHECK_REQUIRED:'Перед сохранением Meta проверит страну карты. Карта ещё не отправлена.',
    CARD_BIN_COUNTRY_UNCONFIRMED:'Meta не подтвердила страну карты. Карта не отправлена.',
    CARD_COUNTRY_POLICY_CONFIRMED:'Проверка страны Meta пройдена.',
    CARD_COUNTRY_CONTEXT_CHANGED:'Страна РК изменилась между запросами Meta. Обновите проверку формы; сохранение карты не отправлено.',
    CARD_BILLING_COUNTRY_MISMATCH:'Meta обнаружила несовпадение страны карты со страной РК. В Meta подтвердите страну бизнеса другим способом либо измените его местонахождение. Сохранение карты не отправлено.',
    CARD_PAYMENT_MODE_INCONCLUSIVE:'Meta не подтвердила режим оплаты РК. Карта не отправлена.',
    CARD_CLIENT_CONTEXT_REQUIRED:'Не подтверждены данные клиента для банковской проверки. Карта не отправлена.',
    CARD_HTTP_CANARY_SCOPE_REQUIRED:'Устаревшая версия обработчика карт ограничивает выбранный РК. Обновите страницу и повторите проверку.',
    CARD_HTTP_FIRST_CANARY_REQUIRED:'Устаревшая версия обработчика карт ожидает другой РК. Обновите страницу и повторите проверку.',
    CARD_LINK_CONFIRMED:'Meta подтвердила точную карту у выбранного РК. Подтверждена только привязка; списания и доступность рекламы не проверялись.',
    CARD_BUSINESS_OWNER_SCOPE_UNVERIFIED:'Meta не подтвердила владельца родительского платёжного аккаунта. Карта не отправлена.',
    CARD_BUSINESS_PARENT_QUERY_UNAVAILABLE:'Не удалось прочитать платёжный аккаунт бизнеса Meta. Карта не отправлена.',
    CARD_BUSINESS_PARENT_SCOPE_UNVERIFIED:'Meta не подтвердила точный бизнес для платёжного аккаунта. Карта не отправлена.',
    CARD_BUSINESS_PARENT_UNVERIFIED:'Родительский платёжный аккаунт бизнеса не подтверждён. Карта не отправлена.',
    CARD_BUSINESS_PARENT_METHODS_UNVERIFIED:'Meta не подтвердила список карт бизнес-кошелька. Повторное сохранение не отправлено.',
    CARD_BUSINESS_WALLET_MASK_COLLISION:'В бизнес-кошельке уже есть карта с теми же последними цифрами. Повторное сохранение остановлено; проверьте существующую карту.',
    CARD_SAVE_RESULT_UNKNOWN:'Карта могла быть отправлена. Повторное добавление заблокировано; нажмите «Проверить результат».',
    CARD_SAVE_LINK_VERIFICATION_PENDING:'Meta приняла сохранение карты; точная привязка проверяется. Повторное добавление заблокировано.',
    CARD_PRIVATE_RUNTIME_CONTEXT_UNCONFIRMED:'Не удалось подготовить добавление карты. Запрос не отправлен.',
    PAYMENT_HTTP_TIMEOUT:'Проверка Meta не завершилась вовремя. Карта не отправлена.',
    PAYMENT_HTTP_UNAVAILABLE:'Не удалось завершить проверку оплаты в Meta. Карта не отправлена.',
    CARD_LINK_OBSERVED:'Meta показывает карту у выбранного РК. Проверена только привязка: платёж и подтверждение банка не проверялись.',
    ALREADY_LINKED:'ReMask уже сохранил эту связь с РК. Для повторной live-проверки нажмите «Проверить привязанные карты».',
    CARD_AND_CVV_REQUIRED:'Для новой привязки нужен CVV. Введите его один раз для выбранной группы РК.',
    CARD_BINDING_RECONCILE_REQUIRED:'Предыдущая привязка ещё не подтверждена. Сначала проверьте состояние карты в Meta; повтор остановлен.',
    CARD_SAVED_CREDENTIAL_UNLINKED:'Карта уже сохранена в Meta, но не прикреплена к выбранному РК. Повторное сохранение не поможет: нужно назначить существующую карту этому РК.',
    CARD_SAVE_REQUIRES_LINK_VERIFICATION:'Meta приняла сохранение карты. Проверяю, прикреплена ли она к выбранному РК.',
    CARD_RECONCILE_UNVERIFIED:'Meta пока не подтвердила эту карту у выбранного РК. Повторное добавление остаётся заблокированным.',
    CARD_RECONCILE_NO_METHOD:'Meta показывает отсутствие способа оплаты у выбранного РК. Карта не привязана; результат предыдущей отправки требует разбора.',
    PAYMENT_ALL_METHODS_NON_CARD_ONLY:'Meta вернула платёжный инструмент, который не является банковской картой. Привязка карты к этому РК не подтверждена.',
    CARD_META_REJECTED:'Meta показала ошибку сохранения карты. Привязка не подтверждена; повторная отправка остановлена.',
    CARD_RETRY_REVIEW_REQUIRED:'Сначала проверьте результат предыдущей попытки в Meta.',
    CARD_RETRY_REVIEW_EXPIRED:'Проверка устарела. Нажмите «Проверить результат» ещё раз.',
    CARD_RETRY_REVIEW_INVALID:'Подтверждение проверки не подходит этой попытке. Проверьте результат заново.',
    CARD_RETRY_ACCOUNT_NOT_EMPTY:'Meta больше не подтверждает отсутствие карты у этого РК. Повтор остановлен.',
    CARD_BINDING_CARD_MISMATCH:'Для проверки выберите карту предыдущей попытки привязки.',
    CARD_BINDING_CHANGED:'Состояние привязки изменилось во время проверки. Обновите результат.',
    CARD_BINDING_IN_PROGRESS:'Предыдущая операция ещё выполняется. Повторная отправка остановлена.',
    CARD_LINK_NOT_VERIFIED:'Карта отправлена в Meta; привязка пока не подтверждена. Повторное добавление остановлено.',
    CARD_MASK_COLLISION_PREEXISTING:'У выбранного РК уже есть карта того же бренда с такими же последними 4 цифрами. Meta не позволяет безопасно отличить её от новой карты, поэтому Save не нажат.',
    PAYMENT_ACCOUNT_SCOPE_UNVERIFIED:'Meta не подтвердила точный РК. Карта не отправлена.',
    CARD_ACCOUNT_SCOPE_UNVERIFIED:'Meta не подтвердила выбор «Только этот аккаунт». Карта не отправлена; добавление карты для всего BM остановлено.',
    PAYMENT_ACCOUNT_BINDING_MISSING:'Нет однозначного соответствия профиля и РК. Обновите выбранный РК.',
    PAYMENT_UI_UNAVAILABLE:'Meta не показала безопасный путь к Billing / Payments для выбранного РК. Карта не отправлена.',
    PERSONAL_AD_ACCOUNT_EXCLUDED:'Личный рекламный аккаунт не используется этой системой привязки. Выберите РК внутри BM.',
    INVALID_PAYMENT_TARGET:'Выбранный РК не прошёл проверку идентификатора. Обновите список аккаунтов.',
    PAYMENT_ACCOUNT_ROW_MISSING:'Meta не показала строку выбранного РК.',
    PAYMENT_ADD_CONTROL_MISSING:'У выбранного РК Meta не показала кнопку добавления способа оплаты.',
    PAYMENT_AD_ACCOUNT_DISABLED:'Meta отключила выбранный РК. Добавление карты остановлено до восстановления РК.',
    PAYMENT_ACCOUNT_SETUP_REQUIRED:'Meta сначала требует настройки страны, валюты и часового пояса РК. Эти настройки нельзя изменить после подтверждения. Карта не отправлена.',
    PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING:'Не удалось выбрать указанную настройку оплаты в форме Meta. Карта не отправлена.',
    PAYMENT_SETUP_INVALID:'Укажите Украину или текущую страну Meta, USD и Europe/Kyiv для этой настройки оплаты.',
    PAYMENT_COUNTRY_UNVERIFIED:'Не удалось однозначно прочитать страну в форме Meta. Настройки не подтверждены, карта не отправлена.',
    PAYMENT_COUNTRY_LOCKED:'Meta заблокировала поле страны. Можно сохранить текущую страну Meta; карта не отправлена.',
    PAYMENT_FORM_NOT_EXPOSED:'Meta не открыла форму карты.',
    CARD_BILLING_FIELDS_REQUIRED:'Нужны дополнительные реквизиты владельца или платёжного адреса.',
    CARD_BANK_CONFIRMATION_REQUIRED:'Банк запросил подтверждение. Завершите его в Facebook/банке, затем нажмите «Проверить результат». Не отправляйте карту повторно.',
    CARD_VERIFICATION_CARD_REQUIRED:'Выберите карту для подтверждения.',
    CARD_VERIFICATION_CREDENTIAL_UNVERIFIED:'Meta не подтвердила точную карту выбранного РК. Банковский запрос не отправлен.',
    CARD_VERIFICATION_OPTIONS_UNVERIFIED:'Не удалось определить банковское подтверждение для этого РК. Запрос не отправлен.',
    CARD_VERIFICATION_MUTATION_NOT_PINNED:'Meta требует другой способ подтверждения. Запрос в банк не отправлен.',
    CARD_VERIFICATION_NO_REQUIRED_TASK:'Meta не показывает обязательную задачу подтверждения карты. Это не доказательство банковской авторизации.',
    SDC_ACTION_CREDENTIAL_UNVERIFIED:'Meta не предоставила точный идентификатор карты для банковского запроса. Повторная операция заблокирована.',
    SDC_AUTH_READY:'Meta разрешает запрос временной банковской операции. Отправляю запрос.',
    SDC_AUTH_SENT_WAIT_CODE:'Запрос временной операции принят Meta. Проверьте её появление в приложении банка.',
    SDC_AUTH_PENDING_WAIT_CODE:'Meta сообщает, что временная банковская операция уже ожидает подтверждения. Новое списание не отправлено.',
    SDC_AUTH_ALREADY_ATTEMPTED:'Запрос на временную операцию уже выполнялся. Повторное списание остановлено.',
    SDC_AUTH_PRECHECK_UNAVAILABLE:'Meta не подтвердила доступ до отправки банковского запроса. Списание не инициировано; после восстановления сессии можно повторить проверку.',
    SDC_AUTH_SUBMIT_FENCE_MISSING:'Без подтверждённой защиты от двойного списания запрос в банк остановлен.',

    SDC_AUTH_RESULT_UNKNOWN:'Результат отправки в банк неизвестен. Повторная отправка заблокирована.',
    SDC_AUTH_REJECTED:'Meta отклонила запуск временной авторизации. Повторная отправка остановлена.',
    SDC_CODE_REQUIRED:'Введите четыре символа кода из банковской операции.',
    SDC_CODE_RESULT_UNVERIFIED:'Не удалось подтвердить результат кода в Meta. Повторный запрос в банк не отправлялся.',
    SDC_CODE_REJECTED:'Meta не приняла код банковского подтверждения.',
    SDC_CODE_VERIFIED_BY_META:'Meta подтвердила код банковской операции для этой карты.',
    SDC_AUTH_NOT_STARTED:'Сначала требуется временная авторизация карты.',
    SDC_DURABLE_GUARD_REQUIRED:'Нет устойчивой записи защиты от повторного банковского списания.',
    SDC_DIFFERENT_CARD_ATTEMPT_BLOCKED:'Попытка для другой карты заблокирована.',
    SDC_AUTH_RESERVATION_LOST:'Сохранённая попытка банковской авторизации изменена; новая операция не отправлена.',
    SDC_UNSUPPORTED_USABILITY:'Meta вернула нераспознанное состояние банковского подтверждения.',
    SDC_NO_AUTH_REQUIRED:'Для карты нет задачи временной SDC-авторизации.',
    CARD_VERIFICATION_SDC_READ_UNVERIFIED:'Невозможно подтвердить статус временной авторизации в Meta.',
    CARD_VERIFICATION_SDC_CARD_UNVERIFIED:'Meta вернула другой идентификатор карты; действие остановлено.',
    CARD_VERIFICATION_HTTP_UNAVAILABLE:'Не удалось проверить запрос в Meta. Временная операция не подтверждена.',
    CARD_BINDING_NOT_FOUND:'В ReMask нет сохранённой привязки выбранной карты к этому РК.',
    PAYMENT_FINANCIAL_ACTION_REQUIRED:'Meta требует платёжное действие; автоматическое списание остановлено.',
    PAYMENT_TERMS_CONFIRMATION_REQUIRED:'Meta требует принятия условий. Нужное действие должно быть подтверждено пользователем.',
    CARD_SAVE_CONTROL_UNAVAILABLE:'Meta не показала доступную кнопку сохранения карты.',
    CARD_BROWSER_INTERRUPTED:'Браузер Meta прервал обработку.',
    CARD_FLOW_TIMEOUT:'Meta не завершила обработку вовремя; автоматический повтор остановлен.',
    CHECKPOINT_REQUIRED:'Meta требует проверки Facebook-аккаунта.',
    SESSION_EXPIRED:'Facebook-сессия истекла.',
    TWO_FACTOR_REQUIRED:'Meta требует двухфакторной проверки.'
  };
  const fields=[...new Set((result.fields||[]).filter(f=>f.required&&f.kind).map(f=>names[f.kind]||f.kind))];
  const setup=result.billing_setup_observed;
  const country=setup?.country_label?' Страна в форме Meta: '+setup.country_label+
    (setup.country_preserved?' — сохранён текущий выбор'+(setup.country_reason==='meta_control_locked'?' (поле заблокировано Meta)':''):'')+
    '. Сохранение настроек в Meta пока не подтверждено.':'';
  const paymentSetupStages={
    current_context_missing:'Meta не вернула подтверждённые настройки текущего РК.',
    country_not_in_options:'Украина отсутствует среди разрешённых Meta стран для этого РК.',
    current_currency_invalid:'Meta вернула некорректную текущую валюту.',
    currency_missing:'Валюта РК не определена и не выбрана для первоначальной настройки.',
    currency_differs_from_existing:'Выбранная валюта отличается от уже установленной в Meta.',
    currency_not_in_options:'Валюта не представлена среди вариантов Meta.',
    timezone_display_unmatched:'Meta показала существующий часовой пояс, но не удалось точно сопоставить его с вариантами.',
    timezone_missing:'В ответе Meta отсутствует часовой пояс и нет подтверждённого выбора.',
    timezone_not_in_options:'Часовой пояс не найден среди вариантов Meta.',
    restrictions_missing:'Meta не вернула условия совместимости страны и валюты.',
    restrictions_incomplete:'Meta вернула неполные условия совместимости страны и валюты.'
  };
  const paymentSetupDetail=typeof result.setup_stage==='string'&&Object.prototype.hasOwnProperty.call(paymentSetupStages,result.setup_stage)
    ?' Причина: '+paymentSetupStages[result.setup_stage]:'';
  const businessWalletNote=result.business_wallet_route_possible===true
    ?' Диагностика Meta: для РК доступен родительский платёжный аккаунт бизнеса. Текущий Save может использовать другой маршрут; изменение маршрута не выполнено.'
    :result.business_wallet_route_unresolved===true
    ?' Диагностика Meta: доступна бизнес-привязка, но платёжный аккаунт бизнеса не подтверждён.':'';
  const verificationStages={
    method_scope_not_confirmed:'Meta не подтвердила принадлежность списка способов оплаты выбранному РК.',
    method_list_not_confirmed:'Список способов оплаты Meta не удалось прочитать полностью.',
    save_credential_identity_missing:'Ответ сохранения не содержит полного идентификатора карты.',
    save_credential_not_in_rk:'Карта из ответа сохранения пока не обнаружена в способах оплаты этого РК.',
    save_credential_ambiguous:'Meta вернула несколько совпадений для карты; привязка не подтверждена.',
    save_credential_metadata_mismatch:'Данные карты в списке Meta не совпадают с ответом сохранения.',
    bank_confirmation_pending:'Банк требует дополнительного подтверждения карты.',
    business_wallet_save_identity_missing:'Нельзя проверить предыдущую карту: Meta не вернула её точный идентификатор.',
    business_wallet_scope_unverified:'Meta не подтвердила список платёжных инструментов именно нужного бизнеса.',
    business_wallet_payment_account_missing:'Meta не показала платёжный аккаунт владельца бизнеса.',
    business_wallet_same_as_rk:'Родительский платёжный аккаунт совпадает с текущим РК; отдельная карта не найдена.',
    business_wallet_methods_unavailable:'Meta не вернула проверяемый список способов оплаты бизнеса.',
    business_wallet_card_ambiguous:'Meta вернула неоднозначные записи этой карты в бизнес-кошельке.',
    business_wallet_card_not_observed:'Карта не обнаружена в прочитанном списке бизнес-кошелька; отсутствие не подтверждено.',
    business_wallet_card_metadata_unverified:'Карта в бизнес-кошельке не прошла точную сверку типа и последних цифр.',
    business_wallet_reports_rk_link_but_rk_methods_missing:'Бизнес-кошелёк показывает связь с РК, но список способов оплаты самого РК её не подтверждает.',
    business_wallet_card_saved_not_attached_to_rk:'Meta показывает эту карту в платёжном аккаунте бизнеса, но не подтверждает привязку к выбранному РК.',
    business_wallet_read_unavailable:'Не удалось проверить бизнес-кошелёк Meta; повторное сохранение остаётся заблокированным.',
    saved_card_identity_missing:'От предыдущего Save не осталось идентификатора карты для проверки.',
    saved_card_identity_observed_but_not_linked:'Meta распознаёт эту сохранённую карту, но её привязка к РК или бизнес-кошельку не подтверждена. Повторное сохранение остановлено.',
    saved_card_identity_not_observed:'Meta не возвращает проверяемые данные карты по идентификаторам предыдущего Save. Удаление или отсутствие карты не доказано.',
    saved_card_identity_read_unavailable:'Запрос данных карты Meta недоступен. Повторная отправка заблокирована.'
  };
  const verificationDetail=typeof result.verification_stage==='string'&&
    Object.prototype.hasOwnProperty.call(verificationStages,result.verification_stage)
    ?' Проверка привязки: '+verificationStages[result.verification_stage]:'';
  const availability=result.card_availability==='only_this_account'?' В форме выбрано «Только этот РК».':'';
  const countryMismatch=result.code==='CARD_BILLING_COUNTRY_MISMATCH'&&/^[A-Z]{2}$/.test(result.billing_country||'')&&/^[A-Z]{2}$/.test(result.card_issuing_country||'')
    ?' Страна РК: '+result.billing_country+'; страна выпуска карты: '+result.card_issuing_country+'.':'';
  const currentCountry=result.code!=='CARD_BILLING_COUNTRY_MISMATCH'&&/^[A-Z]{2}$/.test(result.billing_country||'')?' Текущая страна РК в Meta: '+result.billing_country+'.':'';
  const funding=result.funding;
  const inventory=funding?.inventory_complete===true&&Array.isArray(funding.all_credential_ids)
    ?' Meta: карт '+(funding.payment_methods?.length||0)+', других платёжных инструментов '+(funding.non_card_credential_count||0)+'.'
    :funding?.code&&/^PAYMENT_[A-Z_]{1,80}$/.test(funding.code)?' Проверка списка: '+funding.code+'.':'';
  const errors=Array.isArray(result.meta_error_codes)?result.meta_error_codes.filter(n=>Number.isInteger(n)&&n>=0&&n<=999999999):[];
  const readFailure=['PAYMENT_HTTP_TIMEOUT','PAYMENT_HTTP_UNAVAILABLE','CHECKPOINT_REQUIRED','BUSINESS_LOGIN_GATE','SESSION_EXPIRED'].includes(result.reconcile_error_code)
    ?' Текущая проверка не завершена: '+result.reconcile_error_code+'.':'';
  return (messages[result.code]||result.code||'Не удалось подтвердить результат')+paymentSetupDetail+verificationDetail+businessWalletNote+country+availability+countryMismatch+currentCountry+inventory+
    readFailure+
    (errors.length?' Коды ошибки Meta: '+errors.join(', ')+'.':'')+
    (Array.isArray(result.meta_error_messages)?' '+result.meta_error_messages.filter(s=>typeof s==='string'&&s.length<=320).slice(0,4).join(' · '):'')+
    (result.missing_fields?.length?' Поля: '+result.missing_fields.map(f=>names[f]||f).join(', ')+'.':'')+
    (fields.length?' Поля формы Meta: '+fields.join(', ')+'.':'');
}


function paymentCardBriefMessage(result){
  // User-visible status has no protocol, contract or inventory description.
  // The original structured metadata remains available to diagnostics.
  const brief={
    CARD_LINK_CONFIRMED:'Карта привязана к РК.',
    CARD_LINK_OBSERVED:'Карта найдена в РК.',
    CARD_SAVE_RESULT_UNKNOWN:'Результат добавления не получен. Повторная отправка остановлена.',
    CARD_SAVED_CREDENTIAL_UNLINKED:'Карта сохранена, но не прикреплена к РК.',
    CARD_BANK_CONFIRMATION_REQUIRED:'Карта привязана. Требуется подтверждение в банке.',
    SDC_AUTH_READY:'Meta разрешает запрос временного списания.',
    SDC_AUTH_PENDING_WAIT_CODE:'Meta уже ожидает подтверждения временной операции. Новый запрос в банк не отправлен.',
    SDC_AUTH_SENT_WAIT_CODE:'Meta приняла запрос временной операции. Проверьте её в банке.',
    SDC_AUTH_ALREADY_ATTEMPTED:'Запрос уже выполнялся. Второй запрос в банк не отправлен.',
    SDC_AUTH_PRECHECK_UNAVAILABLE:'Meta недоступна. Запрос в банк не отправлен.',
    SDC_AUTH_RESULT_UNKNOWN:'Ответ о временной операции не получен. Повторная отправка остановлена.',
    SDC_AUTH_REJECTED:'Meta отклонила временную операцию.',
    CARD_VERIFICATION_NO_REQUIRED_TASK:'Meta не требует дополнительной проверки карты.',
    CARD_VERIFICATION_MUTATION_NOT_PINNED:'Meta требует другого подтверждения. Запрос в банк не отправлен.',
    CARD_VERIFICATION_SDC_READ_UNVERIFIED:'Не удалось узнать статус временной операции.',
    SESSION_EXPIRED:'Нужно обновить Facebook-сессию.',
    BUSINESS_LOGIN_GATE:'Нужно восстановить доступ к Meta Business.',
    CHECKPOINT_REQUIRED:'Meta требует подтверждения Facebook-профиля.'
  };
  if(result?.code in brief)return brief[result.code];
  if(!result?.code)return 'Не удалось получить результат.';
  const described=paymentCardMessage({code:result.code,missing_fields:result.missing_fields});
  return described===result.code?'Не удалось завершить действие в Meta.':described;
}

function paymentCardAuthEvidence(result){
  const route=result?.diagnostic?.facebook_route;
  if(route==='/checkpoint')return ' · Facebook перенаправил worker на /checkpoint.';
  if(route==='/login')return ' · Facebook перенаправил worker на /login.';
  return '';
}

function paymentSetupPayload(){
  return {setup_country:'UA',setup_country_mode:$('paymentSetupCountry').value==='CURRENT'?'current':'prefer_ua',
    setup_currency:$('paymentSetupCurrency').value||'USD',setup_timezone:$('paymentSetupTimezone').value||'Europe/Kyiv'};
}

async function inspectFundingRows(rows,container){
  await concurrent(rows,1,async r=>{
    try{return await apiJson('ajax/pythonWorkerJobs.php',post({action:'payment_status',profile:r.profile,account_id:r.id,...paymentAssetHint(r)}));}
    catch(e){return {error:e.message};}
  },(d,t,res,idx)=>{
    const r=rows[idx],f=res?.funding,line=document.createElement('div');line.className='ws-result';
    if(res?.error||!f){
      line.className+=' bad';line.textContent=r.profile+' / '+r.id+': '+(res?.error||'Нет результата проверки');
      if(/CHECKPOINT_REQUIRED/.test(res?.error||''))line.textContent+=' — Meta требует проверки аккаунта; проверка карты не выполнена.';
      r.funding={funding_verified:false,verification_status:'UNVERIFIED'};
    }else{
      r.funding=f;
      const methods=(f.payment_methods||[]).map(m=>m.type+' •••• '+m.last4).join(', ');
      const status=f.verification_status==='LINKED'?'Карта присутствует; платёжная проверка не подтверждена':
        f.verification_status==='NONE'?'Meta показывает отсутствие платёжных методов':
        f.code==='PAYMENT_METHODS_FILTERED_NO_CARD'&&f.account_scope_verified===true&&f.business_scope_verified===true&&f.methods_query_verified===true
          ?'РК и BM подтверждены. В полученном списке Meta привязанная карта пока не обнаружена.'
          :'Платёжный метод или соответствие выбранному РК не удалось подтвердить';
      line.textContent=r.profile+' / '+r.id+': '+status+(methods?' — '+methods:'');
    }
    container.appendChild(line);setProgress(d,t);
  });render();
}

async function savePaymentCard(){
  const expiry=$('paymentCardExpiry').value.trim().match(/^(\d{1,2})\s*\/\s*(\d{2}|\d{4})$/);
  if(!expiry)throw new Error('Срок действия: ММ/ГГ.');
  const payload={action:'add',number:$('paymentCardNumber').value,month:expiry[1],year:expiry[2]};
  ['holder','country','address','city','region','postal_code','label'].forEach(key=>{payload[key]=$('paymentCard_'+key).value.trim();});
  try{return (await apiJson('ajax/paymentCards.php',post(payload))).card;}
  finally{$('paymentCardNumber').value='';payload.number='';}
}

async function paymentVerifyConfirmedLink(row,card){
  // Only after a confirmed exact-card link, ask Meta which financial action
  // is necessary. No independent/manual bank charge and no blind retries.
  const base={card_id:card.id,profile:row.profile,account_id:row.id,...paymentAssetHint(row)};
  const observed=await apiJson('ajax/paymentCards.php',post({action:'verify',...base}));
  if(observed?.result?.code==='SDC_AUTH_READY')
    return await apiJson('ajax/paymentCards.php',post({action:'authorize',...base}));
  return observed;
}

async function bindPaymentCard(rows,card,cvv,container,reviews={}){
  const missing=[];
  let stopAfterBilling=false,stopAfterUncertain=false;
  if(!card?.id)throw new Error('Выберите сохранённую карту или добавьте новую.');
  if(cvv&&!/^\d{3,4}$/.test(cvv))throw new Error('CVV должен содержать 3 или 4 цифры.');
  // One Save at a time; uncertain submission never triggers a retry.
  await concurrent(rows,1,async r=>{
    if(stopAfterBilling)return {skipped:true,code:'BATCH_STOPPED_BILLING_FIELDS'};
    if(stopAfterUncertain)return {skipped:true,code:'BATCH_STOPPED_UNCERTAIN_RESULT'};
    const review=reviews[r.profile+'|'+String(r.id).replace(/^act_/,'')];
    try{
      const response=await apiJson('ajax/paymentCards.php',post({action:'bind',card_id:card.id,...(cvv?{cvv}:{}),
      client_info:JSON.stringify({color_depth:String(window.screen.colorDepth),java_enabled:false,
        screen_height:String(window.innerHeight),screen_width:String(window.innerWidth)}),
      ...(review?{retry_confirmed:'1',retry_review:review.token}:{}),profile:r.profile,account_id:r.id,...paymentAssetHint(r),...paymentSetupPayload()}));
      const result=response?.result||{};
      if((result.status==='SUBMITTED_UNVERIFIED'||result.status==='VERIFYING')&&result.submitted!==false){
        // Save may have been observed or the browser may have timed out after
        // an unknown boundary. Never advance to another RK on either case.
        // One read-only exact-RK reconciliation may prove linkage; only that
        // positive proof is strong enough to continue the selected batch.
        try{
          const checked=await apiJson('ajax/paymentCards.php',post({action:'reconcile',card_id:card.id,
            profile:r.profile,account_id:r.id,...paymentAssetHint(r)}));
          if(checked?.result?.status==='LINKED')return checked;
          stopAfterUncertain=true;
          return checked;
        }catch(_){stopAfterUncertain=true;return response;}
      }
      if(result.status==='ACTION_REQUIRED'||
        (result.submitted!==false&&result.status!=='LINKED'))stopAfterUncertain=true;
      if(result.card_linked===true
          && ['LINKED','ACTION_REQUIRED'].includes(result.status)
          && result.funding?.account_scope_verified===true
          && result.funding?.business_scope_verified===true
          && result.funding?.payment_account_relation_verified===true){
        // User explicitly selected this card for this RK. On a confirmed
        // link, check the Meta SDC task and initiate its once-only temporary
        // authorization only when Meta says UNVERIFIED_OR_PENDING_AUTH.
        try{response.verification_follow_up=await paymentVerifyConfirmedLink(r,card);}
        catch(e){response.verification_follow_up={error:e.message};}
      }
      return response;
    }catch(e){stopAfterUncertain=true;return {error:e.message};}
  },(d,t,res,idx)=>{
    const r=rows[idx],result=res?.result,line=document.createElement('div');
    if(result?.code==='CARD_BILLING_FIELDS_REQUIRED'){
      missing.push(...(result.missing_fields||[]));stopAfterBilling=true;
    }
    line.className='ws-result '+(result?.status==='LINKED'?'ok':'bad');
    line.textContent=res?.skipped
      ?r.profile+' / '+r.id+': не запускался — пакет остановлен после '+(res.code==='BATCH_STOPPED_BILLING_FIELDS'?'запроса обязательных платёжных реквизитов':'неподтверждённого результата предыдущего РК')+'.'
      :r.profile+' / '+r.id+' · •••• '+card.last4+': '+paymentCardBriefMessage(result||{code:res?.error||'Результат неизвестен'});
    paymentApplyFunding(r,result?.funding||{funding_verified:false,verification_status:'UNVERIFIED'});
    container.appendChild(line);
    const follow=res?.verification_follow_up;
    if(follow){
      const check=follow?.result;
      const summary=document.createElement('div');
      summary.className='ws-result '+(check?.status==='VERIFIED'?'ok':'bad');
      summary.textContent='Банковская проверка · '+r.profile+' / '+r.id+': '+
        paymentCardBriefMessage(check||{code:follow?.error||'CARD_VERIFICATION_HTTP_UNAVAILABLE'});
      container.appendChild(summary);
      if(check)paymentConfirmationPanel(r,{...check,card_id:card.id},container);
    }
    setProgress(d,t);
  });render();return [...new Set(missing)];
}

function paymentAssetHint(row){
  const businessId=String(row.business_id||row.business?.id||'').replace(/^\s+|\s+$/g,'');
  if(!/^\d{5,30}$/.test(businessId))return {};
  const assetId=String(row.business_asset_id||'').replace(/^\s+|\s+$/g,'');
  const name=String(row.account_name||row.name||'').trim();
  return {business_id:businessId,...(/^\d{5,30}$/.test(assetId)?{business_asset_id:assetId}:{}),account_name:name};
}

function paymentApplyFunding(row,funding){
  row.funding=funding;
  // Restored batches contain target copies. Update the canonical inventory too.
  if(typeof state!=='undefined')for(const target of state.inventory?.ad_accounts||[]){
    if(target.profile===row.profile&&String(target.id).replace(/^act_/,'')===String(row.id).replace(/^act_/,''))target.funding=funding;
  }
}

function paymentConfirmationPanel(row,result,container){
  if(!result||String(result.code||'').startsWith('SDC_')
      ||result.code==='CARD_VERIFICATION_NO_REQUIRED_TASK')return;
  const methods=result.funding?.payment_methods||[];
  const ids=[result.credential?.id,result.credential?.credential_id].filter(Boolean);
  const card=methods.find(m=>ids.includes(m.credential_id))||(methods.length===1?methods[0]:null);
  const status=result.card_confirmation_status||card?.card_confirmation_status||'UNKNOWN';
  const state=result.sdc_usability;
  // A temporary bank hold, a statement descriptor and a 3-D Secure push are
  // different flows. Do not pretend a push was sent or ask for an SDC code
  // before a confirmed bank-authorization outcome.
  let message='';
  if(state==='PENDING_VERIFICATION'||result.code==='SDC_AUTH_PENDING_WAIT_CODE')
    message='Meta уже ожидает завершения временной операции по карте. Повторное списание не отправлено.';
  else if(result.code==='SDC_AUTH_SENT_WAIT_CODE'&&result.verification_triggered===true)
    message='Meta приняла запрос временной операции. Проверьте её в банке.';
  else if(state==='UNVERIFIED_OR_PENDING_AUTH'||result.code==='SDC_AUTH_READY')
    message='Meta требует временную банковскую операцию.';
  else if(result.code==='CARD_VERIFICATION_NO_REQUIRED_TASK')
    message='Дополнительное подтверждение карты Meta не запросила.';
  else if(status==='REQUIRED')
    message='Карта привязана, но Meta требует подтверждения.';
  else if(status==='CLEAR')
    message='Карта видна в РК. Банковская операция не подтверждена.';
  if(!message)return;
  const line=document.createElement('div');line.className='ws-result';
  line.textContent=row.profile+' / '+row.id+': '+message;
  container.appendChild(line);
}

function paymentCardTargetPlan(rows,bindings,cardId){
  const plan={fresh:[],pending:[],linked:[],blocked:[]},seen=new Set();
  for(const row of rows){
    const account=String(row.id).replace(/^act_/,''); const key=row.profile+'|'+account;
    if(seen.has(key))continue;seen.add(key);
    const binding=bindings.find(b=>b.profile===row.profile&&b.account_id===account);
    if(binding&&(['IN_PROGRESS','SUBMITTED_UNVERIFIED'].includes(binding.status)||
      (binding.status==='ACTION_REQUIRED'&&binding.submitted!==false))){
      plan[!cardId||binding.card_id===cardId?'pending':'blocked'].push(row);
    }else if(binding?.status==='LINKED'&&binding.card_id===cardId)plan.linked.push(row);
    else plan.fresh.push(row);
  }
  return plan;
}

async function inspectCardVerificationHTTP(rows,card,container){
  if(!card?.id)throw new Error('Выберите сохранённую карту для проверки.');
  await concurrent(rows,1,async r=>{
    try{
      const original=await apiJson('ajax/paymentCards.php',post({action:'verify',
        card_id:card.id,profile:r.profile,account_id:r.id,...paymentAssetHint(r)}));
      let outcome=original;
      if(original?.result?.code==='SDC_AUTH_READY'){
        // Once-only authorization, only after Meta's exact-card screen says
        // UNVERIFIED_OR_PENDING_AUTH. The SQLite ledger blocks all replays.
        outcome=await apiJson('ajax/paymentCards.php',post({action:'authorize',
          card_id:card.id,profile:r.profile,account_id:r.id,...paymentAssetHint(r)}));
      }
      if(outcome?.result)outcome.result.card_id=card.id;
      return outcome;
    }catch(e){return {error:e.message};}
  },(d,t,res,idx)=>{
    const row=rows[idx],result=res?.result,line=document.createElement('div');
    line.className='ws-result '+(result?.code==='CARD_VERIFICATION_NO_REQUIRED_TASK'?'ok':'bad');
    line.textContent=row.profile+' / '+row.id+' · •••• '+card.last4+': '+
      paymentCardBriefMessage(result||{code:res?.error||'CARD_VERIFICATION_HTTP_UNAVAILABLE'});
    container.appendChild(line);
    if(result?.funding)paymentApplyFunding(row,result.funding);
    if(result)paymentConfirmationPanel(row,result,container);
    setProgress(d,t);
  });render();
}

async function reconcilePaymentCard(rows,card,container){
  if(!card?.id)throw new Error('Выберите карту предыдущей попытки привязки.');
  const reviews={};
  await concurrent(rows,1,async r=>{
    try{
      const response=await apiJson('ajax/paymentCards.php',post({
        action:'reconcile',card_id:card.id,profile:r.profile,account_id:r.id,...paymentAssetHint(r)}));
      const linked=response?.result;
      if(linked?.card_linked===true
          && ['LINKED','ACTION_REQUIRED'].includes(linked.status)
          && linked?.funding?.account_scope_verified===true
          && linked?.funding?.business_scope_verified===true
          && linked?.funding?.payment_account_relation_verified===true){
        try{response.verification_follow_up=await paymentVerifyConfirmedLink(r,card);}
        catch(e){response.verification_follow_up={error:e.message};}
      }
      return response;
    }catch(e){return {error:e.message};}
  },(d,t,res,idx)=>{
    const r=rows[idx],result=res?.result,line=document.createElement('div');
    line.className='ws-result '+(result?.status==='LINKED'?'ok':'bad');
    line.textContent=r.profile+' / '+r.id+' · •••• '+card.last4+': '+paymentCardBriefMessage(result||{code:res?.error||'Результат неизвестен'});
    paymentApplyFunding(r,result?.funding||{funding_verified:false,verification_status:'UNVERIFIED'});
    if(result?.code==='CARD_RECONCILE_NO_METHOD'&&result.retry_review?.token&&result.retry_review?.expires_at)
      reviews[r.profile+'|'+String(r.id).replace(/^act_/,'')]={...result.retry_review,card_id:card.id};
    container.appendChild(line);
    paymentConfirmationPanel(r,result,container);
    const check=res?.verification_follow_up;
    if(check){
      const statusLine=document.createElement('div');statusLine.className='ws-result';
      statusLine.textContent='Банковское подтверждение · '+r.profile+' / '+r.id+': '+
        paymentCardBriefMessage(check?.result||{code:check?.error||'CARD_VERIFICATION_HTTP_UNAVAILABLE'});
      container.appendChild(statusLine);
      if(check?.result)paymentConfirmationPanel(r,{...check.result,card_id:card.id},container);
    }
    const f=result?.funding;
    if(f?.ui_preview&&/^[A-Za-z0-9+/=]+$/.test(f.ui_preview)){
      const preview=document.createElement('details'),summary=document.createElement('summary'),image=document.createElement('img');
      summary.textContent='Экран проверки Meta';image.alt='Meta — '+r.profile+' / '+r.id;image.style.maxWidth='100%';
      image.src='data:image/jpeg;base64,'+f.ui_preview;preview.appendChild(summary);preview.appendChild(image);container.appendChild(preview);
    }
    setProgress(d,t);
  });render();return reviews;
}

async function showFunding(restored=null){
  const rows=restored?.rows||selectedRows('ad_accounts');if(!rows.length)return;
  if(restored)remaskClearPaymentSecrets();
  const selected=rows.map(r=>esc(r.profile+' / '+r.id)).join('<br>');
  openModal('Карты и привязка — '+rows.length+' РК',`
    <div class="ws-muted">Выбранные рекламные аккаунты: ${selected}</div>
    <div class="ws-form mt-3">
      <div class="full"><label for="paymentCardSelect">Сохранённая карта</label><select id="paymentCardSelect"><option value="">Загрузка карт…</option></select></div>
      <div id="paymentCardCvvField"><label for="paymentCardCvv">CVV для новой привязки</label><input id="paymentCardCvv" type="password" inputmode="numeric" maxlength="4" autocomplete="off"></div>
    </div>
    <details class="mt-2"><summary>Настройки оплаты</summary><div class="ws-form mt-2">
      <div><label for="paymentSetupCountry">Страна оплаты РК</label><select id="paymentSetupCountry"><option value="UA">Украина; если поле заблокировано — текущая страна Meta</option><option value="CURRENT">Текущая страна Meta</option></select></div>
      <div><label for="paymentSetupCurrency">Валюта, если РК ещё не настроен</label><select id="paymentSetupCurrency"><option value="USD">USD — доллар США</option></select></div>
      <div><label for="paymentSetupTimezone">Часовой пояс РК</label><input id="paymentSetupTimezone" value="Europe/Kyiv"></div>
    </div></details>
    <details id="paymentCardNew" class="mt-3"><summary>Добавить новую карту</summary>
      <div class="ws-form mt-2">
        <div><label for="paymentCardNumber">Номер карты</label><input id="paymentCardNumber" type="password" inputmode="numeric" autocomplete="off"></div>
        <div><label for="paymentCardExpiry">Срок действия</label><input id="paymentCardExpiry" placeholder="ММ/ГГ" autocomplete="off"></div>
        <div><label for="paymentCard_holder">Имя владельца карты</label><input id="paymentCard_holder" autocomplete="off"></div>
        <div><label for="paymentCard_label">Название карты в ReMask</label><input id="paymentCard_label" maxlength="80"></div>
        <div><label for="paymentCard_country">Страна платёжного адреса</label><input id="paymentCard_country" placeholder="Как указано у банка"></div>
        <div><label for="paymentCard_postal_code">Почтовый индекс</label><input id="paymentCard_postal_code"></div>
        <div class="full"><label for="paymentCard_address">Платёжный адрес</label><input id="paymentCard_address"></div>
        <div><label for="paymentCard_city">Город</label><input id="paymentCard_city"></div>
        <div><label for="paymentCard_region">Область / штат</label><input id="paymentCard_region"></div>
      </div>
      <button id="paymentCardSave" type="button" class="mt-2">Сохранить карту</button>
      <button id="paymentCardSaveBind" type="button" class="mt-2">Сохранить и привязать к выбранным РК</button>
    </details>
    <div id="paymentCardBillingMissing" class="ws-form mt-2"></div>
    <label id="paymentCardRetryField" hidden class="mt-2"><input id="paymentCardRetryConfirmed" type="checkbox"> Разрешаю одну повторную попытку после проверки Meta</label>
    <div class="mt-3"><button id="paymentCardBind" type="button" class="btn btn-primary">Привязать карту</button></div>
    <details class="mt-2"><summary>Диагностика</summary>
      <button id="paymentCardPrepare" type="button">Проверить форму Meta</button>
      <button id="paymentCardInspect" type="button">Проверить привязанные карты</button></details>
    <button id="paymentCardVerify" type="button" class="mt-2">Запросить подтверждение карты в банке</button>
    <div id="paymentCardAssignments" class="ws-muted mt-2"></div>
    <div id="paymentCardProgress" class="ws-muted mt-2" aria-live="polite"></div>
    <div id="fundingResults" aria-live="polite"></div>`,'',null);
  const container=$('fundingResults'),select=$('paymentCardSelect');let cards=[],bindings=[],busy=false,reviews={},billingMissing=[];
  const targetPlan=()=>paymentCardTargetPlan(rows,bindings,select.value);
  const pending=()=>targetPlan().pending.length>0&&targetPlan().fresh.length===0;
  const retryAvailable=()=>pending()&&bindings.filter(b=>b.card_id===select.value&&['IN_PROGRESS','SUBMITTED_UNVERIFIED','ACTION_REQUIRED'].includes(b.status)).every(b=>{
    const review=reviews[b.profile+'|'+b.account_id];return review?.card_id===select.value&&Date.parse(review.expires_at)>Date.now();
  });
  const retryConfirmed=()=>retryAvailable()&&$('paymentCardRetryConfirmed').checked===true;
  const updatePrimary=()=>{
    const plan=targetPlan(),checking=pending(),retry=retryConfirmed();
    $('paymentCardBind').textContent=checking?(retry?'Повторить привязку':'Проверить результат'):
      plan.fresh.length?(plan.pending.length||plan.linked.length||plan.blocked.length?'Продолжить привязку — '+plan.fresh.length+' РК':'Привязать карту'):
      plan.blocked.length?'Выберите карту незавершённой привязки':'Все выбранные РК уже привязаны';
    $('paymentCardBind').disabled=busy||(!plan.fresh.length&&!plan.pending.length);
    $('paymentCardRetryField').hidden=!retryAvailable();
    $('paymentCardCvvField').hidden=(checking&&!retry)||(plan.fresh.length===0&&!retry);
    $('paymentCardSaveBind').disabled=busy||checking;
  };
  const refreshCards=async(preferred='')=>{
    const data=await apiJson('ajax/paymentCards.php',post({action:'list'}));cards=data.cards||[];
    select.innerHTML='<option value="">Выберите карту</option>';
    cards.forEach(card=>{const option=document.createElement('option');option.value=card.id;
      option.textContent=(card.label?card.label+' · ':'')+card.brand+' •••• '+card.last4+' · '+String(card.month).padStart(2,'0')+'/'+String(card.year).slice(-2);select.appendChild(option);});
    const assignments=$('paymentCardAssignments');assignments.textContent='';
    bindings=(data.bindings||[]).filter(binding=>rows.some(r=>r.profile===binding.profile&&String(r.id).replace(/^act_/,'')===binding.account_id));
    bindings.forEach(binding=>{
      const line=document.createElement('div');
      const status={LINKED:'видна в Meta у этого РК',BLOCKED:'не привязана',FAILED:'ошибка',IN_PROGRESS:'операция начата',SUBMITTED_UNVERIFIED:'результат требует проверки',ACTION_REQUIRED:'требуется подтверждение'}[binding.status]||binding.status;
      line.textContent=binding.profile+' / act_'+binding.account_id+' · •••• '+binding.last4+' · '+status+
        (binding.last_result_code?' · '+paymentCardBriefMessage({...binding,code:binding.last_result_code}):'');assignments.appendChild(line);
    });
    if(preferred)select.value=preferred;
    else if(!select.value&&rows.length===1&&bindings.length===1)select.value=bindings[0].card_id;
    const plan=targetPlan();
    if(select.value&&plan.linked.length===rows.length){
      paymentCardResumeRemove();
    }
    updatePrimary();
  };
  const showMissingBilling=missing=>{
    const labels={holder:'Имя владельца карты',country:'Страна платёжного адреса',address:'Платёжный адрес',city:'Город',region:'Область / штат',postal_code:'Почтовый индекс'};
    billingMissing=[...new Set(missing.filter(k=>labels[k]))];
    $('paymentCardBillingMissing').innerHTML=billingMissing.map(k=>'<div><label for="paymentCardExisting_'+k+'">'+labels[k]+' — требуется Meta</label><input id="paymentCardExisting_'+k+'" autocomplete="off"></div>').join('');
  };
  const saveExistingBilling=async card=>{
    const patch={};for(const key of billingMissing){const value=$('paymentCardExisting_'+key).value.trim();if(!value)throw new Error('Заполните обязательные реквизиты карты.');patch[key]=value;}
    if(Object.keys(patch).length){await apiJson('ajax/paymentCards.php',post({action:'billing_update',card_id:card.id,...patch}));showMissingBilling([]);}
  };
  const run=async(task,clearSecrets=true)=>{
    if(busy)return;busy=true;container.innerHTML='';
    paymentCardResumeWrite(rows,select.value);
    const controls=$('workspaceModalBody').querySelectorAll('input,select,button');controls.forEach(el=>el.disabled=true);
    $('paymentCardProgress').textContent='Обработка группы из '+rows.length+' РК. Ожидаю ответ Meta…';
    try{await task();}catch(e){const line=document.createElement('div');line.className='ws-result bad';line.textContent=e.message;container.appendChild(line);}
    finally{if(clearSecrets)remaskClearPaymentSecrets();controls.forEach(el=>el.disabled=false);$('paymentCardProgress').textContent='';busy=false;updatePrimary();}
  };
  const save=async(bind)=>{
    if(bind&&pending())throw new Error(paymentCardBriefMessage({code:'CARD_BINDING_RECONCILE_REQUIRED'}));
    const cvv=$('paymentCardCvv').value;
    const card=await savePaymentCard();paymentCardResumeWrite(rows,card.id);await refreshCards(card.id);$('paymentCardNew').open=false;
    const line=document.createElement('div');line.className='ws-result ok';line.textContent='Карта •••• '+card.last4+' сохранена в ReMask.';container.appendChild(line);
    if(bind){const plan=targetPlan();showMissingBilling(await bindPaymentCard(plan.fresh,card,cvv,container));await refreshCards(card.id);}
  };
  select.addEventListener('change',()=>{paymentCardResumeWrite(rows,select.value);$('paymentCardCvv').value='';$('paymentCardRetryConfirmed').checked=false;reviews={};showMissingBilling([]);updatePrimary();});
  $('paymentCardRetryConfirmed').addEventListener('change',updatePrimary);
  $('paymentCardSave').addEventListener('click',()=>run(()=>save(false),false));
  $('paymentCardSaveBind').addEventListener('click',()=>run(()=>save(true)));
  $('paymentCardBind').addEventListener('click',()=>run(async()=>{
    const card=cards.find(c=>c.id===select.value);
    if(pending()&&!retryConfirmed()){
      $('paymentCardRetryConfirmed').checked=false;
      reviews=await reconcilePaymentCard(targetPlan().pending,card,container);
    }else{
      if(!card?.id)throw new Error('Выберите сохранённую карту.');
      const confirmed=retryConfirmed(),cvv=$('paymentCardCvv').value;
      if(confirmed&&!/^\d{3,4}$/.test(cvv))throw new Error('Введите CVV для одной повторной попытки.');
      await saveExistingBilling(card);
      const plan=targetPlan(),targets=confirmed?plan.pending:plan.fresh;
      if(!targets.length)throw new Error(plan.blocked.length?'Для незавершённых РК выберите карту предыдущей попытки.':'Все выбранные РК уже привязаны.');
      if(plan.pending.length&&!confirmed){const note=document.createElement('div');note.className='ws-result';note.textContent='Незавершённые привязки: '+plan.pending.length+' РК. Повторная отправка пропущена.';container.appendChild(note);}
      if(plan.linked.length){const note=document.createElement('div');note.className='ws-result ok';note.textContent='Уже привязаны: '+plan.linked.length+' РК. Пропущены.';container.appendChild(note);}
      if(plan.blocked.length){const note=document.createElement('div');note.className='ws-result bad';note.textContent='Для '+plan.blocked.length+' РК нужно проверить предыдущую карту. Остальные продолжаются.';container.appendChild(note);}
      try{showMissingBilling(await bindPaymentCard(targets,card,cvv,container,confirmed?reviews:{}));}
      finally{reviews={};$('paymentCardRetryConfirmed').checked=false;}
    }
    await refreshCards(card?.id||'');
  }));
  $('paymentCardInspect').addEventListener('click',()=>run(async()=>{
    const card=cards.find(c=>c.id===select.value);
    if(card){
      reviews=await reconcilePaymentCard(rows,card,container);
      $('paymentCardRetryConfirmed').checked=false;
      await refreshCards(card.id);
    }else await inspectFundingRows(rows,container);
  },false));
  $('paymentCardVerify').addEventListener('click',()=>run(async()=>{
    const card=cards.find(c=>c.id===select.value);
    if(!card)throw new Error('Выберите карту.');
    await inspectCardVerificationHTTP(rows,card,container);
    await refreshCards(card.id);
  },false));
  $('paymentCardPrepare').addEventListener('click',()=>run(async()=>{
    for(let i=0;i<rows.length;i++){
      const r=rows[i],data=await apiJson('ajax/paymentCards.php',post({action:'prepare',...(select.value?{card_id:select.value}:{}),profile:r.profile,account_id:r.id,...paymentAssetHint(r),...paymentSetupPayload()}));
      const line=document.createElement('div');line.className='ws-result '+(['FORM_READY','FORM_CONFIRMED'].includes(data.result.status)?'ok':'bad');
      line.textContent=r.profile+' / '+r.id+': '+paymentCardBriefMessage(data.result)+paymentCardAuthEvidence(data.result)
        +(data.result.country_policy?' '+paymentCardBriefMessage(data.result.country_policy):'');container.appendChild(line);setProgress(i+1,rows.length);
      if(data.result.code==='CARD_BILLING_FIELDS_REQUIRED')showMissingBilling([...billingMissing,...(data.result.missing_fields||[])]);
      if(data.result.ui_preview&&/^[A-Za-z0-9+/=]+$/.test(data.result.ui_preview)){
        const preview=document.createElement('details'),summary=document.createElement('summary'),image=document.createElement('img');
        summary.textContent='Экран Meta перед вводом карты';image.alt='Meta — '+r.profile+' / '+r.id;image.style.maxWidth='100%';
        image.src='data:image/jpeg;base64,'+data.result.ui_preview;preview.appendChild(summary);preview.appendChild(image);container.appendChild(preview);
      }
    }
  },false));
  try{
    await refreshCards(restored?.cardId||'');
    // A previously submitted Save needs read-only confirmation on reopen.
    // Never require CVV or trigger a second Save just to check its result.
    const existing=targetPlan();
    const selectedCard=cards.find(card=>card.id===select.value);
    if(selectedCard&&existing.pending.length){
      await run(async()=>{
        reviews=await reconcilePaymentCard(existing.pending,selectedCard,container);
        await refreshCards(selectedCard.id);
      },false);
    }
    if(restored)$('paymentCardProgress').textContent='Группа из '+rows.length+' РК восстановлена. Проверка отправленных карт не повторяет сохранение.';
  }catch(e){select.innerHTML='<option value="">Список карт недоступен</option>';const line=document.createElement('div');line.className='ws-result bad';line.textContent=e.message;container.appendChild(line);}
}

// Restore the group after a reload without replaying any financial mutation.
function restorePaymentCardBatch(){
  const saved=paymentCardResumeRead();
  if(saved)showFunding(saved).catch(()=>{});
}
if(typeof window!=='undefined'){
  if(document.readyState==='complete')queueMicrotask(restorePaymentCardBatch);
  else window.addEventListener('load',restorePaymentCardBatch,{once:true});
}

