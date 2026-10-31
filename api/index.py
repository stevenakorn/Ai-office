"""AI 視覺辦公室：多家 AI 轉發後端（Flask，可部署到 Vercel）

API 金鑰只放在伺服器的環境變數裡，瀏覽器永遠看不到。
  ANTHROPIC_API_KEY   Claude
  OPENAI_API_KEY      OpenAI
  GEMINI_API_KEY      Google Gemini
  CUSTOM_BASE_URL     任何 OpenAI 相容服務的網址，例如 https://api.deepseek.com/v1
  CUSTOM_API_KEY      上面那個服務的金鑰
  CUSTOM_NAME         顯示名稱，例如 DeepSeek（選填）
  OFFICE_TOKEN        存取密碼，強烈建議設定，否則任何人拿到網址都能用你的額度
  ALLOWED_ORIGINS     只有前端放在別的網域（例如 GitHub Pages）才需要，逗號分隔
"""
import hmac
import os
import re

import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)


# Vercel 會把 /api/xxx 轉送成 /api/index?__p=xxx，這裡把原本的路徑還原
class VercelPath:
    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        from urllib.parse import parse_qsl, urlencode
        qs = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        p = next((v for k, v in qs if k == "__p"), None)
        if p is not None:
            environ["PATH_INFO"] = "/api/" + p.lstrip("/")
            environ["QUERY_STRING"] = urlencode([(k, v) for k, v in qs if k != "__p"])
        return self.wsgi(environ, start_response)


app.wsgi_app = VercelPath(app.wsgi_app)

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public")
TIMEOUT = int(os.environ.get("AI_TIMEOUT", "110"))
MAX_PROMPT = 60_000
MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "4000"))
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/\-]{1,120}$")


def env(name):
    return (os.environ.get(name) or "").strip()


def configured():
    return {
        "anthropic": bool(env("ANTHROPIC_API_KEY")),
        "openai": bool(env("OPENAI_API_KEY")),
        "gemini": bool(env("GEMINI_API_KEY")),
        "custom": bool(env("CUSTOM_BASE_URL") and env("CUSTOM_API_KEY")),
    }


# ---------------------------------------------------------------- CORS + auth
@app.after_request
def cors(resp):
    origin = request.headers.get("Origin")
    allowed = [o.strip().rstrip("/") for o in env("ALLOWED_ORIGINS").split(",") if o.strip()]
    if origin and ("*" in allowed or origin.rstrip("/") in allowed):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Office-Token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp


@app.route("/api/<path:_p>", methods=["OPTIONS"])
def preflight(_p):
    return "", 204


def unauthorized():
    token = env("OFFICE_TOKEN")
    if not token:
        return None
    given = request.headers.get("X-Office-Token", "")
    if hmac.compare_digest(given.encode(), token.encode()):
        return None
    return jsonify(error="存取密碼錯誤"), 401


class ProviderError(Exception):
    pass


def check(r, name):
    if r.status_code >= 400:
        detail = r.text[:400]
        try:
            j = r.json()
            detail = (j.get("error") or {}).get("message") or j.get("message") or detail
        except Exception:
            pass
        raise ProviderError(f"{name} 回傳錯誤 {r.status_code}：{detail}")
    return r.json()


# ---------------------------------------------------------------- providers
def call_anthropic(model, prompt):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": MAX_TOKENS,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=TIMEOUT)
    j = check(r, "Claude")
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")


def call_openai(model, prompt):
    # Responses API：新舊模型都支援
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}", "Content-Type": "application/json"},
        json={"model": model, "input": prompt},
        timeout=TIMEOUT)
    j = check(r, "OpenAI")
    if j.get("output_text"):
        return j["output_text"]
    out = []
    for item in j.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    out.append(c.get("text", ""))
    return "".join(out)


