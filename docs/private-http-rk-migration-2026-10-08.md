# ReMask: исполнение Create RK через прямой HTTP

## Причина и граница изменения

Последний подтверждённый Prepare на production 6229168:
job d787b0f1d53b4a7aaf33ddaeb39a2df1, 7 октября 22:46–22:47 UTC.
Профиль 15 повторно остановился на AD_ACCOUNT_CREATE_UI_CHANGED /
ad_account_wizard_action_missing до финальной отправки. Профиль 14 остановился
на CREATE_BM_PRE_SUBMIT_TRANSPORT: Business HTTP precheck вернул 400; POST не
отправлялся. Это не доказательство бана и не успешный комплект.

Предыдущий код требовал новый UI capture для каждого RK, а затем вызывал
graphql_browser_native. Такой путь оставался зависимым от Chromium даже после
переноса положительной проверки inventory в HTTP.

## Новый путь RK

1. Существующие same-job / cross-job guards сохраняются. Неизвестная предыдущая
   отправка сначала проходит reconciliation, не очищается сменой транспорта.
2. Точная свежая BM inventory сначала читается через cookie/proxy HTTP.
   Положительная пара требует конкретного BM и RK. Отсутствие требует полной
   response collection; URL, кэш и paginated fragment не являются доказательством.
   Наличие любого RK в подтверждённой полной inventory блокирует второй RK
   в стандартном режиме одного комплекта.
3. Используется сохранённый наблюдённый контракт. Это doc_id + точная схема
   variables из перехваченного запроса Meta, не один найденный doc_id.
4. Actor, BM, name, currency, timezone и client_mutation_id задаются для текущей
   операции с сохранением исходных JSON scalar types. Cookies, DTSG, LSD,
   request/session/revision fields старого захвата не входят в шаблон.
5. При отсутствующем/просроченном контракте Chromium получает схему и блокирует
   исходный CREATE. В режиме захвата не открываются currency/timezone popovers:
   неизменяемые параметры выставляются в HTTP payload и проверяются перед POST.
6. Только session.graphql выполняет настоящий POST с текущими cookies, proxy,
   user-agent и свежим Bootstrap точного BM. Browser-native POST не является
   резервным транспортом.
7. Durable CREATE_SUBMIT_INTENT сохраняется до POST. Сбой сохранения intent
   классифицируется как pre-submit и запрещает отправку.
8. Exact BM→RK inventory подтверждает ответ перед CREATE_CONFIRMED/COMMIT.
   Потеря ответа идёт в read-only reconciliation. Partial data+errors и пустой
   ответ не считаются ни успехом, ни доказанным отказом.

Контракт хранится атомарно в facebook-web/ad-account-contract.json на volume.
Срок пригодности — 24 часа от наблюдения, а не от последнего использования.
Неизвестные identity/session fields делают захват непригодным для общего кэша;
свежий точный контракт всё ещё может исполниться для своей операции.
Явный отказ схемы инвалидирует контракт и допускает ограниченный повторный
захват. Возможная отправка не разрешает смену транспорта или слепой повтор.

## Проверки

Новые поведенческие проверки используют настоящий FacebookWebSession с
синтетическим HTTP peer и настоящим SQLite provisioning state. Они проходят
precheck → submit → verify → commit при явном запрете browser lease и
graphql_browser_native. Покрыты перенос между профилями, scalar types,
checkpoint failure до POST, timeout после POST, точное восстановление без
второго CREATE, partial response, чужой BM, paginated empty и существующий RK
с другим именем. Дополнительные Chromium fixtures проверяют получение
контракта без выбора timezone/currency.

CI требует полного backend без skips, Node интерфейсов, PHP runtime/payment
guards и Chromium fixtures. Локальная среда без PHP/Chrome не заменяет CI.
Production release выполняется только после прохождения этих проверок.

## Остальные операции

### Новый Prepare профиля 14: 8 октября, 05:53 UTC

На сборке 6a614cf job ed952a84ef444c5b8241d2b9cdd9465e остановился на
CREATE_BM_PRE_SUBMIT_TRANSPORT до CREATE POST. Facebook bootstrap был получен
через Marketplace; Business precheck вернул HTTP 400 на /latest/home.
Это подтверждает повтор ошибки, но не причину HTTP 400 и не наличие бана.

Для известного useBusinessCreationMutationMutation после 400/404 либо
неподтверждённого HTML главной страницы добавлена одна read-only HTTP проверка
известной страницы /create. Отправка разрешена только при HTTP 200 и свежем
Business DTSG. Login/checkpoint/401/403 и rate limit не запускают fallback.
Точная BM-проверка CREATE_RK не переключается на общую страницу создания.
Диагностика сохраняет две проверенные поверхности, статусы, размер ответа и
наличие токена, без значений токенов и query-параметров конечного URL.
Исправление покрывает route-level отказ главной страницы; доступность /create
для реального профиля 14 должна подтверждаться отдельным live trace.

| Операция | Исполнение после изменения |
| --- | --- |
| Create BM | Прямой cookie/proxy GraphQL, без browser POST fallback |
| Create RK | Прямой HTTP по наблюдённому контракту; Chromium получает отсутствующий/устаревший контракт |
| RK verification/reconciliation | HTTP первым; ограниченный read-only browser при inconclusive |
| FP inventory | Полная private inventory первым; partial/error сохраняют browser fallback |
| Create FP | HTTP по наблюдённой схеме; Chromium только получает и отменяет исходный POST при отсутствии схемы |
| Page-access REQUEST | Существующий приватный контракт; owner approval и operator assignment ещё browser |
| Payment | Существующая Meta form; funding inspection/readiness отдельно |

