#!/usr/bin/env python3
"""
Procentjakten – festserver.
Starta:  python server.py          (port 8000)
         python server.py 8080     (annan port)
Kräver bara Python 3.8+ – inga extra paket.
"""
import json, os, random, re, socket, sys, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
RESULTS = os.path.join(HERE, "results.json")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
SECONDS = 30
GRACE = 2.5          # extra sekunder för nätfördröjning
PIN = os.environ.get("PJ_PIN") or str(random.randint(1000, 9999))

with open(os.path.join(HERE, "questions.json"), encoding="utf-8") as f:
    QUESTIONS = json.load(f)

lock = threading.Lock()
players = {}
if os.path.exists(RESULTS):
    try:
        with open(RESULTS, encoding="utf-8") as f:
            players = json.load(f)
    except Exception:
        players = {}


def save():
    tmp = RESULTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(players, f, ensure_ascii=False, indent=1)
    os.replace(tmp, RESULTS)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


IP = os.environ.get("PJ_IP") or lan_ip()
URL = (os.environ.get("PJ_URL") or os.environ.get("RENDER_EXTERNAL_URL") or f"http://{IP}:{PORT}").rstrip("/")
WIFI_SSID = os.environ.get("PJ_WIFI_SSID", "")
WIFI_PASS = os.environ.get("PJ_WIFI_PASS", "")


def norm(s):
    s = (s or "").lower().strip().replace(",", ".").replace("°", " ")
    s = re.sub(r"(?:(?<=\d)|\b)(kg|kilo|grader|grad|minuter|minut|min|st|stycken)\b", " ", s)
    s = re.sub(r"[^a-z0-9.åäö]", "", s)
    return s.strip(".")


def is_correct(answer, accept):
    a = norm(answer)
    if not a:
        return False
    for ok in accept:
        o = norm(ok)
        if a == o:
            return True
        try:
            if abs(float(a) - float(o)) < 1e-9:
                return True
        except ValueError:
            pass
    return False


def public_q(i):
    q = QUESTIONS[i]
    return {"index": i, "total": len(QUESTIONS), "pct": q["pct"], "q": q["q"],
            "seq": q.get("seq", ""), "kb": q.get("kb", "text")}


def cleared_pct(p):
    return QUESTIONS[p["correct"] - 1]["pct"] if p["correct"] > 0 else None


def expire_if_needed(p):
    """Markera spelaren som ute om tiden gått ut utan svar (t.ex. stängd mobil)."""
    if p["started"] and not p["out"] and not p["done"]:
        el = time.time() - p["started"]
        if el > SECONDS + GRACE + 3:
            p["out"] = True
            p["total_ms"] += SECONDS * 1000
            p["started"] = None
            p["last_answer"] = ""
            return True
    return False


def player_view(p):
    v = {"name": p["name"], "score": p["correct"], "out": p["out"], "done": p["done"],
         "cleared": cleared_pct(p), "total_ms": p["total_ms"]}
    if not p["out"] and not p["done"]:
        v["next"] = public_q(p["level"])
        if p["started"]:
            v["seconds_left"] = max(0.0, SECONDS - (time.time() - p["started"]))
    return v


def leaderboard():
    rows = []
    for pid, p in players.items():
        expire_if_needed(p)
        status = "klar" if p["done"] else ("ute" if p["out"] else "spelar")
        rows.append({"name": p["name"], "correct": p["correct"], "cleared": cleared_pct(p),
                     "status": status, "total_ms": p["total_ms"],
                     "now": QUESTIONS[p["level"]]["pct"] if status == "spelar" else None})
    rows.sort(key=lambda r: (-r["correct"], r["total_ms"]))
    return rows


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def file(self, name, ctype):
        path = os.path.join(STATIC, name)
        if not os.path.exists(path):          # filerna får även ligga direkt i mappen
            path = os.path.join(HERE, name)
        with open(path, "rb") as f:
            self.send(200, f.read(), ctype)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self.file("index.html", "text/html; charset=utf-8")
        if u.path in ("/tv", "/tv/"):
            return self.file("tv.html", "text/html; charset=utf-8")
        if u.path == "/qr.js":
            return self.file("qr.js", "application/javascript; charset=utf-8")
        if u.path == "/api/info":
            return self.send(200, {"url": URL, "wifi_ssid": WIFI_SSID, "wifi_pass": WIFI_PASS, "levels": [q["pct"] for q in QUESTIONS], "seconds": SECONDS})
        if u.path == "/api/state":
            pid = (qs.get("id") or [""])[0]
            with lock:
                p = players.get(pid)
                if not p:
                    return self.send(404, {"error": "okänd spelare"})
                if expire_if_needed(p):
                    save()
                return self.send(200, player_view(p))
        if u.path == "/api/leaderboard":
            with lock:
                rows = leaderboard()
            return self.send(200, {"rows": rows, "levels": [q["pct"] for q in QUESTIONS]})
        self.send(404, {"error": "finns inte"})

    def do_POST(self):
        u = urlparse(self.path)
        d = self.body()
        if u.path == "/api/join":
            name = re.sub(r"\s+", " ", str(d.get("name", ""))).strip()[:20]
            if not name:
                return self.send(400, {"error": "Skriv ett namn"})
            pid = uuid.uuid4().hex
            with lock:
                players[pid] = {"name": name, "level": 0, "correct": 0, "out": False, "done": False,
                                "started": None, "total_ms": 0, "joined": time.time(), "last_answer": ""}
                save()
            return self.send(200, {"id": pid})

        if u.path == "/api/start":
            with lock:
                p = players.get(str(d.get("id", "")))
                if not p:
                    return self.send(404, {"error": "okänd spelare"})
                expire_if_needed(p)
                if p["out"] or p["done"]:
                    return self.send(200, player_view(p))
                if not p["started"]:
                    p["started"] = time.time()
                    save()
                return self.send(200, player_view(p))

        if u.path == "/api/answer":
            with lock:
                p = players.get(str(d.get("id", "")))
                if not p:
                    return self.send(404, {"error": "okänd spelare"})
                if p["out"] or p["done"] or not p["started"]:
                    return self.send(200, {"ignored": True, **player_view(p)})
                q = QUESTIONS[p["level"]]
                elapsed = time.time() - p["started"]
                late = elapsed > SECONDS + GRACE
                ans = str(d.get("answer", ""))[:60]
                ok = (not late) and is_correct(ans, q["accept"])
                p["total_ms"] += int(min(elapsed, SECONDS) * 1000)
                p["started"] = None
                p["last_answer"] = ans
                if ok:
                    p["correct"] += 1
                    p["level"] += 1
                    if p["level"] >= len(QUESTIONS):
                        p["done"] = True
                        p["level"] = len(QUESTIONS) - 1
                else:
                    p["out"] = True
                save()
                return self.send(200, {"correct": ok, "late": late, "answer": q["a"], "explanation": q["x"],
                                       "pct": q["pct"], **player_view(p)})

        if u.path == "/api/reset":
            if str(d.get("pin", "")) != PIN:
                return self.send(403, {"error": "Fel PIN"})
            with lock:
                players.clear()
                save()
            return self.send(200, {"ok": True})

        self.send(404, {"error": "finns inte"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    line = "=" * 56
    print(line)
    print("  PROCENTJAKTEN är igång!")
    print(f"  Topplista (öppna på TV:n):  {URL}/tv")
    print(f"  Gästerna går till:          {URL}")
    print(f"  PIN för att nollställa:     {PIN}")
    print("  Stäng av med Ctrl+C")
    print(line)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nHej då!")
