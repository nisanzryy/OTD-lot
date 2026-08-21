import os, oracledb
from dotenv import load_dotenv
load_dotenv()

dsn = oracledb.makedsn(os.getenv("ORACLE_HOST"), int(os.getenv("ORACLE_PORT")), service_name=os.getenv("ORACLE_SERVICE"))

with oracledb.connect(user=os.getenv("ORACLE_USER"), password=os.getenv("ORACLE_PASSWORD"), dsn=dsn) as conn:
    cur = conn.cursor()
    cur.execute("select user from dual")
    print("USER:", cur.fetchone()[0])

    cur.execute("select * from global_name")
    print("DB:", cur.fetchone()[0])

    cur.execute("""
        select count(*)
        from all_objects
        where owner='GODS_ADMIN' and object_name='V_STAREP_COMMENTS'
    """)
    print("Can see GODS_ADMIN.V_STAREP_COMMENTS:", cur.fetchone()[0])