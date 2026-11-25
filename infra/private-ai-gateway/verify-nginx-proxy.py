"""Approved private proxy health and one forwarded root GET; never inference/MCP."""

import http.client
import ipaddress
import json
import os
import socket
import ssl


hostname = "ai.espen.io"
expected = ipaddress.ip_address(os.environ["EXPECTED_PRIVATE_IP"])
if expected not in ipaddress.ip_network("10.0.3.0/28"):
    raise RuntimeError("The proxy address is outside the approved container subnet.")
addresses = {
    item[4][0] for item in socket.getaddrinfo(hostname, 443, family=socket.AF_INET)
}
if addresses != {str(expected)}:
    raise RuntimeError("Private DNS does not match the deployed container's private IP.")

context = ssl.create_default_context(cadata=os.environ["PUBLIC_CERTIFICATE_PEM"])
connection = http.client.HTTPSConnection(hostname, port=443, context=context, timeout=30)
try:
    connection.request("GET", "/healthz")
    response = connection.getresponse()
    body = response.read(4096)
    if response.status != 200 or body != b"proxy-ready; upstream status not checked\n":
        raise RuntimeError("Proxy-local health failed; no forwarded request attempted.")
    print(json.dumps({"dnsVerified": True, "tlsVerified": True, "localProxyHealth": 200}), flush=True)

    connection.request("GET", "/")
    response = connection.getresponse()
    body = response.read(4096)
    upstream = response.getheader("X-AI-Gateway-Proxy-Upstream-Status")
    if upstream != str(response.status):
        raise RuntimeError("Forwarded root response did not identify a matching upstream status.")
    if response.status >= 500:
        if response.status != 502 or "application/json" not in response.getheader("Content-Type", ""):
            raise RuntimeError(f"Unexpected forwarded failure: HTTP {response.status}.")
        if not isinstance(json.loads(body), dict):
            raise RuntimeError("Forwarded 502 did not match the upstream JSON error shape.")
    print(json.dumps({
        "forwardingVerified": True,
        "forwardedRootStatus": response.status,
        "upstreamRuntimeCertified": False,
        "modelOrMcpCalled": False,
    }), flush=True)
finally:
    connection.close()
