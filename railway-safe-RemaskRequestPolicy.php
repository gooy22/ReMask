<?php
declare(strict_types=1);
final class RemaskRequestPolicy
{
    /** State-changing AJAX calls are POST-only, including authenticated internal callers. */
    public static function enforce(string $endpoint): void
    {
        $method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
        if ($method === 'POST') return;
        $reads = [
            'creativeLibrary.php'=>['','list','get'], 'metaDrafts.php'=>['','list','get'], 'metaMediaLibrary.php'=>['','list','get'],
            'metaBundles.php'=>['','list','get'], 'metaHierarchy.php'=>['','inventory','sync_result','snapshot_profile','funding_status'],
            'pythonWorkerJobs.php'=>['','csrf','health','ready','payment_status','funding_status','preflight','profile_state','docids','private_launch_contracts','private_launch_sources','status','get'],
            'pythonWorkerState.php'=>['','health','list','get'], 'pythonProfileContext.php'=>['','list','resolve'],
            'paymentCards.php'=>['','list'],
        ];
        foreach (['creativePreview.php','metaActivity.php','metaAssetManager.php','metaAssetReadiness.php','metaAssets.php',
            'metaAudienceEstimate.php','metaBundleMedia.php','metaCreativePreview.php','metaJobGet.php','metaJobItemInsights.php',
            'metaJobsList.php','metaPlacementCapabilities.php','metaPreflight.php','metaTargetingSearch.php',
            'metaTemplateAdvertisingHelper.php','metaTemplateTargetingSearch.php','metaUsage.php','metaWorkspace.php',
            'pythonWorkerBusinesses.php','pythonWorkerPages.php','getAccountStatistics.php'] as $name) $reads[$name] = ['','list','get'];
        $action = strtolower(trim((string)($_GET['action'] ?? '')));
        if ($method === 'GET' && isset($reads[$endpoint]) && in_array($action, $reads[$endpoint], true)) return;
        http_response_code(405);
        header('Allow: POST');
        header('Content-Type: application/json; charset=utf-8');
        header('Cache-Control: no-store');
        echo json_encode(['ok'=>false,'error'=>['code'=>'METHOD_NOT_ALLOWED','message'=>'Use POST with a valid CSRF token for changes.']]);
        exit;
    }
}
