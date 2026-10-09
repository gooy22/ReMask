__d("useBillingBinInfoQuery.graphql",[],(function(t, n, r, o, a, i) {
    "use strict";
    var e = (function() {
        var e = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "bin"
        }
          , t = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "country"
        }
          , r = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "currency"
        }
          , o = {
            defaultValue: null,
            kind: "LocalArgument",
            name: "paymentAccountID"
        }
          , a = [{
            alias: null,
            args: [{
                kind: "Variable",
                name: "account_id",
                variableName: "paymentAccountID"
            }, {
                kind: "Variable",
                name: "bin",
                variableName: "bin"
            }, {
                kind: "Variable",
                name: "country",
                variableName: "country"
            }, {
                kind: "Variable",
                name: "currency",
                variableName: "currency"
            }],
            concreteType: "CreditCardBinInfoShim",
            kind: "LinkedField",
            name: "credit_card_bin_info_shim",
            plural: !1,
            selections: [{
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "card_restriction",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "is_supported",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "mpi_processor",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "request_postal_code",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "require_3ds",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "require_emandate",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "require_phone_number_or_email",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "skip_cvv_for_eea_save",
                storageKey: null
            }, {
                alias: null,
                args: null,
                kind: "ScalarField",
                name: "supports_recurring",
                storageKey: null
            }, {
                alias: null,
                args: null,
                concreteType: "CurrencyAmount",
                kind: "LinkedField",
                name: "verification_charge_amount",
                plural: !1,
                selections: [{
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
                }],
                storageKey: null
            }],
            storageKey: null
        }];
        return {
            fragment: {
                argumentDefinitions: [e, t, r, o],
                kind: "Fragment",
                metadata: null,
                name: "useBillingBinInfoQuery",
                selections: a,
                type: "Query",
                abstractKey: null
            },
            kind: "Request",
            operation: {
                argumentDefinitions: [o, e, t, r],
                kind: "Operation",
                name: "useBillingBinInfoQuery",
                selections: a
            },
            params: {
                id: n("useBillingBinInfoQuery_facebookRelayOperation"),
                metadata: {},
                name: "useBillingBinInfoQuery",
                operationKind: "query",
                text: null
            }
        }
    }
    )();
    a.exports = e
}
));
