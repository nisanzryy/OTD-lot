import os
import base64
import json
import warnings
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
import pandas as pd
import streamlit as st
import oracledb
from dotenv import load_dotenv
import urllib.parse
import streamlit.components.v1 as components
import requests
import urllib3
import datetime
from streamlit_autorefresh import st_autorefresh
from token_fetcher import get_valid_token, load_saved_token, TOKEN_EXPIRY_MINUTES
load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="PDWH Lots - Single Tool", layout="wide")

def load_css():
    with open("static/style.css") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

def get_logo_base64():
    with open("static/infineon_logo.png", "rb") as f:
        return base64.b64encode(f.read()).decode()

load_css()
logo_b64 = get_logo_base64()

def format_delta(val):
    if val is None:
        return "—"
    try:
        if pd.isna(val):
            return "—"
    except Exception:
        pass
    try:
        v = float(str(val).strip())
        if v == int(v):
            return str(int(v))
        else:
            return f"{v:.1f}"
    except Exception:
        return str(val)

# ==================== PERSISTENT FILE STORAGE ====================
DESCRIPTIONS_FILE = "descriptions_data.json"

def save_descriptions_to_file(df: pd.DataFrame):
    try:
        df.to_json(DESCRIPTIONS_FILE, orient="records", indent=2)
    except Exception:
        pass

def load_descriptions_from_file() -> pd.DataFrame:
    cols = ["LOT NUMBER", "OPERATION", "DESCRIPTION", "ISSUE", "REMARKS"]
    try:
        if os.path.exists(DESCRIPTIONS_FILE):
            df = pd.read_json(DESCRIPTIONS_FILE, orient="records", dtype=False)
            if df.empty:
                return pd.DataFrame(columns=cols)
            for c in cols:
                if c not in df.columns:
                    df[c] = ""
            for c in cols:
                df[c] = df[c].astype(str).replace({"nan": "", "None": ""})
            return df[cols]
        else:
            return pd.DataFrame(columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)

# ---------- DB connections ----------
def get_conn():
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
    return oracledb.connect(
        user="cerberus_read",
        password="Th5s64R#AcN4d",
        dsn="dwh-db.klm.infineon.com:1551/dwh"
    )

# ==================== DATA FUNCTIONS ====================
@st.cache_data(ttl=60)
def load_history_multi(lot_number: str, operations: tuple):
    if not operations:
        return pd.DataFrame()

    op_list = ",".join([f"'{op}'" for op in operations])

    # ── Generated text matching FabCockpit "Text" column ──
    gen_text_mes = """
        CASE transaction
            WHEN '2RSV' THEN
                CASE WHEN is_reserved = 'Y'
                     THEN 'LOT WAS RESERVED FOR ENTITY ' ||
                          NVL(is_reserved_machine, actual_entity) ||
                          '   FROM OPERAT. ' ||
                          NVL(is_reserved_operator, operator_id)
                     ELSE 'Reservation canceled.'
                END
            WHEN '2SON' THEN
                NVL(operator_id,'') || ' Special Run END' ||
                CASE WHEN quantity_1 IS NOT NULL
                     THEN ' /Wafer: ' || TO_CHAR(TO_NUMBER(quantity_1))
                     ELSE '' END ||
                CASE WHEN actual_entity IS NOT NULL
                     THEN ' /Entity: ' || actual_entity ELSE '' END
            WHEN '2SIN' THEN
                NVL(operator_id,'') || ' Special Run START' ||
                CASE WHEN quantity_1 IS NOT NULL
                     THEN ' /Wafer: ' || TO_CHAR(TO_NUMBER(quantity_1))
                     ELSE '' END ||
                CASE WHEN actual_entity IS NOT NULL
                     THEN ' /Entity: ' || actual_entity ELSE '' END
            WHEN '2PSO' THEN
                'SIP moved out logged by ' || NVL(operator_id,'SYSTEM')
            WHEN '2PSI' THEN
                'SIP moved in logged by ' || NVL(operator_id,'SYSTEM')
            WHEN 'MVIN' THEN
                'Lot moved in logged by ' || NVL(operator_id,'SYSTEM') ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'MVOU' THEN
                'Lot moved out logged by ' || NVL(operator_id,'SYSTEM') ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'TRST' THEN
                'Lot moved to/from store logged by ' || NVL(operator_id,'SYSTEM') ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'SLTA' THEN
                'Lotattribute set logged by ' || NVL(operator_id,'SYSTEM') ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'COMM' THEN
                'Lot comment logged by' ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'RLLT' THEN
                'Lot released logged by ' || NVL(operator_id,'SYSTEM') ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'HLLT' THEN
                'Lot hold logged by ' || NVL(operator_id,'SYSTEM') ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN '1RCL' THEN
                'Lot moved to entity logged by' ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'ALFG' THEN
                'Transaction logged logged by' ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN '2SHL' THEN
                NVL(NULLIF(TRIM(commenttext),''),
                    'ALF Stop logged by ' || NVL(operator_id,'SYSTEM'))
            WHEN '2HLD' THEN
                'Lot set on HOLD by ' || NVL(operator_id,'SYSTEM') ||
                CASE WHEN hold_code IS NOT NULL
                     THEN ' | Code: ' || hold_code ELSE '' END ||
                CASE WHEN hold_comment IS NOT NULL AND TRIM(hold_comment) != ''
                     THEN CHR(10) || SUBSTR(hold_comment,1,500) ELSE '' END
            WHEN '2HRL' THEN
                'Hold released by ' || NVL(operator_id,'SYSTEM') ||
                CASE WHEN hold_comment IS NOT NULL AND TRIM(hold_comment) != ''
                     THEN CHR(10) || SUBSTR(hold_comment,1,500) ELSE '' END
            WHEN 'EI' THEN
                'EI comment logged by' ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'EDC' THEN
                'Transaction logged logged by' ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'JC' THEN
                'Transaction logged logged by' ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN 'TASK' THEN
                'Transaction logged logged by' ||
                CASE WHEN commenttext IS NOT NULL AND TRIM(commenttext) != ''
                     THEN CHR(10) || commenttext ELSE '' END
            WHEN '2CRT' THEN
                'Lot created by ' || NVL(operator_id,'SYSTEM')
            WHEN '2SCR' THEN
                'Lot scrapped by ' || NVL(operator_id,'SYSTEM')
            ELSE
                NVL(
                    NULLIF(TRIM(commenttext),''),
                    '(' || transaction || ' by ' ||
                    NVL(operator_id,'SYSTEM') || ')'
                )
        END
    """

    # ── Same pattern for fallback tables ──
    gen_text_fallback = """
        CASE transcode
            WHEN 'MVOU' THEN
                'Lot moved out logged by ' || NVL(operator,'SYSTEM') ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'MVIN' THEN
                'Lot moved in logged by ' || NVL(operator,'SYSTEM') ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'RLLT' THEN
                'Lot released logged by ' || NVL(operator,'SYSTEM') ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'HLLT' THEN
                'Lot hold logged by ' || NVL(operator,'SYSTEM') ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN '1RCL' THEN
                'Lot moved to entity logged by' ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'TRST' THEN
                'Lot moved to/from store logged by ' || NVL(operator,'SYSTEM') ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'SLTA' THEN
                'Lotattribute set logged by ' || NVL(operator,'SYSTEM') ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'COMM' THEN
                'Lot comment logged by' ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'JC' THEN
                'Transaction logged logged by' ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'EDC' THEN
                'Transaction logged logged by' ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN 'TASK' THEN
                'Transaction logged logged by' ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN '2HLD' THEN
                'Lot set on HOLD by ' || NVL(operator,'SYSTEM') ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            WHEN '2HRL' THEN
                'Hold released by ' || NVL(operator,'SYSTEM') ||
                CASE WHEN transaction_comment IS NOT NULL
                          AND TRIM(transaction_comment) != ''
                     THEN CHR(10) || REPLACE(transaction_comment,';',chr(20))
                     ELSE '' END
            ELSE
                NVL(
                    NULLIF(TRIM(REPLACE(transaction_comment,';',chr(20))),''),
                    '(' || transcode || ' by ' || NVL(operator,'SYSTEM') || ')'
                )
        END
    """

    tables_to_try = [
        {
            "name": "DWH_MES_WIP_DATA",
            "sql": f"""
                SELECT
                    trans_timestamp  AS TRANS_TIMESTAMP,
                    operation        AS OPERATION,
                    transaction      AS TRANSACTION,
                    current_sip      AS UPS,
                    actual_entity    AS EQUIPMENT,
                    operator_id      AS OPERATOR_ID,
                    commenttext      AS COMMENTTEXT,
                    {gen_text_mes}   AS TEXT
                FROM DWH_ADMIN.DWH_MES_WIP_DATA
                WHERE lot_number = :lot
                AND operation IN ({op_list})
                ORDER BY trans_timestamp DESC
            """,
            "conn_fn": get_dwh_conn,
        },
        {
            "name": "DWH_WIP_DATA_BIG_MOTHER",
            "sql": f"""
                SELECT
                    time_stamp       AS TRANS_TIMESTAMP,
                    operation        AS OPERATION,
                    transcode        AS TRANSACTION,
                    sps_number       AS UPS,
                    equipment        AS EQUIPMENT,
                    operator         AS OPERATOR_ID,
                    REPLACE(transaction_comment,';',chr(20)) AS COMMENTTEXT,
                    {gen_text_fallback} AS TEXT
                FROM DWH_ADMIN.DWH_WIP_DATA_BIG_MOTHER
                WHERE lot = :lot
                AND operation IN ({op_list})
                ORDER BY time_stamp DESC
            """,
            "conn_fn": get_dwh_conn,
        },
        {
            "name": "DWH_WIP_DATA",
            "sql": f"""
                SELECT
                    time_stamp       AS TRANS_TIMESTAMP,
                    operation        AS OPERATION,
                    transcode        AS TRANSACTION,
                    sps_number       AS UPS,
                    equipment        AS EQUIPMENT,
                    operator         AS OPERATOR_ID,
                    REPLACE(transaction_comment,';',chr(20)) AS COMMENTTEXT,
                    {gen_text_fallback} AS TEXT
                FROM DWH_ADMIN.DWH_WIP_DATA
                WHERE lot = :lot
                AND operation IN ({op_list})
                ORDER BY time_stamp DESC
            """,
            "conn_fn": get_dwh_conn,
        },
        {
            "name": "DWH_WIP_DATA_28",
            "sql": f"""
                SELECT
                    time_stamp       AS TRANS_TIMESTAMP,
                    operation        AS OPERATION,
                    transcode        AS TRANSACTION,
                    sps_number       AS UPS,
                    equipment        AS EQUIPMENT,
                    operator         AS OPERATOR_ID,
                    REPLACE(transaction_comment,';',chr(20)) AS COMMENTTEXT,
                    {gen_text_fallback} AS TEXT
                FROM DWH_ADMIN.DWH_WIP_DATA_28
                WHERE lot = :lot
                AND operation IN ({op_list})
                ORDER BY time_stamp DESC
            """,
            "conn_fn": get_dwh_conn,
        },
    ]

    all_results = []
    for tbl in tables_to_try:
        try:
            with tbl["conn_fn"]() as conn:
                df = pd.read_sql(tbl["sql"], conn, params={"lot": lot_number})
            if not df.empty:
                df["TRANS_TIMESTAMP"] = pd.to_datetime(
                    df["TRANS_TIMESTAMP"], errors="coerce"
                )
                all_results.append(df)
                # NO break — check ALL tables
        except Exception:
            continue

    if not all_results:
        return pd.DataFrame()

    combined = pd.concat(all_results, ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["TRANS_TIMESTAMP", "OPERATION", "TRANSACTION"],
        keep="first"
    ).sort_values("TRANS_TIMESTAMP", ascending=False).reset_index(drop=True)

    return combined

