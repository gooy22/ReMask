/* REMASK_PRIVATE_LAUNCH_CATALOG_V1 */
function remaskFundingState(info) {
    if (!info) return {label:'NOT LOADED',cls:'muted'};
    if (info.error) return {label:'ERROR',cls:'job-failed'};
    const data=info.data||{};
    if (data.funding_verified === false || data.verification_status === 'NOT_CHECKED')
        return {label:'NOT CHECKED',cls:'muted'};
    if (data._cache?.stale) return {label:'STALE',cls:'muted'};
    const accountStatus=data.account_status;
    if (accountStatus === undefined || accountStatus === null || accountStatus === '')
        return {label:'UNKNOWN',cls:'muted'};
    if (Number(accountStatus)!==1) return {label:'ACCOUNT '+accountStatus,cls:'job-failed'};
    if (data.expired_funding_source_details && Object.keys(data.expired_funding_source_details).length)
        return {label:'EXPIRED',cls:'job-failed'};
    if (data.is_prepay_account) {
        const balance=Number(data.balance);
        return Number.isFinite(balance) && balance>0
            ? {label:'PREPAY BALANCE',cls:'muted'}
            : {label:'PREPAY EMPTY',cls:'muted'};
    }
    if (data.funding_source_details?.id || data.funding_source)
        return {label:'SOURCE SAVED',cls:'muted'};
    return {label:'UNKNOWN',cls:'muted'};
}
fundingState=remaskFundingState;
fundingReviewLabel=function(funding) {
    return remaskFundingState(funding ? {data:funding} : null).label;
};
$('preflight').textContent='Загрузить сохранённые РК';
$('syncMeta').textContent='Обновить список';
const remaskFundingButton=$('loadFunding');
if (remaskFundingButton) remaskFundingButton.textContent='Показать состояние оплаты';
const remaskOriginalLoadFunding=loadFunding;
loadFunding=async function(force=false) {
    await remaskOriginalLoadFunding(force);
    $('fundingStatus').textContent='Платёжная привязка не проверена. Сохранённый РК или funding ID не подтверждает готовность карты.';
};

// Catalog selection cannot unlock the legacy token-based review or ad submission.
const remaskPrivateLaunchNotice='РК и FP загружены из сохранённых данных. Проверка доступа FP для рекламы и привязки карты ещё не выполнена. Запуск рекламы недоступен до этих проверок.';
const remaskOriginalValidateReady=validateReady;
validateReady=function() {
    remaskOriginalValidateReady();
    for (const id of ['reviewLaunch','launchButton','serverDryRun','dryRunPlan']) {
        const button=$(id);
        if (button) { button.disabled=true; button.title=remaskPrivateLaunchNotice; }
    }
};
validateReady();
if ($('reviewStatus')) $('reviewStatus').textContent=remaskPrivateLaunchNotice;
