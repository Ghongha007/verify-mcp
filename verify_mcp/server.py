#!/usr/bin/env python3
"""verify-mcp: a small MCP server with read-only lookups for verification work.

No API keys and no third-party packages. It speaks the Model Context Protocol
over stdio (one JSON-RPC message per line), so any MCP client can start it with
`python3 -m verify_mcp`.
"""
from __future__ import annotations

import ipaddress
import json
import math
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import __version__

UA = {"User-Agent": f"verify-mcp/{__version__}", "Accept": "application/json, */*"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_BYTES = 2_000_000


class ToolError(Exception):
    """A problem the model should be told about in plain words."""


# ------------------------------------------------------------------ helpers

def _public_host(host: str) -> None:
    """Refuse hosts that resolve to private, loopback or link-local addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ToolError(f"could not resolve host '{host}'")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ToolError("that address is on a private or local network; this server only looks up public hosts")


def _check_url(url: str) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ToolError("give a full http or https URL")
    return u


def _domain(value: str) -> str:
    v = value.strip().lower()
    if "://" in v:
        v = urllib.parse.urlsplit(v).hostname or ""
    v = v.split("/")[0].split(":")[0].strip(".")
    if not v or "." not in v or any(c.isspace() for c in v) or len(v) > 253:
        raise ToolError("give a domain name such as example.org")
    return v.encode("idna").decode("ascii")


def _get(url: str, timeout: int = 30, headers: dict | None = None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(MAX_BYTES), r
    except urllib.error.HTTPError as e:
        raise ToolError(f"the service answered HTTP {e.code} for {urllib.parse.urlsplit(url).hostname}")
    except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
        raise ToolError(f"could not reach {urllib.parse.urlsplit(url).hostname}: {getattr(e, 'reason', e)}")


def _json(url: str, **kw):
    body, _ = _get(url, **kw)
    try:
        return json.loads(body)
    except ValueError:
        raise ToolError(f"{urllib.parse.urlsplit(url).hostname} did not return JSON (it may be overloaded; try again later)")


def _ts(s: str) -> str:
    """Wayback timestamp 20261116041925 -> 2026-11-16 04:19:25 UTC."""
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]} UTC" if len(s) >= 14 else s


# ------------------------------------------------------------------ tools

def wayback_history(url: str, limit: int = 20, year_from: int | None = None, year_to: int | None = None) -> str:
    _check_url(url if "://" in url else "http://" + url)
    limit = max(1, min(int(limit), 200))
    base = ("https://web.archive.org/cdx/search/cdx?output=json&fl=timestamp,statuscode,digest,original"
            "&collapse=digest&url=" + urllib.parse.quote(url, safe=""))
    if year_from:
        base += f"&from={int(year_from)}"
    if year_to:
        base += f"&to={int(year_to)}"
    first = _json(base + "&limit=1", timeout=45)
    if len(first) < 2:
        return f"The Wayback Machine has no captures of {url}" + (" in that range." if year_from or year_to else ".")
    rows = _json(base + f"&limit=-{limit}", timeout=45)[1:]
    out = [f"Wayback Machine captures of {url}",
           f"Earliest{' in range' if year_from or year_to else ''}: {_ts(first[1][0])}  https://web.archive.org/web/{first[1][0]}/{first[1][3]}",
           f"Latest {len(rows)} captures where the content changed (newest last):"]
    for ts, status, _digest, orig in rows:
        out.append(f"  {_ts(ts)}  HTTP {status}  https://web.archive.org/web/{ts}/{orig}")
    out.append("An archived copy shows what a crawler saw at that time. Open the snapshot to confirm it holds the content; "
               "many social platforms archive as a login page.")
    return "\n".join(out)


def wayback_closest(url: str, date: str = "") -> str:
    _check_url(url if "://" in url else "http://" + url)
    q = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    stamp = "".join(c for c in date if c.isdigit())
    if stamp:
        q += "&timestamp=" + stamp
    snap = (_json(q).get("archived_snapshots") or {}).get("closest") or {}
    if not snap.get("available"):
        return f"No Wayback Machine snapshot found for {url}."
    return f"Closest snapshot: {snap['url'].replace('http://', 'https://', 1)}\nCaptured: {_ts(snap.get('timestamp', ''))}\nHTTP status at capture: {snap.get('status', '?')}"


def domain_registration(domain: str) -> str:
    d = _domain(domain)
    data = _json("https://rdap.org/domain/" + d, headers={"Accept": "application/rdap+json"})
    ev = {e.get("eventAction"): e.get("eventDate") for e in data.get("events", [])}
    registrar = ""
    for ent in data.get("entities", []):
        if "registrar" in ent.get("roles", []):
            for item in (ent.get("vcardArray") or [None, []])[1]:
                if item[0] == "fn":
                    registrar = item[3]
    ns = sorted({n.get("ldhName", "").lower() for n in data.get("nameservers", []) if n.get("ldhName")})
    out = [f"Registration record (RDAP) for {data.get('ldhName', d).lower()}",
           f"Registered:   {ev.get('registration', 'not given')}",
           f"Last changed: {ev.get('last changed', 'not given')}",
           f"Expires:      {ev.get('expiration', 'not given')}",
           f"Registrar:    {registrar or 'not given'}",
           f"Status:       {', '.join(data.get('status', [])) or 'not given'}",
           f"Name servers: {', '.join(ns) or 'not given'}",
           "Registration date is when the current registration began. A domain can be older (dropped and re-registered), "
           "and registrant details are usually redacted. Not every top-level domain offers RDAP."]
    return "\n".join(out)


def dns_records(name: str, record_type: str = "A") -> str:
    d = _domain(name)
    t = record_type.strip().upper()
    if t not in ("A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "CAA"):
        raise ToolError("record_type must be one of A, AAAA, MX, TXT, NS, CNAME, SOA, CAA")
    data = _json(f"https://cloudflare-dns.com/dns-query?name={urllib.parse.quote(d)}&type={t}",
                 headers={"Accept": "application/dns-json"})
    ans = data.get("Answer") or []
    if not ans:
        return f"No {t} records for {d} (DNS status code {data.get('Status')})."
    return "\n".join([f"{t} records for {d} (via Cloudflare public DNS):"] +
                     [f"  {a.get('data')}   (TTL {a.get('TTL')}s)" for a in ans])


def certificate_names(domain: str, limit: int = 100) -> str:
    d = _domain(domain)
    limit = max(1, min(int(limit), 500))
    rows = _json("https://crt.sh/?output=json&q=" + urllib.parse.quote(d), timeout=60)
    if not rows:
        return f"No certificates logged for {d}."
    first: dict[str, str] = {}
    for r in rows:
        when = (r.get("not_before") or "")[:10]
        for n in (r.get("name_value") or "").lower().split("\n"):
            n = n.strip()
            if n and (n not in first or when < first[n]):
                first[n] = when
    names = sorted(first.items(), key=lambda kv: (kv[1], kv[0]))
    out = [f"Host names seen in public TLS certificates for {d} ({len(names)} names, {len(rows)} certificates; source crt.sh):",
           f"Earliest certificate: {names[0][1]}"]
    out += [f"  {when}  {n}" for n, when in names[:limit]]
    if len(names) > limit:
        out.append(f"  ... and {len(names) - limit} more")
    out.append("Dates are when the first certificate naming that host became valid. A name here once had a certificate; it may no longer exist.")
    return "\n".join(out)


def expand_link(url: str) -> str:
    cur = url.strip()
    hops = []
    for _ in range(8):
        u = _check_url(cur)
        _public_host(u.hostname)
        req = urllib.request.Request(cur, method="HEAD", headers=UA)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=20) as r:
                status, loc = r.status, r.headers.get("Location")
        except urllib.error.HTTPError as e:
            status, loc = e.code, e.headers.get("Location")
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            raise ToolError(f"could not reach {u.hostname}: {getattr(e, 'reason', e)}")
        hops.append(f"  HTTP {status}  {cur}")
        if status in (301, 302, 303, 307, 308) and loc:
            cur = urllib.parse.urljoin(cur, loc)
            continue
        break
    else:
        hops.append("  stopped after 8 redirects")
    return "\n".join([f"Redirect chain ({len(hops)} step{'s' if len(hops) != 1 else ''}):"] + hops +
                     [f"Final address: {cur}",
                      "Only HTTP redirects are followed. Pages that redirect with JavaScript or a meta tag will show as the final address here."])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def reverse_image_links(image_url: str) -> str:
    _check_url(image_url)
    q = urllib.parse.quote(image_url.strip(), safe="")
    return "\n".join([
        "Open these to look for earlier copies of the image. Each engine indexes different sites, so try more than one.",
        f"Google Lens: https://lens.google.com/uploadbyurl?url={q}",
        f"Bing:        https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIVSP&sbisrc=UrlPaste&q=imgurl:{q}",
        f"Yandex:      https://yandex.com/images/search?rpt=imageview&url={q}",
        f"TinEye:      https://tineye.com/search?url={q}",
        "These links send the image address to each search engine. Do not use them for an image that is private or sensitive."])


def _sun(lat: float, lon: float, when: datetime):
    R, D = math.radians, math.degrees
    u = when.astimezone(timezone.utc)
    jd = u.toordinal() + 1721424.5 + (u.hour + u.minute / 60 + u.second / 3600) / 24
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (math.sin(R(m)) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(R(2 * m)) * (0.019993 - 0.000101 * t) + math.sin(R(3 * m)) * 0.000289)
    omega = 125.04 - 1934.136 * t
    lam = l0 + c - 0.00569 - 0.00478 * math.sin(R(omega))
    eps = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60 + 0.00256 * math.cos(R(omega))
    decl = math.asin(math.sin(R(eps)) * math.sin(R(lam)))
    y = math.tan(R(eps / 2)) ** 2
    eot = 4 * D(y * math.sin(2 * R(l0)) - 2 * e * math.sin(R(m)) + 4 * e * y * math.sin(R(m)) * math.cos(2 * R(l0))
                - 0.5 * y * y * math.sin(4 * R(l0)) - 1.25 * e * e * math.sin(2 * R(m)))
    tst = (u.hour * 60 + u.minute + u.second / 60 + eot + 4 * lon) % 1440
    ha = tst / 4 - 180
    zen = math.acos(max(-1.0, min(1.0, math.sin(R(lat)) * math.sin(decl) + math.cos(R(lat)) * math.cos(decl) * math.cos(R(ha)))))
    den = math.cos(R(lat)) * math.sin(zen)
    if abs(den) < 1e-9:
        az = 180.0 if lat > D(decl) else 0.0
    else:
        a = D(math.acos(max(-1.0, min(1.0, (math.sin(R(lat)) * math.cos(zen) - math.sin(decl)) / den))))
        az = (a + 180) % 360 if ha > 0 else (540 - a) % 360
    return az, 90 - D(zen)


def _coords(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ToolError("latitude must be -90 to 90 and longitude -180 to 180 (east positive)")
    return lat, lon


def _offset(s: str) -> timezone:
    s = (s or "Z").strip()
    if s.upper() in ("Z", "UTC"):
        return timezone.utc
    try:
        sign = -1 if s.startswith("-") else 1
        hh, _, mm = s.lstrip("+-").partition(":")
        return timezone(sign * timedelta(hours=int(hh), minutes=int(mm or 0)))
    except ValueError:
        raise ToolError("utc_offset must look like +05:30 or -04:00")


def sun_position(latitude: float, longitude: float, time: str) -> str:
    lat, lon = _coords(latitude, longitude)
    try:
        when = datetime.fromisoformat(time.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ToolError("time must be ISO format with an offset, e.g. 2026-11-16T10:30+05:30")
    if when.tzinfo is None:
        raise ToolError("time needs a UTC offset, e.g. 2026-11-16T10:30+05:30 or 2026-11-16T05:00Z")
    az, el = _sun(lat, lon, when)
    out = [f"Sun azimuth {az:.1f} degrees from north, elevation {el:.1f} degrees."]
    if el <= 0:
        out.append("The sun is below the horizon, so there are no sun shadows.")
    else:
        out.append(f"Shadows point towards {(az + 180) % 360:.1f} degrees and are {1 / math.tan(math.radians(el)):.2f} times the height of the object.")
    out.append("No correction for atmospheric refraction; treat elevations under 3 degrees as approximate.")
    return "\n".join(out)


def shadow_times(latitude: float, longitude: float, date: str, utc_offset: str = "Z",
                 shadow_azimuth: float | None = None, object_height: float | None = None,
                 shadow_length: float | None = None, tolerance: float = 2.0) -> str:
    lat, lon = _coords(latitude, longitude)
    tz = _offset(utc_offset)
    try:
        day = datetime.strptime(date.strip(), "%Y-%m-%d").replace(tzinfo=tz)
    except ValueError:
        raise ToolError("date must be YYYY-MM-DD")
    if (object_height is None) != (shadow_length is None):
        raise ToolError("object_height and shadow_length go together")
    want_el = None
    if object_height is not None:
        if object_height <= 0 or shadow_length <= 0:
            raise ToolError("object_height and shadow_length must be positive")
        want_el = math.degrees(math.atan2(object_height, shadow_length))
    want_az = (float(shadow_azimuth) + 180) % 360 if shadow_azimuth is not None else None
    if want_az is None and want_el is None:
        raise ToolError("give shadow_azimuth, or object_height with shadow_length, or both")
    tol = max(0.1, min(float(tolerance), 20))
    wins = []
    for i in range(1440):
        w = day + timedelta(minutes=i)
        az, el = _sun(lat, lon, w)
        if el <= 0:
            continue
        if want_az is not None and abs((az - want_az + 180) % 360 - 180) > tol:
            continue
        if want_el is not None and abs(el - want_el) > tol:
            continue
        if wins and (w - wins[-1][1]) <= timedelta(minutes=1):
            wins[-1][1] = w
        else:
            wins.append([w, w, az, el])
    head = f"Sun elevation from the shadow ratio: {want_el:.1f} degrees.\n" if want_el is not None else ""
    if not wins:
        return head + "No time on that date matches. Check the date, the place and the compass direction, or use a larger tolerance."
    lines = [f"  {a:%H:%M} to {b:%H:%M} local time (sun azimuth about {az:.0f}, elevation about {el:.0f})" for a, b, az, el in wins]
    tail = ("\nShadow length alone usually matches twice, in the morning and the afternoon; use the side the shadow falls on to choose."
            if want_az is None else "")
    return head + f"Matching windows on {date} (tolerance {tol:g} degrees):\n" + "\n".join(lines) + tail


S = lambda desc, **extra: {"type": "string", "description": desc, **extra}
N = lambda desc, **extra: {"type": "number", "description": desc, **extra}
I = lambda desc, **extra: {"type": "integer", "description": desc, **extra}

TOOLS = [
    (wayback_history,
     "List Wayback Machine captures of a web page: the earliest capture and the most recent captures where the content changed. "
     "Use to check when a page first existed, or to find a version from before an edit or deletion.",
     {"url": S("Page address"), "limit": I("How many recent captures to list (default 20, max 200)"),
      "year_from": I("Only captures from this year on"), "year_to": I("Only captures up to this year")}, ["url"]),
    (wayback_closest,
     "Find the Wayback Machine snapshot of a page closest to a date (or the latest if no date is given).",
     {"url": S("Page address"), "date": S("Optional date such as 2024-03-15 or 20240315")}, ["url"]),
    (domain_registration,
     "Look up a domain's public registration record (RDAP, the successor to WHOIS): registration date, registrar, name servers. "
     "Use to check how old a website is. Registrant identity is normally redacted and is not returned.",
     {"domain": S("Domain name such as example.org")}, ["domain"]),
    (dns_records,
     "Look up public DNS records for a domain through Cloudflare's public resolver.",
     {"name": S("Domain name"), "record_type": S("A, AAAA, MX, TXT, NS, CNAME, SOA or CAA (default A)")}, ["name"]),
    (certificate_names,
     "List host names that appear in public TLS certificates for a domain (certificate transparency logs via crt.sh), with first-seen dates. "
     "Use to see what sub-sites an organisation has run and when a site first went live. The service is often slow.",
     {"domain": S("Domain name such as example.org"), "limit": I("Maximum names to list (default 100)")}, ["domain"]),
    (expand_link,
     "Follow a shortened or tracking link through its redirects without loading the page, and report each step and the final address.",
     {"url": S("The link to expand")}, ["url"]),
    (reverse_image_links,
     "Build reverse image search links (Google Lens, Bing, Yandex, TinEye) for a publicly reachable image address. Nothing is sent by this tool; "
     "the search happens when a person opens a link.",
     {"image_url": S("Direct address of the image")}, ["image_url"]),
    (sun_position,
     "Compute where the sun was at a place and time: compass direction, height above the horizon, and the direction and relative length of shadows. "
     "Use to check a claimed time of a photo or video against its shadows.",
     {"latitude": N("Decimal degrees, north positive"), "longitude": N("Decimal degrees, east positive"),
      "time": S("ISO time with UTC offset, e.g. 2026-11-16T10:30+05:30")}, ["latitude", "longitude", "time"]),
    (shadow_times,
     "Find the times of day at which the sun matches a measured shadow at a known place and date. Give the compass direction the shadow points, "
     "or an object's height and its shadow length, or both.",
     {"latitude": N("Decimal degrees, north positive"), "longitude": N("Decimal degrees, east positive"),
      "date": S("Local date, YYYY-MM-DD"), "utc_offset": S("Local offset from UTC such as +05:30 (default Z)"),
      "shadow_azimuth": N("Direction the shadow points, degrees clockwise from north"),
      "object_height": N("Height of the object, any unit"), "shadow_length": N("Shadow length, same unit"),
      "tolerance": N("Degrees of measurement error to allow (default 2)")}, ["latitude", "longitude", "date"]),
]
REGISTRY = {fn.__name__: (fn, set(props), req) for fn, _d, props, req in TOOLS}


def tool_list() -> list[dict]:
    return [{"name": fn.__name__, "description": desc,
             "inputSchema": {"type": "object", "properties": props, "required": req},
             "annotations": {"readOnlyHint": True}}
            for fn, desc, props, req in TOOLS]


def call_tool(name: str, args: dict) -> tuple[str, bool]:
    if name not in REGISTRY:
        return f"Unknown tool '{name}'.", True
    fn, allowed, required = REGISTRY[name]
    args = {k: v for k, v in (args or {}).items() if k in allowed and v is not None}
    missing = [k for k in required if k not in args]
    if missing:
        return f"Missing required input: {', '.join(missing)}.", True
    try:
        return fn(**args), False
    except ToolError as e:
        return str(e), True
    except (TypeError, ValueError) as e:
        return f"Bad input: {e}", True
    except Exception as e:  # keep the server alive whatever a remote service returns
        return f"Unexpected error in {name}: {type(e).__name__}: {e}", True


# ------------------------------------------------------------------ protocol

def handle(msg: dict):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method is None or mid is None:
        return None  # a response or a notification: nothing to send back
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "verify-mcp", "version": __version__},
                  "instructions": "Read-only lookups for verification. Results are evidence to weigh, not conclusions: "
                                  "report what each lookup shows and what it cannot show."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        text, is_error = call_tool(params.get("name", ""), params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    elif method in ("resources/list", "prompts/list"):
        result = {method.split("/")[0]: []}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "--version"):
        print(f"verify-mcp {__version__}: an MCP server over stdio. Start it from an MCP client; see README.md.")
        return 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(msg) if isinstance(msg, dict) else {
                "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