@st.cache_data(ttl=300)
def load_lot_monitor_data(lot: str):
    sql = """
WITH route_master_data AS (
    SELECT cl.Facility, cl.Route, nvl(ro.operation, cl.operation) AS Operation,
           o.store_flag_rep AS store_flag, ro.operation_sequence_number,
           CASE WHEN ro.operation_sequence_number > visited_until THEN 'Y' ELSE 'N' END AS is_off_route,
           nvl(o.operation_long_desc, nvl(o.operation_short_desc,'??')) AS Operation_Desc,
           CASE WHEN loop_back_indicator>ro.OPERATION_SEQUENCE_NUMBER THEN NULL
                ELSE ro.queue_cycle_time END AS queue_cycle_time,
           CASE WHEN loop_back_indicator>ro.OPERATION_SEQUENCE_NUMBER THEN NULL
                ELSE round(ro.process_cycle_time + ro.queue_cycle_time ,3) END AS due_CT,
           cl.lot AS Lot, cl.route_order_seq, cl.route_leave_time_stamp,
           NVL(ro.optional_oper_flag, 'N') AS optional_oper_flag
    FROM (
        SELECT lot, facility, route, operation, route_order_seq,
               operation_sequence_number, loop_back_indicator,
               CASE WHEN LEAD(operation_sequence_number,1,99999)
                         OVER (PARTITION BY lot,route ORDER BY TIME_STAMP) < visited_until
                    THEN visited_until
                    ELSE LEAD(operation_sequence_number, 1, 99999)
                         OVER(PARTITION BY lot, route ORDER BY TIME_STAMP)
               END AS TO_OPERATION_SEQUENCE_NUMBER,
               CASE WHEN LEAD(operation)OVER(PARTITION BY lot ORDER BY time_stamp) IS NULL
                    THEN 99999 ELSE visited_until END AS visited_until,
               LEAD(route_order_seq, 1, sysdate+1/24)
                   OVER(PARTITION BY lot ORDER BY route_order_seq) AS route_leave_time_stamp
        FROM (
            SELECT f_sql.*,
                   coalesce(LEAD(operation_sequence_number,1,9999)
                       OVER (PARTITION BY Lot,Route ORDER BY Time_Stamp),
                       operation_sequence_number) AS visited_until,
                   CASE WHEN nvl(f_sql.Route,'n/a') !=
                             LAG(nvl(f_sql.Route,'n/a'), 1, 'prev')
                             OVER(PARTITION BY f_sql.Lot ORDER BY f_sql.Time_Stamp)
                        OR f_sql.operation_sequence_number <
                           LAG(f_sql.OPERATION_SEQUENCE_NUMBER)
                           OVER(PARTITION BY f_sql.lot ORDER BY f_sql.TIME_STAMP)
                        THEN f_sql.Time_Stamp ELSE NULL END AS route_order_seq
            FROM (
                SELECT t.lot, t.facility, t.route, t.Time_Stamp AS Time_Stamp,
                       CASE WHEN nvl(r.route,'n/a') !=
                                 LAG(nvl(r.route,'n/a'), 1, 'prev')
                                 OVER(PARTITION BY t.lot ORDER BY t.Time_Stamp)
                            OR r.route IS NULL
                            OR nvl(r.route,'n/a') !=
                               LEAD(nvl(r.route,'n/a'), 1, 'next')
                               OVER(PARTITION BY t.lot ORDER BY t.Time_Stamp)
                            OR r.OPERATION_SEQUENCE_NUMBER >
                               LEAD(r.OPERATION_SEQUENCE_NUMBER)
                               OVER(PARTITION BY t.lot, t.ROUTE ORDER BY t.Time_Stamp)
                            OR r.OPERATION_SEQUENCE_NUMBER <
                               LAG(r.OPERATION_SEQUENCE_NUMBER)
                               OVER(PARTITION BY t.lot, t.ROUTE ORDER BY t.Time_Stamp)
                            THEN t.operation ELSE NULL END AS Operation,
                       r.operation_sequence_number,
                       MAX(r.OPERATION_SEQUENCE_NUMBER)
                           OVER(PARTITION BY t.lot,t.route ORDER BY t.Time_Stamp
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS loop_back_indicator
                FROM dwh_wip_data_all_transactions t
                LEFT OUTER JOIN dwh_route_operation r
                    ON (t.FACILITY = r.FACILITY AND t.ROUTE = r.ROUTE
                        AND t.operation = r.OPERATION AND t.DATE_STAMP <= r.create_date)
                WHERE t.lot = :lot_param AND t.operation <> t.to_operation
            ) f_sql
            WHERE f_sql.Operation IS NOT NULL
        )
        WHERE route_order_seq IS NOT NULL
    ) cl
    LEFT OUTER JOIN dwh_route_operation ro
        ON (cl.facility = ro.facility AND cl.route = ro.route
            AND ro.operation_sequence_number >= cl.operation_sequence_number
            AND ro.operation_sequence_number < cl.TO_OPERATION_SEQUENCE_NUMBER)
    LEFT OUTER JOIN dwh_operation o
        ON (nvl(ro.facility,cl.facility) = o.facility
            AND nvl(ro.operation,cl.operation) = o.operation)
    WHERE NVL(ro.optional_oper_flag, 'N') = 'N'
),
lot_hold_history AS (
    SELECT lot, Facility, Route, Operation, Transcode,
           time_stamp AS rllt_time_stamp,
           (time_stamp - HLLT_TIME_STAMP) AS actual_HoldTime
    FROM (
        SELECT w.*, LAG(time_stamp) OVER (
                   PARTITION BY lot, facility, route, operation
                   ORDER BY time_stamp) AS HLLT_TIME_STAMP
        FROM (
            SELECT lot, Facility, Route, Operation, Transcode, time_stamp
            FROM dwh_wip_data_all_transactions
            WHERE Lot = :lot_param AND deleted_flag <> 'Y'
                AND transcode IN (
                    SELECT var_value FROM PPI_var
                    WHERE var_name IN ('HOLD START TRANSACTION','HOLD RELEASE TRANSACTION')
                )
        ) w
    ) h
    WHERE transcode IN (
        SELECT var_value FROM PPI_var WHERE var_name = 'HOLD RELEASE TRANSACTION'
    )
),
lot_valid_moves AS (
    SELECT a.facility, a.route, a.operation, a.to_operation, a.lot,
           a.Time_Stamp AS Trans_Time, a.transcode, a.qty_out_1, a.unit_1, a.equipment,
           (SELECT equipment_desc FROM dwh_equipment e
            WHERE e.equipment = a.equipment AND ROWNUM = 1) AS equipment_description,
           a.work_center, a.cost_center, a.rework_flag,
           a.hot_lot_flag, a.hot_oper_flag, a.super_hot_flag,
           a.enter_operation_time_stamp AS enter_op_ts,
           a.due_cycle_time,
           a.due_cycle_time - a.due_process_cycle_time AS due_wait_time,
           nvl((a.Time_Stamp - nvl(
               LAG(a.Time_Stamp) OVER(PARTITION BY a.lot ORDER BY a.lot, a.Time_Stamp),
               a.enter_operation_time_stamp)), 0) AS cycleTime,
           nvl((a.movein_time_stamp - nvl(
               LAG(a.Time_Stamp) OVER(PARTITION BY a.lot ORDER BY a.lot, a.Time_Stamp),
               a.enter_operation_time_stamp)), 0) AS wt,
           nvl(a.sps_number, a.main_sps) AS sps_number,
           (SELECT SUM(actual_holdtime) FROM lot_hold_history h
            WHERE a.facility = h.facility AND a.route = h.route
                AND a.operation = h.operation AND a.lot = h.lot
                AND a.enter_operation_time_stamp <= h.rllt_time_stamp
                AND a.time_stamp >= h.rllt_time_stamp) AS actual_holdtime
    FROM dwh_wip_data_all_transactions a
    WHERE a.operation <> a.to_operation AND a.lot = :lot_param AND a.deleted_flag <> 'Y'
)
SELECT
    nvl(l.Operation, r.Operation)                           AS OPERATION,
    r.Operation_Desc                                         AS OPERATION_DESC,
    nvl(r.optional_oper_flag,'??')                          AS OPTIONAL_OPER_FLAG,
    nvl(r.store_flag, CASE WHEN l.Operation < '1000' THEN 'Y' ELSE 'N' END) AS STORE_FLAG,
    round(SUM(nvl(l.due_cycle_time, r.due_CT)), 4)          AS TARGET_CT,
    round(SUM(l.cycleTime), 4)                              AS ACTUAL_CT,
    round(SUM(l.wt), 4)                                     AS WAITTIME,
    round(SUM(nvl(l.due_wait_time, r.queue_cycle_time)), 4) AS TARGET_WAITTIME,
    l.Trans_Time                                             AS TRANS_TIME,
    round(decode(SUM(nvl(l.due_cycle_time,r.due_CT)), 0, 1,
        SUM(l.Trans_Time - l.enter_op_ts) / SUM(nvl(l.due_cycle_time,r.due_CT))), 4) AS FF_TO_TARGET,
    l.SPS_number AS SPS_NUMBER, l.equipment AS EQUIPMENT,
    l.equipment_description AS EQUIPMENT_DESC,
    round(avg(SUM(l.cycleTime)) OVER(
        PARTITION BY l.facility, l.route, l.operation, l.sps_number), 4) AS RPT,
    l.qty_out_1 AS AMOUNT, l.unit_1 AS UNIT, l.rework_flag AS REWORK,
    nvl(l.hot_lot_flag, 'N') AS HOT_LOT_FLAG,
    nvl(l.hot_oper_flag, 'N') AS HOT_OPER_FLAG,
    nvl(l.super_hot_flag, 'N') AS SUPER_HOT_FLAG,
    nvl(l.Facility, r.Facility) AS FACILITY,
    nvl(l.Route, nvl(r.Route,
        CASE WHEN r.store_flag='Y' THEN 'store' ELSE r.Operation_Desc END)) AS ROUTE,
    l.transcode AS TRANSCODE, l.cost_center AS COST_CENTER,
    l.work_center AS WORK_CENTER,
    round(SUM(l.actual_holdtime) * 24, 4) AS HOLD_TIME_HRS
FROM lot_valid_moves l
FULL OUTER JOIN route_master_data r
    ON (r.facility = l.facility AND nvl(r.route,'n/a') = nvl(l.route,'n/a')
        AND r.operation = l.operation AND r.lot = l.lot
        AND (l.Trans_Time >= r.route_order_seq AND l.Trans_Time < r.route_leave_time_stamp))
WHERE nvl(r.optional_oper_flag, 'Y') = 'N'
GROUP BY
    l.Facility, r.Facility, l.Route, r.Route, l.Lot, r.Lot,
    r.operation_sequence_number, l.Operation, r.Operation,
    r.Operation_Desc, r.store_flag, r.optional_oper_flag,
    l.cost_center, l.work_center, l.to_operation, l.Trans_Time,
    l.transcode, l.qty_out_1, l.unit_1, l.equipment,
    l.equipment_description, l.SPS_number, l.rework_flag,
    l.hot_lot_flag, l.HOT_Oper_Flag, l.Super_Hot_Flag,
    r.route_order_seq, l.lot
ORDER BY r.operation_sequence_number, l.Trans_Time
    """
    with get_cerberus_conn() as conn:
        df = pd.read_sql(sql, conn, params={"lot_param": lot})
    if "TRANS_TIME" in df.columns:
        df["TRANS_TIME"] = pd.to_datetime(df["TRANS_TIME"], errors="coerce")
    if "TARGET_CT" in df.columns and "ACTUAL_CT" in df.columns:
        df["DELAY"] = round(df["TARGET_CT"].fillna(0) - df["ACTUAL_CT"].fillna(0), 4)
    if "ACTUAL_CT" in df.columns and "OPERATION" in df.columns:
        has_actual     = df["ACTUAL_CT"].notna()
        ops_with_actual = set(df[has_actual]["OPERATION"].tolist())
        df = df[has_actual | ~df["OPERATION"].isin(ops_with_actual)].reset_index(drop=True)
    if "DELAY" in df.columns:
        cols = df.columns.tolist()
        cols.remove("DELAY")
        cols.insert(cols.index("ACTUAL_CT") + 1, "DELAY")
        df = df[cols]
    return df