Это законченный перенос транспорта CREATE_RK, а не заявление, что все операции
Meta теперь работают без Chromium. Он внедрён в существующий engine и SQLite
state, без второй альтернативной системы. Успешный CI/health не считается
успешным реальным комплектом: для этого нужен новый live job trace.


## Дополнительный аудит контракта и новый отказ 09:12 Киева

Production bb1f481, job b2117ac1bf5c43c395f92c6e26bd2bed, profile 14:
06:12:39 UTC — Facebook bootstrap из Marketplace;
06:12:41 UTC — обе Business HTML страницы (/latest/home и /create) вернули
400, 1542 байта, без DTSG, без распознанного login/checkpoint redirect.
CREATE_BM_PRE_SUBMIT_TRANSPORT; CREATE POST не отправлен. До загрузки,
отправки и проверки RK-контракта эта задача не дошла. Нельзя называть это
отказом RK-контракта или подтверждённым баном. Предыдущий запасной HTML
маршрут этот реальный запуск не восстановил.

Новая HTTP подготовка различает отказ HTML-маршрута и авторизацию операции:
для одного известного CREATE BM, у которого ещё нет BM scope, допускается
свежий authenticated Facebook document той же сессии. Это новый GET без
повторного CREATE, с проверкой CurrentUserInitialData.USER_ID == c_user,
наличия свежего DTSG и HTTP 200. Старый bootstrap/token сам по себе не
разрешает отправку. Auth challenge, rate limit, proxy/header failure не
разрешают эту ветку. RK сохраняет отдельную проверку точного Business.
Это проверенный локальными HTTP fixtures путь; совместимость реального
Business GraphQL с этим auth context требует live ответа, не выводится из CI.

HTTP GET документов Business теперь передаёт navigation metadata;
JS/query discovery сохраняет обычные GET headers. Диагностика precheck
показывает document_kind и SHA256 тела вместо сырого HTML: можно отличить
proxy/header rejection от Meta error document и сравнить ответы. Классификация
не доказывает причины любого неизвестного HTTP 400 и не записывает cookies.
RK cache resolve теперь отдельно показывает missing/stale/expired/available,
возраст и doc_id без variables или session fields.

## FP CREATE через существующий engine

FanPageContractStore сохраняет наблюдённые category IDs и типизированные
подстановки name/actor/bio/client_mutation_id. Шаблоны разделены по выбранной
категории, TTL 24 часа, запись атомарная 0600. Нельзя взять схему одной категории
и молча использовать её для другой; unknown IDs/auth fields запрещены.

Handler сначала сохраняет старые same-job/cross-job guards. При отсутствии
контракта bounded Chromium lease только заполняет реальную форму и перехватывает
финальный POST: GraphqlMutationCapture отменяет его до Meta. Неизвестный POST
на финальном gate также отменяется и не становится replayable. Отсутствие
пригодной схемы — CONTRACT_UNAVAILABLE/INVALID до отправки, без выдуманного doc_id.

Сохранённая схема исполняется только web.graphql с текущими cookies/proxy/auth.
Durable intent записывается перед POST (legacy PAGE_CREATE_CLICK_INTENT остаётся
именем фазы для совместимости SQLite history, transport=private HTTP). Свежая
управляемая Page inventory должна уникально подтвердить имя и новый Page ID,
а при известном ответе совпасть с response ID. Один ответ с Page ID не позволяет
COMMIT при недоступном inventory. Partial response может быть подтверждён
inventory, но не сертифицирует response schema. Timeout сохраняет неизвестный
результат и существующую reconciliation-защиту, не переключает на browser POST.

Поведенческие fixtures проверяют настоящий FacebookWebSession с HTTP peer,
intent-before-POST, одну отправку, независимую verification, неизвестный ответ,
partial response, отказ записи intent, current actor и category isolation.
Cache-hit execution проходит с явным запретом Chromium CREATE. Отдельный
browser-method fixture проверяет route.abort и отсутствие submit callback
при schema capture. Старые reconciliation tests выполняют свой state fixture
через границу нового executor, без сохранения legacy mutation в production.

## Незавершённые контракты

Этот выпуск не переводит owner approval, operator assignment и привязку карты
на HTTP. Оригинальный runtime был извлечён и проверен: он содержит generic
PrivateApiPost и payUnsettled schema, но не пригодные схемы этих операций.
Page access request в текущем коде просит ADVERTISE; это не full control и
не ownership transfer. Approval требует свежего request ID и owner actor proof;
assignment — точной person/asset relation и проверенных task rights.
Нельзя статически переносить request/person ID из чужого профиля.
Card binding требует свежего Meta payment encryption/session protocol,
проверки exact RK + masked instrument и отдельного 3DS состояния; нельзя
придумать контракт из одного doc_id или повторно использовать чужой ciphertext.
До наблюдения и проверки этих схем полный private-only Prepare + Payment
не подтверждён. Browser capture и read-only fallback остаются явно указанными
границами, а не скрываются под названием HTTP.
