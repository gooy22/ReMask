<?php
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/RemaskPrivateFormSchema.php';
try {
    // Static reviewed metadata: editing a template never opens a FB session.
    MetaEndpoint::ok(RemaskPrivateFormSchema::load() + ['template_account_bound'=>false]);
} catch (Throwable $error) { MetaEndpoint::fail($error); }
