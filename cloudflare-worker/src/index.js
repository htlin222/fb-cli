// Facebook OAuth callback relay + mention webhook receiver.
//
// Routes
//   GET  /callback?code=...&state=...    stash OAuth code in KV by state (10 min TTL)
//   GET  /poll?state=...                  read+delete OAuth code (single-use)
//   GET  /webhook?hub.mode=subscribe&hub.challenge=X&hub.verify_token=Y
//                                         Meta verification handshake
//   POST /webhook                         receive Page events from Meta;
//                                         HMAC-verify (X-Hub-Signature-256) with
//                                         FB_APP_SECRET, store each `change` in KV
//   GET  /mentions?token=Y[&since=ms]     CLI polls; returns events with
//                                         time > since (default 0 = all)
//   GET  /health  or  /                   liveness probe
//
// Secrets (set via `wrangler secret put <NAME>`):
//   FB_APP_SECRET             -- the Meta app secret, for HMAC verify
//   FB_WEBHOOK_VERIFY_TOKEN   -- random string shared with Meta + CLI; doubles
//                                as bearer for /mentions polls

const HTML_OK = `<!doctype html>
<meta charset=utf-8>
<title>Facebook OAuth callback</title>
<style>body{font:16px system-ui;margin:4rem auto;max-width:32rem;padding:0 1rem}</style>
<h2>Facebook OAuth code captured.</h2>
<p>You can close this tab &mdash; the script will pick it up automatically.</p>`;

const MENTION_TTL_SEC = 86400 * 7; // keep events for 7 days

async function verifyHmac(appSecret, rawBody, sigHeader) {
  if (!sigHeader || !sigHeader.startsWith("sha256=")) return false;
  const expected = sigHeader.slice(7);
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(appSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(rawBody));
  const computed = [...new Uint8Array(sig)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  // Constant-time compare.
  if (computed.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < computed.length; i++) {
    diff |= computed.charCodeAt(i) ^ expected.charCodeAt(i);
  }
  return diff === 0;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ---- OAuth callback relay ------------------------------------------
    if (url.pathname === "/callback") {
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      const error = url.searchParams.get("error");
      if (error) {
        return new Response(
          `OAuth error: ${error} - ${url.searchParams.get("error_description") || ""}`,
          { status: 400 },
        );
      }
      if (!code || !state) {
        return new Response("missing code or state", { status: 400 });
      }
      await env.FB_AUTH.put(`code:${state}`, code, { expirationTtl: 600 });
      return new Response(HTML_OK, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }

    if (url.pathname === "/poll") {
      const state = url.searchParams.get("state");
      if (!state) return new Response("missing state", { status: 400 });
      const code = await env.FB_AUTH.get(`code:${state}`);
      if (!code) return new Response("", { status: 204 });
      await env.FB_AUTH.delete(`code:${state}`);
      return new Response(code, {
        headers: { "content-type": "text/plain" },
      });
    }

    // ---- Webhook: Meta verification handshake --------------------------
    if (url.pathname === "/webhook" && request.method === "GET") {
      const mode = url.searchParams.get("hub.mode");
      const token = url.searchParams.get("hub.verify_token");
      const challenge = url.searchParams.get("hub.challenge");
      if (mode === "subscribe" && token === env.FB_WEBHOOK_VERIFY_TOKEN) {
        return new Response(challenge ?? "", { status: 200 });
      }
      return new Response("verification failed", { status: 403 });
    }

    // ---- Webhook: receive event from Meta ------------------------------
    if (url.pathname === "/webhook" && request.method === "POST") {
      const rawBody = await request.text();
      if (!env.FB_APP_SECRET) {
        return new Response("server missing FB_APP_SECRET", { status: 500 });
      }
      const sigHeader = request.headers.get("x-hub-signature-256");
      const ok = await verifyHmac(env.FB_APP_SECRET, rawBody, sigHeader);
      if (!ok) return new Response("bad signature", { status: 403 });

      let payload;
      try {
        payload = JSON.parse(rawBody);
      } catch {
        return new Response("bad json", { status: 400 });
      }

      const now = Date.now();
      let stored = 0;
      for (const entry of payload.entry || []) {
        for (const change of entry.changes || []) {
          // Key shape: mention:<unix_ms>:<entry_id>:<rand>  -- lex-sortable.
          const key = `mention:${String(now).padStart(15, "0")}:${entry.id}:${crypto.randomUUID().slice(0, 8)}`;
          await env.FB_AUTH.put(
            key,
            JSON.stringify({
              entry_id: entry.id,
              time_ms: now,
              entry_time: entry.time,
              field: change.field,
              value: change.value,
            }),
            { expirationTtl: MENTION_TTL_SEC },
          );
          stored++;
        }
      }
      return new Response(JSON.stringify({ stored }), {
        headers: { "content-type": "application/json" },
      });
    }

    // ---- CLI polls /mentions -------------------------------------------
    if (url.pathname === "/mentions") {
      const token = url.searchParams.get("token");
      if (!env.FB_WEBHOOK_VERIFY_TOKEN || token !== env.FB_WEBHOOK_VERIFY_TOKEN) {
        return new Response("unauthorized", { status: 401 });
      }
      const since = parseInt(url.searchParams.get("since") || "0", 10);
      const list = await env.FB_AUTH.list({ prefix: "mention:", limit: 1000 });
      const events = [];
      for (const k of list.keys) {
        const parts = k.name.split(":");
        const ts = parseInt(parts[1], 10);
        if (ts <= since) continue;
        const val = await env.FB_AUTH.get(k.name);
        if (val) events.push(JSON.parse(val));
      }
      events.sort((a, b) => a.time_ms - b.time_ms);
      return new Response(JSON.stringify({ events, count: events.length }), {
        headers: { "content-type": "application/json" },
      });
    }

    if (url.pathname === "/" || url.pathname === "/health") {
      return new Response("fb-cb worker ok", { status: 200 });
    }

    return new Response("not found", { status: 404 });
  },
};
