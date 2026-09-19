<?php

/** Launch funding policy shared by review and regression tests. */
final class MetaFundingGuard
{
    public static function assertLaunchReady(array $funding): void
    {
        if (!empty($funding['expired_funding_source_details'])) {
            throw new InvalidArgumentException('PAYMENT ISSUE: Meta reports an expired funding source.');
        }

        $details = is_array($funding['funding_source_details'] ?? null)
            ? $funding['funding_source_details']
            : [];
        $detailsStatus = strtoupper(trim((string)($details['status'] ?? '')));
        if (in_array($detailsStatus, ['EXPIRED', 'DISABLED', 'FAILED', 'INVALID'], true)) {
            throw new InvalidArgumentException('PAYMENT ISSUE: funding source status is ' . $detailsStatus . '.');
        }

        $rawFundingId = $funding['funding_source'] ?? ($details['id'] ?? '');
        $fundingId = is_scalar($rawFundingId) ? trim((string)$rawFundingId) : '';
        $isPrepay = filter_var($funding['is_prepay_account'] ?? false, FILTER_VALIDATE_BOOLEAN);
        if ($fundingId === '' && !$isPrepay) {
            throw new InvalidArgumentException('NO ACTIVE FUNDING SOURCE: attach a valid payment method in Meta before Launch.');
        }
    }
}
