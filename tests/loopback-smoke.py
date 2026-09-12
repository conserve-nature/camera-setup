#!/usr/bin/env python3
"""Check local IPv4/IPv6 sockets and name resolution; never change networking."""
import ipaddress
import socket


def check(family, address):
    with socket.socket(family, socket.SOCK_STREAM) as server:
        server.settimeout(5)
        if family == socket.AF_INET6:
            server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        server.bind((address, 0))
        server.listen(1)
        with socket.socket(family, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(server.getsockname())
            with server.accept()[0] as peer:
                peer.settimeout(5)
                client.sendall(b'loopback')
                assert peer.recv(8) == b'loopback'
                peer.sendall(b'ok')
                assert client.recv(2) == b'ok'
    print(f'PASS: TCP round trip over {address}')


def main():
    check(socket.AF_INET, '127.0.0.1')
    check(socket.AF_INET6, '::1')
    for family, expected in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):
        found = {entry[4][0] for entry in socket.getaddrinfo('localhost', None, family, socket.SOCK_STREAM)}
        assert expected in found, (family, found)
        assert all(ipaddress.ip_address(address).is_loopback for address in found), found
    print('PASS: localhost resolves to IPv4 and IPv6 loopback')
    found = {entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None, 0, socket.SOCK_STREAM)}
    assert found and all(ipaddress.ip_address(address).is_loopback for address in found), found
    print('PASS: local hostname resolves to loopback')


if __name__ == '__main__':
    main()
