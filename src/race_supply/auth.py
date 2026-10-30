"""短期JWT鉴权：固定算法、签发者和受众，并使用SQLite白名单支持即时退出。

角色从当前用户表读取，不信任前端角色或JWT里的角色声明。本机签名密钥自动
生成在忽略目录，重启后继续有效；从旧会话方案升级后需要重新登录。
"""
import hashlib
import os
from pathlib import Path
import secrets
import time
import jwt


class JwtAuth:
    def __init__(self, store, directory: Path):
        self.store=store
        path=directory/"jwt.key"
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"w",encoding="ascii") as file:
                file.write(secrets.token_urlsafe(64))
        except FileExistsError:
            pass
        self.key=path.read_text(encoding="ascii")
        if len(self.key)<48:
            raise ValueError("本机JWT签名密钥无效")

    def issue(self, old_token, user):
        now=int(time.time())
        token=jwt.encode({"sub":str(user["id"]),"iat":now,"exp":now+900,
                          "jti":secrets.token_hex(16),"iss":"race-supply","aud":"race-supply-web"},
                         self.key,algorithm="HS256")
        with self.store.connection() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?",(hashlib.sha256(old_token.encode()).hexdigest(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),user["id"],now+900))
        return token

    def verify(self, token):
        try:
            claims=jwt.decode(token,self.key,algorithms=["HS256"],audience="race-supply-web",issuer="race-supply",
                              options={"require":["sub","iat","exp","jti","iss","aud"]})
            user=self.store.user_for_token(token)
            if not user or str(user["id"])!=claims["sub"]:
                return None
            return user
        except (jwt.InvalidTokenError,TypeError,ValueError):
            return None