@st.cache_data(ttl=300)
def get_table_columns(table_name: str):
    parts = table_name.upper().split(".")
    owner = parts[0] if len(parts) > 1 else None
    table = parts[1] if len(parts) > 1 else parts[0]
    if owner:
        sql = f"""
        SELECT column_name, data_type FROM all_tab_columns
        WHERE owner = '{owner}' AND table_name = '{table}' ORDER BY column_id
        """
    else:
        sql = f"""
        SELECT column_name, data_type FROM user_tab_columns
        WHERE table_name = '{table}' ORDER BY column_id
        """
    with get_cerberus_conn() as conn:
        return pd.read_sql(sql, conn)



@st.cache_data(ttl=300)
def load_otd_priority_orders_auto():
    """Fully automatic — no manual token needed"""
    url = "https://otdapiprod.muc.infineon.com/api/server/PriorityOrder/1/getPriorityOrders"

    token = get_valid_token()
    if not token:
        return pd.DataFrame(), "❌ Could not fetch token automatically"

    t = token.strip()
    if not t.lower().startswith("bearer "):
        t = f"Bearer {t}"

    headers = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "Authorization": t,
        "Origin":        "https://overview-otd.icp.infineon.com",
        "Referer":       "https://overview-otd.icp.infineon.com/",
    }

    try:
        response = requests.post(
            url, headers=headers, json={}, timeout=30, verify=False
        )
        if response.status_code == 401:
            # Token expired — delete cached token and retry
            if os.path.exists("otd_token.json"):
                os.remove("otd_token.json")
            load_otd_priority_orders_auto.clear()
            return pd.DataFrame(), "🔄 Token expired — will auto refresh on next load"

        response.raise_for_status()
        data = response.json()

        if "priorityOrders" in data:
            return pd.DataFrame(data["priorityOrders"]), None
        else:
            return pd.DataFrame(), f"⚠️ Unexpected response: {list(data.keys())}"

    except Exception as e:
        return pd.DataFrame(), f"❌ Error: {str(e)[:200]}"
    
def format_delta_otd(val):
    if val is None:
        return "—", ""
    try:
        if pd.isna(val):
            return "—", ""
    except Exception:
        pass
    try:
        v = float(str(val).strip())
        if v < 0:
            return f"{v:.4f}", "color:#d32f2f; font-weight:bold;"
        elif v > 0:
            return f"{v:.4f}", "color:#2e7d32; font-weight:500;"
        else:
            return "0.0000", "color:#757575;"
    except Exception:
        return str(val), "color:#000000;"

def get_status_badge(status: str) -> str:
    icons = {"In Production":"🔵","Confirmed":"🟣","Warning":"🟠","Done":"🟢","Terminated":"🔴"}
    key = status.lower().replace(" ","-") if status in icons else "x"
    icon = icons.get(status, "⚪")
    return f'<span class="sb sb-{key}">{icon} {esc_cell(status)}</span>'

def get_priority_badge(priority: str) -> str:
    icons = {"Hot":"🔥","Rocket":"🚀","Normal":""}
    key = priority.lower() if priority in icons else "x"
    icon = icons.get(priority, "")
    prefix = (icon + " ") if icon else ""
    return f'<span class="pb pb-{key}">{prefix}{esc_cell(priority)}</span>'

import html as html_lib
def esc_cell(v):
    return html_lib.escape(str(v), quote=False)

# ==================== GLOBAL PERSISTENT STORAGE ====================
if "global_descriptions" not in st.session_state:
    st.session_state["global_descriptions"] = load_descriptions_from_file()

# ==================== ROUTER ====================
qp   = st.query_params
page = qp.get("page", "list")

# ==================== CATEGORY LIST PAGE ====================
if page == "category_list":
    st.markdown(
        f'<div class="inf-header"><div style="display:flex; align-items:center; justify-content:space-between;"><div><h1 style="margin:0; border:none; color:white;">📂 Issue Category List</h1><p style="margin:4px 0 0 0; color:#C8F0D8;">All submitted descriptions grouped by category</p></div><img src="data:image/png;base64,{logo_b64}" style="height:55px;"></div></div>',
        unsafe_allow_html=True,
    )
    if st.button("⬅ Back to List", key="back_from_category"):
        st.query_params.clear()
        st.query_params["page"] = "list"
        st.rerun()
    all_desc = st.session_state.get("global_descriptions", pd.DataFrame())
    if all_desc.empty:
        st.info("📭 No descriptions submitted yet.")
    else:
        st.markdown(f"**Total records:** {len(all_desc)}")
        st.download_button(
            "⬇ Download All Descriptions CSV",
            all_desc.to_csv(index=False).encode("utf-8"),
            file_name="all_descriptions.csv",
            mime="text/csv",
            key="dl_all_desc",
        )
        st.markdown("---")
        categories = all_desc["ISSUE"].replace("", "Uncategorized").fillna("Uncategorized").unique()
        categories = sorted(categories)
        for cat in categories:
            cat_df = all_desc[
                all_desc["ISSUE"].replace("", "Uncategorized").fillna("Uncategorized") == cat
            ].copy()
            with st.expander(f"📁 {cat}  —  {len(cat_df)} record(s)", expanded=False):
                st.dataframe(
                    cat_df, use_container_width=True, hide_index=True,
                    column_config={
                        "LOT NUMBER":  st.column_config.TextColumn("Lot Number"),
                        "OPERATION":   st.column_config.TextColumn("Operation"),
                        "DESCRIPTION": st.column_config.TextColumn("Description", width="large"),
                        "ISSUE":       st.column_config.TextColumn("Issue"),
                        "REMARKS":     st.column_config.TextColumn("Remarks"),
                    },
                )
    st.stop()