def call_gemini(model, prompt):
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": env("GEMINI_API_KEY"), "Content-Type": "application/json"},
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
        timeout=TIMEOUT)
    j = check(r, "Gemini")
    cands = j.get("candidates") or []
    if not cands:
        reason = (j.get("promptFeedback") or {}).get("blockReason", "沒有回傳內容")
        raise ProviderError(f"Gemini 沒有產出：{reason}")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def call_custom(model, prompt):
    base = env("CUSTOM_BASE_URL").rstrip("/")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {env('CUSTOM_API_KEY')}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=TIMEOUT)
    j = check(r, env("CUSTOM_NAME") or "自訂服務")
    return (j.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


CALLERS = {"anthropic": call_anthropic, "openai": call_openai, "gemini": call_gemini, "custom": call_custom}

SKIP_OPENAI = ("embedding", "tts", "whisper", "dall-e", "image", "audio", "realtime",
               "transcribe", "moderation", "search", "sora", "davinci", "babbage")


def list_models(provider):
    if provider == "anthropic":
        r = requests.get("https://api.anthropic.com/v1/models?limit=100",
                         headers={"x-api-key": env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"},
                         timeout=20)
        return [m["id"] for m in check(r, "Claude").get("data", [])]
    if provider == "openai":
        r = requests.get("https://api.openai.com/v1/models",
                         headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"}, timeout=20)
        ids = [m["id"] for m in check(r, "OpenAI").get("data", [])]
        return sorted((i for i in ids if not any(s in i for s in SKIP_OPENAI)), reverse=True)
    if provider in ("gemini", "gemini_video"):
        method = "predictLongRunning" if provider == "gemini_video" else "generateContent"
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                         headers={"x-goog-api-key": env("GEMINI_API_KEY")}, timeout=20)
        return sorted((m["name"].removeprefix("models/") for m in check(r, "Gemini").get("models", [])
                       if method in m.get("supportedGenerationMethods", [])), reverse=True)
    if provider == "custom":
        r = requests.get(f"{env('CUSTOM_BASE_URL').rstrip('/')}/models",
                         headers={"Authorization": f"Bearer {env('CUSTOM_API_KEY')}"}, timeout=20)
        return [m["id"] for m in check(r, env("CUSTOM_NAME") or "自訂服務").get("data", [])]
    return []


# ---------------------------------------------------------------- routes
@app.get("/api/providers")
def providers():
    if (bad := unauthorized()):
        return bad
    return jsonify(providers=configured(), custom_name=env("CUSTOM_NAME") or None)


@app.get("/api/models")
def models():
    if (bad := unauthorized()):
        return bad
    p = request.args.get("provider", "")
    if not configured().get("gemini" if p == "gemini_video" else p):
        return jsonify(error=f"{p} 沒有設定金鑰"), 400
    try:
        return jsonify(models=list_models(p))
    except ProviderError as e:
        return jsonify(error=str(e)), 502
    except requests.RequestException as e:
        return jsonify(error=f"連線失敗：{e.__class__.__name__}"), 502


@app.post("/api/chat")
def chat():
    if (bad := unauthorized()):
        return bad
    body = request.get_json(silent=True) or {}
    p, model, prompt = body.get("provider"), (body.get("model") or "").strip(), body.get("prompt") or ""
    if p not in CALLERS:
        return jsonify(error="不支援的 AI 服務"), 400
    if not configured()[p]:
        return jsonify(error=f"後端沒有設定 {p} 的 API 金鑰"), 400
    if not MODEL_RE.match(model):
        return jsonify(error="模型 ID 格式不正確"), 400
    if not prompt or len(prompt) > MAX_PROMPT:
        return jsonify(error="提示內容是空的或太長"), 400
    try:
        return jsonify(text=CALLERS[p](model, prompt))
    except ProviderError as e:
        return jsonify(error=str(e)), 502
    except requests.Timeout:
        return jsonify(error="AI 回應逾時，請改用較快的模型或稍後再試"), 504
    except requests.RequestException as e:
        return jsonify(error=f"連線失敗：{e.__class__.__name__}"), 502


# ---------------------------------------------------------------- video (Google Veo)
VIDEO_HOST = "generativelanguage.googleapis.com"
OP_RE = re.compile(r"^models/[A-Za-z0-9._\-]+/operations/[A-Za-z0-9._\-]+$")
CHUNK = 3_000_000  # 每次回傳 3MB，避開 Vercel 單次回應 4.5MB 的上限


def gkey():
    return {"x-goog-api-key": env("GEMINI_API_KEY")}


@app.post("/api/video/start")
def video_start():
    if (bad := unauthorized()):
        return bad
    if not configured()["gemini"]:
        return jsonify(error="後端沒有設定 GEMINI_API_KEY"), 400
    body = request.get_json(silent=True) or {}
    model, prompt = (body.get("model") or "").strip(), (body.get("prompt") or "").strip()
    aspect = body.get("aspect") if body.get("aspect") in ("16:9", "9:16") else "16:9"
    if not MODEL_RE.match(model) or not prompt or len(prompt) > 4000:
        return jsonify(error="影片模型或提示詞格式不正確"), 400
    try:
        r = requests.post(f"https://{VIDEO_HOST}/v1beta/models/{model}:predictLongRunning",
                          headers={**gkey(), "Content-Type": "application/json"},
                          json={"instances": [{"prompt": prompt}], "parameters": {"aspectRatio": aspect}},
                          timeout=60)
        return jsonify(op=check(r, "Veo").get("name"))
    except ProviderError as e:
        return jsonify(error=str(e)), 502
    except requests.RequestException as e:
        return jsonify(error=f"連線失敗：{e.__class__.__name__}"), 502


@app.get("/api/video/status")
def video_status():
    if (bad := unauthorized()):
        return bad
    op = request.args.get("op", "")
    if not OP_RE.match(op):
        return jsonify(error="任務編號格式不正確"), 400
    try:
        j = check(requests.get(f"https://{VIDEO_HOST}/v1beta/{op}", headers=gkey(), timeout=30), "Veo")
    except ProviderError as e:
        return jsonify(error=str(e)), 502
    except requests.RequestException as e:
        return jsonify(error=f"連線失敗：{e.__class__.__name__}"), 502
    if not j.get("done"):
        return jsonify(done=False)
    if j.get("error"):
        return jsonify(done=True, error=(j["error"].get("message") or "生成失敗"))
    resp = (j.get("response") or {}).get("generateVideoResponse") or {}
    samples = resp.get("generatedSamples") or []
    if not samples:
        reasons = resp.get("raiMediaFilteredReasons") or ["沒有產出影片，可能被安全規則擋下"]
        return jsonify(done=True, error="；".join(map(str, reasons)))
    return jsonify(done=True, uri=samples[0]["video"]["uri"])


@app.get("/api/video/file")
def video_file():
    if (bad := unauthorized()):
        return bad
    uri = request.args.get("uri", "")
    if not uri.startswith(f"https://{VIDEO_HOST}/"):
        return jsonify(error="影片網址不正確"), 400
    start = max(0, int(request.args.get("start", "0") or 0))
    try:
        r = requests.get(uri, headers={**gkey(), "Range": f"bytes={start}-{start + CHUNK - 1}"},
                         timeout=90, allow_redirects=True)
    except requests.RequestException as e:
        return jsonify(error=f"下載失敗：{e.__class__.__name__}"), 502
    if r.status_code == 206:
        content = r.content
        total = int((r.headers.get("Content-Range", "").split("/") + ["0"])[1] or 0) or start + len(content)
    elif r.status_code == 200:  # 對方不支援分段下載時，自己切
        total, content = len(r.content), r.content[start:start + CHUNK]
    else:
        return jsonify(error=f"下載失敗：HTTP {r.status_code}"), 502
    out = app.response_class(content, mimetype="video/mp4")
    out.headers["X-Total-Size"] = str(total)
    out.headers["Cache-Control"] = "no-store"
    return out


# 本機執行時順便提供網頁；在 Vercel 上由 public/ 直接提供
@app.get("/")
def home():
    return send_from_directory(PUBLIC_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
