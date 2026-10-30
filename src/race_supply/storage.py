"""本机单企业SQLite存储：身份、版本、会话与审计；每次操作独立连接。

SQL均使用参数绑定。文档正文保存在受权限控制的数据库中，不挂载上传目录。
后续迁移PostgreSQL时替换此层，不能省略同等的所有权与版本过滤。
"""

from contextlib import contextmanager
from pathlib import Path
import hashlib
import hmac
import secrets
import sqlite3
import time


def password_hash(password: str, salt: str | None = None) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("密码需为12至256个字符")
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
    return salt + ":" + value.hex()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('employee','admin')));
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts(username TEXT NOT NULL, ip TEXT NOT NULL, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS attempts_at ON login_attempts(at);
            CREATE TABLE IF NOT EXISTS documents(
                id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL, series TEXT NOT NULL, version INTEGER NOT NULL,
                scope TEXT NOT NULL CHECK(scope IN ('personal','shared')),
                status TEXT NOT NULL CHECK(status IN ('draft','published','archived')),
                kind TEXT NOT NULL CHECK(kind IN ('policy','scenario')),
                content TEXT NOT NULL, created REAL NOT NULL, published REAL,
                UNIQUE(owner_id,scope,series,version));
            CREATE TABLE IF NOT EXISTS conversations(
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL, content TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                action TEXT NOT NULL, target TEXT NOT NULL, created REAL NOT NULL);
            """)
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS shared_versions ON documents(series,version) WHERE scope='shared'")
            # 可重复的兼容迁移，已有本机数据不丢失。
            for name, definition in [("deleted","INTEGER NOT NULL DEFAULT 0"),
                                     ("processing","TEXT NOT NULL DEFAULT 'ready'"),
                                     ("filename","TEXT NOT NULL DEFAULT ''")]:
                if name not in {r[1] for r in db.execute("PRAGMA table_info(documents)")}:
                    db.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
            if "prompt_version" not in {r[1] for r in db.execute("PRAGMA table_info(messages)")}:
                db.execute("ALTER TABLE messages ADD COLUMN prompt_version INTEGER")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_user(self, username: str, password: str, role: str):
        import re
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username) or role not in {"employee","admin"}:
            raise ValueError("用户名需为3至64位字母、数字或_.-；角色无效")
        value = password_hash(password)
        with self.connection() as db:
            return db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                              (username,value,role)).lastrowid

    def login(self, username: str, password: str, ip: str):
        now = time.time()
        with self.connection() as db:
            db.execute("DELETE FROM login_attempts WHERE at < ?", (now-300,))
            count = db.execute("SELECT count(*) FROM login_attempts WHERE username=? OR ip=?",(username,ip)).fetchone()[0]
            if count >= 10:
                raise ValueError("尝试过多，请5分钟后再试")
            db.execute("INSERT INTO login_attempts VALUES(?,?,?)",(username,ip,now))
            row = db.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
        # 不存在的账户同样做密码派生，避免明显的时序枚举。
        expected = row["password_hash"] if row else "00"*16 + ":" + "00"*32
        valid_length=12 <= len(password) <= 256
        actual = password_hash(password if valid_length else "invalid-password", expected.split(":")[0])
        if not row or not valid_length or not hmac.compare_digest(actual,expected):
            raise PermissionError("用户名或密码错误")
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?",(now,))
            db.execute("INSERT INTO sessions VALUES(?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),row["id"],now+8*3600))
        return token, {k:row[k] for k in ("id","username","role")}

    def user_for_token(self, token: str):
        with self.connection() as db:
            row=db.execute("SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires>?",
                           (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, token: str):
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(token.encode()).hexdigest(),))

    def audit(self, user_id: int, action: str, target: str):
        with self.connection() as db:
            db.execute("INSERT INTO audit(user_id,action,target,created) VALUES(?,?,?,?)",(user_id,action,target,time.time()))

    def add_document(self, uid, title, series, scope, kind, content):
        with self.connection() as db:
            # IMMEDIATE避免同一系列并发上传计算出重复版本号。
            db.execute("BEGIN IMMEDIATE")
            count=db.execute("SELECT count(*) FROM documents WHERE owner_id=?",(uid,)).fetchone()[0]
            if count>=100:
                raise ValueError("本机试点每人最多100份文档")
            if scope=="shared":
                version=db.execute("SELECT coalesce(max(version),0)+1 FROM documents WHERE scope='shared' AND series=?",(series,)).fetchone()[0]
            else:
                version=db.execute("SELECT coalesce(max(version),0)+1 FROM documents WHERE owner_id=? AND scope=? AND series=?",(uid,scope,series)).fetchone()[0]
                db.execute("UPDATE documents SET status='archived' WHERE owner_id=? AND scope='personal' AND series=?",(uid,series))
            return db.execute("INSERT INTO documents(owner_id,title,series,version,scope,status,kind,content,created) VALUES(?,?,?,?,?,'draft',?,?,?)",
                              (uid,title,series,version,scope,kind,content,time.time())).lastrowid

    def documents(self, user, *, chat_only=False):
        with self.connection() as db:
            rows=db.execute("SELECT * FROM documents WHERE deleted=0 ORDER BY id DESC").fetchall()
        def visible(d):
            if chat_only:
                return (d["scope"]=="personal" and d["owner_id"]==user["id"] and d["status"]!="archived") or (d["scope"]=="shared" and d["status"]=="published")
            return user["role"]=="admin" or (d["scope"]=="personal" and d["owner_id"]==user["id"]) or (d["scope"]=="shared" and d["status"]=="published")
        return [dict(d) for d in rows if visible(d)]

    def change_status(self, doc_id: int, status: str):
        if status not in {"published","archived"}:
            raise ValueError("文档状态无效")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM documents WHERE id=? AND deleted=0",(doc_id,)).fetchone()
            if not row or row["scope"]!="shared":
                raise ValueError("共享文档不存在")
            if status=="published":
                if row["processing"]!="ready":
                    raise ValueError("资料尚未成功完成向量化，不能发布")
                if row["status"]!="draft":
                    raise ValueError("仅草稿可发布；已归档版本不得重新生效")
                newer=db.execute("SELECT 1 FROM documents WHERE scope='shared' AND series=? AND version>? AND status='published'",(row["series"],row["version"])).fetchone()
                if newer:
                    raise ValueError("已有更高版本生效，不能发布旧版")
                db.execute("UPDATE documents SET status='archived' WHERE scope='shared' AND series=? AND status='published'",(row["series"],))
            db.execute("UPDATE documents SET status=?,published=? WHERE id=?",(status,time.time(),doc_id))

    def conversation(self, uid, conversation_id=None):
        with self.connection() as db:
            if conversation_id is None:
                return db.execute("INSERT INTO conversations(user_id,created) VALUES(?,?)",(uid,time.time())).lastrowid
            row=db.execute("SELECT id FROM conversations WHERE id=? AND user_id=?",(conversation_id,uid)).fetchone()
            if not row:
                raise PermissionError("会话不存在或无权访问")
            return row["id"]

    def add_message(self, cid, role, content, sources="[]", prompt_version=None):
        with self.connection() as db:
            return db.execute("INSERT INTO messages(conversation_id,role,content,sources,created,prompt_version) VALUES(?,?,?,?,?,?)",(cid,role,content,sources,time.time(),prompt_version)).lastrowid

    def messages(self, cid):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id LIMIT 1000",(cid,))]

    def conversations(self, user):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT id,created FROM conversations WHERE user_id=? ORDER BY id DESC LIMIT 100",(user["id"],))]

    def logs(self):
        with self.connection() as db:
            return {"messages":[dict(r) for r in db.execute("SELECT m.*,c.user_id FROM messages m JOIN conversations c ON c.id=m.conversation_id ORDER BY m.id DESC LIMIT 200")],
                    "audit":[dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]}
