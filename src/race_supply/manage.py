"""本机账户管理：密码通过getpass输入，不放在参数、日志或仓库。"""
import argparse
import getpass
import os
from pathlib import Path
import sqlite3

from race_supply.storage import Store


def main():
    from dotenv import load_dotenv
    load_dotenv(".env.local",override=False)
    parser=argparse.ArgumentParser(description="创建本机账户")
    parser.add_argument("username")
    parser.add_argument("--role",choices=["employee","admin"],default="employee")
    args=parser.parse_args()
    first=getpass.getpass("密码（至少12字符）: ")
    if first!=getpass.getpass("再次输入: "):
        parser.error("两次密码不一致")
    try:
        Store(Path(os.getenv("RACE_DATA_DIR","work/web"))/"race_supply.sqlite3").create_user(args.username,first,args.role)
    except (ValueError,sqlite3.IntegrityError):
        parser.error("创建失败：检查用户名、密码长度或账户是否已存在")
    print("账户已创建。")


if __name__=="__main__":
    main()
