"""Kết nối Facebook Page + Instagram qua Graph API và đăng bài theo lịch.
Chỉ dùng thư viện chuẩn của Python. File này được server.py gọi, không chạy riêng."""
import base64, json, os, threading, time, uuid
import urllib.request, urllib.error, urllib.parse

GRAPH = "https://graph.facebook.com/v24.0"
HERE = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(HERE, "social.json")
JOBS_FILE = os.path.join(HERE, "jobs.json")
IMG_DIR = os.path.join(HERE, "jobs")
_lock = threading.RLock()


class GraphError(Exception):
    pass


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _friendly(err):
    code, msg = err.get("code"), err.get("message", "")
    hint = ""
    if code == 190:
        hint = "Token hết hạn hoặc không hợp lệ."
    elif code in (3, 10, 200, 299):
        hint = "Thiếu quyền truy cập Page hoặc Instagram."
    return "%s%s (mã %s)" % (msg, (" — " + hint) if hint else "", code)


def _http(method, url, data=None, ctype=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
    except Exception as e:
        raise GraphError("Không kết nối được tới Facebook: %s" % e)
    try:
        d = json.loads(body)
    except Exception:
        raise GraphError("Facebook trả về dữ liệu lạ: " + body[:200])
    if isinstance(d, dict) and "error" in d:
        raise GraphError(_friendly(d["error"]))
    return d


def _get(path, token, **params):
    if token:
        params["access_token"] = token
    return _http("GET", GRAPH + path + "?" + urllib.parse.urlencode(params))


def _post(path, token, fields, files=None):
    fields = dict(fields)
    fields["access_token"] = token
    if not files:
        return _http("POST", GRAPH + path, urllib.parse.urlencode(fields).encode(),
                     "application/x-www-form-urlencoded")
    b = "----shop" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode("utf-8"))
    for k, (fn, data, ct) in files.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
                    % (b, k, fn, ct)).encode("utf-8") + data + b"\r\n")
    out.append(("--%s--\r\n" % b).encode())
    return _http("POST", GRAPH + path, b"".join(out), "multipart/form-data; boundary=" + b, timeout=120)


def _public(cfg):
    pages = cfg.get("pages", [])
    act = next((p for p in pages if p["id"] == cfg.get("active")), None)
    pub = lambda p: {"id": p["id"], "name": p["name"], "ig_user": p.get("ig_user") or ""}
    return {"connected": bool(act), "page": pub(act) if act else None,
            "pages": [pub(p) for p in pages], "long_lived": cfg.get("long_lived", False)}


def status():
    return _public(_load(CFG_FILE, {}))


def connect(user_token, app_id="", app_secret=""):
    tok = (user_token or "").strip()
    if not tok:
        raise GraphError("Chưa dán token.")
    long_lived = False
    if app_id and app_secret:
        d = _get("/oauth/access_token", None, grant_type="fb_exchange_token", client_id=app_id.strip(),
                 client_secret=app_secret.strip(), fb_exchange_token=tok)
        tok, long_lived = d["access_token"], True
    d = _get("/me/accounts", tok, fields="id,name,access_token,instagram_business_account{id,username}", limit=50)
    pages = []
    for p in d.get("data", []):
        ig = p.get("instagram_business_account") or {}
        pages.append({"id": p["id"], "name": p["name"], "token": p["access_token"],
                      "ig_id": ig.get("id", ""), "ig_user": ig.get("username", "")})
    if not pages:
        raise GraphError("Token không thấy Page nào.")
    cfg = {"pages": pages, "active": pages[0]["id"], "long_lived": long_lived}
    with _lock:
        _save(CFG_FILE, cfg)
    return _public(cfg)


def select(page_id):
    with _lock:
        cfg = _load(CFG_FILE, {})
        if not any(p["id"] == page_id for p in cfg.get("pages", [])):
            raise GraphError("Không tìm thấy Page này.")
        cfg["active"] = page_id
        _save(CFG_FILE, cfg)
    return _public(cfg)


def _page(page_id=None):
    cfg = _load(CFG_FILE, {})
    pid = page_id or cfg.get("active")
    p = next((p for p in cfg.get("pages", []) if p["id"] == pid), None)
    if not p:
        raise GraphError("Chưa kết nối Facebook.")
    return p


def _upload_photo_directly(page, message, img):
    fields = {"message": message}
    d = _post("/%s/photos" % page["id"], page["token"], fields,
              files={"source": ("sanpham.jpg", img, "image/jpeg")})
    return d["id"]


def _photo_url(page, photo_id):
    imgs = _get("/%s" % photo_id, page["token"], fields="images").get("images") or []
    if not imgs:
        raise GraphError("Không lấy được địa chỉ ảnh từ Facebook.")
    return imgs[0]["source"]


