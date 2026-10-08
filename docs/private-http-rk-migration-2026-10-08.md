# ReMask: исполнение Create RK через прямой HTTP

Этот файл сохраняет историю миграции. Актуальная production-регистрация БМ/РК
описана в последних разделах: она не использует Chromium даже для получения
контракта. Описание UI capture в ранних разделах относится к предыдущим этапам.

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


## Outbound HTTP/2 transport

The shared `FacebookWebSession` previously used aiohttp's HTTP/1.1 default.
It now uses a profile-scoped HTTPX 0.28.1 transport with HTTP/2 enabled,
connection pooling, HTTP/SOCKS proxy support, environment proxy settings disabled,
and zero configured transport retries. HTTP/2 is negotiated with the origin;
a proxy CONNECT request can correctly remain HTTP/1.1. A server without HTTP/2
uses HTTP/1.1 on the same request, without an application-level retry.

All existing private GraphQL and HTML requests in this shared session use the
new transport. Existing bridge/resolver/internal HTTP clients remain independent.
The state machine and durable-before-submit boundary are unchanged. POST
redirects are not followed. Stream resets/timeouts after submit remain ambiguous
and require reconciliation. Whole-request deadlines include streaming bodies;
HTML reads remain bounded and text responses have a 16 MiB decompressed budget.
Imported cookies are Secure and scoped to `.facebook.com`; Set-Cookie updates
remain within the individual client's cookie jar. Credentials are not sent to
IP-check or fbcdn hosts. Diagnostics record profile, method, host, HTTP status
and negotiated response version, without query strings, cookies, or proxy secrets.

Ten transport regressions cover real TLS ALPN HTTP/2 through a local HTTP/1.1
CONNECT tunnel with connection reuse, HTTP/1.1-only fallback, profile isolation,
Set-Cookie refresh, bounded streaming, safe diagnostics, proxy-change rejection,
POST redirect protection, body deadlines, and durable intent / lost responses.
No live Meta success or immunity from checkpoints is implied by these tests.

Latest observed live job on the preceding 02ba0cd deployment:
`f5c5e3f4fc8a4df0acb50014bf60fb74`, accepted 2026-10-08 07:03:54 UTC.
Profile 14 stopped at Business authentication with SESSION_EXPIRED, before POST.
Profile 15 reached BM 1428816905866955 but its RK cache was missing and bounded
schema capture stopped on the Details screen with `ad_account_wizard_action_missing`,
no CREATE attempted. HTTP/2 does not resolve these semantic/capture failures by
itself. No working RK schema was observed in that job.


## Production BM/RK HTTP-only entrypoints (supersedes earlier fallback table)

Latest job d45c6c11ba594bcc86a7d04e00c2980f on 60e1026, 07:28 UTC: Meta GETs
negotiated HTTP/2. Profile 14 stopped at Business session verification before
POST. Profile 15 had no cached RK schema and still called the Chromium cold
capture path twice, failing on Details. No RK CREATE was attempted.

The production registry now binds BUSINESS and AD_ACCOUNT to
provisioning/private_create_handlers.py. These handlers have no browser factory,
lease, UI action or native browser GraphQL call. Legacy UI handlers remain as
compatibility code for historical observers and regression fixtures; the registry
no longer executes them. All BM/RK jobs select private-only transport. The
low-level BM submit function also removes its browser compatibility POST branch.

Cold RK discovery uses authenticated HTML and Meta JS GETs with a 55-second,
16 MB aggregate budget. Tree-sitter requires the exact persisted Relay artifact
and sender variables. Unique immutable import/input aliases can be resolved;
reassignment, shadowing, spreads, computed properties, unknown runtime fields and
conflicting schemas are rejected. JavaScript is never evaluated. Profile values
are typed bindings; cookies, auth fields and opaque foreign identities cannot
enter the stored template. Unobserved input fields are not invented. Unsupported
module shapes return PRIVATE_AD_ACCOUNT_CONTRACT_UNAVAILABLE before POST.

Both actions persist CREATE_SUBMIT_INTENT before POST. Response IDs remain
unverified until separate HTTP inventory proves their exact scope. Lost responses
enter VERIFY, not another CREATE. New Jobs and new prechecks cannot erase an
older possibly submitted intent. Complete empty inventory immediately after an
ambiguous submit does not authorize re-submission. Last confirmed entities are
not deleted on transient failures. RK commits preserve the BM-to-RK binding;
BM proof survives service.complete so Prepare/Workspace can use it.

Auth checks match actual login forms/redirects rather than incidental JavaScript
login_form references. Safe Business precheck diagnostics identify the auth
reason. This addresses false positives, but the old profile 14 log does not
prove that this was its cause.

HTTPX already negotiates HTTP/2 and decodes supported response compression.
HPACK compresses headers, not the GraphQL body; unverified request-body gzip is
not introduced. Tests exercise the production registry, real FacebookWebSession
HTTP fixtures, cold discovery, intent storage failure, independent inventory,
lost responses and cross-Job reconciliation. CI/deployment success is not a live
Meta bundle success. Page ownership/full operator rights and card attachment
remain separate contracts and are not certified by this BM/RK change.


## HTTP readiness и ограничения времени после 085c3ea

Проверка `/api/v1/profiles/{profile_id}/preflight?purpose=business` теперь
использует тот же профильный HTTP транспорт: proxy check, текущий actor/DTSG,
полноту BM inventory и доступность текущего либо подтверждённого кандидата
CREATE. Никакой browser factory, Page inventory, Graph API access token или
CREATE POST в этой проверке нет. Это предварительная готовность, а не
доказательство разрешения Meta на создание. Checkpoint/login при discovery
не скрывается сохранённым контрактом. Последние сохранённые страницы остаются
в ответе с явным `pages_source=saved_profile_pages`, без заявлений о live sync.

`/ready` публикует `private_http_contract_v1` и transport для BM/RK вместо
устаревшего `business_suite_ui_v1`. Бюджет каждого HTTP шага BM/RK по умолчанию
240 секунд и не зависит от размера Chromium pool или количества профилей.
Настройки `REMASK_PRIVATE_BUSINESS_STEP_TIMEOUT` и
`REMASK_PRIVATE_AD_ACCOUNT_STEP_TIMEOUT` задают отдельные HTTP бюджеты.
Standalone `REMASK_ADD_FP/BM/RK_HARD_TIMEOUT_SECONDS` сохранены. FP/PageAccess
в смешанных задачах получают свой прежний UI budget; это не заявление о
полном удалении Chromium из FP/PageAccess/payment.

Разбор текущих JS модулей RK дополнен схемой RelayHooks.useMutation:
immutable first-return commit binding должен указывать на точный graphql
artifact. Переназначение, shadowing, другой hook/module/artifact, неизвестные
runtime-поля и отсутствующий sender не дают контракт. JS не исполняется;
распознавание покрыто синтетическими модулями, а не выдано за наблюдение
нового живого контракта Meta.

Проверки охватывают API entrypoint без browser startup, отсутствие секретов
в preflight, inventory/contract readiness, checkpoint в позднем discovery,
независимость HTTP таймаутов от 1/100/500 workers и неизменяемость hook binding.
После deployment 085c3ea/d327ab78 в доступных логах нет нового Prepare/CREATE
trace. Успешный CI и healthcheck не заменяют live Meta verification.
