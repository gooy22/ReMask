"""Safe transport diagnostics; never infer cookie expiry from a proxy failure."""


def private_transport_error(error):
    payload = getattr(error, 'meta_payload', None)
    failure = payload.get('transport_failure') if isinstance(payload, dict) else None
    kind = failure.get('kind') if isinstance(failure, dict) else None
    # The cause's class is trusted metadata, unlike an exception message or URL.
    if not isinstance(kind, str):
        cause = error.__cause__
        kind = type(cause).__name__ if cause else type(error).__name__
    if kind == 'ProxyError':
        return 502, {'code':'PRIVATE_HTTP_PROXY_UNAVAILABLE',
                     'message':'The selected profile proxy is unavailable. Check its connection.'}
    if kind in {'TimeoutError','ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout','ServerTimeoutError'}:
        return 504, {'code':'PRIVATE_HTTP_READ_TIMEOUT',
                     'message':'The private Meta request timed out. Check the profile connection.'}
    if kind in {'ConnectError','ReadError','WriteError','NetworkError','ClientConnectionError',
                'ClientConnectorError','ServerDisconnectedError'}:
        return 502, {'code':'PRIVATE_HTTP_CONNECTION_UNAVAILABLE',
                     'message':'The selected profile connection to Meta is unavailable.'}
    if kind in {'RemoteProtocolError','LocalProtocolError','ProtocolError'}:
        return 502, {'code':'PRIVATE_HTTP_PROTOCOL_ERROR',
                     'message':'The private Meta connection did not complete successfully.'}
    return 502, {'code':'PRIVATE_HTTP_READ_UNAVAILABLE',
                 'message':'The private Meta response could not be read. No retry was performed.'}
