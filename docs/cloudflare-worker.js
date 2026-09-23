// Optional Cloudflare Worker that relays DeepSeek API calls (useful where
// api.deepseek.com is not directly reachable from your network).
//
// Deploy it, then set in .streamlit/secrets.toml:
//   DEEPSEEK_BASE_URL = "https://<your-worker>.workers.dev/deepseek"
//
// It only relays POST /deepseek/chat/completions and adds no credentials: the app sends its
// own API key (Authorization header), which the worker forwards untouched.
const UPSTREAM = "https://api.deepseek.com";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/deepseek/chat/completions") {
      return new Response("Not found", { status: 404 });
    }
    const headers = new Headers(request.headers);
    headers.delete("host");
    return fetch(new Request(UPSTREAM + "/chat/completions", { method: "POST", headers, body: request.body }));
  },
};