# ==================== DETAIL PAGE ====================
if page == "detail":
    lot = qp.get("lot", "")
    op  = qp.get("op",  "")
    st.markdown(
        f'<div class="inf-header"><div style="display:flex; align-items:center; justify-content:space-between;"><div><h1 style="margin:0; border:none; color:white;">📋 Lot History Details</h1><p style="margin:4px 0 0 0; color:#C8F0D8;">Infineon Technologies | DWH Live Data</p></div><img src="data:image/png;base64,{logo_b64}" style="height:55px;"></div></div>',
        unsafe_allow_html=True,
    )
    top = st.columns([4, 3, 1])
    top[0].write(f"**Lot:** {lot}")
    if top[2].button("⬅ Back to list", key="back_to_list_detail"):
        st.query_params.clear()
        st.query_params["page"] = "list"
        st.rerun()
    if not lot:
        st.error("Missing lot parameter.")
        st.stop()
    with st.spinner(f"🔍 Loading delay summary for {lot}..."):
        try:
            monitor_df = load_lot_monitor_data(lot)
        except Exception as e:
            st.error(f"❌ Error loading lot monitor data: {e}")
            monitor_df = pd.DataFrame()
    delay_ops        = []
    delay_summary_df = pd.DataFrame()
    if (not monitor_df.empty and "DELAY" in monitor_df.columns and "OPERATION" in monitor_df.columns):
        delay_summary = (
            monitor_df.groupby("OPERATION", as_index=False).agg(
                OPERATION_DESC=("OPERATION_DESC", "first"),
                SUM_DELAY=("DELAY", "sum"),
                COUNT=("DELAY", "count"),
            )
        )
        delay_summary["SUM_DELAY"] = delay_summary["SUM_DELAY"].round(4)
        delay_summary = delay_summary[delay_summary["SUM_DELAY"] <= -0.5]
        delay_summary = delay_summary.sort_values("SUM_DELAY", ascending=True).reset_index(drop=True)
        delay_summary_df = delay_summary
        delay_ops = delay_summary["OPERATION"].tolist()
    if delay_ops:
        st.success(f"✅ Found **{len(delay_ops)}** operation(s) with delay ≤ -0.5. Loading history for all...")
        with st.expander("📊 Delay Summary (operations shown below)", expanded=False):
            st.dataframe(
                delay_summary_df, use_container_width=True, hide_index=True,
                column_config={
                    "OPERATION":      st.column_config.TextColumn("Operation"),
                    "OPERATION_DESC": st.column_config.TextColumn("Description"),
                    "SUM_DELAY":      st.column_config.NumberColumn("Sum of Delays", format="%.4f"),
                },
            )
            st.markdown("**🔗 Jump to operation:**")
            btn_cols = st.columns(min(len(delay_ops), 8))
            for idx, op_code in enumerate(delay_ops):
                col_idx = idx % min(len(delay_ops), 8)
                if btn_cols[col_idx].button(f"➡ {op_code}", key=f"jump_btn_{op_code}", use_container_width=True):
                    st.session_state["active_op_tab"] = idx + 1
                    st.rerun()
    else:
        st.info(f"ℹ️ No delay summary operations found. Showing history for operation: **{op}**")
        if op:
            delay_ops = [op]
        else:
            st.error("No operations to display history for.")
            st.stop()
    top[1].write(f"**Operations:** {', '.join(delay_ops)}")
    with st.spinner(f"📋 Loading history for {len(delay_ops)} operation(s)..."):
        try:
            hist_all = load_history_multi(lot, tuple(delay_ops))
        except Exception as e:
            st.error(f"❌ Error loading history: {e}")
            hist_all = pd.DataFrame()
    st.write(f"**Total history rows across all operations:** {len(hist_all)}")
    if "active_op_tab" not in st.session_state:
        st.session_state["active_op_tab"] = 0
    if hist_all.empty:
        st.warning("⚠️ No history records found for these operations.")
    else:
        col_config = {
    "TRANS_TIMESTAMP": st.column_config.DatetimeColumn(
        "Time", format="DD/MM/YYYY HH:mm:ss"
    ),
    "OPERATION":   st.column_config.TextColumn("Operation"),
    "TRANSACTION": st.column_config.TextColumn("Transaction"),
    "UPS":         st.column_config.TextColumn("UPS"),
    "EQUIPMENT":   st.column_config.TextColumn("Equipment"),
    "OPERATOR_ID": st.column_config.TextColumn("Operator"),
    "TEXT":        st.column_config.TextColumn(
        "Text", width="large"   # ← renamed from COMMENTTEXT
    ),
    }

    # ── Columns to show (FabCockpit order) ──
    SHOW_COLS = [
        "TRANS_TIMESTAMP", "TRANSACTION", "OPERATION",
        "UPS", "EQUIPMENT", "OPERATOR_ID", "TEXT"
    ]

    with st.expander(f"📋 Total History — All Operations ({len(hist_all)} rows)", expanded=True):
            nav_options = ["📋 All Operations"] + [
                f"{op_code} — {delay_summary_df[delay_summary_df['OPERATION']==op_code]['OPERATION_DESC'].iloc[0] if not delay_summary_df.empty and op_code in delay_summary_df['OPERATION'].values else ''}"
                for op_code in delay_ops
            ]
            default_idx  = st.session_state.get("active_op_tab", 0)
            selected_nav = st.selectbox("🔍 Select Operation to View:", options=nav_options, index=default_idx, key="op_nav_selectbox")
            selected_idx = nav_options.index(selected_nav)
            if selected_idx != st.session_state.get("active_op_tab", 0):
                st.session_state["active_op_tab"] = selected_idx
                st.rerun()
            if selected_idx == 0:
                st.caption(f"Showing all {len(hist_all)} records across {len(delay_ops)} operation(s)")
                _cols = [c for c in SHOW_COLS if c in hist_all.columns]
                st.dataframe(
                    hist_all[_cols],
                    width="stretch",
                    hide_index=True,
                    column_config=col_config,
                    height=500,
                )
                st.download_button("⬇ Download All History CSV", hist_all.to_csv(index=False).encode("utf-8"),
                    file_name=f"history_{lot}_all_ops.csv", mime="text/csv", key="dl_all")
            else:
                op_code = delay_ops[selected_idx - 1]
                op_hist = hist_all[hist_all["OPERATION"] == op_code].copy()
                if not delay_summary_df.empty:
                    row = delay_summary_df[delay_summary_df["OPERATION"] == op_code]
                    if not row.empty:
                        c1, c2 = st.columns(2)
                        c1.metric("Operation", op_code)
                        c2.metric("Sum of Delays", f"{row['SUM_DELAY'].iloc[0]:.4f} days")
                if op_hist.empty:
                    st.warning(f"⚠️ No history records found for operation {op_code}")
                else:
                    st.caption(f"{len(op_hist)} history record(s) for operation {op_code}")
                    _cols = [c for c in SHOW_COLS if c in op_hist.columns]
                    st.dataframe(
                        op_hist[_cols],
                        width="stretch",
                        hide_index=True,
                        column_config=col_config,
                        height=500,
                    )
                    st.download_button(f"⬇ Download CSV for {op_code}",
                        op_hist.to_csv(index=False).encode("utf-8"),
                        file_name=f"history_{lot}_{op_code}.csv", mime="text/csv", key=f"dl_{op_code}")
            ISSUE_CATEGORIES = ["—","Single Tool","Disposition","Store TD/UPE/UPD","Delay load",
                "QMP UPE/TD/PI","Delay exms merge/split","APC/Space","Batching","Special meas",
                "Tool error/down/conversion","Verify recipe","Missing","Fab/IT dowm",
                "ALF PCM/ FCT","Data load/high fail","100% scan","PI rack","Space violation","EXMS error"]
            REMARKS_OPTIONS = ["—","Jazni","Waty","Suhazni"]
            if selected_idx > 0:
                op_code      = delay_ops[selected_idx - 1]
                remark_key   = f"remark_{lot}_{op_code}"
                issue_key    = f"issue_{lot}_{op_code}"
                assignee_key = f"assignee_{lot}_{op_code}"
                rows_key     = f"rows_{lot}_{op_code}"
                if rows_key not in st.session_state:
                    existing = st.session_state["global_descriptions"]
                    if not existing.empty:
                        filtered_existing = existing[
                            (existing["LOT NUMBER"].astype(str) == str(lot)) &
                            (existing["OPERATION"].astype(str)  == str(op_code))
                        ].copy()
                    else:
                        filtered_existing = pd.DataFrame()
                    st.session_state[rows_key] = (
                        filtered_existing.reset_index(drop=True) if not filtered_existing.empty
                        else pd.DataFrame(columns=["LOT NUMBER","OPERATION","DESCRIPTION","ISSUE","REMARKS"])
                    )
                st.markdown("---")
                st.markdown("#### 📝 Description & Issue Classification")
                with st.form(key=f"form_{lot}_{op_code}"):
                    fc1, fc2, fc3, fc4, fc5, fc6 = st.columns([2, 2, 4, 3, 2, 1])
                    fc1.text_input("Lot Number", value=lot,     disabled=True, key=f"lot_in_{op_code}")
                    fc2.text_input("Operation",  value=op_code, disabled=True, key=f"op_in_{op_code}")
                    Description = fc3.text_input("Description", placeholder="Enter your Description here...", key=remark_key)
                    issue_sel   = fc4.selectbox("Issue Category", options=ISSUE_CATEGORIES, key=issue_key)
                    remarks_sel = fc5.selectbox("Remarks", options=REMARKS_OPTIONS, key=assignee_key)
                    fc6.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
                    submitted = fc6.form_submit_button("✅ Submit", use_container_width=True)
                if submitted:
                    new_row = pd.DataFrame([{
                        "LOT NUMBER":  lot, "OPERATION": op_code, "DESCRIPTION": Description,
                        "ISSUE":       issue_sel   if issue_sel   != "—" else "",
                        "REMARKS":     remarks_sel if remarks_sel != "—" else "",
                    }])
                    st.session_state[rows_key] = pd.concat([st.session_state[rows_key], new_row], ignore_index=True)
                    st.session_state["global_descriptions"] = pd.concat(
                        [st.session_state["global_descriptions"], new_row], ignore_index=True)
                    save_descriptions_to_file(st.session_state["global_descriptions"])
                    st.rerun()
                if not st.session_state[rows_key].empty:
                    st.dataframe(st.session_state[rows_key], use_container_width=True, hide_index=True,
                        column_config={
                            "LOT NUMBER":  st.column_config.TextColumn("Lot Number"),
                            "OPERATION":   st.column_config.TextColumn("Operation"),
                            "DESCRIPTION": st.column_config.TextColumn("Description", width="large"),
                            "ISSUE":       st.column_config.TextColumn("Issue"),
                            "REMARKS":     st.column_config.TextColumn("Remarks"),
                        })
                    st.download_button(f"⬇ Download Description CSV for {op_code}",
                        st.session_state[rows_key].to_csv(index=False).encode("utf-8"),
                        file_name=f"description_{lot}_{op_code}.csv", mime="text/csv",
                        key=f"dl_description_{op_code}")
                    if st.button(f"🗑️ Clear Description for {op_code}", key=f"clear_{op_code}"):
                        st.session_state[rows_key] = pd.DataFrame(
                            columns=["LOT NUMBER","OPERATION","DESCRIPTION","ISSUE","REMARKS"])
                        st.session_state["global_descriptions"] = st.session_state["global_descriptions"][
                            ~((st.session_state["global_descriptions"]["LOT NUMBER"] == lot) &
                              (st.session_state["global_descriptions"]["OPERATION"]  == op_code))
                        ].reset_index(drop=True)
                        save_descriptions_to_file(st.session_state["global_descriptions"])
                        st.rerun()
                else:
                    st.info("No description added yet. Use the form above to add.")
    st.stop()

