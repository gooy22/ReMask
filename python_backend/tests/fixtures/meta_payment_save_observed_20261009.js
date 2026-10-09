__d("BillingAddCreditCardScreenQuery_facebookRelayOperation",[],(function(t, n, r, o, a, i) {
    a.exports = "27759194723782263"
}
));
__d("BillingAddCreditCardScreenQuery.graphql",[],(function(t, n, r, o, a, i) {
    "use strict";
    var e = (function() {
        var e = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "country"
        }
          , t = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "currency"
        }
          , r = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "intent"
        }
          , o = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "paymentAccountID"
        }
          , a = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "primary_email",
            storageKey: null
        }
          , i = {
            kind: "InlineFragment",
            selections: [{
                alias: null,
                args: null,
                concreteType: "Phone",
                kind: "LinkedField",
                name: "most_recently_verified_cell_phone",
                plural: !1,
                selections: [{
                    alias: null,
                    args: null,
                    concreteType: "PhoneNumber",
                    kind: "LinkedField",
                    name: "phone_number",
                    plural: !1,
                    selections: [{
                        alias: null,
                        args: null,
                        kind: "ScalarField",
                        name: "universal_number",
                        storageKey: null
                    }],
                    storageKey: null
                }],
                storageKey: null
            }],
            type: "User",
            abstractKey: null
        }
          , l = [{
            kind: "Variable",
            name: "legacy_account_id",
            variableName: "paymentAccountID"
        }]
          , s = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "__typename",
            storageKey: null
        }
          , u = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "is_using_ec",
            storageKey: null
        }
          , c = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "payment_modes",
            storageKey: null
        }
          , d = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "is_eligible_for_postpay_upgrade",
            storageKey: null
        }
          , m = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "name",
            storageKey: null
        }
          , p = {
            alias: null,
            args: null,
            concreteType: "BillingSafeModeState",
            kind: "LinkedField",
            name: "billing_safe_mode_state",
            plural: !1,
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "status",
                storageKey: null
            }],
            storageKey: null
        }
          , _ = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "has_cvco_restriction",
            storageKey: null
        }
          , f = [{
            kind: "Variable",
            name: "country",
            variableName: "country"
        }, {
            kind: "Variable",
            name: "currency",
            variableName: "currency"
        }, {
            kind: "Variable",
            name: "intent",
            variableName: "intent"
        }]
          , g = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "can_save_to_business",
            storageKey: null
        }
          , h = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "check_make_default",
            storageKey: null
        }
          , y = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "id",
            storageKey: null
        }
          , C = [{
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "amount_with_offset",
            storageKey: null
        }, {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "currency",
            storageKey: null
        }];
        return {
            fragment: {
                argumentDefinitions: [e, t, r, o],
                kind: "Fragment",
                metadata: null,
                name: "BillingAddCreditCardScreenQuery",
                selections: [{
                    alias: null,
                    args: null,
                    concreteType: "Viewer",
                    kind: "LinkedField",
                    name: "viewer",
                    plural: !1,
                    selections: [a, {
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "actor",
                        plural: !1,
                        selections: [i],
                        storageKey: null
                    }],
                    storageKey: null
                }, {
                    alias: null,
                    args: l,
                    concreteType: null,
                    kind: "LinkedField",
                    name: "payment_account",
                    plural: !1,
                    selections: [{
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "billable_account",
                        plural: !1,
                        selections: [s, u, {
                            args: null,
                            kind: "FragmentSpread",
                            name: "useBillingAddCreditCard_info"
                        }, {
                            args: null,
                            kind: "FragmentSpread",
                            name: "useBillingLandingFooter_data"
                        }, c, d, {
                            alias: null,
                            args: null,
                            concreteType: "BusinessPaymentAccount",
                            kind: "LinkedField",
                            name: "owner_business_payment_account",
                            plural: !1,
                            selections: [{
                                alias: null,
                                args: null,
                                concreteType: "AdBusiness",
                                kind: "LinkedField",
                                name: "business",
                                plural: !1,
                                selections: [m],
                                storageKey: null
                            }],
                            storageKey: null
                        }, {
                            kind: "InlineFragment",
                            selections: [p, _],
                            type: "AdAccount",
                            abstractKey: null
                        }],
                        storageKey: null
                    }, s, {
                        alias: null,
                        args: f,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "billing_payment_method_options",
                        plural: !0,
                        selections: [s, {
                            kind: "InlineFragment",
                            selections: [{
                                args: null,
                                kind: "FragmentSpread",
                                name: "BillingAddCreditCardView_cards"
                            }, g, h],
                            type: "AdAccountNewCreditCardOption",
                            abstractKey: null
                        }],
                        storageKey: null
                    }],
                    storageKey: null
                }],
                type: "Query",
                abstractKey: null
            },
            kind: "Request",
            operation: {
                argumentDefinitions: [o, e, t, r],
                kind: "Operation",
                name: "BillingAddCreditCardScreenQuery",
                selections: [{
                    alias: null,
                    args: null,
                    concreteType: "Viewer",
                    kind: "LinkedField",
                    name: "viewer",
                    plural: !1,
                    selections: [a, {
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "actor",
                        plural: !1,
                        selections: [s, i, y],
                        storageKey: null
                    }],
                    storageKey: null
                }, {
                    alias: null,
                    args: l,
                    concreteType: null,
                    kind: "LinkedField",
                    name: "payment_account",
                    plural: !1,
                    selections: [{
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "billable_account",
                        plural: !1,
                        selections: [s, u, {
                            kind: "TypeDiscriminator",
                            abstractKey: "__isBillableAccount"
                        }, {
                            alias: null,
                            args: null,
                            concreteType: "BusinessPaymentAccount",
                            kind: "LinkedField",
                            name: "owner_business_payment_account",
                            plural: !1,
                            selections: [y, {
                                alias: null,
                                args: null,
                                concreteType: "AdBusiness",
                                kind: "LinkedField",
                                name: "business",
                                plural: !1,
                                selections: [m, y],
                                storageKey: null
                            }],
                            storageKey: null
                        }, {
                            alias: null,
                            args: null,
                            concreteType: null,
                            kind: "LinkedField",
                            name: "billable_account_tax_info",
                            plural: !1,
                            selections: [s, {
                                alias: null,
                                args: null,
                                kind: "ScalarField",
                                name: "business_country_code",
                                storageKey: null
                            }, {
                                alias: null,
                                args: null,
                                kind: "ScalarField",
                                name: "predicated_business_country_code",
                                storageKey: null
                            }, {
                                alias: null,
                                args: null,
                                kind: "ScalarField",
                                name: "entity",
                                storageKey: null
                            }, {
                                alias: null,
                                args: null,
                                kind: "ScalarField",
                                name: "can_update_tax_country",
                                storageKey: null
                            }],
                            storageKey: null
                        }, {
                            alias: null,
                            args: [{
                                kind: "Literal",
                                name: "requested_flags",
                                value: ["TAX_COUNTRY_MISMATCH"]
                            }],
                            kind: "ScalarField",
                            name: "billing_flags",
                            storageKey: 'billing_flags(requested_flags:["TAX_COUNTRY_MISMATCH"])'
                        }, {
                            alias: null,
                            args: null,
                            kind: "ScalarField",
                            name: "application_type",
                            storageKey: null
                        }, d, {
                            alias: null,
                            args: null,
                            kind: "ScalarField",
                            name: "payment_item_type",
                            storageKey: null
                        }, c, {
                            alias: null,
                            args: null,
                            concreteType: "CurrencyAmount",
                            kind: "LinkedField",
                            name: "available_funds",
                            plural: !1,
                            selections: C,
                            storageKey: null
                        }, {
                            kind: "InlineFragment",
                            selections: [_, p],
                            type: "AdAccount",
                            abstractKey: null
                        }, {
                            alias: null,
                            args: null,
                            kind: "ScalarField",
                            name: "india_emandate_increase_status",
                            storageKey: null
                        }, {
                            alias: null,
                            args: null,
                            concreteType: "CurrencyAmount",
                            kind: "LinkedField",
                            name: "india_emandate_higher_config_amount",
                            plural: !1,
                            selections: C,
                            storageKey: null
                        }, {
                            kind: "InlineFragment",
                            selections: [{
                                alias: null,
                                args: null,
                                kind: "ScalarField",
                                name: "product_variant",
                                storageKey: null
                            }],
                            type: "Company",
                            abstractKey: "__isCompany"
                        }, y],
                        storageKey: null
                    }, s, {
                        alias: null,
                        args: f,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "billing_payment_method_options",
                        plural: !0,
                        selections: [s, {
                            kind: "InlineFragment",
                            selections: [h, g, {
                                alias: null,
                                args: null,
                                kind: "ScalarField",
                                name: "verify_tokenization_required",
                                storageKey: null
                            }, {
                                alias: null,
                                args: [{
                                    kind: "Literal",
                                    name: "limit",
                                    value: 5
                                }, {
                                    kind: "Variable",
                                    name: "payment_legacy_account_id",
                                    variableName: "paymentAccountID"
                                }],
                                concreteType: "Image",
                                kind: "LinkedField",
                                name: "billing_icons",
                                plural: !0,
                                selections: [{
                                    alias: null,
                                    args: null,
                                    kind: "ScalarField",
                                    name: "uri",
                                    storageKey: null
                                }, {
                                    alias: null,
                                    args: null,
                                    kind: "ScalarField",
                                    name: "scale",
                                    storageKey: null
                                }, {
                                    alias: null,
                                    args: null,
                                    kind: "ScalarField",
                                    name: "height",
                                    storageKey: null
                                }, {
                                    alias: null,
                                    args: null,
                                    kind: "ScalarField",
                                    name: "width",
                                    storageKey: null
                                }],
                                storageKey: null
                            }],
                            type: "AdAccountNewCreditCardOption",
                            abstractKey: null
                        }, y],
                        storageKey: null
                    }, y],
                    storageKey: null
                }]
            },
            params: {
                id: n("BillingAddCreditCardScreenQuery_facebookRelayOperation"),
                metadata: {},
                name: "BillingAddCreditCardScreenQuery",
                operationKind: "query",
                text: null
            }
        }
    }
    )();
    a.exports = e
}
));
__d("BillingAddCreditCardState",[],(function(t, n, r, o, a, i, l) {
    "use strict";
    var e, s = e || (e = o("react")), u = (function(e) {
        function t() {
            for (var t, n = arguments.length, a = new Array(n), i = 0; i < n; i++)
                a[i] = arguments[i];
            return t = e.call.apply(e, [this].concat(a)) || this,
            t.name = "add_credit_card_state_display",
            t.mapPropsToQuery = function(e) {
                var t, n = e.country, a = e.currency, i = e.paymentAccountID, l = e.pmCapabilityPaymentIntent;
                return {
                    country: n != null ? n : null,
                    currency: a != null ? a : null,
                    intent: (t = o("enumUtils").enumValueToKey(l, r("pm_capability_PaymentMethodUsabilityIntent"))) != null ? t : null,
                    paymentAccountID: i
                }
            }
            ,
            t.query = o("BillingAddCreditCardScreen.react").query,
            babelHelpers.assertThisInitialized(t) || babelHelpers.assertThisInitialized(t)
        }
        babelHelpers.inheritsLoose(t, e);
        var n = t.prototype;
        return n.onLoaded = function(t) {
            var e = t.isEntrypointWizard;
            e === !0 && o("BillingPTTUtils").init()
        }
        ,
        n.onDisplay = function(t, n) {
            var e = function(t, r, o, a, i, l, s, u, c, d, m, p, _) {
                return n("onNext", {
                    businessPaymentAccountID: m,
                    clientInfo: t,
                    country: r,
                    creditCard: o,
                    fbinEntity: a,
                    hasFunds: i,
                    inCountrySpoofingExperiment: p,
                    makePrimaryCheckbox: d,
                    paymentType: l,
                    productType: s,
                    recurring: u,
                    showAutomaticBillingContent: c,
                    skipCvvForEeaSave: _
                })
            }
              , a = function() {
                return n("onClose")
            }
              , i = function() {
                return r("BillingWizardRootUPLogger").logClickEvent("submit_button", {
                    cta_text: "Change Business Location"
                }),
                n("onChangeBusinessLocation")
            };
            return s.jsx(o("BillingAddCreditCardScreen.react").BillingAddCreditCardScreen, babelHelpers.extends({}, t, {
                onChangeBusinessLocation: i,
                onClose: a,
                onSubmitCard: e
            }))
        }
        ,
        t
    }
    )(o("BillingWizardDisplayState").DisplayState);
    l.default = u
}
));
__d("BillingSaveCardCredentialStateMutation_facebookRelayOperation",[],(function(t, n, r, o, a, i) {
    a.exports = "28619313357728847"
}
));
__d("BillingSaveCardCredentialStateMutation.graphql",[],(function(t, n, r, o, a, i) {
    "use strict";
    var e = (function() {
        var e = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "getRiskVerificationInfoForAllCredentialsOnPaymentAccount"
        }
          , t = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "includeCreateNewFromOldFragment"
        }
          , r = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "input"
        }
          , o = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "paymentAccountID"
        }
          , a = [{
            kind: "Variable",
            name: "data",
            variableName: "input"
        }]
          , i = {
            alias: null,
            args: [{
                kind: "Literal",
                name: "requested_flags",
                value: ["TAX_INFO_PROBLEM_HARD", "TAX_INFO_PROBLEM_SOFT", "TAX_INFO_PROBLEM_SOFT_CRTICAL", "CURRENCY_COUNTRY_MISMATCH"]
            }],
            kind: "ScalarField",
            name: "billing_flags",
            storageKey: 'billing_flags(requested_flags:["TAX_INFO_PROBLEM_HARD","TAX_INFO_PROBLEM_SOFT","TAX_INFO_PROBLEM_SOFT_CRTICAL","CURRENCY_COUNTRY_MISMATCH"])'
        }
          , l = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "__typename",
            storageKey: null
        }
          , s = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "has_acked_soft_tax_info_problem",
            storageKey: null
        }
          , u = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "is_using_ec",
            storageKey: null
        }
          , c = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "currency",
            storageKey: null
        }
          , d = [{
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "amount_with_offset",
            storageKey: null
        }, c]
          , m = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "is_eligible_for_postpay_upgrade",
            storageKey: null
        }
          , p = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "payment_modes",
            storageKey: null
        }
          , _ = {
            alias: null,
            args: null,
            concreteType: "BillingAutoReloadInfo",
            kind: "LinkedField",
            name: "auto_reload_info",
            plural: !1,
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "auto_reload_status",
                storageKey: null
            }],
            storageKey: null
        }
          , f = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "entity",
            storageKey: null
        }
          , g = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "business_country_code",
            storageKey: null
        }
          , h = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "ent_credential_id",
            storageKey: null
        }
          , y = [{
            kind: "Variable",
            name: "legacy_account_id",
            variableName: "paymentAccountID"
        }]
          , C = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "use_case",
            storageKey: null
        }
          , b = {
            kind: "InlineFragment",
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "resolvable_type",
                storageKey: null
            }],
            type: "CVCOCredentialRequiredVerificationInfo",
            abstractKey: null
        }
          , v = {
            kind: "InlineFragment",
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "next_onboarding_step",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "status",
                storageKey: null
            }],
            type: "BillingSafeModeRequiredVerificationInfo",
            abstractKey: null
        }
          , S = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "name",
            storageKey: null
        }
          , R = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "application_type",
            storageKey: null
        }
          , L = {
            alias: null,
            args: null,
            concreteType: "BillingCanCreateNewFromOldInfo",
            kind: "LinkedField",
            name: "can_close_old_and_create_new_billable_account",
            plural: !1,
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "can_create",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "reason",
                storageKey: null
            }],
            storageKey: null
        }
          , E = {
            alias: null,
            args: null,
            concreteType: null,
            kind: "LinkedField",
            name: "billable_account_tax_info",
            plural: !1,
            selections: [g, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "can_update_tax_country",
                storageKey: null
            }],
            storageKey: null
        }
          , k = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "can_update_currency_timezone",
            storageKey: null
        }
          , I = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "is_new_account_in_cnfo",
            storageKey: null
        }
          , T = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "id",
            storageKey: null
        }
          , D = [S, T]
          , x = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "card_association",
            storageKey: null
        }
          , $ = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "card_association_name",
            storageKey: null
        }
          , P = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "credential_id",
            storageKey: null
        }
          , N = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "expiry_month",
            storageKey: null
        }
          , M = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "expiry_year",
            storageKey: null
        }
          , w = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "is_expired",
            storageKey: null
        }
          , A = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "last_four_digits",
            storageKey: null
        }
          , F = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "supports_recurring",
            storageKey: null
        }
          , O = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "bank_name",
            storageKey: null
        }
          , B = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "bank_account_type",
            storageKey: null
        }
          , W = {
            kind: "InlineFragment",
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "email",
                storageKey: null
            }],
            type: "PaymentPaypalBillingAgreement",
            abstractKey: null
        }
          , q = {
            kind: "InlineFragment",
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "legal_entity_name",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "partition_from",
                storageKey: null
            }],
            type: "IEntEC",
            abstractKey: "__isIEntEC"
        }
          , U = {
            kind: "InlineFragment",
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "stored_balance_type",
                storageKey: null
            }, {
                alias: null,
                args: null,
                concreteType: "CurrencyAmount",
                kind: "LinkedField",
                name: "balance_amount",
                plural: !1,
                selections: [{
                    alias: null,
                    args: null,
                    kind: "ScalarField",
                    name: "currency_name",
                    storageKey: null
                }],
                storageKey: null
            }],
            type: "StoredBalance",
            abstractKey: null
        }
          , V = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "display_name",
            storageKey: null
        }
          , H = {
            kind: "InlineFragment",
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "credential_type",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "user_display_name",
                storageKey: null
            }, V],
            type: "LPMCredential",
            abstractKey: null
        }
          , G = {
            kind: "InlineFragment",
            selections: [V],
            type: "AltPayCredential",
            abstractKey: null
        }
          , z = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "async_card_status_ent_id",
            storageKey: null
        }
          , j = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "external_uri",
            storageKey: null
        }
          , K = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "nonce",
            storageKey: null
        }
          , Q = {
            alias: null,
            args: null,
            concreteType: "IframeBrokeredPaymentURLParams",
            kind: "LinkedField",
            name: "params",
            plural: !0,
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "key",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "value",
                storageKey: null
            }],
            storageKey: null
        }
          , X = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "supports_native_otp",
            storageKey: null
        }
          , Y = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "external_reference_id",
            storageKey: null
        }
          , J = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "credential_authentication_id",
            storageKey: null
        }
          , Z = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "india_card_issuer",
            storageKey: null
        }
          , ee = [{
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "uri",
            storageKey: null
        }, {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "scale",
            storageKey: null
        }, {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "height",
            storageKey: null
        }, {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "width",
            storageKey: null
        }]
          , te = [{
            kind: "InlineDataFragmentSpread",
            name: "BillingPaymentIconUtils_data",
            selections: ee,
            args: null,
            argumentDefinitions: []
        }]
          , ne = {
            alias: null,
            args: null,
            kind: "ScalarField",
            name: "card_verification_status",
            storageKey: null
        };
        return {
            fragment: {
                argumentDefinitions: [e, t, r, o],
                kind: "Fragment",
                metadata: null,
                name: "BillingSaveCardCredentialStateMutation",
                selections: [{
                    alias: null,
                    args: a,
                    concreteType: "XFBBillingSaveCardCredentialResponse",
                    kind: "LinkedField",
                    name: "xfb_billing_save_card_credential",
                    plural: !1,
                    selections: [{
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "payment_account",
                        plural: !1,
                        selections: [{
                            kind: "InlineDataFragmentSpread",
                            name: "BillingCheckForRequiredAccountInformationState_paymentAccount",
                            selections: [i, {
                                alias: null,
                                args: null,
                                concreteType: null,
                                kind: "LinkedField",
                                name: "billable_account",
                                plural: !1,
                                selections: [l, s, u],
                                storageKey: null
                            }, s],
                            args: null,
                            argumentDefinitions: []
                        }, {
                            kind: "InlineDataFragmentSpread",
                            name: "BillingCheckMakePrimaryState_paymentAccount",
                            selections: [{
                                alias: null,
                                args: null,
                                concreteType: null,
                                kind: "LinkedField",
                                name: "billable_account",
                                plural: !1,
                                selections: [l, {
                                    alias: null,
                                    args: null,
                                    concreteType: "CurrencyAmount",
                                    kind: "LinkedField",
                                    name: "account_balance",
                                    plural: !1,
                                    selections: [{
                                        kind: "InlineDataFragmentSpread",
                                        name: "BillingCurrencyAmount_amount",
                                        selections: d,
                                        args: null,
                                        argumentDefinitions: []
                                    }],
                                    storageKey: null
                                }, c],
                                storageKey: null
                            }],
                            args: null,
                            argumentDefinitions: []
                        }, {
                            kind: "InlineDataFragmentSpread",
                            name: "BillingDecideShouldShowAutoReloadOptionState_paymentAccount",
                            selections: [{
                                alias: null,
                                args: null,
                                concreteType: null,
                                kind: "LinkedField",
                                name: "billable_account",
                                plural: !1,
                                selections: [m, p, _, {
                                    alias: null,
                                    args: null,
                                    concreteType: null,
                                    kind: "LinkedField",
                                    name: "billable_account_tax_info",
                                    plural: !1,
                                    selections: [f],
                                    storageKey: null
                                }],
                                storageKey: null
                            }],
                            args: null,
                            argumentDefinitions: []
                        }, {
                            condition: "includeCreateNewFromOldFragment",
                            kind: "Condition",
                            passingValue: !0,
                            selections: [{
                                fragment: {
                                    kind: "InlineFragment",
                                    selections: [{
                                        kind: "InlineDataFragmentSpread",
                                        name: "BillingCheckSharedStoredBalanceUtils_account",
                                        selections: [l, {
                                            kind: "InlineFragment",
                                            selections: [{
                                                alias: null,
                                                args: null,
                                                concreteType: null,
                                                kind: "LinkedField",
                                                name: "tax_info",
                                                plural: !1,
                                                selections: [g],
                                                storageKey: null
                                            }],
                                            type: "BusinessPaymentAccount",
                                            abstractKey: null
                                        }],
                                        args: null,
                                        argumentDefinitions: []
                                    }],
                                    type: "PaymentAccount",
                                    abstractKey: "__isPaymentAccount"
                                },
                                kind: "AliasedInlineFragmentSpread",
                                name: "BillingCheckSharedStoredBalanceUtils_account"
                            }]
                        }, {
                            kind: "InlineDataFragmentSpread",
                            name: "BillingCheckRiskState_paymentAccount",
                            selections: [{
                                condition: "getRiskVerificationInfoForAllCredentialsOnPaymentAccount",
                                kind: "Condition",
                                passingValue: !0,
                                selections: [{
                                    alias: "billing_payment_methods_risk",
                                    args: null,
                                    concreteType: "PaymentCredentialDetails",
                                    kind: "LinkedField",
                                    name: "billing_payment_methods",
                                    plural: !0,
                                    selections: [{
                                        alias: null,
                                        args: null,
                                        concreteType: null,
                                        kind: "LinkedField",
                                        name: "credential",
                                        plural: !1,
                                        selections: [h, {
                                            alias: null,
                                            args: y,
                                            concreteType: null,
                                            kind: "LinkedField",
                                            name: "required_risk_verification_info",
                                            plural: !1,
                                            selections: [{
                                                kind: "InlineDataFragmentSpread",
                                                name: "BillingRiskCheckUtils_restriction",
                                                selections: [C, b, v],
                                                args: null,
                                                argumentDefinitions: []
                                            }],
                                            storageKey: null
                                        }],
                                        storageKey: null
                                    }],
                                    storageKey: null
                                }]
                            }],
                            args: [{
                                kind: "Variable",
                                name: "getRiskVerificationInfoForAllCredentialsOnPaymentAccount",
                                variableName: "getRiskVerificationInfoForAllCredentialsOnPaymentAccount"
                            }, {
                                kind: "Variable",
                                name: "paymentAccountID",
                                variableName: "paymentAccountID"
                            }],
                            argumentDefinitions: [e, o]
                        }, l, {
                            kind: "InlineFragment",
                            selections: [{
                                alias: null,
                                args: null,
                                concreteType: "AdBusiness",
                                kind: "LinkedField",
                                name: "business",
                                plural: !1,
                                selections: [S],
                                storageKey: null
                            }],
                            type: "BusinessPaymentAccount",
                            abstractKey: null
                        }, {
                            alias: null,
                            args: null,
                            concreteType: null,
                            kind: "LinkedField",
                            name: "billable_account",
                            plural: !1,
                            selections: [{
                                condition: "includeCreateNewFromOldFragment",
                                kind: "Condition",
                                passingValue: !0,
                                selections: [{
                                    fragment: {
                                        kind: "InlineFragment",
                                        selections: [{
                                            kind: "InlineDataFragmentSpread",
                                            name: "BillingAccountInformationUtilsCreateNewFromOld_account",
                                            selections: [l, R, L, E, c, k, I],
                                            args: null,
                                            argumentDefinitions: []
                                        }],
                                        type: "BillableAccount",
                                        abstractKey: "__isBillableAccount"
                                    },
                                    kind: "AliasedInlineFragmentSpread",
                                    name: "BillingAccountInformationUtilsCreateNewFromOld_account"
                                }]
                            }],
                            storageKey: null
                        }],
                        storageKey: null
                    }, {
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "linked_payment_account",
                        plural: !1,
                        selections: [{
                            alias: null,
                            args: null,
                            concreteType: null,
                            kind: "LinkedField",
                            name: "billable_account",
                            plural: !1,
                            selections: D,
                            storageKey: null
                        }],
                        storageKey: null
                    }, {
                        alias: null,
                        args: null,
                        concreteType: "ExternalCreditCard",
                        kind: "LinkedField",
                        name: "credit_card",
                        plural: !1,
                        selections: [x, $, P, N, M, T, w, A, F, {
                            kind: "InlineDataFragmentSpread",
                            name: "BillingPaymentMethodDisplayUtils_paymentCredential",
                            selections: [{
                                kind: "InlineFragment",
                                selections: [l, {
                                    kind: "InlineFragment",
                                    selections: [$, A],
                                    type: "ExternalCreditCard",
                                    abstractKey: null
                                }, {
                                    kind: "InlineFragment",
                                    selections: [O, B, A],
                                    type: "DirectDebit",
                                    abstractKey: null
                                }, W, q, U, H, G],
                                type: "PaymentCredential",
                                abstractKey: "__isPaymentCredential"
                            }],
                            args: null,
                            argumentDefinitions: []
                        }],
                        storageKey: null
                    }, {
                        alias: null,
                        args: null,
                        concreteType: "XFBBillingCardVerificationResponse",
                        kind: "LinkedField",
                        name: "card_verification",
                        plural: !1,
                        selections: [z, j, K, Q, X, Y, J, Z, {
                            alias: null,
                            args: null,
                            concreteType: "Image",
                            kind: "LinkedField",
                            name: "india_issuer_icon",
                            plural: !1,
                            selections: te,
                            storageKey: null
                        }, {
                            alias: null,
                            args: null,
                            concreteType: "Image",
                            kind: "LinkedField",
                            name: "card_association_icon",
                            plural: !1,
                            selections: te,
                            storageKey: null
                        }, x],
                        storageKey: null
                    }, ne, {
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "risk_verification_info",
                        plural: !1,
                        selections: [C, b],
                        storageKey: null
                    }],
                    storageKey: null
                }],
                type: "Mutation",
                abstractKey: null
            },
            kind: "Request",
            operation: {
                argumentDefinitions: [r, e, o, t],
                kind: "Operation",
                name: "BillingSaveCardCredentialStateMutation",
                selections: [{
                    alias: null,
                    args: a,
                    concreteType: "XFBBillingSaveCardCredentialResponse",
                    kind: "LinkedField",
                    name: "xfb_billing_save_card_credential",
                    plural: !1,
                    selections: [{
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "payment_account",
                        plural: !1,
                        selections: [{
                            kind: "TypeDiscriminator",
                            abstractKey: "__isPaymentAccount"
                        }, i, {
                            alias: null,
                            args: null,
                            concreteType: null,
                            kind: "LinkedField",
                            name: "billable_account",
                            plural: !1,
                            selections: [l, s, u, T, {
                                alias: null,
                                args: null,
                                concreteType: "CurrencyAmount",
                                kind: "LinkedField",
                                name: "account_balance",
                                plural: !1,
                                selections: d,
                                storageKey: null
                            }, c, m, p, _, {
                                alias: null,
                                args: null,
                                concreteType: null,
                                kind: "LinkedField",
                                name: "billable_account_tax_info",
                                plural: !1,
                                selections: [l, f],
                                storageKey: null
                            }, {
                                condition: "includeCreateNewFromOldFragment",
                                kind: "Condition",
                                passingValue: !0,
                                selections: [{
                                    kind: "TypeDiscriminator",
                                    abstractKey: "__isBillableAccount"
                                }, R, L, E, k, I]
                            }],
                            storageKey: null
                        }, s, l, T, {
                            condition: "includeCreateNewFromOldFragment",
                            kind: "Condition",
                            passingValue: !0,
                            selections: [{
                                kind: "InlineFragment",
                                selections: [{
                                    alias: null,
                                    args: null,
                                    concreteType: null,
                                    kind: "LinkedField",
                                    name: "tax_info",
                                    plural: !1,
                                    selections: [l, g],
                                    storageKey: null
                                }],
                                type: "BusinessPaymentAccount",
                                abstractKey: null
                            }]
                        }, {
                            condition: "getRiskVerificationInfoForAllCredentialsOnPaymentAccount",
                            kind: "Condition",
                            passingValue: !0,
                            selections: [{
                                alias: "billing_payment_methods_risk",
                                args: null,
                                concreteType: "PaymentCredentialDetails",
                                kind: "LinkedField",
                                name: "billing_payment_methods",
                                plural: !0,
                                selections: [{
                                    alias: null,
                                    args: null,
                                    concreteType: null,
                                    kind: "LinkedField",
                                    name: "credential",
                                    plural: !1,
                                    selections: [l, h, {
                                        alias: null,
                                        args: y,
                                        concreteType: null,
                                        kind: "LinkedField",
                                        name: "required_risk_verification_info",
                                        plural: !1,
                                        selections: [l, {
                                            kind: "TypeDiscriminator",
                                            abstractKey: "__isBillingRequiredVerificationInfo"
                                        }, C, b, v],
                                        storageKey: null
                                    }, T],
                                    storageKey: null
                                }],
                                storageKey: null
                            }]
                        }, {
                            kind: "InlineFragment",
                            selections: [{
                                alias: null,
                                args: null,
                                concreteType: "AdBusiness",
                                kind: "LinkedField",
                                name: "business",
                                plural: !1,
                                selections: D,
                                storageKey: null
                            }],
                            type: "BusinessPaymentAccount",
                            abstractKey: null
                        }],
                        storageKey: null
                    }, {
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "linked_payment_account",
                        plural: !1,
                        selections: [l, {
                            alias: null,
                            args: null,
                            concreteType: null,
                            kind: "LinkedField",
                            name: "billable_account",
                            plural: !1,
                            selections: [l, S, T],
                            storageKey: null
                        }, T],
                        storageKey: null
                    }, {
                        alias: null,
                        args: null,
                        concreteType: "ExternalCreditCard",
                        kind: "LinkedField",
                        name: "credit_card",
                        plural: !1,
                        selections: [x, $, P, N, M, T, w, A, F, {
                            kind: "InlineFragment",
                            selections: [l, {
                                kind: "InlineFragment",
                                selections: [O, B],
                                type: "DirectDebit",
                                abstractKey: null
                            }, W, q, U, H, G],
                            type: "PaymentCredential",
                            abstractKey: "__isPaymentCredential"
                        }],
                        storageKey: null
                    }, {
                        alias: null,
                        args: null,
                        concreteType: "XFBBillingCardVerificationResponse",
                        kind: "LinkedField",
                        name: "card_verification",
                        plural: !1,
                        selections: [z, j, K, Q, X, Y, J, Z, {
                            alias: null,
                            args: null,
                            concreteType: "Image",
                            kind: "LinkedField",
                            name: "india_issuer_icon",
                            plural: !1,
                            selections: ee,
                            storageKey: null
                        }, {
                            alias: null,
                            args: null,
                            concreteType: "Image",
                            kind: "LinkedField",
                            name: "card_association_icon",
                            plural: !1,
                            selections: ee,
                            storageKey: null
                        }, x],
                        storageKey: null
                    }, ne, {
                        alias: null,
                        args: null,
                        concreteType: null,
                        kind: "LinkedField",
                        name: "risk_verification_info",
                        plural: !1,
                        selections: [l, C, b],
                        storageKey: null
                    }],
                    storageKey: null
                }]
            },
            params: {
                id: n("BillingSaveCardCredentialStateMutation_facebookRelayOperation"),
                metadata: {},
                name: "BillingSaveCardCredentialStateMutation",
                operationKind: "mutation",
                text: null
            }
        }
    }
    )();
    a.exports = e
}
));
__d("BillingSaveCardCredentialState",[],(function(t, n, r, o, a, i, l, s) {
    "use strict";
    var e, u = 3212061, c = e !== void 0 ? e : e = n("BillingSaveCardCredentialStateMutation.graphql");
    function d(e, t, n, o, a) {
        return function() {
            return e.showAutomaticBillingContent === !0 ? e.hasFunds === !0 ? t.MFT_USABILITY_FIXATHON_FLOW_9_1_HOLD_OUT.read() ? r("BillingCreditCardConstants").successBodyRecurringWithFundsUpdated() : t.MFT_USABILITY_FIXATHON_FLOW_10_2_HOLD_OUT.read() ? r("BillingCreditCardConstants").successBodyRecurringWithFundsFourDots(n, o) : r("BillingCreditCardConstants").successBodyRecurringWithFunds(n, o) : t.MFT_USABILITY_FIXATHON_FLOW_10_2_HOLD_OUT.read() ? r("BillingCreditCardConstants").successBodyRecurringFourDots(n, o) : r("BillingCreditCardConstants").successBodyRecurring(n, o) : a ? r("BillingCreditCardConstants").successBodyNonRecurringInPostpayUpgrade(n, o) : r("BillingCreditCardConstants").successBody(n, o)
        }
    }
    var m = (function(e) {
        function t() {
            for (var t, n = arguments.length, r = new Array(n), o = 0; o < n; o++)
                r[o] = arguments[o];
            return t = e.call.apply(e, [this].concat(r)) || this,
            t.name = "save_credit_card_state_decision",
            babelHelpers.assertThisInitialized(t) || babelHelpers.assertThisInitialized(t)
        }
        babelHelpers.inheritsLoose(t, e);
        var a = t.prototype;
        return a.onDecide = (function() {
            var e = n("asyncToGeneratorRuntime").asyncToGenerator(function*(e, t) {
                var n, a, i, l, m, p, _, f, g, h = t.gk, y = t.qe, C = t.relay;
                if (!e.creditCard || e.saveCardAndPay === !0)
                    return {
                        event: "onAddCardCredential",
                        newProps: babelHelpers.extends({}, e)
                    };
                var b = e.businessPaymentAccountID
                  , v = e.clientInfo
                  , S = e.creditCard
                  , R = e.hasAcknowledgedCountryMismatch
                  , L = e.inCountrySpoofingExperiment
                  , E = e.paymentAccountID
                  , k = e.skipCvvForEeaSave
                  , I = b != null && S.credentialSharability != null ? E : null
                  , T = b != null && S.credentialSharability != null ? b : E
                  , D = yield o("BillingCreditCardUtils").buildSaveCardCredentialInput(S, T, (n = e.country) != null ? n : "", e.currency, (a = e.paymentIntent) != null ? a : "ADD_PM", e.pmCapabilityPaymentIntent, !1, v, h, y, C, void 0, k, I, {
                    errorMessage: "BillingSaveCardCredentialState failed to generate a platform trust token",
                    getIsPTTRequired: function() {
                        return r("MetaConfig")._("494")
                    },
                    sourceState: "save_credit_card_state_decision"
                })
                  , x = e.taxCountryVerificationMethod;
                if (R !== !0 && L === !0) {
                    var $ = yield o("BillingCountryVerificationUtils").queryTaxCountryValidationData(C, E);
                    if (($ == null ? void 0 : $.status) !== "CONFIRMED")
                        if (($ == null ? void 0 : $.canUpdateTaxCountry) === !0) {
                            var P, N, M = (P = (N = S.cardNumber) == null ? void 0 : N.getBin()) != null ? P : "", w = D.platform_trust_token, A = yield o("BillingCountryVerificationUtils").queryBinProperties(C, T, M, w);
                            if (A !== "" && A !== e.country)
                                return {
                                    event: "onResolveLocationMismatch",
                                    newProps: babelHelpers.extends({}, e, {
                                        ptt: w
                                    })
                                }
                        } else
                            x = "SHOW_STEPUP_OPTIONS"
                }
                var F = C.commitMutation;
                if (D.platform_trust_token === "") {
                    var O;
                    F = C.commitSecureMutation,
                    r("BillingWizardRootUPLogger").logDebugEvent("BillingSaveCardCredentialState_token_proxy_fallback", {
                        country: (O = e.country) != null ? O : ""
                    })
                }
                var B = null;
                try {
                    var W = yield y.attempt_to_fix_stale_wizard_queries_univser.enabled.get();
                    B = yield F({
                        mutation: c,
                        variables: {
                            getRiskVerificationInfoForAllCredentialsOnPaymentAccount: !0,
                            includeCreateNewFromOldFragment: W,
                            input: D,
                            paymentAccountID: E
                        }
                    }, {
                        event_data: {
                            is_ptt_empty_string: D.platform_trust_token === "" ? "true" : "false"
                        }
                    }, !0, function(e) {
                        var t, n;
                        return {
                            extra_data: {
                                credential_id: e == null || (t = e.xfb_billing_save_card_credential) == null || (t = t.credit_card) == null ? void 0 : t.credential_id
                            },
                            payload_data: {
                                flow_milestone: (e == null || (n = e.xfb_billing_save_card_credential) == null || (n = n.credit_card) == null ? void 0 : n.credential_id) != null ? "PaymentMethodAdded" : void 0
                            }
                        }
                    })
                } catch (t) {
                    if (!(t instanceof r("BillingError")))
                        throw r("BillingWizardRootUPLogger").logDebugEvent("BillingSaveCardCredentialState_unexpected_error", {
                            error_message: t instanceof Error ? t.message : String(t)
                        }),
                        t;
                    if (r("BillingWizardRootUPLogger").logBillingPayloadError(t.type, t.errorPayload),
                    t.errorPayload.exception_code === u)
                        return {
                            event: "onSelf",
                            newProps: babelHelpers.extends({}, e, {
                                creditCard: void 0,
                                status: {
                                    body: t.description,
                                    headline: t.summary,
                                    type: "ERROR"
                                }
                            })
                        };
                    throw t.sourceState = "save_credit_card_state_decision",
                    t.paymentIntent = e.paymentIntent,
                    t
                }
                var q = (i = B) == null || (i = i.xfb_billing_save_card_credential) == null ? void 0 : i.credit_card
                  , U = q == null ? void 0 : q.credential_id;
                if (U == null)
                    throw new (r("BillingError"))("BillingSaveCardCredentialStateMutation mutation came back with no credential ID","mutation response came back with missing or invalid value",{
                        event_action: "mutation",
                        event_result: "failure",
                        event_side: "client_side"
                    },{
                        action: "mutate",
                        document_name: "save_credit_card"
                    },"critical_error",{
                        sourceState: "save_credit_card_state_decision"
                    });
                var V = (l = q == null ? void 0 : q.last_four_digits) != null ? l : "****"
                  , H = q == null ? void 0 : q.card_association_name
                  , G = S.credentialSharability
                  , z = o("BillingPaymentMethodDisplayUtils").getPaymentMethodDisplayFromFragment(q).toString()
                  , j = (m = B.xfb_billing_save_card_credential) == null ? void 0 : m.payment_account
                  , K = j == null || (p = j.business) == null ? void 0 : p.name
                  , Q = (_ = B.xfb_billing_save_card_credential) == null || (_ = _.linked_payment_account) == null || (_ = _.billable_account) == null ? void 0 : _.name
                  , X = (j == null ? void 0 : j.__typename) === "BusinessPaymentAccount" && (j == null ? void 0 : j.billable_account) == null
                  , Y = e.isNewAccountTransitionsFlow === !0 && (e.recurring === !1 || (q == null ? void 0 : q.supports_recurring) === !1)
                  , J = d(e, h, V, H, Y)
                  , Z = e.showAutomaticBillingContent === !0 ? h.MFT_USABILITY_FIXATHON_FLOW_10_2_HOLD_OUT.read() ? r("BillingCreditCardConstants").successHeadlineRecurringFourDots(V, H) : r("BillingCreditCardConstants").successHeadlineRecurring(V, H) : Y ? s._(/*BTDS*/
                "Automatic billing not turned on") : r("BillingCreditCardConstants").successHeadline
                  , ee = G == null && X && e.showAutomaticBillingContent !== !0 && !Y && y.billing_fixathon_2026h2_9_1.enable_h2_fixathon_9_1_flow.read() === !0
                  , te = G != null ? {
                    body: r("BillingCreditCardConstants").successBodyForBizCredentialSave(G, K, G === "BUSINESS_NOT_SHARABLE" ? Q : ""),
                    headline: r("BillingCreditCardConstants").successHeadlineForBizCredentialSave(z),
                    type: "SUCCESS"
                } : ee ? {
                    body: r("BillingCreditCardConstants").successBodyBusinessUpsell(V, H, K),
                    headline: Z,
                    type: "SUCCESS"
                } : {
                    body: J,
                    headline: Z,
                    type: Y ? "LEARN" : "SUCCESS"
                }
                  , ne = (f = B.xfb_billing_save_card_credential) == null ? void 0 : f.card_verification_status
                  , re = o("BillingCreditCardUtils").updateCreditCardAfterSave(e.creditCard, U, H != null ? H : void 0, V)
                  , oe = {
                    paymentMethodID: U,
                    verification_info: (g = B.xfb_billing_save_card_credential) == null ? void 0 : g.risk_verification_info
                }
                  , ae = babelHelpers.extends({}, e, {
                    creditCard: re,
                    paymentMethodID: U,
                    riskInfo: oe,
                    taxCountryVerificationMethod: x
                });
                if (ne === "SUCCESS")
                    return {
                        event: "onNext",
                        newProps: babelHelpers.extends({}, ae, {
                            paymentMethodType: "CREDIT_CARD",
                            status: te
                        })
                    };
                if (ne === "AUTHENTICATION_REQUIRED")
                    return this.handleAuthenticationRequired(B, ae, te, ne);
                var ie = s._(/*BTDS*/
                "We weren't able to complete verification, please try again.")
                  , le = s._(/*BTDS*/
                "Couldn't verify card");
                throw new (r("BillingError"))("BillingSaveCardCredentialStateMutation GraphQL call returned an unexpected status: " + (ne != null ? ne : "NULL"),"mutation response came back with missing or invalid value",{
                    event_action: "mutation",
                    event_result: "failure",
                    event_side: "client_side"
                },{
                    action: "mutate",
                    document_name: "save_credit_card"
                },"critical_error",{
                    description: ie.toString(),
                    sourceState: "save_credit_card_state_decision",
                    summary: le.toString()
                })
            });
            function t(t, n) {
                return e.apply(this, arguments)
            }
            return t
        }
        )(),
        a.handleAuthenticationRequired = function(t, n, a, i) {
            var e, l, s = t == null || (e = t.xfb_billing_save_card_credential) == null ? void 0 : e.card_verification, u = (s == null ? void 0 : s.supports_native_otp) === !0, c = (s == null ? void 0 : s.credential_authentication_id) != null, d = "iframe_3ds";
            if (c ? d = "cardinal_3ds" : u && (d = "native_otp"),
            r("BillingWizardRootUPLogger").logEvent({
                event_action: "check",
                event_result: "init",
                event_side: "client",
                extra_data: {
                    activation_path: d,
                    card_status_ent_id: String((l = s == null ? void 0 : s.async_card_status_ent_id) != null ? l : ""),
                    card_verification_status: i,
                    supports_cardinal_3ds: String(c),
                    supports_native_otp: String(u)
                },
                target_name: "auth_required"
            }),
            s == null)
                throw new (r("BillingError"))("BillingSaveCardCredentialStateMutation failed to return verification parameters","mutation response came back with missing or invalid value",{
                    event_action: "mutation",
                    event_result: "failure",
                    event_side: "client_side"
                },{
                    action: "mutate",
                    document_name: "save_credit_card"
                },"critical_error");
            if (c)
                return {
                    event: "onAuthenticationRequiredWithCardinal3DS",
                    newProps: babelHelpers.extends({}, n, {
                        cardStatusEntID: s.async_card_status_ent_id,
                        credentialAuthenticationId: s.credential_authentication_id,
                        externalRefID: s.external_reference_id,
                        hidePaymentAmountSection: !0
                    })
                };
            if (s.supports_native_otp === !0) {
                var m;
                return {
                    event: "onAuthenticationRequiredWithNativeOTP",
                    newProps: babelHelpers.extends({}, n, {
                        cardAssociation: s.card_association,
                        cardAssociationIcon: s.card_association_icon != null ? o("BillingPaymentIconUtils.react").getCDSImageProps(s.card_association_icon) : null,
                        cardStatusEntID: s.async_card_status_ent_id,
                        externalRefID: s.external_reference_id,
                        hidePaymentAmountSection: !0,
                        indiaCardIssuer: s.india_card_issuer != null ? s.india_card_issuer : null,
                        initResults: {
                            nonce: s.nonce,
                            params: s.params,
                            url: (m = s.external_uri) != null ? m : ""
                        },
                        issuerIcon: s.india_issuer_icon != null ? o("BillingPaymentIconUtils.react").getCDSImageProps(s.india_issuer_icon) : null
                    })
                }
            } else {
                var p, _ = {
                    nonce: s.nonce,
                    params: s.params,
                    url: (p = s.external_uri) != null ? p : ""
                };
                return {
                    event: "onAuthenticationRequired",
                    newProps: babelHelpers.extends({}, n, {
                        cardStatusEntID: s.async_card_status_ent_id,
                        initResults: _,
                        status: a
                    })
                }
            }
        }
        ,
        t
    }
    )(o("BillingWizardDecisionState").DecisionState);
    l.default = m
}
));
