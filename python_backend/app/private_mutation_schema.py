"""Static links for Meta's current flat Relay CREATE sender; never execute JS."""
from __future__ import annotations

from .private_contract_discovery import _literal, _pairs, _walk

_FUNCTIONS = {"function_expression", "function_declaration", "arrow_function"}


def _scope(node):
    while node.parent is not None and node.type not in _FUNCTIONS:
        node = node.parent
    return node


def _declarations(scope, name):
    return [n for n in _walk(scope) if n.type == "variable_declarator"
        and _scope(n) == scope and n.child_by_field_name("name").text == name]


def _resolve(node, seen=frozenset()):
    if node is None or len(seen) > 12:
        raise ValueError("unresolved sender binding")
    if node.type != "identifier":
        return node
    scope = _scope(node)
    while True:
        declarations = _declarations(scope, node.text)
        parameters = scope.child_by_field_name("parameters")
        if parameters is not None and any(n.type == "identifier" and n.text == node.text for n in _walk(parameters)):
            raise ValueError("dynamic sender parameter")
        if declarations:
            if len(declarations) != 1:
                raise ValueError("ambiguous sender binding")
            value = declarations[0].child_by_field_name("value")
            marker = (scope.start_byte, scope.end_byte, node.text)
            if value is None or value.start_byte >= node.start_byte or marker in seen:
                raise ValueError("cyclic sender binding")
            # Writes in nested scopes count only when they refer to this binding.
            for child in _walk(scope):
                if child.type not in {"assignment_expression", "augmented_assignment_expression", "update_expression"}:
                    continue
                left = child.child_by_field_name("left") or child.child_by_field_name("argument")
                while left is not None and left.type in {"member_expression", "subscript_expression"}:
                    left = left.child_by_field_name("object")
                if left is None or left.type != "identifier" or left.text != node.text:
                    continue
                inner = _scope(left)
                shadowed = False
                while inner != scope:
                    params = inner.child_by_field_name("parameters")
                    if _declarations(inner, left.text) or (params is not None and any(n.type == "identifier" and n.text == left.text for n in _walk(params))):
                        shadowed = True
                        break
                    inner = _scope(inner.parent)
                if not shadowed:
                    raise ValueError("mutable sender binding")
            return _resolve(value, seen | {marker})
        if scope.parent is None:
            raise ValueError("missing sender binding")
        scope = _scope(scope.parent)


def _import(node):
    if node is None or node.type != "call_expression":
        return ""
    args, function = node.child_by_field_name("arguments"), node.child_by_field_name("function")
    if function is None or function.type != "identifier" or args is None or len(args.named_children) != 1:
        return ""
    try:
        value = _literal(args.named_children[0])
        return value if isinstance(value, str) else ""
    except ValueError:
        return ""


def _first_result(node):
    node = _resolve(node)
    if node.type != "subscript_expression" or _literal(node.child_by_field_name("index")) != 0:
        raise ValueError("not first hook result")
    return _resolve(node.child_by_field_name("object"))


def _one(modules, name):
    versions = modules.get(name, {})
    if len(versions) != 1:
        raise ValueError("missing or conflicting module")
    return next(iter(versions.values()))


def _exported_artifact(modules, reference, friendly):
    reference = _resolve(reference)
    if _import(reference) == friendly + ".graphql":
        return True
    if reference.type != "member_expression":
        return False
    module_name = _import(reference.child_by_field_name("object"))
    prop = reference.child_by_field_name("property")
    if not module_name or prop is None or prop.type != "property_identifier":
        return False
    module = _one(modules, module_name)
    exports = []
    for node in _walk(module):
        if node.type != "assignment_expression":
            continue
        left = node.child_by_field_name("left")
        if left.type == "member_expression" and left.child_by_field_name("property").text == prop.text:
            exports.append(node.child_by_field_name("right"))
    if len(exports) != 1:
        return False
    value = _resolve(exports[0])
    if _import(value) == friendly + ".graphql":
        return True
    # Generated lazy artifact export: var cache, artifact=cache!==void 0?
    # cache:cache=require(exactArtifact). The cache has exactly one write.
    if value.type != "ternary_expression":
        return False
    condition = value.child_by_field_name("condition")
    yes, no = value.child_by_field_name("consequence"), value.child_by_field_name("alternative")
    if condition.type != "binary_expression" or yes.type != "identifier" or no.type != "assignment_expression":
        return False
    if condition.child_by_field_name("left").text != yes.text or condition.child_by_field_name("right").text.replace(b" ", b"") != b"void0":
        return False
    operator = condition.child_by_field_name("operator")
    if operator is None or operator.text != b"!==" or no.child_by_field_name("left").text != yes.text:
        return False
    if _import(no.child_by_field_name("right")) != friendly + ".graphql":
        return False
    declarations = _declarations(_scope(value), yes.text)
    if len(declarations) != 1 or declarations[0].child_by_field_name("value") is not None:
        return False
    writes = [n for n in _walk(module) if n.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"}
        and (n.child_by_field_name("left") or n.child_by_field_name("argument")).text == yes.text]
    return len(writes) == 1 and writes[0] == no