# ==================== LOT MONITOR PAGE ====================
if page == "lot_monitor":
    lot = qp.get("lot", "")
    st.markdown(
        f'<div class="inf-header"><div style="display:flex; align-items:center; justify-content:space-between;"><div><h1 style="margin:0; border:none; color:white;">📊 Lot Monitor - {lot}</h1><p style="margin:4px 0 0 0; color:#C8F0D8;">Infineon Technologies | Detailed Lot Analysis</p></div><img src="data:image/png;base64,{logo_b64}" style="height:55px;"></div></div>',
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns([1, 11])
    if col1.button("⬅ Back to List", key="back_to_list_monitor"):
        st.query_params.clear()
        st.query_params["page"] = "list"
        st.rerun()
    if not lot:
        st.error("❌ No lot number provided!")
        st.stop()
    with st.expander("🔍 Debug: View Table Columns", expanded=False):
        for tbl in ["LOC_AL_USER_QUERIES.EI_USAGE_TRANSACTIONS", "DWH_ADMIN.DWH_OPERATION"]:
            st.markdown(f"**📋 `{tbl}` columns:**")
            try:
                cols_df = get_table_columns(tbl)
                if cols_df.empty:
                    st.warning("⚠️ No columns found")
                else:
                    st.dataframe(cols_df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"❌ Error: {e}")
            st.markdown("---")
    with st.spinner(f"📊 Loading lot monitor data for {lot}..."):
        try:
            monitor_df = load_lot_monitor_data(lot)
            if monitor_df.empty:
                st.warning(f"⚠️ No data found for lot {lot}")
                st.stop()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total Operations", len(monitor_df))
            c2.metric("Route",
                monitor_df["ROUTE"].dropna().iloc[0]
                if "ROUTE" in monitor_df.columns and not monitor_df["ROUTE"].dropna().empty else "N/A")
            c3.metric("Facility",
                monitor_df["FACILITY"].dropna().iloc[0]
                if "FACILITY" in monitor_df.columns and not monitor_df["FACILITY"].dropna().empty else "N/A")
            c4.metric("Total Actual CT (days)",
                f"{monitor_df['ACTUAL_CT'].sum():.4f}" if "ACTUAL_CT" in monitor_df.columns else "N/A")
            with st.expander("📋 Operation Details", expanded=False):
                st.dataframe(monitor_df, use_container_width=True, hide_index=True,
                    column_config={
                        "OPERATION":          st.column_config.TextColumn("Operation"),
                        "OPERATION_DESC":     st.column_config.TextColumn("Description"),
                        "STORE_FLAG":         st.column_config.TextColumn("Store Flag"),
                        "OPTIONAL_OPER_FLAG": st.column_config.TextColumn("Optional"),
                        "TARGET_CT":          st.column_config.NumberColumn("Target CT",    format="%.4f"),
                        "ACTUAL_CT":          st.column_config.NumberColumn("Actual CT",    format="%.4f"),
                        "DELAY":              st.column_config.NumberColumn("Delay",        format="%.4f"),
                        "WAITTIME":           st.column_config.NumberColumn("Wait Time",    format="%.4f"),
                        "TARGET_WAITTIME":    st.column_config.NumberColumn("Target Wait",  format="%.4f"),
                        "FF_TO_TARGET":       st.column_config.NumberColumn("FF to Target", format="%.4f"),
                        "TRANS_TIME":         st.column_config.DatetimeColumn("Timestamp",  format="DD/MM/YYYY HH:mm"),
                        "EQUIPMENT":          st.column_config.TextColumn("Equipment"),
                        "EQUIPMENT_DESC":     st.column_config.TextColumn("Equipment Desc"),
                        "SPS_NUMBER":         st.column_config.TextColumn("SPS"),
                        "RPT":                st.column_config.NumberColumn("RPT",          format="%.4f"),
                        "AMOUNT":             st.column_config.NumberColumn("Qty Out"),
                        "UNIT":               st.column_config.TextColumn("Unit"),
                        "REWORK":             st.column_config.TextColumn("Rework"),
                        "HOT_LOT_FLAG":       st.column_config.TextColumn("Hot Lot"),
                        "FACILITY":           st.column_config.TextColumn("Facility"),
                        "ROUTE":              st.column_config.TextColumn("Route"),
                        "TRANSCODE":          st.column_config.TextColumn("Transcode"),
                        "COST_CENTER":        st.column_config.TextColumn("Cost Center"),
                        "WORK_CENTER":        st.column_config.TextColumn("Work Center"),
                        "HOLD_TIME_HRS":      st.column_config.NumberColumn("Hold Time (hrs)", format="%.4f"),
                    })
                st.download_button("⬇ Download CSV", monitor_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"lot_monitor_{lot}.csv", mime="text/csv")
            with st.expander("📊 Delay Summary by Operation", expanded=False):
                if "DELAY" in monitor_df.columns and "OPERATION" in monitor_df.columns:
                    delay_summary = (
                        monitor_df.groupby("OPERATION", as_index=False).agg(
                            OPERATION_DESC=("OPERATION_DESC", "first"),
                            SUM_DELAY=("DELAY", "sum"),
                        )
                    )
                    delay_summary["SUM_DELAY"] = delay_summary["SUM_DELAY"].round(4)
                    delay_summary = delay_summary[delay_summary["SUM_DELAY"] <= -0.5]
                    delay_summary = delay_summary.sort_values("SUM_DELAY", ascending=True).reset_index(drop=True)
                    if delay_summary.empty:
                        st.success("✅ No operations with delay ≤ -0.5 found for this lot.")
                    else:
                        st.caption(f"⚠️ {len(delay_summary)} operation(s) with delay ≤ -0.5 found.")
                        st.dataframe(delay_summary, use_container_width=True, hide_index=True,
                            column_config={
                                "OPERATION":      st.column_config.TextColumn("Operation"),
                                "OPERATION_DESC": st.column_config.TextColumn("Description"),
                                "SUM_DELAY":      st.column_config.NumberColumn("Sum of Delays", format="%.4f"),
                            })
                else:
                    st.warning("⚠️ DELAY or OPERATION column not available.")
        except Exception as e:
            st.error(f"❌ Error loading data: {str(e)}")
            st.exception(e)
    st.stop()

# ==================== LIST PAGE ====================
# ✅ CRITICAL GUARD — only runs if page == "list"
if page != "list":
    st.stop()

# ── Sidebar ──
st.sidebar.markdown(
    f"""<div style="text-align:center; padding:8px 0 16px 0;">
        <img src="data:image/png;base64,{logo_b64}" style="width:130px;">
    </div>""",
    unsafe_allow_html=True,
)
st.sidebar.markdown("---")
# ── Sidebar — Auto Token Status ──
st.sidebar.markdown("### 🔑 OTD Authentication")
_saved_token, _token_valid = load_saved_token()
if _token_valid:
    try:
        import json as _json
        with open("otd_token.json") as _f:
            _tdata   = _json.load(_f)
        _saved_at  = datetime.datetime.fromisoformat(_tdata["saved_at"])
        _elapsed   = (datetime.datetime.now() - _saved_at).total_seconds() / 60
        _remaining = TOKEN_EXPIRY_MINUTES - _elapsed
        st.sidebar.success(f"✅ Auto-authenticated")
        st.sidebar.caption(f"⏱ Token refreshes in ~{_remaining:.0f} min")
    except Exception:
        st.sidebar.success("✅ Token active")
else:
    st.sidebar.info("🔄 Fetching token automatically...")

st.sidebar.markdown("---")
total_desc = len(st.session_state.get("global_descriptions", pd.DataFrame()))
if st.sidebar.button(f"📂 List of Category ({total_desc} records)", use_container_width=True, key="goto_category_list"):
    st.query_params.clear()
    st.query_params["page"] = "category_list"
    st.rerun()

st.sidebar.markdown("---")
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = datetime.datetime.now()
st.sidebar.caption(f"🕒 Last refresh: {st.session_state.last_refresh.strftime('%H:%M:%S')}")
if st.sidebar.button("🔄 Clear Cache & Refresh", key="clear_cache_btn", use_container_width=True):
    st.cache_data.clear()
    st.session_state.page_num = 1
    st.session_state.last_refresh = datetime.datetime.now()
    st.rerun()

st.sidebar.markdown("---")
# st.sidebar.header("Filters")

# ── Header ──
st.markdown(
    f'<div class="inf-header"><div style="display:flex; align-items:center; justify-content:space-between;"><div><h1 style="margin:0; border:none; color:white;">📋 OTD Order Tracking</h1><p style="margin:4px 0 0 0; color:#C8F0D8;">Infineon Technologies | Live OTD Data</p></div><img src="data:image/png;base64,{logo_b64}" style="height:55px;"></div></div>',
    unsafe_allow_html=True,
)

now_str = datetime.datetime.now().strftime("%d %b %Y, %H:%M:%S")
st.markdown(
    f'<div style="display:flex; align-items:center; gap:12px; margin:8px 0;"><span style="font-size:0.82rem; color:#555;">🕒 Last Update: <strong>{now_str}</strong></span><span style="background:#e8f5e9; color:#2e7d32; padding:2px 10px; border-radius:12px; font-size:0.78rem; border:1px solid #a5d6a7;">✅ Auto Refresh</span></div>',
    unsafe_allow_html=True,
)

# ── AFTER (replace with) ──
with st.spinner("🔄 Loading OTD Priority Orders automatically..."):
    otd_df, error_msg = load_otd_priority_orders_auto()

if error_msg:
    if "refresh" in str(error_msg).lower():
        st.info("🔄 Token refreshing... please wait")
        datetime.time.sleep(3)
        st.rerun()
    else:
        st.error(f"❌ {error_msg}")
        st.stop()

if otd_df.empty:
    st.warning("⚠️ No data returned from OTD API.")
    st.stop()

# ── Column Mapping ──
COLUMN_MAP = {
    "febesOrderId":          "External Order",
    "owner":                 "Owner",
    "orderStatus":           "Order Status",
    "orderQuantity":         "Start Quan.",
    "orderUnit":             "Order Unit",
    "orderRemark":           "Order Rem.",
    "waferSize":             "Wafer Size",
    "lotNumber":             "Lot Number",
    "lotResponsible":        "Lot Responsible",
    "deltaCurrentToPlan":    "Delta Current To Plan",
    "requestor":             "Requestor",
    "lotPurpose":            "Lot Purpose",
    "priority":              "Priority",
    "priorityCorridor":      "Priority Corridor",
    "currentFacility":       "Current Facility",
    "currentOperation":      "Current Op.",
    "confirmedDeliveryDate": "Confirmed Delivery",
    "currentDeliveryDate":   "Current Delivery",
    "businessDivision":      "Division",
    "processGroup":          "Process Group",
    "createUser":            "Create User", 
}
available_cols = {k: v for k, v in COLUMN_MAP.items() if k in otd_df.columns}
display_df     = otd_df[list(available_cols.keys())].copy()
display_df     = display_df.rename(columns=available_cols)

# ── Sidebar Filters ──
if "Owner" in display_df.columns:
    all_owners_otd  = sorted(display_df["Owner"].dropna().unique().tolist())
    default_owners  = ["DEVT"] if "DEVT" in all_owners_otd else all_owners_otd
    selected_owners = st.sidebar.multiselect("Owner", options=all_owners_otd, default=default_owners, key="otd_owner_filter")
    if selected_owners:
        display_df = display_df[display_df["Owner"].isin(selected_owners)]

# if "Order Status" in display_df.columns:
#     all_statuses      = sorted(display_df["Order Status"].dropna().unique().tolist())
#     selected_statuses = st.sidebar.multiselect("Order Status", options=all_statuses, default=all_statuses, key="otd_status_filter")
#     if selected_statuses:
#         display_df = display_df[display_df["Order Status"].isin(selected_statuses)]

# if "Priority" in display_df.columns:
#     all_priorities      = sorted(display_df["Priority"].dropna().unique().tolist())
#     selected_priorities = st.sidebar.multiselect("Priority", options=all_priorities, default=all_priorities, key="otd_priority_filter")
#     if selected_priorities:
#         display_df = display_df[display_df["Priority"].isin(selected_priorities)]

# ── Search ──
st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
scols  = st.columns([4, 1])
search = scols[0].text_input("🔍 Search Lot Number", value="", placeholder="e.g. VA543907", key="search_lot_input")
with scols[1]:
    st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
    st.button("Search 🔍", use_container_width=True, key="search_lots_button")
if search.strip():
    s = search.strip().upper()
    if "Lot Number" in display_df.columns:
        display_df = display_df[display_df["Lot Number"].astype(str).str.upper().str.contains(s, na=False)]

# ── Metrics ──
total_rows_otd = len(display_df)
status_counts  = {}
if "Order Status" in display_df.columns:
    status_counts = display_df["Order Status"].value_counts().to_dict()
mc1, mc2, mc3, mc4, mc5 = st.columns(5)
mc1.metric("Total Orders",  total_rows_otd)
mc2.metric("In Production", status_counts.get("In Production", 0))
mc3.metric("Confirmed",     status_counts.get("Confirmed", 0))
mc4.metric("Done",          status_counts.get("Done", 0))
mc5.metric("Warning",       status_counts.get("Warning", 0))
st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)

