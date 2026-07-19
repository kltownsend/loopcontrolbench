#!/usr/bin/env python3
"""Minimal HTTPS-CONNECT allowlist proxy for the sandbox install phase.

The install container runs on an --internal Docker network with no egress. Its ONLY route
out is this proxy, which tunnels HTTPS (CONNECT) to an allowlist of package hosts and nothing
else. That means a malicious build backend or dependency hook cannot reach the cloud metadata
endpoint (169.254.169.254), internal services, or arbitrary hosts, even if it ignores the proxy
env vars: with no other route, the packets have nowhere to go.

Defenses:
  - CONNECT only, port 443 only. No plain-HTTP forwarding, no other methods.
  - Host allowlist (exact or dot-suffix match).
  - After DNS resolution, refuse any host that resolves to a private, loopback, link-local,
    reserved, or multicast address (blocks DNS-rebinding to internal or metadata IPs).

Usage: allowlist_proxy.py <port> <comma-separated-allowed-hosts>
Trusted component: runs from the base image, never from an untrusted repo.
"""
import socket, sys, threading, ipaddress, select

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
ALLOW = tuple(h.strip().lower() for h in (sys.argv[2] if len(sys.argv) > 2 else
              'pypi.org,files.pythonhosted.org,pythonhosted.org').split(',') if h.strip())

def host_allowed(host):
    h = host.lower().rstrip('.')
    return any(h == a or h.endswith('.' + a) for a in ALLOW)

def resolves_public(host):
    """True only if the host resolves and every address is a normal public address."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return False
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True

def pipe(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 60)
            if not r:
                break
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        pass

def handle(client):
    client.settimeout(30)
    try:
        req = b''
        while b'\r\n\r\n' not in req:
            chunk = client.recv(4096)
            if not chunk:
                return
            req += chunk
            if len(req) > 16384:
                return
        line = req.split(b'\r\n', 1)[0].decode('latin-1', 'replace')
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != 'CONNECT':
            client.sendall(b'HTTP/1.1 405 Method Not Allowed\r\n\r\n'); return
        target = parts[1]
        host, _, port = target.partition(':')
        if port not in ('', '443'):
            client.sendall(b'HTTP/1.1 403 Forbidden\r\n\r\n'); return
        if not host_allowed(host) or not resolves_public(host):
            client.sendall(b'HTTP/1.1 403 Forbidden\r\n\r\n'); return
        upstream = socket.create_connection((host, 443), timeout=30)
        client.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
        client.settimeout(None); upstream.settimeout(None)
        pipe(client, upstream)
        upstream.close()
    except OSError:
        pass
    finally:
        client.close()

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', PORT)); srv.listen(128)
    sys.stderr.write('allowlist proxy on :%d, allow=%s\n' % (PORT, ','.join(ALLOW)))
    sys.stderr.flush()
    while True:
        client, _ = srv.accept()
        threading.Thread(target=handle, args=(client,), daemon=True).start()

if __name__ == '__main__':
    main()
