import os
import oracledb
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    """Main PDWH connection"""
    dsn = oracledb.makedsn(
        os.getenv("ORACLE_HOST"),
        int(os.getenv("ORACLE_PORT", "1521")),
        service_name=os.getenv("ORACLE_SERVICE"),
    )
    return oracledb.connect(
        user=os.getenv("ORACLE_USER"),
        password=os.getenv("ORACLE_PASSWORD"),
        dsn=dsn
    )

def get_dwh_conn():
    """DWH connection for history"""
    dsn = oracledb.makedsn(
        os.getenv("DWH_HOST"),
        int(os.getenv("DWH_PORT", "1551")),
        sid=os.getenv("DWH_SID"),
    )
    return oracledb.connect(
        user=os.getenv("DWH_USER"),
        password=os.getenv("DWH_PASSWORD"),
        dsn=dsn
    )

def get_pmaps_conn():
    """PMAPS connection for delta data"""
    dsn = oracledb.makedsn(
        os.getenv("PMAPS_HOST"),
        int(os.getenv("PMAPS_PORT", "1522")),
        sid=os.getenv("PMAPS_SID"),
    )
    return oracledb.connect(
        user=os.getenv("PMAPS_USER"),
        password=os.getenv("PMAPS_PASSWORD"),
        dsn=dsn
    )

def get_cerberus_conn():
    """NEW: Cerberus connection for Lot Monitor"""
    dsn = oracledb.makedsn(
        "dwh.klm.infineon.com",
        1551,
        sid="dwh"
    )
    return oracledb.connect(
        user="cerberus_read",
        password="Th5s64R#AcN4d",
        dsn=dsn
    )