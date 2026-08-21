import os, oracledb
from dotenv import load_dotenv
load_dotenv()

dsn = oracledb.makedsn(
    os.getenv("DWH_HOST"),
    int(os.getenv("DWH_PORT")),
    sid=os.getenv("DWH_SID"),
)
conn = oracledb.connect(
    user=os.getenv("DWH_USER"),
    password=os.getenv("DWH_PASSWORD"),
    dsn=dsn,
)

cur = conn.cursor()
cur.execute("select sysdate from dual")
print(cur.fetchone())
conn.close()
