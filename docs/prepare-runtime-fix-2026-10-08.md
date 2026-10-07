# Prepare: исправление по runtime trace 7–8 октября 2026

## Подтверждённая попытка на production

Источник: Railway runtime deployment b0934c2e-0814-4b5a-978d-d2f6d811cead, commit 554fff1.
Job: 9e28820527c6440c863c294589c2c6fd. Время ниже — UTC; Киев +3 часа.

| Профиль | Время | Подтверждённое событие |
|---|---|---|
| 14 | 22:06:29 | Business bootstrap не получен; общий bootstrap взят с www.facebook.com/marketplace/ |
| 14 | 22:06:30 | CREATE_BM получил HTTP-успешный JSON с error=1357001, «Log in to continue» / «Not logged in». ReMask ошибочно показал CREATE_BM_META_ERROR |
| 15 | 22:06:39 | Private CREATE_BM успешно вернул data.bizkit_create_business.id = 1428816905866955 |
| 15 | 22:09:06 | Открыт Add-RK для этого BM, currency=USD, timezone_id=137 |
| 15 | 22:09:52 | AD_ACCOUNT_CREATE_REQUEST_NOT_OBSERVED. Открыты одновременно «Add an ad account» и форма Details. Вместо Next выбран gridcell «Create a new ad account» из перекрытого окна. Locator.click завершился таймаутом; graphql_candidates=[] |
| 15 | 22:12:13 | Item завершён AD_ACCOUNT_LIVE_CAPTURE_TIMEOUT после reconciliation |

Это разные причины отказа. Ответ профиля 14 подтверждает требование авторизации Business; он не доказывает бан или лимит BM. Для профиля 15 успешный CREATE_BM подтверждён, успешный комплект не подтверждён.

## Причины в коде и итоговая правка

1. _click_ad_account_form_action_by_visible_text выбирал первый dialog с подходящим текстом. Meta сохраняет chooser в DOM под новым окном Details. Теперь выбирается окно с доступным действием; elementFromPoint исключает перекрытые элементы. Порядок диалогов в DOM не определяет правильную кнопку.
2. _click_ad_account_final_interactive разрешал startswith(Create + пробел). Пункт «Create a new ad account» проходил как финальный CREATE. Теперь сопоставление полное, aria-label/title/text проверяются отдельно; gridcell/row/menuitem не являются финальной кнопкой. Тот же запрет действует в резервном semantic обработчике и локализованных формах.
3. GraphqlMutationCapture устанавливал route с glob **/*graphql*. Один завершающий * не покрывает слеш в /api/graphql/. Старые unit tests вызывали handler напрямую и не обнаруживали дефект регистрации маршрута. Теперь общий GRAPHQL_ROUTE_PATTERN покрывает URL со слешем, параметрами и без слеша, используется одинаково для route/unroute и legacy Add-RK gate.
4. Capture checkpoint ранее ставился до поиска реальной кнопки. Теперь callback сохраняет CREATE_CLICK_INTENT и включает interception непосредственно перед единственным физическим кликом. Нет доступного Next/Create — нет отправки и нет ложного pending CREATE.
5. Общий bootstrap с Marketplace ранее считался достаточным для Business mutation. При таком fallback выполняется отдельная read-only проверка Business. Успешная проверка обновляет DTSG/LSD и envelope от текущего Business документа; login/checkpoint останавливает операцию до GraphQL POST.
6. JSON error=1357001 при HTTP 200 теперь инвалидирует bootstrap и становится SESSION_EXPIRED с явным request_rejected. Business checkpoint сохраняет CREATE_REJECTED либо CREATE_NOT_SUBMITTED при отказе до POST. Generic error=1357054 не переименовывается в ошибку сессии без доказательств.

## Проверки, которые предотвращают повтор этой регрессии

tests/meta-ad-account-dialogs.browser.py исполняет настоящие методы в Chromium на локальной структуре Meta dialog. Все запросы создаются синтетически и перехватываются; аккаунты, cookies и платёжные данные не используются.

Покрыты два порядка диалогов, foreground Next, ошибочный chooser, локализованный gridcell, финальный CTA после 120 глобальных элементов, дублирование aria-label/text, отключённый Next без checkpoint и полный Details → Confirm → перехват ровно одного CREATE. Отдельно проверяются GraphQL endpoints /api/graphql/, /api/graphql/?method=post и /api/graphql.

В CI старый foreground-dialog сценарий обязан воспроизвести отказ на 554fff1, новая версия обязана пройти все 11 браузерных случаев. Общий backend, Node UI и PHP runtime/payment guards остаются обязательными. Сохранён тест dispatched-click timeout: возможная отправка наблюдается без второго клика.

## Граница подтверждения

Пройденный локальный браузерный сценарий доказывает исправление воспроизведённых дефектов выбора и перехвата. Он не доказывает готовность реального профиля 14 или успешный полный комплект на Meta. Истёкшая/отклонённая авторизация требует восстановления действительной сессии; код не должен маскировать её под CREATE_RESULT_UNKNOWN.

Предыдущие неизвестные отправки остаются под защитой от дублей: один новый selector или отсутствие наблюдённого запроса не доказывает отсутствия объекта. Reconciliation сохраняет последний подтверждённый BM/RK и требует фактического доказательства результата.

Сохраняется существующая модель: отдельные bundles профиля, один BM + один RK на bundle, cookie private transport, ограниченный browser capture/verification. Этот patch устраняет доказанные runtime дефекты существующего движка.
