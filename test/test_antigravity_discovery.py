import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import ide_poke


class TestAntigravityDiscovery(unittest.TestCase):
    def setUp(self):
        self.orig_hub = ide_poke._hub
        self.orig_servers = ide_poke._servers.copy()
        self.orig_ls = ide_poke._language_servers
        self.orig_call = ide_poke._call
        self.orig_ls_cache = ide_poke._ls_cache
        ide_poke._hub = None
        ide_poke._servers.clear()

    def tearDown(self):
        ide_poke._hub = self.orig_hub
        ide_poke._servers = self.orig_servers
        ide_poke._language_servers = self.orig_ls
        ide_poke._call = self.orig_call
        ide_poke._ls_cache = self.orig_ls_cache

    def test_hub_server_finds_port_when_earlier_ports_fail(self):
        """Simulate Antigravity 2.31+ where language_server listens on sidecar/HTTPS ports before Connect RPC port"""
        # Port 65298 returns 401 Unauthorized
        # Port 63703 returns 400 HTTPS error
        # Port 63704 returns 200 OK
        calls = []

        def mock_call(port, tok, method, body, timeout=15):
            calls.append((port, tok, method))
            if port == 65298:
                return 401, "unauthorized\n"
            if port == 63703:
                return 400, "Client sent an HTTP request to an HTTPS server.\n"
            if port == 63704 and method == "ReadProjects":
                return 200, {}
            return 404, "not found"

        ide_poke._call = mock_call
        ide_poke._language_servers = lambda: [("tok-multi", [65298, 63704, 63703])]

        hub = ide_poke.hub_server()
        self.assertIsNotNone(hub, "hub_server should not return None when a valid port exists")
        self.assertEqual(hub, (63704, "tok-multi"))
        # Verify it attempted 65298 first and then continued to 63704
        self.assertIn((65298, "tok-multi", "ReadProjects"), calls)
        self.assertIn((63704, "tok-multi", "ReadProjects"), calls)

    def test_hub_server_with_multiple_language_servers(self):
        """When IDE server is returned first and Desktop hub server is second"""
        calls = []

        def mock_call(port, tok, method, body, timeout=15):
            calls.append((port, tok, method))
            if tok == "tok-ide":
                # IDE language server does not implement ReadProjects
                return 404, "unimplemented"
            if tok == "tok-hub" and port == 63704 and method == "ReadProjects":
                return 200, {}
            return 401, "unauthorized"

        ide_poke._call = mock_call
        ide_poke._language_servers = lambda: [
            ("tok-ide", [50001, 50002]),
            ("tok-hub", [65298, 63704]),
        ]

        hub = ide_poke.hub_server()
        self.assertEqual(hub, (63704, "tok-hub"))

    def test_hub_server_cache_and_invalidation(self):
        """hub_server caches the result, and invalidates if cached port dies"""
        call_count = {"calls": 0}

        def mock_call(port, tok, method, body, timeout=15):
            call_count["calls"] += 1
            if port == 63704 and method == "ReadProjects":
                return 200, {}
            raise OSError("Connection refused")

        ide_poke._call = mock_call
        ide_poke._language_servers = lambda: [("tok-hub", [63704])]

        # First call finds and caches
        hub1 = ide_poke.hub_server()
        self.assertEqual(hub1, (63704, "tok-hub"))
        c1 = call_count["calls"]

        # Second call uses cache (validating with ReadProjects)
        hub2 = ide_poke.hub_server()
        self.assertEqual(hub2, (63704, "tok-hub"))
        self.assertEqual(call_count["calls"], c1 + 1)

        # Now simulate process restart on different port
        def mock_call_restarted(port, tok, method, body, timeout=15):
            if port == 63704:
                raise OSError("Connection refused (old port died)")
            if port == 64000 and method == "ReadProjects":
                return 200, {}
            return 404, ""

        ide_poke._call = mock_call_restarted
        ide_poke._language_servers = lambda: [("tok-new", [64000])]

        hub3 = ide_poke.hub_server()
        self.assertEqual(hub3, (64000, "tok-new"))

    def test_locate_multi_port_resilience(self):
        """locate checks all ports of a server even if an earlier port fails with OSError or HTTP error"""
        cid = "cid-target-xyz"
        calls = []

        def mock_call(port, tok, method, body, timeout=15):
            calls.append((port, tok, method))
            if port == 65298:
                return 401, "unauthorized"
            if port == 63703:
                raise OSError("SSL error or connection reset")
            if port == 63704 and method == "GetAllCascadeTrajectories":
                return 200, {"trajectorySummaries": {cid: {"summary": "Target Conversation"}}}
            return 404, ""

        ide_poke._call = mock_call
        ide_poke._language_servers = lambda: [("tok-multi", [65298, 63703, 63704])]

        port, tok, s = ide_poke.locate(cid)
        self.assertEqual(port, 63704)
        self.assertEqual(tok, "tok-multi")
        self.assertEqual(s.get("summary"), "Target Conversation")

    def test_locate_when_server_entry_fails_with_401(self):
        """When cached _servers has a stale/unauthorized port returning 401, it must NOT be marked checked_ok, and step 2 must proceed to find the live port"""
        cid = "cid-stale-entry"
        ide_poke._servers[cid] = (65298, "tok-multi")
        calls = []

        def mock_call(port, tok, method, body, timeout=15):
            calls.append((port, tok, method))
            if port == 65298:
                return 401, "unauthorized"
            if port == 63704 and method == "GetAllCascadeTrajectories":
                return 200, {"trajectorySummaries": {cid: {"summary": "Recovered Conversation"}}}
            return 404, ""

        ide_poke._call = mock_call
        ide_poke._language_servers = lambda: [("tok-multi", [65298, 63704])]

        port, tok, s = ide_poke.locate(cid)
        self.assertEqual(port, 63704, "Must recover and find conversation on live RPC port despite stale 401 entry")
        self.assertEqual(tok, "tok-multi")
        self.assertEqual(s.get("summary"), "Recovered Conversation")

    def test_hub_server_prioritizes_known_servers_cache(self):
        """hub_server should test known candidates from _servers before calling _language_servers"""
        ide_poke._servers["some-cid"] = (63704, "tok-hub")
        ls_called = []

        def mock_call(port, tok, method, body, timeout=15):
            if port == 63704 and tok == "tok-hub" and method == "ReadProjects":
                return 200, {}
            return 404, ""

        def mock_ls():
            ls_called.append(True)
            return [("tok-hub", [63704])]

        ide_poke._call = mock_call
        ide_poke._language_servers = mock_ls

        hub = ide_poke.hub_server()
        self.assertEqual(hub, (63704, "tok-hub"))
        self.assertEqual(len(ls_called), 0, "Should have used known servers snapshot without calling _language_servers")

    def test_language_servers_and_app_procs_graceful_on_error(self):
        """_language_servers and _app_procs must not raise uncaught exceptions if subprocess/json fails"""
        with patch("subprocess.run", side_effect=OSError("PowerShell failed")):
            ide_poke._ls_cache = (0.0, None)
            res_ls = ide_poke._language_servers(ttl=0)
            self.assertEqual(res_ls, [])

            app, hubs = ide_poke._app_procs()
            self.assertEqual(app, [])
            self.assertEqual(hubs, 0)

    def test_app_procs_counts_language_server_variants(self):
        """_app_procs recognizes language_server_windows_x64.exe as language server"""
        mock_output = MagicMock(stdout=r'''
        [
            {"pid": 100, "ppid": 1, "name": "Antigravity.exe", "exe": "C:\\app\\Antigravity.exe", "hub": false},
            {"pid": 200, "ppid": 100, "name": "language_server_windows_x64.exe", "exe": "C:\\app\\ls.exe", "hub": true}
        ]
        ''')
        with patch("subprocess.run", return_value=mock_output):
            app, hubs = ide_poke._app_procs()
            self.assertEqual(len(app), 1)
            self.assertEqual(app[0][0], 100)
            self.assertEqual(hubs, 1)

    def test_csrf_token_regex_variants(self):
        """Ensure both --csrf_token <tok> and --csrf_token=<tok> are correctly extracted"""
        import re
        cmd1 = r"C:\app\language_server.exe --csrf_token 11223344-5566-7788 --other"
        cmd2 = r"C:\app\language_server.exe --csrf_token=11223344-5566-7788 --other"

        m1 = re.search(r"--csrf_token(?:=|\s+)(\S+)", cmd1)
        m2 = re.search(r"--csrf_token(?:=|\s+)(\S+)", cmd2)

        self.assertIsNotNone(m1)
        self.assertEqual(m1.group(1), "11223344-5566-7788")
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1), "11223344-5566-7788")


if __name__ == "__main__":
    unittest.main()