# ── JS filter helpers ──
def to_js_array(lst):
    return json.dumps(list(lst), ensure_ascii=False).replace("</", "<\\/")

def get_col_options(col):
    if col in display_df.columns:
        return sorted([str(x) for x in display_df[col].dropna().unique().tolist()])
    return []

ext_order_js  = to_js_array(get_col_options("External Order"))
owner_js      = to_js_array(get_col_options("Owner"))
status_js     = to_js_array(get_col_options("Order Status"))
qty_js        = to_js_array(get_col_options("Start Quan."))
unit_js       = to_js_array(get_col_options("Order Unit"))
rem_js        = to_js_array(get_col_options("Order Rem."))
wafer_js      = to_js_array(get_col_options("Wafer Size"))
lotnum_js     = to_js_array(get_col_options("Lot Number"))
lot_resp_js   = to_js_array(get_col_options("Lot Responsible"))
delta_js      = to_js_array(get_col_options("Delta Current To Plan"))
requestor_js  = to_js_array(get_col_options("Requestor"))
lot_purp_js   = to_js_array(get_col_options("Lot Purpose"))
priority_js   = to_js_array(get_col_options("Priority"))
corridor_js   = to_js_array(get_col_options("Priority Corridor"))
facility_js   = to_js_array(get_col_options("Current Facility"))
cur_op_js     = to_js_array(get_col_options("Current Op."))
conf_del_js   = to_js_array(get_col_options("Confirmed Delivery"))
cur_del_js    = to_js_array(get_col_options("Current Delivery"))
division_js   = to_js_array(get_col_options("Division"))
proc_grp_js   = to_js_array(get_col_options("Process Group"))
create_user_js = to_js_array(get_col_options("Create User"))


# ── Pagination is client-side (in iframe). All rows are sent so filters work across the whole dataset. ──
total_rows_otd_full = len(display_df)

# ── Build rows ──
all_rows_html = ""
for _, row in display_df.iterrows():
    ext_order    = str(row.get("External Order",     "—") or "—")
    owner        = str(row.get("Owner",              "—") or "—")
    order_status = str(row.get("Order Status",       "—") or "—")
    start_qty    = str(row.get("Start Quan.",         "—") or "—")
    order_unit   = str(row.get("Order Unit",         "—") or "—")
    order_rem    = str(row.get("Order Rem.",          "—") or "—")
    wafer_size   = str(row.get("Wafer Size",         "—") or "—")
    lot_number   = str(row.get("Lot Number",         "—") or "—")
    lot_resp     = str(row.get("Lot Responsible",    "—") or "—")
    requestor    = str(row.get("Requestor",          "—") or "—")
    lot_purpose  = str(row.get("Lot Purpose",        "—") or "—")
    priority     = str(row.get("Priority",           "—") or "—")
    corridor     = str(row.get("Priority Corridor",  "—") or "—")
    facility     = str(row.get("Current Facility",   "—") or "—")
    cur_op       = str(row.get("Current Op.",        "—") or "—")
    conf_del     = str(row.get("Confirmed Delivery", "—") or "—")
    cur_del      = str(row.get("Current Delivery",   "—") or "—")
    division     = str(row.get("Division",           "—") or "—")
    proc_grp     = str(row.get("Process Group",      "—") or "—")
    create_user  = str(row.get("Create User",        "—") or "—")
    delta_raw              = row.get("Delta Current To Plan", None)
    delta_val              = str(delta_raw) if delta_raw is not None else "—"
    delta_str, delta_style = format_delta_otd(delta_raw)
    status_badge   = get_status_badge(order_status)
    priority_badge = get_priority_badge(priority)
    row_style      = "font-weight:bold; background:#f9f0ff;" if order_status == "Confirmed" else ""
    lot_encoded    = urllib.parse.quote(lot_number)

    def ea(v):
        return v.replace('&','&amp;').replace('"','&quot;').replace("'","&#39;").replace('<','&lt;').replace('>','&gt;')

    tr_open = '<tr class="hl">' if order_status == "Confirmed" else '<tr>'
    all_rows_html += (
        f'{tr_open}'
        f'<td class="b">{esc_cell(ext_order)}</td>'
        f'<td class="c">{esc_cell(owner)}</td>'
        f'<td>{status_badge}</td>'
        f'<td class="c">{esc_cell(start_qty)}</td>'
        f'<td class="c">{esc_cell(order_unit)}</td>'
        f'<td class="sm wbw">{esc_cell(order_rem)}</td>'
        f'<td class="c">{esc_cell(wafer_size)}</td>'
        f'<td class="wb">{esc_cell(lot_number)}</td>'
        f'<td>{esc_cell(lot_resp)}</td>'
        f'<td class="r" style="{delta_style}">{esc_cell(delta_str)}</td>'
        f'<td class="sm">{esc_cell(requestor)}</td>'
        f'<td class="c">{esc_cell(lot_purpose)}</td>'
        f'<td>{priority_badge}</td>'
        f'<td class="c">{esc_cell(corridor)}</td>'
        f'<td class="c">{esc_cell(facility)}</td>'
        f'<td class="c">{esc_cell(cur_op)}</td>'
        f'<td class="c">{esc_cell(conf_del)}</td>'
        f'<td class="c">{esc_cell(cur_del)}</td>'
        f'<td class="c">{esc_cell(division)}</td>'
        f'<td class="c">{esc_cell(proc_grp)}</td>'
        f'<td class="c">{esc_cell(create_user)}</td>'
        f'<td class="act" data-l="{lot_encoded}"></td>'
        f'</tr>\n'
    )

table_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{{box-sizing:border-box;}}
body{{margin:0;padding:0;font-family:sans-serif;}}
#tableWrapper{{position:relative;}}
.otd-filter-popup{{display:none;position:fixed;background:white;border:1px solid #ccc;
    border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.25);z-index:99999;
    width:300px;max-height:420px;overflow:hidden;flex-direction:column;}}
.otd-filter-popup.show{{display:flex;}}
.otd-filter-popup-header{{background:#00695c;color:white;padding:10px 14px;font-weight:bold;
    font-size:0.9rem;flex-shrink:0;display:flex;justify-content:space-between;align-items:center;}}
