<?php
declare(strict_types=1);
require_once dirname(__DIR__).'/railway-meta-official-fields.php';

function assert_budget(bool $value, string $message): void {
    if (!$value) throw new RuntimeException($message);
}
function fixture_budget(string $section, string $field, mixed $value): array {
    $payload=[
        'campaign'=>['name'=>'UI campaign','objective'=>'OUTCOME_TRAFFIC'],
        'adset'=>[
            'name'=>'UI adset','optimization_goal'=>'LINK_CLICKS',
            'billing_event'=>'IMPRESSIONS',
            'targeting'=>['geo_locations'=>['countries'=>['UA']]],
        ],
        'creative'=>['name'=>'UI creative'],
        'ad'=>['name'=>'UI ad'],
    ];
    $payload[$section][$field]=$value;
    return $payload;
}

foreach ([['campaign','daily_budget'],['campaign','lifetime_budget'],
          ['adset','daily_budget'],['adset','lifetime_budget']] as [$section,$field]) {
    foreach ([1200, '1200', '0001200', 9007199254740991, '9007199254740991'] as $value) {
        $result=MetaOfficialFields::validateAndNormalizePayload(fixture_budget($section,$field,$value));
        $expected=(int)$value;
        assert_budget($result[$section][$field] === $expected,
            "$section.$field must preserve its exact selected minor-unit amount");
        assert_budget($result['_meta_official_v1'] === true, 'Official payload marker missing');
    }
    foreach ([0, -1, 1200.5, '1200.5', '1e3', '12e2', '0', '',
              '9007199254740992', 9007199254740992, true, false, [], 'invalid'] as $value) {
        // An empty optional budget is not an active selection; all other
        // budgets still missing must fail exactly-one-budget validation.
        try {
            MetaOfficialFields::validateAndNormalizePayload(fixture_budget($section,$field,$value));
            throw new RuntimeException("$section.$field accepted invalid budget ".json_encode($value));
        } catch (InvalidArgumentException $expected) {
            assert_budget(str_contains($expected->getMessage(), 'budget') ||
                str_contains($expected->getMessage(), 'positive integer'), 'Unexpected budget error');
        }
    }
}
// The PHP builder bridge must preserve and validate the amount from the job.
$payload=fixture_budget('campaign','daily_budget',1200);
$fromUi=MetaOfficialFields::applyBuilderToPayload($payload,[
    'campaign'=>['daily_budget'=>'1500'], 'targeting'=>['exclusions'=>['interests'=>[['id'=>'123456']]]],
]);
$validated=MetaOfficialFields::validateAndNormalizePayload($fromUi);
assert_budget($validated['campaign']['daily_budget']===1500, 'Selected UI budget lost in PHP builder');
assert_budget($validated['adset']['targeting']['exclusions']['interests'][0]['id']==='123456',
    'Selected targeting lost during budget normalization');
echo "Meta official Launch budget parser preserves exact integers and rejects lossy inputs.\n";
