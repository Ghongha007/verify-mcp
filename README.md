# verify-mcp

A small [Model Context Protocol](https://modelcontextprotocol.io) server that gives an AI assistant nine read-only lookups for verification work: page history, domain age, redirect chains, sun and shadow maths.

- **No API keys.** Every lookup uses a free public service or is computed locally.
- **No dependencies.** One Python package, standard library only. Python 3.10 or newer.
- **Read-only.** It looks things up. It never posts, saves or publishes anything.
- **No people lookups.** There are no tools for finding a person's accounts, phone, address or face.

## Tools

| Tool | Question it answers | Source |
|---|---|---|
| `wayback_history` | When did this page first exist? What did it say before it was edited? | Internet Archive CDX API |
| `wayback_closest` | What is the archived copy nearest to this date? | Internet Archive |
| `domain_registration` | How old is this website's domain? Who is the registrar? | RDAP (via rdap.org) |
| `dns_records` | Where does this domain point? Who handles its mail? | Cloudflare public DNS |
| `certificate_names` | What sub-sites has this domain run, and since when? | Certificate transparency (crt.sh) |
| `expand_link` | Where does this short link really go? | Direct HEAD requests |
| `reverse_image_links` | Where can I search for earlier copies of this image? | Builds links only |
| `sun_position` | Where was the sun, and which way did shadows fall, at this place and time? | Computed locally (NOAA equations) |
| `shadow_times` | At what time of day does this shadow fit? | Computed locally |

Each result ends with a line on what the lookup cannot show, so the assistant has the caveat in front of it when it writes the answer.

## Install

```
git clone https://github.com/Ghongha007/verify-mcp.git
cd verify-mcp
pip install .
```

**Claude Code**

```
claude mcp add verify -- python3 -m verify_mcp
```

**Claude Desktop and other clients** that use a JSON config:

```json
{
  "mcpServers": {
    "verify": { "command": "python3", "args": ["-m", "verify_mcp"] }
  }
}
```

If you did not `pip install`, set the working directory to this folder, or use the full path to Python and add `"env": {"PYTHONPATH": "/path/to/verify-mcp"}`.

## Examples

Ask the assistant in plain words:

- "This site says it has reported on the region since 2015. When was the domain registered and when did the Wayback Machine first see it?"
- "Where does this short link go? Don't open it."
- "The post says this video was filmed at 5 pm on 16 November at 28.6139, 77.2090. The lamp-post shadow points north-west. Does that fit?"

Sample output of `domain_registration`:

```
Registration record (RDAP) for gephi.org
Registered:   2008-06-02T10:16:57.736Z
Registrar:    Gandi SAS
Name servers: fred.ns.cloudflare.com, lisa.ns.cloudflare.com
Registration date is when the current registration began. A domain can be older
(dropped and re-registered), and registrant details are usually redacted.
```

## What leaves your machine

| Tool | What is sent, and to whom |
|---|---|
| `wayback_history`, `wayback_closest` | the page address, to archive.org |
| `domain_registration` | the domain, to rdap.org and the registry it redirects to |
| `dns_records` | the domain, to cloudflare-dns.com |
| `certificate_names` | the domain, to crt.sh |
| `expand_link` | a HEAD request to the link itself and to each redirect. **The site will see your IP address.** |
| `reverse_image_links`, `sun_position`, `shadow_times` | nothing |

If you are checking a link sent by a hostile party, remember that `expand_link` contacts their server from your network. Use it through a VPN, or not at all.

## Safety choices

- `expand_link` refuses addresses that resolve to private, loopback or link-local networks, and anything that is not http or https. It sends HEAD requests only and never downloads the page. The address is checked before each request; this does not defend against a DNS server that changes its answer between the check and the request, so do not run the server on a machine with sensitive internal services if you plan to expand untrusted links.
- Responses are capped at 2 MB.
- A failed lookup returns a plain error to the assistant and the server keeps running.

## Limits

- The public services behind these tools rate-limit and go down. `crt.sh` in particular is often slow or returns errors; try again later.
- RDAP is not offered by every top-level domain, and many country-code domains return little.
- The sun maths ignores atmospheric refraction, so it is approximate when the sun is within about 3 degrees of the horizon.
- The protocol layer is written by hand against MCP versions 2024-11-05 to 2025-06-18 and supports tools over stdio only. It has been tested with scripted JSON-RPC messages (see `tests/`) and with the official MCP Python client library (version 2.3.0): handshake, tool listing and tool calls all worked. It has not yet been tried inside Claude Desktop or other chat applications, so please report problems.

## Tests

```
python3 -m unittest discover -s tests
```

The tests run offline.

## Related

- [india-osint](https://github.com/Ghongha007/india-osint): public records and open data for investigating stories in India
- [ai-for-osint](https://github.com/Ghongha007/ai-for-osint): AI tools for investigators, with where each runs and its known limits
- [chat2net](https://github.com/Ghongha007/chat2net): turn a chat or social-media export into a Gephi network
- [osint-skills](https://github.com/Ghongha007/osint-skills): agent skills for verification, geolocation, chronolocation and fact-checking
- [osint-visuals](https://github.com/Ghongha007/osint-visuals): agent skills for evidence graphics: ship and flight tracks, shadow diagrams, 3D scenes, satellite before/after and scroll stories

## Licence

MIT. Made by [OSINT JOURNO](https://github.com/OSINT-JOURNO).
