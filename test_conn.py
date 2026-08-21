import os
import oracledb
from dotenv import load_dotenv

load_dotenv()

host = os.getenv("ORACLE_HOST")
port = int(os.getenv("ORACLE_PORT", "1521"))
svc  = os.getenv("ORACLE_SERVICE")

dsn = oracledb.makedsn(host, port, service_name=svc)
print("DSN:", dsn) 
conn = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=dsn
)

cur = conn.cursor()
cur.execute("select sysdate from dual")
print(cur.fetchone())
conn.close()