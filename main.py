import os
import shutil
from fastapi import FastAPI, Request, Form, UploadFile, File, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from db import SessionLocal, init_db, User, Post, Comment, Like
from passlib.hash import bcrypt
from datetime import datetime


APP_NAME = "WhoMe"
app = FastAPI()

# подключение статики
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# инициализация базы
init_db()


# зависимость для получения сессии
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# текущий юзер из куки
def cur_user(request: Request, db: Session):
    username = request.cookies.get("whome_user")
    if not username:
        return None
    return db.query(User).filter(User.username == username).first()


# 🔹 Главная (лента)
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    me = cur_user(request, db)
    posts = db.query(Post).order_by(Post.created_at.desc()).all()

    data = []
    for p in posts:
        data.append({
            "id": p.id,
            "content": p.content,
            "image": p.image,
            "username": p.author.username,
            "first_name": p.author.first_name,
            "last_name": p.author.last_name,
            "avatar": p.author.avatar,
            "is_verified": p.author.is_verified,
            "like_count": len(p.likes),
            "user_id": p.user_id,
            "comments": [{"username": c.user.username, "content": c.content} for c in p.comments]
        })

    return templates.TemplateResponse("index.html", {"request": request, "me": me, "posts": data, "APP": APP_NAME})


# 🔹 Регистрация
@app.post("/register")
def register(username: str = Form(...), password: str = Form(...),
             first_name: str = Form(None), last_name: str = Form(None),
             db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        return {"error": "Пользователь уже существует"}

    hashed = bcrypt.hash(password)
    user = User(username=username, password=hashed,
                first_name=first_name, last_name=last_name)
    db.add(user)
    db.commit()

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("whome_user", username, httponly=False)
    return resp


# 🔹 Логин
@app.post("/login")
def login(username: str = Form(...), password: str = Form(...),
          db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not bcrypt.verify(password, user.password):
        return {"error": "Неверные данные"}

    user.last_visit = datetime.utcnow()
    db.commit()

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("whome_user", username, httponly=False)
    return resp


# 🔹 Создать пост
@app.post("/post")
def create_post(request: Request, content: str = Form(...),
                image: UploadFile = File(None),
                db: Session = Depends(get_db)):
    me = cur_user(request, db)
    if not me:
        return RedirectResponse("/login", status_code=303)

    img_path = None
    if image and image.filename:
        os.makedirs("static/posts", exist_ok=True)
        img_path = f"static/posts/{me.username}_{image.filename}"
        with open(img_path, "wb") as f:
            shutil.copyfileobj(image.file, f)

    post = Post(user_id=me.id, content=content, image=img_path)
    db.add(post)
    db.commit()
    return RedirectResponse("/", status_code=303)


# 🔹 Комментарий
@app.post("/post/{post_id}/comment")
def add_comment(request: Request, post_id: int, content: str = Form(...),
                db: Session = Depends(get_db)):
    me = cur_user(request, db)
    if not me:
        return RedirectResponse("/login", status_code=303)

    comment = Comment(post_id=post_id, user_id=me.id, content=content)
    db.add(comment)
    db.commit()
    return RedirectResponse("/", status_code=303)


# 🔹 Лайк
@app.post("/like/{post_id}")
def toggle_like(request: Request, post_id: int, db: Session = Depends(get_db)):
    me = cur_user(request, db)
    if not me:
        return {"error": "auth required"}

    like = db.query(Like).filter(Like.post_id == post_id, Like.user_id == me.id).first()
    if like:
        db.delete(like)
        db.commit()
        return {"status": "unliked"}
    else:
        new_like = Like(post_id=post_id, user_id=me.id)
        db.add(new_like)
        db.commit()
        return {"status": "liked"}


# 🔹 Удаление поста
@app.post("/post/{post_id}/delete")
def delete_post(request: Request, post_id: int, db: Session = Depends(get_db)):
    me = cur_user(request, db)
    post = db.query(Post).filter(Post.id == post_id).first()
    if not me or (post.user_id != me.id and not me.is_admin):
        return RedirectResponse("/", status_code=303)

    db.delete(post)
    db.commit()
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