.otd-filter-popup-header button{{background:none;border:none;color:white;cursor:pointer;font-size:1.1rem;line-height:1;}}
.otd-filter-search{{padding:8px 12px;border-bottom:1px solid #eee;flex-shrink:0;}}
.otd-filter-search input{{width:100%;padding:6px 10px;border:1px solid #ccc;border-radius:4px;font-size:0.85rem;color:#000;}}
.otd-filter-actions{{padding:6px 12px;display:flex;gap:8px;border-bottom:1px solid #eee;flex-shrink:0;}}
.otd-filter-actions button{{padding:4px 10px;border:1px solid #ccc;border-radius:4px;cursor:pointer;font-size:0.78rem;background:#f5f5f5;color:#333;}}
.otd-filter-actions button:hover{{background:#e0f2f1;}}
.otd-filter-list{{overflow-y:auto;max-height:230px;padding:4px 0;flex:1;}}
.otd-filter-item{{display:flex;align-items:center;padding:5px 14px;cursor:pointer;font-size:0.83rem;color:#333;}}
.otd-filter-item:hover{{background:#f0f9f7;}}
.otd-filter-item input[type="checkbox"]{{margin-right:8px;cursor:pointer;width:15px;height:15px;accent-color:#00695c;flex-shrink:0;}}
.otd-filter-footer{{padding:8px 14px;display:flex;justify-content:flex-end;gap:8px;border-top:1px solid #eee;background:#fafafa;flex-shrink:0;}}
.otd-btn-cancel{{padding:5px 14px;border:1px solid #ccc;border-radius:4px;cursor:pointer;background:white;font-size:0.83rem;color:#333;}}
.otd-btn-apply{{padding:5px 14px;border:none;border-radius:4px;cursor:pointer;background:#00695c;color:white;font-size:0.83rem;font-weight:bold;}}
.otd-btn-apply:hover{{background:#004d40;}}
.otd-table{{width:100%;min-width:2400px;border-collapse:collapse;font-size:0.82rem;}}
.otd-table thead tr{{background:#004d40;color:white;text-align:left;}}
.otd-table th{{padding:8px 10px;white-space:nowrap;position:relative;user-select:none;cursor:pointer;}}
.otd-table th:hover{{background:#00695c;}}
.otd-table th.filter-active{{background:#00897b !important;}}
.otd-table th.no-filter{{cursor:default;}}
.otd-table th.no-filter:hover{{background:#004d40 !important;}}
.otd-table td{{font-size:0.82rem;color:#000;padding:6px 8px;}}
.otd-table td.c{{text-align:center;}}
.otd-table td.r{{text-align:right;}}
.otd-table td.sm{{font-size:0.78rem;}}
.otd-table td.wb{{word-break:break-all;}}
.otd-table td.wbw{{word-break:break-word;}}
.otd-table td.b{{font-weight:bold;word-break:break-all;}}
.otd-table td.act{{text-align:center;}}
.otd-table tr.hl{{font-weight:bold;background:#f9f0ff;}}
.otd-table td.act a{{display:block;color:white;padding:3px 6px;font-size:0.72rem;text-align:center;border-radius:4px;text-decoration:none;}}
.otd-table td.act a.btn-mon{{background:#1976d2;margin-bottom:4px;}}
.otd-table td.act a.btn-his{{background:#00897b;}}
.sb,.pb{{padding:2px 8px;border-radius:10px;font-size:0.75rem;white-space:nowrap;border:1px solid #ccc;}}
.sb-in-production{{background:#e3f2fd;color:#1565c0;border-color:#90caf9;}}
.sb-confirmed{{background:#f3e5f5;color:#6a1b9a;border-color:#ce93d8;}}
.sb-warning{{background:#fff3e0;color:#e65100;border-color:#ffcc80;}}
.sb-done{{background:#e8f5e9;color:#1b5e20;border-color:#a5d6a7;}}
.sb-terminated{{background:#fce4ec;color:#880e4f;border-color:#f48fb1;}}
.sb-x{{background:#f5f5f5;color:#333;}}
.pb-hot{{background:#ffebee;color:#c62828;border-color:#ef9a9a;}}
.pb-rocket{{background:#fff8e1;color:#f57f17;border-color:#ffe082;}}
.pb-normal{{background:#f1f8e9;color:#33691e;border-color:#c5e1a5;}}
.pb-x{{background:#f5f5f5;color:#333;}}
.filter-icon{{font-size:0.65rem;margin-left:4px;opacity:0.8;}}
.otd-table tbody tr:nth-child(even){{background:#f9f9f9;}}
.otd-table tbody tr:hover{{background:#e0f2f1 !important;}}
.otd-table tbody tr.hidden{{display:none;}}
.otd-table tbody tr.page-hidden{{display:none;}}
.pagination-bar{{padding:10px 16px;background:#f5f5f5;border:1px solid #ddd;border-top:none;
    display:flex;justify-content:space-between;align-items:center;
    border-radius:0 0 8px 8px;font-size:0.82rem;color:#555;flex-wrap:wrap;gap:8px;}}
.pag-btn{{padding:5px 12px;border:1px solid #ccc;border-radius:4px;cursor:pointer;
    background:white;font-size:0.82rem;color:#333;}}
.pag-btn:hover{{background:#e0f2f1;border-color:#00695c;}}
.pag-btn:disabled{{opacity:0.4;cursor:not-allowed;}}
.pag-btn.active{{background:#00695c;color:white;border-color:#00695c;}}
</style>
</head>
<body>
<div class="otd-filter-popup" id="filterPopup">
  <div class="otd-filter-popup-header">
    <span id="popupTitle">Filter</span>
    <button onclick="closeFilter()">✕</button>
  </div>
  <div class="otd-filter-search">
    <input type="text" id="filterSearch" placeholder="🔍 Search..." oninput="searchFilter()">
  </div>
  <div class="otd-filter-actions">
    <button onclick="selectAll()">✅ All</button>
    <button onclick="clearAll()">🗑️ Clear</button>
  </div>
  <div class="otd-filter-list" id="filterList"></div>
  <div class="otd-filter-footer">
    <button class="otd-btn-cancel" onclick="cancelFilter()">CANCEL</button>
    <button class="otd-btn-apply" onclick="applyFilter()">APPLY</button>
  </div>
</div>
<div style="font-family:sans-serif;">
  <div style="background:#00695c;color:white;padding:12px 16px;border-radius:8px 8px 0 0;
              display:flex;justify-content:space-between;align-items:center;">
    <span style="font-weight:bold;font-size:1rem;">📋 OTD Order Tracking</span>
    <span style="display:flex;align-items:center;gap:12px;">
      <button id="btnClearFilters" onclick="clearAllFilters()" style="background:#ffffff;color:#00695c;border:none;padding:5px 12px;border-radius:6px;font-size:0.8rem;font-weight:bold;cursor:pointer;">✖ Clear Filters</button>
      <span id="rowCount" style="font-size:0.85rem;">{total_rows_otd_full} orders</span>
    </span>
  </div>
  <div id="tableWrapper">
    <div style="overflow-x:auto;border:1px solid #ddd;">
    <table class="otd-table" id="otdTable">
      <thead><tr>
        <th style="min-width:110px;" onclick="openFilter('extorder',this)">External Order <span class="filter-icon">▼</span></th>
        <th style="min-width:60px;"  onclick="openFilter('owner',this)">Owner <span class="filter-icon">▼</span></th>
        <th style="min-width:120px;" onclick="openFilter('status',this)">Order Status <span class="filter-icon">▼</span></th>
        <th style="min-width:80px;"  onclick="openFilter('qty',this)">Start Qty <span class="filter-icon">▼</span></th>
        <th style="min-width:70px;"  onclick="openFilter('unit',this)">Unit <span class="filter-icon">▼</span></th>
        <th style="min-width:120px;" onclick="openFilter('rem',this)">Order Rem. <span class="filter-icon">▼</span></th>
        <th style="min-width:80px;"  onclick="openFilter('wafer',this)">Wafer Size <span class="filter-icon">▼</span></th>
        <th style="min-width:110px;" onclick="openFilter('lotnum',this)">Lot Number <span class="filter-icon">▼</span></th>
        <th style="min-width:180px;" onclick="openFilter('lotresp',this)">Lot Responsible <span class="filter-icon">▼</span></th>
        <th style="min-width:140px;" onclick="openFilter('delta',this)">Delta To Plan <span class="filter-icon">▼</span></th>
        <th style="min-width:200px;" onclick="openFilter('requestor',this)">Requestor <span class="filter-icon">▼</span></th>
        <th style="min-width:90px;"  onclick="openFilter('purpose',this)">Lot Purpose <span class="filter-icon">▼</span></th>
        <th style="min-width:90px;"  onclick="openFilter('priority',this)">Priority <span class="filter-icon">▼</span></th>
        <th style="min-width:120px;" onclick="openFilter('corridor',this)">Priority Corridor <span class="filter-icon">▼</span></th>
        <th style="min-width:110px;" onclick="openFilter('facility',this)">Current Facility <span class="filter-icon">▼</span></th>
        <th style="min-width:110px;" onclick="openFilter('curop',this)">Current Op. <span class="filter-icon">▼</span></th>
        <th style="min-width:130px;" onclick="openFilter('confdel',this)">Confirmed Delivery <span class="filter-icon">▼</span></th>
        <th style="min-width:130px;" onclick="openFilter('curdel',this)">Current Delivery <span class="filter-icon">▼</span></th>
        <th style="min-width:90px;"  onclick="openFilter('division',this)">Division <span class="filter-icon">▼</span></th>
        <th style="min-width:110px;" onclick="openFilter('procgrp',this)">Process Group <span class="filter-icon">▼</span></th>
        <th style="min-width:110px;" onclick="openFilter('createuser',this)">Create User <span class="filter-icon">▼</span></th>
        <th style="min-width:100px;" class="no-filter">Action</th>
      </tr></thead>
      <tbody id="tableBody">{all_rows_html}</tbody>
    </table>
    </div>
  </div>
  <div class="pagination-bar">
    <span id="footerCount">Total: {total_rows_otd_full} orders</span>
    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
      <button class="pag-btn" id="btnFirst"  onclick="goPage(1)">«</button>
      <button class="pag-btn" id="btnPrev"   onclick="goPage(currentPage-1)">‹ Prev</button>
      <span id="pageButtons" style="display:flex;gap:4px;flex-wrap:wrap;"></span>
      <button class="pag-btn" id="btnNext"   onclick="goPage(currentPage+1)">Next ›</button>
      <button class="pag-btn" id="btnLast"   onclick="goPage(totalPages)">»</button>
      <span id="pageInfo" style="margin-left:8px;font-size:0.82rem;color:#555;"></span>
    </div>
    <span id="visibleBadge" style="background:#e0f2f1;color:#00695c;padding:2px 8px;
          border-radius:10px;font-size:0.78rem;font-weight:bold;">{total_rows_otd_full} visible</span>
  </div>
</div>
<script>
// ── Filter Data (ALL rows, all pages) ──
const filterData={{
  extorder:{ext_order_js},owner:{owner_js},status:{status_js},qty:{qty_js},unit:{unit_js},
  rem:{rem_js},wafer:{wafer_js},lotnum:{lotnum_js},lotresp:{lot_resp_js},delta:{delta_js},
  requestor:{requestor_js},purpose:{lot_purp_js},priority:{priority_js},corridor:{corridor_js},
  facility:{facility_js},curop:{cur_op_js},confdel:{conf_del_js},curdel:{cur_del_js},
  division:{division_js},procgrp:{proc_grp_js},createuser:{create_user_js}
}};

let activeFilters={{
  extorder:null,owner:null,status:null,qty:null,unit:null,rem:null,wafer:null,
  lotnum:null,lotresp:null,delta:null,requestor:null,purpose:null,priority:null,
  corridor:null,facility:null,curop:null,confdel:null,curdel:null,division:null,procgrp:null,createuser:null
}};

const colToIdx={{
  extorder:0,owner:1,status:2,qty:3,unit:4,rem:5,wafer:6,lotnum:7,lotresp:8,delta:9,
  requestor:10,purpose:11,priority:12,corridor:13,facility:14,curop:15,confdel:16,
  curdel:17,division:18,procgrp:19,createuser:20
}};

// ── Pagination State ──
const PAGE_SIZE = 200;
let currentPage = 1;
let totalPages  = 1;
let visibleRows = []; // filtered rows

let currentCol=null,currentThEl=null,tempSelected=[];

// ── Filter Popup ──
function openFilter(col,thEl){{
  currentCol=col;currentThEl=thEl;
  const popup=document.getElementById('filterPopup');
  const thRect=thEl.getBoundingClientRect();
  let left=thRect.left,top=thRect.bottom+2;
  const vw=window.innerWidth;
  if(left+300>vw)left=vw-308;if(left<4)left=4;
  popup.style.left=left+'px';popup.style.top=top+'px';
  document.getElementById('popupTitle').textContent='Filter: '+thEl.textContent.replace('▼','').trim();
  document.getElementById('filterSearch').value='';
  tempSelected=activeFilters[col]?[...activeFilters[col]]:[...(filterData[col]||[])];
  renderList(filterData[col]||[]);
  popup.classList.add('show');
  setTimeout(()=>document.getElementById('filterSearch').focus(),50);
}}

function renderList(options){{
  const list=document.getElementById('filterList');list.innerHTML='';
  if(options.length===0){{
    list.innerHTML='<div style="padding:10px 14px;color:#999;font-size:0.83rem;">No options</div>';
    return;
  }}
  options.forEach(opt=>{{
    const item=document.createElement('div');item.className='otd-filter-item';
    const cb=document.createElement('input');cb.type='checkbox';
    cb.checked=tempSelected.includes(opt);
    cb.addEventListener('change',()=>{{
      if(cb.checked){{if(!tempSelected.includes(opt))tempSelected.push(opt);}}
      else{{tempSelected=tempSelected.filter(x=>x!==opt);}}
    }});
    const lbl=document.createElement('span');lbl.textContent=opt||'(Blank)';
    item.appendChild(cb);item.appendChild(lbl);
    item.addEventListener('click',e=>{{
      if(e.target!==cb){{cb.checked=!cb.checked;cb.dispatchEvent(new Event('change'));}}
    }});
    list.appendChild(item);
  }});
}}

function searchFilter(){{
  const q=document.getElementById('filterSearch').value.toLowerCase();
  renderList(filterData[currentCol].filter(x=>x.toLowerCase().includes(q)));
}}

function selectAll(){{
  const q=document.getElementById('filterSearch').value.toLowerCase();
  filterData[currentCol].filter(x=>x.toLowerCase().includes(q)).forEach(o=>{{
    if(!tempSelected.includes(o))tempSelected.push(o);
  }});
  renderList(filterData[currentCol].filter(x=>x.toLowerCase().includes(q)));
}}

function clearAll(){{
  const q=document.getElementById('filterSearch').value.toLowerCase();
  const opts=filterData[currentCol].filter(x=>x.toLowerCase().includes(q));
  tempSelected=tempSelected.filter(x=>!opts.includes(x));
  renderList(opts);
}}

function applyFilter(){{
  const allOpts=filterData[currentCol];
  if(tempSelected.length>=allOpts.length){{
    activeFilters[currentCol]=null;
    if(currentThEl)currentThEl.classList.remove('filter-active');
  }}else{{
    activeFilters[currentCol]=[...tempSelected];
    if(currentThEl)currentThEl.classList.add('filter-active');
  }}
  applyAllFilters();
  closeFilter();
}}

function cancelFilter(){{closeFilter();}}
function closeFilter(){{
  document.getElementById('filterPopup').classList.remove('show');
  currentCol=null;currentThEl=null;
}}

function clearAllFilters(){{
  Object.keys(activeFilters).forEach(k=>{{activeFilters[k]=null;}});
  document.querySelectorAll('#otdTable th.filter-active').forEach(th=>th.classList.remove('filter-active'));
  closeFilter();
  applyAllFilters();
}}

// ── Core: Filter + Paginate ──
function applyAllFilters(){{
  const allRows=Array.from(document.querySelectorAll('#tableBody tr'));

  // 1. Determine which rows pass the filter
  visibleRows=allRows.filter(row=>{{
    for(const[col,selected]of Object.entries(activeFilters)){{
      if(!selected)continue;
      let val=(row.cells[colToIdx[col]]?row.cells[colToIdx[col]].innerText.trim():'');
      if(col==='status'||col==='priority'){{val=val.replace(/^\\W+/u,'').trim();}}
      if(!selected.includes(val))return false;
    }}
    return true;
  }});

  // 2. Reset to page 1 whenever filter changes
  currentPage=1;
  totalPages=Math.max(1,Math.ceil(visibleRows.length/PAGE_SIZE));

  // 3. Update UI
  renderPage();
  updatePaginationBar();
}}

function renderPage(){{
  const allRows=Array.from(document.querySelectorAll('#tableBody tr'));
  const visibleSet=new Set(visibleRows);
  const start=(currentPage-1)*PAGE_SIZE;
  const end=start+PAGE_SIZE;
  const pageSet=new Set(visibleRows.slice(start,end));

  allRows.forEach(row=>{{
    if(!visibleSet.has(row)){{
      // filtered out
      row.classList.add('hidden');
      row.classList.remove('page-hidden');
    }}else if(!pageSet.has(row)){{
      // visible but not on this page
      row.classList.remove('hidden');
      row.classList.add('page-hidden');
    }}else{{
      // show
      row.classList.remove('hidden');
      row.classList.remove('page-hidden');
    }}
  }});

  const total=visibleRows.length;
  const showFrom=total===0?0:start+1;
  const showTo=Math.min(end,total);

  document.getElementById('visibleBadge').textContent=total+' visible';
  document.getElementById('rowCount').textContent=total+' of {total_rows_otd_full} orders';
  document.getElementById('footerCount').textContent=
    'Showing '+showFrom+'–'+showTo+' of '+total+' orders';
}}

function goPage(p){{
  if(p<1||p>totalPages)return;
  currentPage=p;
  renderPage();
  updatePaginationBar();
}}

function updatePaginationBar(){{
  document.getElementById('btnFirst').disabled=currentPage===1;
  document.getElementById('btnPrev').disabled=currentPage===1;
  document.getElementById('btnNext').disabled=currentPage===totalPages;
  document.getElementById('btnLast').disabled=currentPage===totalPages;

  // Page number buttons (show up to 7)
  const container=document.getElementById('pageButtons');
  container.innerHTML='';
  let startP=Math.max(1,currentPage-3);
  let endP=Math.min(totalPages,startP+6);
  if(endP-startP<6)startP=Math.max(1,endP-6);

  for(let i=startP;i<=endP;i++){{
    const btn=document.createElement('button');
    btn.className='pag-btn'+(i===currentPage?' active':'');
    btn.textContent=i;
    btn.onclick=(()=>{{const pg=i;return()=>goPage(pg);}})();
    container.appendChild(btn);
  }}

  document.getElementById('pageInfo').textContent=
    'Page '+currentPage+' / '+totalPages;
}}

// ── Init ──
(function(){{
  // Build action links (kept out of srcdoc to save ~250KB)
  document.querySelectorAll('#tableBody td.act').forEach(td=>{{
    const lot=td.getAttribute('data-l')||'';
    td.innerHTML='<a class="btn-mon" href="/?page=lot_monitor&lot='+lot+'" target="_blank" rel="noopener">📊 Monitor</a>'+
                 '<a class="btn-his" href="/?page=detail&lot='+lot+'&op=" target="_blank" rel="noopener">📋 History</a>';
  }});
  const allRows=Array.from(document.querySelectorAll('#tableBody tr'));
  visibleRows=[...allRows];
  totalPages=Math.max(1,Math.ceil(visibleRows.length/PAGE_SIZE));
  renderPage();
  updatePaginationBar();
}})();

document.addEventListener('click',e=>{{
  const popup=document.getElementById('filterPopup');
  if(popup.classList.contains('show')&&!popup.contains(e.target)&&!e.target.closest('th'))
    closeFilter();
}});
</script>
</body>
</html>"""

_table_bytes = table_html.encode("utf-8")
st.caption(f"📦 iframe HTML size: {len(_table_bytes)/1024:.0f} KB ({len(_table_bytes):,} bytes)")
import os, hashlib
_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(_static_dir, exist_ok=True)
_table_path = os.path.join(_static_dir, "otd_table.html")
with open(_table_path, "wb") as _f:
    _f.write(_table_bytes)
_cache_bust = hashlib.md5(_table_bytes).hexdigest()[:10]
components.iframe(f"app/static/otd_table.html?v={_cache_bust}", height=750, scrolling=True)
st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)

# ── Bottom Action Bar ──
# ── Bottom Action Bar (download only) ──
st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
bcols = st.columns([8, 2])
with bcols[1]:
    st.download_button(
        "⬇ Download CSV",
        display_df.to_csv(index=False).encode("utf-8"),
        file_name=f"otd_orders_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        key="download_otd_csv"
    )
