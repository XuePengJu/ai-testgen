#!/usr/bin/env python3
"""从远程 MySQL 5.7 导出结构+数据为 SQL 文件（绕过新版客户端缺 mysql_native_password 插件的问题）。"""
import os
import sys
import pymysql
from dotenv import load_dotenv

load_dotenv()

OUT = sys.argv[1] if len(sys.argv) > 1 else "backups/ai-testgen-remote-dump-20261011.sql"
DB = os.environ["DB_NAME"]

conn = pymysql.connect(
    host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]),
    user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    database=DB, charset="utf8mb4",
)
cur = conn.cursor()

cur.execute("SHOW TABLES")
tables = [r[0] for r in cur.fetchall()]
print(f"表数量: {len(tables)}")

with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"-- Dump of {DB} from {os.environ['DB_HOST']} (MySQL 5.7.44) via PyMySQL\n")
    f.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")

    total_rows = 0
    for t in tables:
        cur.execute(f"SHOW CREATE TABLE `{t}`")
        ddl = cur.fetchone()[1]
        f.write(f"DROP TABLE IF EXISTS `{t}`;\n{ddl};\n\n")
        cur.execute(f"SELECT * FROM `{t}`")
        cols = [d[0] for d in cur.description]
        collist = ",".join(f"`{c}`" for c in cols)
        n = 0
        buf = []
        while True:
            rows = cur.fetchmany(500)
            if not rows:
                break
            for row in rows:
                vals = []
                for v in row:
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, (bytes, bytearray)):
                        vals.append(f"_binary'{v.hex()}'" if v else "_binary''")
                    elif isinstance(v, bool):
                        vals.append("1" if v else "0")
                    else:
                        # pymysql escape_string 处理引号/反斜杠/换行
                        vals.append("'" + pymysql.converters.escape_string(str(v)) + "'")
                buf.append(f"({','.join(vals)})")
                n += 1
                if len(buf) >= 100:
                    f.write(f"INSERT INTO `{t}` ({collist}) VALUES\n" + ",\n".join(buf) + ";\n")
                    buf = []
        if buf:
            f.write(f"INSERT INTO `{t}` ({collist}) VALUES\n" + ",\n".join(buf) + ";\n")
        f.write("\n")
        total_rows += n
        print(f"  {t}: {n} 行")

    # 触发器
    cur.execute("SELECT TRIGGER_NAME, ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s", (DB,))
    trigs = cur.fetchall()
    for name, stmt in trigs:
        cur.execute(f"SHOW TRIGGERS FROM `{DB}` LIKE '{name}'")
        row = cur.fetchone()
        # row: Trigger, Event, Table, Statement, Timing, Created, sql_mode, Definer, ...
        f.write(f"DROP TRIGGER IF EXISTS `{name}`;\nCREATE TRIGGER `{name}` {row[4]} {row[1]} ON `{row[2]}` FOR EACH ROW {row[3]}\n;\n")
        print(f"  trigger: {name}")

    f.write("SET FOREIGN_KEY_CHECKS=1;\n")
    print(f"完成: 共 {len(tables)} 表, {total_rows} 行, {len(trigs)} 触发器 -> {OUT}")

conn.close()
