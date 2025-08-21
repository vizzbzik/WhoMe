import os, time, sqlite3, shutil, re
from fastapi import FastAPI, Request, Form, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_NAME = "WhoMe"

# ── FS ─────────────────────────────────────────────────────────────────────────
os.makedirs("static/avatars", exist_ok=True)
os.makedirs("static/posts", exist_ok=True)

# ── DB ─────────────────────────────────────────────────────────────────────────
def db():
    conn = sqlite3.connect("whome.db")
    conn.row_factory = sqlite3.Row
    return conn

with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        username TEXT UNIQUE,
        first_name TEXT,
        last_name TEXT,
        avatar TEXT,
        password TEXT,
        is_admin INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        content TEXT,
        image TEXT,
        created_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS chats(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user1_id INTEGER,
        user2_id INTEGER,
        UNIQUE(user1_id,user2_id)
    );
    CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        sender_id INTEGER,
        kind TEXT,
        body TEXT,
        created_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS channels(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        description TEXT,
        owner_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS channel_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id INTEGER,
        sender_id INTEGER,
        content TEXT,
        image TEXT,
        created_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS premium_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        given_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Комментарии: фикс TIMESTAMP, есть content, верные внешние ключи
    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    UNIQUE(user_id, post_id)
);
    """)

# ── APP ────────────────────────────────────────────────────────────────────────
app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── helpers ───────────────────────────────────────────────────────────────────
def cur_user(request: Request):
    u = request.cookies.get("whome_user")
    if not u:
        return None
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
    return row  # row['is_admin'] = 1 если админ

# dependency для Depends(...)
def get_current_user(request: Request):
    return cur_user(request)

def valid_username(u:str)->bool:
    return bool(re.fullmatch(r"@[a-zA-Z0-9_]{3,20}", u or ""))

# ── password helpers ───────────────────────────────────────────────────
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(password, hashed)
    except Exception:
        return False

# === Список каналов ===
@app.get("/channels", response_class=HTMLResponse)
def list_channels(request: Request):
    me = cur_user(request)
    with db() as c:
        channels = c.execute("SELECT * FROM channels ORDER BY name ASC").fetchall()
    return templates.TemplateResponse("channels.html", {
        "request": request, "channels": channels, "me": me
    })

# === Форма создания канала (только админ) ===
@app.get("/channels/create", response_class=HTMLResponse)
def create_channel_form(request: Request):
    me = cur_user(request)
    if not me or not me["is_admin"]:
        return PlainTextResponse("Permission denied", status_code=403)
    return templates.TemplateResponse("create_channel.html", {"request": request, "me": me})

# === Создание канала ===
@app.post("/channels/create")
def create_channel(request: Request, name: str = Form(...), description: str = Form("")):
    me = cur_user(request)
    if not me or not me["is_admin"]:
        return PlainTextResponse("Permission denied", status_code=403)
    with db() as c:
        c.execute("INSERT INTO channels(name, description, owner_id) VALUES(?,?,?)", (name, description, me["id"]))
        c.commit()
    return RedirectResponse("/channels", status_code=303)

# === Просмотр канала ===
@app.get("/channel/{channel_id}", response_class=HTMLResponse)
def view_channel(request: Request, channel_id: int):
    me = cur_user(request)
    with db() as c:
        channel = c.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
        if not channel:
            return PlainTextResponse("Channel not found", status_code=404)
        messages = c.execute("""
            SELECT cm.*, u.username, u.avatar
            FROM channel_messages cm
            JOIN users u ON cm.sender_id = u.id
            WHERE cm.channel_id=?
            ORDER BY cm.created_at ASC
        """, (channel_id,)).fetchall()
    return templates.TemplateResponse("channel_chat.html", {
        "request": request, "me": me, "channel": channel, "messages": messages
    })

# === Отправка сообщения в канал ===
@app.post("/channel/{channel_id}/send")
def send_channel_message(request: Request, channel_id: int, content: str = Form(""), image: UploadFile = File(None)):
    me = cur_user(request)
    if not me:
        return RedirectResponse("/login", status_code=303)

    img_path = None
    if image and image.filename:
        os.makedirs("static/channel_uploads", exist_ok=True)
        ext = os.path.splitext(image.filename)[1]
        img_path = f"static/channel_uploads/{int(time.time())}{ext}"
        with open(img_path, "wb") as f:
            f.write(image.file.read())

    with db() as c:
        c.execute("""
            INSERT INTO channel_messages(channel_id, sender_id, content, image, created_at)
            VALUES(?,?,?,?,?)
        """, (channel_id, me["id"], content, img_path, int(time.time())))
        c.commit()

    return RedirectResponse(f"/channel/{channel_id}", status_code=303)

# ── PAGES ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    me = cur_user(request)
    with db() as c:
        posts = c.execute("""
            SELECT posts.*, users.username, users.first_name, users.last_name, users.avatar, users.is_verified
            FROM posts
            JOIN users ON posts.user_id = users.id
            ORDER BY posts.created_at DESC
        """).fetchall()

        # все комментарии сразу, сгруппуем по post_id
        comm_rows = c.execute("""
            SELECT c.*, u.username, u.avatar
            FROM comments c
            JOIN users u ON u.id = c.user_id
            ORDER BY c.created_at ASC, c.id ASC
        """).fetchall()

    comments_by_post = {}
    counts = {}
    for r in comm_rows:
        pid = r["post_id"]
        comments_by_post.setdefault(pid, []).append(r)
        counts[pid] = counts.get(pid, 0) + 1

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "me": me,
            "posts": posts,
            "comments_by_post": comments_by_post,
            "comment_counts": counts,
            "APP": APP_NAME,
        },
    )

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request":request, "APP":APP_NAME})

# ── REGISTER ───────────────────────────────────────────────────────────
@app.post("/register")
def register(
    email: str = Form(...),
    username: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    password: str = Form(...),
    avatar: UploadFile = File(None)
):
    if not valid_username(username):
        return RedirectResponse("/register?e=bad_username", status_code=303)

    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE username=? OR email=?", (username, email)).fetchone():
            return RedirectResponse("/register?e=exists", status_code=303)

        avatar_path = None
        if avatar and avatar.filename:
            os.makedirs("static/avatars", exist_ok=True)
            clean_username = username.lstrip("@")
            avatar_path = f"static/avatars/{clean_username}.png"
            with open(avatar_path, "wb") as f:
                shutil.copyfileobj(avatar.file, f)

        password_hash = hash_password(password)
        c.execute(
            "INSERT INTO users(email,username,first_name,last_name,avatar,password) VALUES(?,?,?,?,?,?)",
            (email, username, first_name, last_name, avatar_path, password_hash)
        )
        c.commit()

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("whome_user", username, httponly=True)
    return resp

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request":request, "APP":APP_NAME})

@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not u or not verify_password(password, u["password"] or ""):
        return RedirectResponse("/login?e=bad", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("whome_user", username, httponly=True)
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("whome_user")
    return resp

# ── POSTS ─────────────────────────────────────────────────────────────────────
@app.post("/post")
def create_post(request: Request, content: str = Form(""), image: UploadFile = File(None)):
    me = cur_user(request)
    if not me: return RedirectResponse("/login", status_code=303)
    img_path = None
    if image and image.filename:
        img_path = f"static/posts/{int(time.time())}_{image.filename}"
        with open(img_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
    with db() as c:
        c.execute("INSERT INTO posts(user_id,content,image,created_at) VALUES(?,?,?,?)",
                  (me["id"], content, img_path, int(time.time())))
        c.commit()
    return RedirectResponse("/", status_code=303)

# ── COMMENTS ──────────────────────────────────────────────────────────────────
@app.post("/post/{post_id}/comment")
def add_comment(
    request: Request,
    post_id: int,
    content: str = Form(...),
    user: dict = Depends(get_current_user)
):
    if not user:
        return RedirectResponse("/login", status_code=303)
    content = (content or "").strip()
    if not content:
        return RedirectResponse("/", status_code=303)

    with db() as c:
        # убеждаемся, что пост есть
        post = c.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            return PlainTextResponse("Post not found", status_code=404)
        c.execute(
            "INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)",
            (post_id, user["id"], content)
        )
        c.commit()

    # возвращаемся на главную (или можно на /#post-{id})
    return RedirectResponse("/", status_code=303)

# ── DELETE POST ───────────────────────────────────────────────────────────────
@app.post("/post/{post_id}/delete")
def delete_post(request: Request, post_id: int):
    me = cur_user(request)
    if not me:
        return RedirectResponse("/login", status_code=303)
    with db() as c:
        post = c.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            return PlainTextResponse("Post not found", status_code=404)
        if post["user_id"] != me["id"] and not me["is_admin"]:
            return PlainTextResponse("Permission denied", status_code=403)
        if post["image"] and os.path.exists(post["image"]):
            os.remove(post["image"])
        c.execute("DELETE FROM posts WHERE id=?", (post_id,))
        c.commit()
    return RedirectResponse("/", status_code=303)

# ── ПРОФИЛИ ──────────────────────────────────────────────────────────────────
@app.get("/profile/{username}", response_class=HTMLResponse)
def profile(request: Request, username: str):
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not u: return PlainTextResponse("User not found", status_code=404)
    me = cur_user(request)
    return templates.TemplateResponse("profile.html", {"request":request, "user":u, "me":me, "APP":APP_NAME})



# ── РЕДАКТИРОВАНИЕ ПРОФИЛЯ ─────────────────────────────────────────────────────
@app.get("/profile/{username}/edit", response_class=HTMLResponse)
def edit_profile_page(request: Request, username: str):
    me = cur_user(request)
    if not me or me["username"] != username:
        return RedirectResponse(f"/profile/{username}", status_code=303)
    return templates.TemplateResponse(
        "profile_edit.html",
        {"request": request, "user": me, "APP": APP_NAME}
    )

@app.post("/profile/{username}/edit")
def edit_profile(
    request: Request,
    username: str,
    first_name: str = Form(...),
    last_name: str = Form(...),
    avatar: UploadFile = File(None)
):
    me = cur_user(request)
    if not me or me["username"] != username:
        return RedirectResponse(f"/profile/{username}", status_code=303)

    avatar_path = me["avatar"]
    if avatar and avatar.filename:
        os.makedirs("static/avatars", exist_ok=True)
        avatar_path = f"static/avatars/{username.lstrip('@')}.png"
        with open(avatar_path, "wb") as f:
            shutil.copyfileobj(avatar.file, f)

    with db() as c:
        c.execute(
            "UPDATE users SET first_name=?, last_name=?, avatar=? WHERE id=?",
            (first_name, last_name, avatar_path, me["id"])
        )
        c.commit()

    resp = RedirectResponse(f"/profile/{username}", status_code=303)
    resp.set_cookie("whome_user", me["username"], httponly=False)
    return resp

#like
@app.post("/like/{post_id}")
def toggle_like(request: Request, post_id: int):
    me = cur_user(request)
    if not me:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)

    with db() as c:
        # Проверяем, есть ли лайк
        cur = c.execute("SELECT id FROM likes WHERE user_id=? AND post_id=?", (me["id"], post_id))
        row = cur.fetchone()
        if row:
            # Удаляем лайк
            c.execute("DELETE FROM likes WHERE id=?", (row["id"],))
            c.commit()
            cur = c.execute("SELECT COUNT(*) FROM likes WHERE post_id=?", (post_id,))
            count = cur.fetchone()[0]
            return {"status": "unliked", "count": count}
        else:
            # Ставим лайк
            c.execute("INSERT INTO likes (user_id, post_id) VALUES (?, ?)", (me["id"], post_id))
            c.commit()
            cur = c.execute("SELECT COUNT(*) FROM likes WHERE post_id=?", (post_id,))
            count = cur.fetchone()[0]
            return {"status": "liked", "count": count}


# ── ADMIN ─────────────────────────────────────────────────────────────────────
@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request):
    me = cur_user(request)
    if not me or not me["is_admin"]:
        return PlainTextResponse("Permission denied", status_code=403)

    with db() as c:
        users = c.execute("SELECT * FROM users ORDER BY id DESC").fetchall()

    return templates.TemplateResponse("admin_users.html", {"request": request, "me": me, "users": users})

@app.post("/admin/users/{user_id}/verify")
def verify_user(request: Request, user_id: int):
    me = cur_user(request)
    if not me or not me["is_admin"]:
        return PlainTextResponse("Permission denied", status_code=403)

    with db() as c:
        c.execute("UPDATE users SET is_verified = 1 WHERE id=?", (user_id,))
        c.commit()

    return RedirectResponse("/admin/users", status_code=303)

# ── CHATS ─────────────────────────────────────────────────────────────────────
def chat_id_for(a_id:int, b_id:int)->int:
    x,y = sorted([a_id,b_id])
    with db() as c:
        row = c.execute("SELECT id FROM chats WHERE user1_id=? AND user2_id=?", (x,y)).fetchone()
        if row: return row["id"]
        c.execute("INSERT INTO chats(user1_id,user2_id) VALUES(?,?)",(x,y))
        c.commit()
        return c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

@app.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request):
    me = cur_user(request)
    if not me: return RedirectResponse("/login", status_code=303)
    with db() as c:
        rows = c.execute("""
            SELECT c.id,
                   CASE WHEN c.user1_id=? THEN u2.username ELSE u1.username END AS peer_username,
                   COALESCE(CASE WHEN c.user1_id=? THEN u2.avatar ELSE u1.avatar END,'') AS peer_avatar,
                   COALESCE(CASE WHEN c.user1_id=? THEN u2.first_name||' '||u2.last_name ELSE u1.first_name||' '||u1.last_name END,'') AS peer_name
            FROM chats c
            JOIN users u1 ON u1.id=c.user1_id
            JOIN users u2 ON u2.id=c.user2_id
            WHERE c.user1_id=? OR c.user2_id=?
            ORDER BY c.id DESC
        """,(me["id"],me["id"],me["id"],me["id"],me["id"])).fetchall()
    return templates.TemplateResponse("chats.html", {"request":request, "me":me, "dialogs":rows, "APP":APP_NAME})

@app.post("/chats/new")
def start_chat(request: Request, peer_username:str=Form(...)):
    me = cur_user(request)
    if not me: return RedirectResponse("/login", status_code=303)
    with db() as c:
        peer = c.execute("SELECT * FROM users WHERE username=?", (peer_username,)).fetchone()
    if not peer: return RedirectResponse("/chats?e=nouser", status_code=303)
    chat_id_for(me["id"], peer["id"])
    return RedirectResponse(f"/chat/{peer_username}", status_code=303)

@app.get("/chat/{username}", response_class=HTMLResponse)
def chat_view(request: Request, username:str):
    me = cur_user(request)
    if not me: return RedirectResponse("/login", status_code=303)
    with db() as c:
        peer = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not peer: return RedirectResponse("/chats?e=nouser", status_code=303)
        cid = chat_id_for(me["id"], peer["id"])
        msgs = c.execute("""
            SELECT m.*, us.username AS sender_username
            FROM messages m JOIN users us ON us.id=m.sender_id
            WHERE m.chat_id=? ORDER BY m.id ASC
        """,(cid,)).fetchall()
    gifts = ["🎁 Подарок", "💐 Цветы", "🍫 Шоколад", "💎 Алмаз", "🚀 Ракета", "⭐ Звезда", "🕊️ Голубь"]
    return templates.TemplateResponse("chat.html", {"request":request, "me":me, "peer":peer, "messages":msgs, "gifts":gifts, "APP":APP_NAME})

@app.post("/chat/{username}/send")
def chat_send(request: Request, username:str, text:str=Form(...)):
    me = cur_user(request)
    if not me: return RedirectResponse("/login", status_code=303)
    with db() as c:
        peer = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        cid = chat_id_for(me["id"], peer["id"])
        c.execute("INSERT INTO messages(chat_id,sender_id,kind,body,created_at) VALUES(?,?,?,?,?)",
                  (cid, me["id"], "text", text, int(time.time())))
        c.commit()
    return RedirectResponse(f"/chat/{username}", status_code=303)

@app.post("/chat/{username}/gift")
def chat_gift(request: Request, username:str, gift_name:str=Form(...)):
    me = cur_user(request)
    if not me: return RedirectResponse("/login", status_code=303)
    with db() as c:
        peer = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        cid = chat_id_for(me["id"], peer["id"])
        c.execute("INSERT INTO messages(chat_id,sender_id,kind,body,created_at) VALUES(?,?,?,?,?)",
                  (cid, me["id"], "gift", gift_name, int(time.time())))
        c.commit()
    return RedirectResponse(f"/chat/{username}", status_code=303)