def _ig_publish(page, caption, image_url):
    ig = page.get("ig_id")
    if not ig:
        raise GraphError("Page chưa liên kết Instagram Professional.")
    c = _post("/%s/media" % ig, page["token"], {"image_url": image_url, "caption": caption[:2200]})["id"]
    for _ in range(30):
        s = _get("/%s" % c, page["token"], fields="status_code").get("status_code")
        if s == "FINISHED":
            break
        if s in ("ERROR", "EXPIRED"):
            raise GraphError("Instagram không xử lý được ảnh.")
        time.sleep(2)
    else:
        raise GraphError("Instagram xử lý ảnh quá lâu.")
    m = _post("/%s/media_publish" % ig, page["token"], {"creation_id": c})["id"]
    link = ""
    try:
        link = _get("/%s" % m, page["token"], fields="permalink").get("permalink", "")
    except GraphError:
        pass
    return link


def _view(j):
    return {k: j.get(k) for k in ("id", "productId", "name", "when", "t", "msg", "links")}


def create_job(d):
    page = _page()
    targets = [t for t in d.get("targets", []) if t in ("fb", "ig")]
    if not targets:
        raise GraphError("Chưa chọn nơi đăng.")
    if not d.get("image"):
        raise GraphError("Sản phẩm chưa có ảnh.")
    img = base64.b64decode(d["image"])
    now = time.time()
    when = max(float(d["when"]), now)
    
    product_id = d.get("productId")
    
    with _lock:
        jobs = _load(JOBS_FILE, [])
        if product_id is not None:
            jobs = [j for j in jobs if j.get("productId") != product_id]

        job = {"id": uuid.uuid4().hex[:10], "productId": product_id, "name": d.get("name", ""),
               "message": d["message"], "when": when, "created": now, "page": page["id"],
               "t": {k: "pending" for k in targets}, "msg": {}, "links": {}}
        os.makedirs(IMG_DIR, exist_ok=True)
        with open(os.path.join(IMG_DIR, job["id"] + ".jpg"), "wb") as f:
            f.write(img)
        jobs.append(job)
        _save(JOBS_FILE, jobs)
    return _view(job)


def list_jobs():
    return [_view(j) for j in _load(JOBS_FILE, [])[-100:]]


def cancel(job_id):
    with _lock:
        jobs = _load(JOBS_FILE, [])
        j = next((x for x in jobs if x["id"] == job_id), None)
        if not j:
            raise GraphError("Không tìm thấy lịch này.")
        for k, v in j["t"].items():
            if v in ("pending", "scheduled"):
                j["t"][k] = "canceled"
        _save(JOBS_FILE, jobs)
        return _view(j)


def _run_job(job):
    page = _page(job["page"])
    with open(os.path.join(IMG_DIR, job["id"] + ".jpg"), "rb") as f:
        img = f.read()
    for k in list(job["t"]):
        if job["t"][k] != "processing":
            continue
        try:
            if k == "fb":
                post_id = _upload_photo_directly(page, job["message"], img)
                job["links"]["fb"] = "https://www.facebook.com/" + post_id
                job["msg"]["fb"] = "Đã đăng lên Facebook thành công."
            else:
                temp_fb_id = _upload_photo_directly(page, job["message"], img)
                img_url = _photo_url(page, temp_fb_id)
                link = _ig_publish(page, job["message"], img_url)
                if link:
                    job["links"]["ig"] = link
                job["msg"]["ig"] = "Đã đăng lên Instagram."
            job["t"][k] = "done"
        except Exception as e:
            job["t"][k] = "error"
            job["msg"][k] = str(e)


def _loop():
    while True:
        try:
            now = time.time()
            with _lock:
                jobs = _load(JOBS_FILE, [])
                due = [j for j in jobs if any(v == "pending" for v in j["t"].values()) and j["when"] <= now]
                updated = False
                for j in due:
                    for k in j["t"]:
                        if j["t"][k] == "pending":
                            j["t"][k] = "processing"
                    updated = True
                if updated:
                    _save(JOBS_FILE, jobs)

            for j in due:
                try:
                    _run_job(j)
                    with _lock:
                        cur = _load(JOBS_FILE, [])
                        for i, x in enumerate(cur):
                            if x["id"] == j["id"]:
                                cur[i] = j
                        _save(JOBS_FILE, cur)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(5)


def start_worker():
    try:
        if os.path.exists(JOBS_FILE):
            os.remove(JOBS_FILE)
    except Exception:
        pass
    threading.Thread(target=_loop, daemon=True).start()


def handle_get(route):
    try:
        if route == "/api/social/status":
            return {"ok": True, **status()}
        if route == "/api/social/jobs":
            return {"ok": True, "jobs": list_jobs()}
        return {"ok": False, "error": "Không có chức năng này."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_post(route, r):
    try:
        if route == "/api/social/connect":
            return {"ok": True, **connect(r.get("userToken"), r.get("appId", ""), r.get("appSecret", ""))}
        if route == "/api/social/select":
            return {"ok": True, **select(r.get("pageId"))}
        if route == "/api/social/schedule":
            return {"ok": True, "job": create_job(r)}
        if route == "/api/social/cancel":
            return {"ok": True, "job": cancel(r.get("id"))}
        return {"ok": False, "error": "Không có chức năng này."}
    except Exception as e:
        return {"ok": False, "error": str(e)}