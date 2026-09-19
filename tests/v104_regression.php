<?php
declare(strict_types=1);

$runtime = getenv('REMASK_TEST_RUNTIME') ?: '/var/www/html';
require_once $runtime . '/classes/MetaOfficialFields.php';
require_once $runtime . '/classes/MetaFundingGuard.php';

$failures = [];
$assert = static function (bool $condition, string $message) use (&$failures): void {
    if (!$condition) $failures[] = $message;
};
$mustThrow = static function (callable $fn, string $expected, string $label) use (&$failures): void {
    try {
        $fn();
        $failures[] = $label . ': expected exception';
    } catch (Throwable $e) {
        if (!str_contains($e->getMessage(), $expected)) {
            $failures[] = $label . ': unexpected message: ' . $e->getMessage();
        }
    }
};

foreach ([
    ['funding_source' => '123'],
    ['funding_source_details' => ['id' => '456', 'status' => 'ACTIVE']],
    ['is_prepay_account' => true],
] as $index => $ready) {
    try {
        MetaFundingGuard::assertLaunchReady($ready);
    } catch (Throwable $e) {
        $failures[] = "ready funding case {$index} rejected: " . $e->getMessage();
    }
}

$mustThrow(
    static fn() => MetaFundingGuard::assertLaunchReady([]),
    'NO ACTIVE FUNDING SOURCE',
    'missing funding'
);
$mustThrow(
    static fn() => MetaFundingGuard::assertLaunchReady(['funding_source' => ['unexpected' => 'shape']]),
    'NO ACTIVE FUNDING SOURCE',
    'malformed funding id'
);
$mustThrow(
    static fn() => MetaFundingGuard::assertLaunchReady(['expired_funding_source_details' => ['id' => '1']]),
    'PAYMENT ISSUE',
    'expired funding'
);
$mustThrow(
    static fn() => MetaFundingGuard::assertLaunchReady(['funding_source_details' => ['id' => '1', 'status' => 'DISABLED']]),
    'PAYMENT ISSUE',
    'disabled funding'
);

$payload = MetaOfficialFields::applyBuilderToPayload([
    'campaign' => [],
    'adset' => ['targeting' => []],
    'creative' => ['name' => 'System creative'],
    'ad' => [],
], [
    'creative' => [
        'name' => 'SDK override',
        'object_story_spec' => ['page_id' => 'attacker-controlled'],
        'image_hash' => 'wrong-image',
        'source_instagram_media_id' => 'wrong-post',
        'degrees_of_freedom_spec' => ['creative_features_spec' => []],
    ],
]);
$safeCreative = MetaOfficialFields::generatedStoryCreativeParams($payload['creative']);
$assert(!isset($safeCreative['name']), 'generated creative name must be system-owned');
$assert(!isset($safeCreative['object_story_spec']), 'object_story_spec must be system-owned');
$assert(!isset($safeCreative['image_hash']), 'image_hash must be system-owned');
$assert(!isset($safeCreative['source_instagram_media_id']), 'Instagram source must be system-owned');
$assert(isset($safeCreative['degrees_of_freedom_spec']), 'safe SDK creative field was dropped');

if ($failures !== []) {
    fwrite(STDERR, "v104 regression failures:\n- " . implode("\n- ", $failures) . "\n");
    exit(1);
}

fwrite(STDOUT, "v104 regression checks passed\n");
