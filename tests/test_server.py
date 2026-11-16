"""Offline tests: protocol handling, input checks and the sun maths. No network is used."""
import json, subprocess, sys, unittest
from verify_mcp import server as s

def rpc(msgs):
    p = subprocess.run([sys.executable, "-m", "verify_mcp"], input="\n".join(json.dumps(m) if not isinstance(m, str) else m for m in msgs) + "\n",
                       capture_output=True, text=True, timeout=30)
    return [json.loads(l) for l in p.stdout.splitlines()]
def call(i, name, args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}

class Protocol(unittest.TestCase):
    def test_handshake_and_list(self):
        r = rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                 {"jsonrpc": "2.0", "id": 3, "method": "ping"}])
        self.assertEqual(len(r), 3)                                   # the notification gets no reply
        self.assertEqual(r[0]["result"]["protocolVersion"], "2024-11-05")
        tools = r[1]["result"]["tools"]
        self.assertEqual(len(tools), 9)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(set(t["inputSchema"]["required"]) <= set(t["inputSchema"]["properties"]))
            self.assertTrue(t["annotations"]["readOnlyHint"])
        self.assertEqual(r[2]["result"], {})
    def test_unknown_version_falls_back(self):
        r = rpc([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}])
        self.assertEqual(r[0]["result"]["protocolVersion"], s.PROTOCOLS[0])
    def test_errors_do_not_kill_the_server(self):
        r = rpc(["this is not json", {"jsonrpc": "2.0", "id": 5, "method": "nope"}, call(6, "nope", {}),
                 call(7, "dns_records", {}), {"jsonrpc": "2.0", "id": 8, "method": "ping"}])
        self.assertEqual(r[0]["error"]["code"], -32700)
        self.assertEqual(r[1]["error"]["code"], -32601)
        self.assertTrue(r[2]["result"]["isError"])
        self.assertIn("Missing required input", r[3]["result"]["content"][0]["text"])
        self.assertEqual(r[4]["result"], {})

class Inputs(unittest.TestCase):
    def bad(self, name, args):
        text, err = s.call_tool(name, args)
        self.assertTrue(err, text)
        return text
    def test_private_and_non_http_addresses_refused(self):
        for u in ("http://127.0.0.1/", "http://localhost:8000/x", "http://10.0.0.5/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"):
            self.assertIn("private or local", self.bad("expand_link", {"url": u}))
        for u in ("file:///etc/passwd", "ftp://example.org/x", "javascript:alert(1)", "example"):
            self.bad("expand_link", {"url": u})
        self.bad("reverse_image_links", {"image_url": "file:///home/x.jpg"})
    def test_domain_cleaning(self):
        self.assertEqual(s._domain("https://Example.ORG/path?x=1"), "example.org")
        self.assertEqual(s._domain("example.org."), "example.org")
        for d in ("", "nodot", "two words.com", "../../etc"):
            with self.assertRaises(s.ToolError):
                s._domain(d)
    def test_bad_values(self):
        self.bad("dns_records", {"name": "example.org", "record_type": "ANY"})
        self.bad("sun_position", {"latitude": 95, "longitude": 0, "time": "2026-01-01T00:00Z"})
        self.bad("sun_position", {"latitude": 10, "longitude": 0, "time": "2026-01-01T00:00"})   # no offset
        self.bad("sun_position", {"latitude": "abc", "longitude": 0, "time": "2026-01-01T00:00Z"})
        self.bad("shadow_times", {"latitude": 10, "longitude": 0, "date": "2026-01-01"})
        self.bad("shadow_times", {"latitude": 10, "longitude": 0, "date": "2026-01-01", "object_height": 2})
        self.bad("shadow_times", {"latitude": 10, "longitude": 0, "date": "01/01/2026", "shadow_azimuth": 10})
    def test_unknown_arguments_are_ignored(self):
        text, err = s.call_tool("sun_position", {"latitude": 0, "longitude": 0, "time": "2026-03-20T12:07Z", "extra": 1})
        self.assertFalse(err)

class Wayback(unittest.TestCase):
    def run_with(self, replies):
        orig, seq = s._json, list(replies)
        s._json = lambda url, **kw: seq.pop(0)
        try:
            return s.call_tool("wayback_closest", {"url": "https://example.org/", "date": "2010-01-01"})
        finally:
            s._json = orig
    def test_service_timeout_is_an_error_not_no_snapshot(self):
        timeout = {"archived_snapshots": {}, "error": {"code": "timeout", "message": "client timeout at 5.0 seconds"}}
        text, err = self.run_with([timeout, timeout])
        self.assertTrue(err)
        self.assertIn("not evidence", text)
    def test_retry_then_success(self):
        ok = {"archived_snapshots": {"closest": {"available": True, "url": "http://web.archive.org/web/20100101000000/http://example.org/", "timestamp": "20100101000000", "status": "200"}}}
        text, err = self.run_with([{"archived_snapshots": {}, "error": {"code": "timeout"}}, ok])
        self.assertFalse(err)
        self.assertIn("https://web.archive.org/web/20100101000000", text)
    def test_genuinely_no_snapshot(self):
        text, err = self.run_with([{"archived_snapshots": {}}])
        self.assertFalse(err)
        self.assertIn("No Wayback Machine snapshot", text)

class Sun(unittest.TestCase):
    def test_known_noon_elevations(self):
        text, err = s.call_tool("sun_position", {"latitude": 28.6139, "longitude": 77.209, "time": "2026-06-21T12:23+05:30"})
        self.assertFalse(err); self.assertIn("elevation 84.8", text)
        text, _ = s.call_tool("sun_position", {"latitude": 51.5, "longitude": -0.12, "time": "2026-12-21T12:00Z"})
        self.assertIn("elevation 15.1", text)
        text, _ = s.call_tool("sun_position", {"latitude": 28.6, "longitude": 77.2, "time": "2026-11-16T23:00+05:30"})
        self.assertIn("below the horizon", text)
    def test_shadow_round_trip(self):
        text, err = s.call_tool("shadow_times", {"latitude": 28.6139, "longitude": 77.209, "date": "2026-11-16", "utc_offset": "+05:30",
                                                 "object_height": 1.8, "shadow_length": 2.4, "tolerance": 1})
        self.assertFalse(err)
        self.assertIn("10:18 to 10:35", text); self.assertIn("13:36 to 13:54", text)
        text, _ = s.call_tool("shadow_times", {"latitude": 28.6139, "longitude": 77.209, "date": "2026-11-16", "utc_offset": "+05:30",
                                               "shadow_azimuth": 328, "object_height": 1.8, "shadow_length": 2.4, "tolerance": 3})
        self.assertEqual(text.count(" to "), 1)

if __name__ == "__main__":
    unittest.main()
