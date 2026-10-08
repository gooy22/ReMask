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

| Операция | Исполнение после изменения |
| --- | --- |
| Create BM | Прямой cookie/proxy GraphQL, без browser POST fallback |
| Create RK | Прямой HTTP по наблюдённому контракту; Chromium получает отсутствующий/устаревший контракт |
| RK verification/reconciliation | HTTP первым; ограниченный read-only browser при inconclusive |
| FP inventory | Полная private inventory первым; partial/error сохраняют browser fallback |
| Create FP | Существующий браузерный CREATE и защита от дублей |
| Page-access REQUEST | Существующий приватный контракт; owner approval и operator assignment ещё browser |
| Payment | Существующая Meta form; funding inspection/readiness отдельно |

Это законченный перенос транспорта CREATE_RK, а не заявление, что все операции
Meta теперь работают без Chromium. Он внедрён в существующий engine и SQLite
state, без второй альтернативной системы. Успешный CI/health не считается
успешным реальным комплектом: для этого нужен новый live job trace.