def _reauth_hook(modules):
    """Prove this helper forwards config.variables to Relay unchanged.

    Its error UI is not executed here. Server reauth/checkpoint responses are
    handled by the HTTP executor and never automatically bypassed.
    """
    module = _one(modules, "useMutationWithReauthHandling")
    factory = module
    while factory.type == "parenthesized_expression":
        factory = factory.named_children[0]
    exports = [n.child_by_field_name("right") for n in _walk(module) if n.type == "assignment_expression"
        and n.child_by_field_name("left").type == "member_expression"
        and n.child_by_field_name("left").child_by_field_name("property").text == b"default"]
    if len(exports) != 1 or exports[0].type != "identifier":
        return False
    functions = [n for n in _walk(module) if n.type == "function_declaration"
        and n.child_by_field_name("name").text == exports[0].text and _scope(n.parent) == factory]
    if len(functions) != 1:
        return False
    outer = functions[0]
    params = outer.child_by_field_name("parameters").named_children
    if len(params) != 1:
        return False
    relay = []
    for call in _walk(outer):
        if call.type != "call_expression":
            continue
        fn = call.child_by_field_name("function")
        args = call.child_by_field_name("arguments")
        if fn.type == "member_expression" and fn.child_by_field_name("property").text == b"useMutation" and _import(fn.child_by_field_name("object")) == "RelayHooks":
            if len(args.named_children) != 1 or args.named_children[0].text != params[0].text:
                return False
            relay.append(call)
    if len(relay) != 1:
        return False
    returns = [n.child_by_field_name("argument") or (n.named_children[0] if n.named_children else None)
        for n in _walk(outer) if n.type == "return_statement" and _scope(n) == outer]
    if len(returns) != 1 or returns[0] is None or returns[0].type != "array":
        return False
    first = returns[0].named_children[0]
    wrappers = [n for n in _walk(outer) if n.type == "function_declaration" and _scope(n.parent) == outer
        and n.child_by_field_name("name").text == first.text]
    if len(wrappers) != 1:
        return False
    wrapper = wrappers[0]
    arguments = wrapper.child_by_field_name("parameters").named_children
    if len(arguments) != 1:
        return False
    sends = [n for n in _walk(wrapper) if n.type == "return_statement" and _scope(n) == wrapper]
    if len(sends) != 1 or not sends[0].named_children:
        return False
    send = sends[0].named_children[0]
    if send.type != "call_expression" or _first_result(send.child_by_field_name("function")) != relay[0]:
        return False
    config = send.child_by_field_name("arguments").named_children
    if len(config) != 1 or config[0].type != "call_expression":
        return False
    fn = config[0].child_by_field_name("function")
    copied = config[0].child_by_field_name("arguments").named_children
    return (fn.text == b"babelHelpers.extends" and len(copied) == 3
        and copied[0].type == "object" and not copied[0].named_children
        and copied[1].text == arguments[0].text and set(_pairs(copied[2])) == {"onError"})


def flat_configs(modules, module, friendly):
    for call in _walk(module):
        if call.type != "call_expression":
            continue
        args = call.child_by_field_name("arguments")
        if args is None or len(args.named_children) != 1 or args.named_children[0].type != "object":
            continue
        try:
            pairs = _pairs(args.named_children[0])
            if "variables" not in pairs:
                continue
            hook = _first_result(call.child_by_field_name("function"))
            if hook.type != "call_expression":
                continue
            hook_args = hook.child_by_field_name("arguments").named_children
            if len(hook_args) != 1 or not _exported_artifact(modules, hook_args[0], friendly):
                continue
            fn = hook.child_by_field_name("function")
            if _import(fn) == "useMutationWithReauthHandling" and _reauth_hook(modules):
                yield pairs["variables"]
            elif (fn.type == "member_expression" and fn.child_by_field_name("property").text == b"useMutation"
                    and _import(fn.child_by_field_name("object")) == "RelayHooks"):
                yield pairs["variables"]
        except (ValueError, TypeError, AttributeError, IndexError):
            continue


def flat_timezone_is_string(modules):
    """Observe the modal's exact first action invocation and timezone.toString()."""
    try:
        module = _one(modules, "BizKitSettingsCreateAdAccountModal.react")
        for call in _walk(module):
            if call.type != "call_expression":
                continue
            args = call.child_by_field_name("arguments").named_children
            if len(args) != 5:
                continue
            hook = _first_result(call.child_by_field_name("function"))
            if hook.type != "call_expression":
                continue
            fn = hook.child_by_field_name("function")
            if (fn.type != "member_expression" or fn.child_by_field_name("property").text != b"useCreateAdAccountMutation"
                    or _import(fn.child_by_field_name("object")) != "BizKitSettingsCreateAdAccountActions"):
                continue
            zone = args[2]
            if zone.type != "call_expression" or zone.child_by_field_name("arguments").named_children:
                continue
            method = zone.child_by_field_name("function")
            if method.type == "member_expression" and method.child_by_field_name("property").text == b"toString":
                return True
    except (ValueError, TypeError, AttributeError, IndexError):
        pass
    return False
