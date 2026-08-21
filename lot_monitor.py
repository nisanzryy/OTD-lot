import streamlit as st
import pandas as pd
import base64
from get_conn import get_cerberus_conn

st.set_page_config(page_title="Lot Monitor", layout="wide")
def load_css():
    with open("static/style.css") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

def get_logo_base64():
    with open("static/infineon_logo.png", "rb") as f:
        return base64.b64encode(f.read()).decode()

load_css()
logo_b64 = get_logo_base64()

# Get lot number from query params
qp = st.query_params
lot_number = qp.get("lot", "")

# Header
st.markdown(
    f"""
    <div class="inf-header">
        <div style="display:flex; align-items:center; justify-content:space-between;">
            <div>
                <h1 style="margin:0; border:none; color:white;">
                    📊 Lot Monitor - {lot_number}
                </h1>
                <p style="margin:4px 0 0 0; color:#C8F0D8;">
                    Infineon Technologies | Detailed Lot Analysis
                </p>
            </div>
            <img src="data:image/png;base64,{logo_b64}" style="height:55px;">
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Back button
col1, col2 = st.columns([1, 11])
if col1.button("⬅ Back"):
    st.query_params.clear()
    st.query_params["page"] = "list"
    st.switch_page("app.py")

if not lot_number:
    st.error("❌ No lot number provided!")
    st.stop()

# Function to load lot monitor data
@st.cache_data(ttl=300)
def load_lot_monitor_data(lot: str):
    sql = f"""
    WITH route_master_data AS (
        SELECT cl.Facility, cl.Route, nvl(ro.operation, cl.operation) AS Operation,
               o.store_flag_rep AS store_flag, ro.operation_sequence_number,
               CASE WHEN ro.operation_sequence_number > visited_until THEN 'Y' ELSE 'N' END AS is_off_route,
               nvl(o.operation_long_desc, nvl(o.operation_short_desc,'??')) AS Operation_Desc,
               CASE WHEN loop_back_indicator>ro.OPERATION_SEQUENCE_NUMBER THEN NULL ELSE ro.queue_cycle_time END AS queue_cycle_time,
               CASE WHEN loop_back_indicator>ro.OPERATION_SEQUENCE_NUMBER THEN NULL ELSE round(ro.process_cycle_time + ro.queue_cycle_time ,3) END AS due_CT,
               cl.lot AS Lot, cl.route_order_seq, cl.route_leave_time_stamp,
               NVL(ro.optional_oper_flag, 'N') AS optional_oper_flag
        FROM (
            SELECT lot, facility, route, operation, route_order_seq,
                   operation_sequence_number, loop_back_indicator,
                   CASE WHEN LEAD(operation_sequence_number,1,99999) OVER (PARTITION BY lot,route ORDER BY TIME_STAMP) < visited_until
                        THEN visited_until
                        ELSE LEAD(operation_sequence_number, 1, 99999) OVER(PARTITION BY lot, route ORDER BY TIME_STAMP)
                   END AS TO_OPERATION_SEQUENCE_NUMBER,
                   CASE WHEN LEAD(operation)OVER(PARTITION BY lot ORDER BY time_stamp) IS NULL
                        THEN 99999
                        ELSE visited_until
                   END AS visited_until,
                   LEAD(route_order_seq, 1, sysdate+1/24)OVER(PARTITION BY lot ORDER BY route_order_seq) AS route_leave_time_stamp
            FROM (
                SELECT f_sql.*,
                       coalesce(LEAD(operation_sequence_number,1,9999) OVER (PARTITION BY Lot,Route ORDER BY Time_Stamp), operation_sequence_number) AS visited_until,
                       CASE WHEN nvl(f_sql.Route,'n/a') != LAG(nvl(f_sql.Route,'n/a'), 1, 'prev')OVER(PARTITION BY f_sql.Lot ORDER BY f_sql.Time_Stamp)
                            OR f_sql.operation_sequence_number < LAG(f_sql.OPERATION_SEQUENCE_NUMBER)OVER(PARTITION BY f_sql.lot ORDER BY f_sql.TIME_STAMP)
                            THEN f_sql.Time_Stamp
                            ELSE NULL
                       END AS route_order_seq
                FROM (
                    SELECT t.lot, t.facility, t.route, t.Time_Stamp as Time_Stamp,
                           CASE WHEN nvl(r.route,'n/a') != LAG(nvl(r.route,'n/a'), 1, 'prev')OVER(PARTITION BY t.lot ORDER BY t.Time_Stamp)
                                OR r.route IS NULL
                                OR nvl(r.route,'n/a') != LEAD(nvl(r.route,'n/a'), 1, 'next')OVER(PARTITION BY t.lot ORDER BY t.Time_Stamp)
                                OR r.OPERATION_SEQUENCE_NUMBER > LEAD(r.OPERATION_SEQUENCE_NUMBER)OVER(PARTITION BY t.lot, t.ROUTE ORDER BY t.Time_Stamp)
                                OR r.OPERATION_SEQUENCE_NUMBER < LAG(r.OPERATION_SEQUENCE_NUMBER)OVER(PARTITION BY t.lot, t.ROUTE ORDER BY t.Time_Stamp)
                                THEN t.operation
                                ELSE NULL
                           END AS Operation,
                           r.operation_sequence_number,
                           MAX(r.OPERATION_SEQUENCE_NUMBER)OVER(PARTITION BY t.lot,t.route ORDER BY t.Time_Stamp ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS loop_back_indicator
                    FROM dwh_wip_data_all_transactions t
                    LEFT OUTER JOIN dwh_route_operation r
                        ON (t.FACILITY = r.FACILITY AND t.ROUTE = r.ROUTE AND t.operation = r.OPERATION AND t.DATE_STAMP<=r.create_date)
                    WHERE t.lot = '{lot}'
                        AND t.operation <> t.to_operation
                ) f_sql
                WHERE f_sql.Operation IS NOT NULL
            )
            WHERE route_order_seq IS NOT NULL
        ) cl
        LEFT OUTER JOIN dwh_route_operation ro
            ON (cl.facility = ro.facility AND cl.route = ro.route AND ro.operation_sequence_number >= cl.operation_sequence_number and ro.operation_sequence_number < cl.TO_OPERATION_SEQUENCE_NUMBER)
        LEFT OUTER JOIN dwh_operation o
            ON (nvl(ro.facility,cl.facility) = o.facility AND nvl(ro.operation,cl.operation) = o.operation)
        WHERE NVL(ro.optional_oper_flag, 'N') = 'N'
    ),
    lot_hold_history AS (
        SELECT lot, Facility, Route, Operation, Transcode, time_stamp AS rllt_time_stamp,
               (time_stamp-HLLT_TIME_STAMP) AS actual_HoldTime
        FROM (
            SELECT w.*, LAG (time_stamp) OVER (PARTITION BY lot, facility, route, operation ORDER BY time_stamp) AS HLLT_TIME_STAMP
            FROM (
                SELECT lot, Facility, Route, Operation, Transcode, time_stamp
                FROM dwh_wip_data_all_transactions
                WHERE Lot = '{lot}'
                    AND deleted_flag <> 'Y'
                    AND transcode in (select var_value from PPI_var where var_name IN ('HOLD START TRANSACTION','HOLD RELEASE TRANSACTION'))
            ) w
        ) h
        WHERE transcode in (select var_value from PPI_var where var_name = 'HOLD RELEASE TRANSACTION')
    ),
    lot_valid_moves AS (
        SELECT a.facility, a.route, a.operation, a.to_operation, a.lot, a.Time_Stamp as Time_Stamp,
               a.transcode, a.qty_out_1, a.unit_1, a.equipment,
               (SELECT equipment_desc FROM dwh_equipment e WHERE e.equipment = a.equipment AND ROWNUM = 1) AS equipment_description,
               a.work_center, a.cost_center, a.rework_flag, a.hot_lot_flag, a.hot_oper_flag, a.super_hot_flag,
               a.enter_operation_time_stamp as enter_operation_time_stamp, a.due_cycle_time,
               a.due_cycle_time - a.due_process_cycle_time AS due_wait_time,
               nvl((a.Time_Stamp - nvl(LAG(a.Time_Stamp)OVER(PARTITION BY a.lot ORDER BY a.lot, a.Time_Stamp), a.enter_operation_time_stamp)), 0) AS cycleTime,
               nvl((a.movein_time_stamp - nvl(LAG(a.Time_Stamp)OVER(PARTITION BY a.lot ORDER BY a.lot, a.Time_Stamp),a.enter_operation_time_stamp)), 0) AS wt,
               nvl(a.sps_number, a.main_sps) as sps_number,
               (SELECT sum(actual_holdtime)
                FROM lot_hold_history h
                WHERE a.facility = h.facility
                    AND a.route = h.route
                    AND a.operation = h.operation
                    AND a.lot = h.lot
                    AND a.enter_operation_time_stamp <= h.rllt_time_stamp
                    AND a.time_stamp >= h.rllt_time_stamp) AS actual_holdtime
        FROM dwh_wip_data_all_transactions a
        WHERE a.operation <> a.to_operation
            AND a.lot = '{lot}'
            AND a.deleted_flag <> 'Y'
    )
    SELECT
        nvl(l.Operation,r.Operation) AS Operation,
        r.Operation_Desc AS "Operation_Desc",
        nvl(r.optional_oper_flag,'??') AS "Optional_Oper_Flag",
        nvl(r.store_flag, case when l.Operation < '1000' then 'Y' else 'N' end) AS "Store_Oper_Flag",
        round(SUM(nvl(l.due_cycle_time,r.due_CT)), 4) AS "Target_Ct",
        round(sum(l.cycleTime), 4) AS "Actual_Ct",
        round(sum(l.wt), 4) AS Waittime,
        round(sum(nvl(l.due_wait_time,r.queue_cycle_time)), 4) AS "Target_Waittime",
        l.time_stamp AS "Time_Stamp",
        round(decode(sum(nvl(l.due_cycle_time,r.due_CT)), 0, 1, SUM(l.time_stamp - l.enter_operation_time_stamp)/sum(nvl(l.due_cycle_time,r.due_CT))), 4) AS "FF_To_TargetFF",
        l.SPS_number AS "Sps_Number",
        l.equipment AS Equipment,
        l.equipment_description AS "Equipment_Description",
        round(avg(sum(l.cycleTime)) over(partition by l.facility, l.route, l.operation, l.sps_number), 4) AS Rpt,
        l.qty_out_1 AS Amount,
        l.unit_1 AS Unit,
        l.rework_flag AS Rework,
        nvl(l.hot_lot_flag, 'N') AS "Hot_Lot_Flag",
        nvl(l.hot_oper_flag, 'N') AS "Hot_Oper_Flag",
        nvl(l.super_hot_flag, 'N') AS "Super_Hot_Flag",
        nvl(l.Facility,r.Facility) AS Facility,
        nvl(l.Route, nvl(r.Route,case when r.store_flag='Y' then 'store' else r.Operation_Desc end)) AS Route,
        l.transcode AS Transcode,
        l.cost_center AS "Cost_Center",
        l.work_center AS "Work_Center"
    FROM lot_valid_moves l
    FULL OUTER JOIN route_master_data r
        ON (r.facility = l.facility
            AND nvl(r.route,'n/a') = nvl(l.route,'n/a')
            AND r.operation = l.operation
            AND r.lot = l.lot
            AND (l.time_stamp >= r.route_order_seq AND l.time_stamp < r.route_leave_time_stamp))
    WHERE nvl(r.optional_oper_flag, 'Y') = 'N'
    GROUP BY l.Facility, r.Facility, l.Route, r.Route, l.Lot, r.Lot, r.operation_sequence_number,
             l.Operation, r.Operation, r.Operation_Desc, r.store_flag, r.optional_oper_flag,
             l.cost_center, l.work_center, l.to_operation, l.time_stamp, l.transcode, l.qty_out_1,
             l.unit_1, l.equipment, l.equipment_description, l.SPS_number, l.rework_flag,
             l.hot_lot_flag, l.HOT_Oper_Flag, l.Super_Hot_Flag, r.route_order_seq, l.lot
    ORDER BY l.time_stamp
    """
    
    with get_cerberus_conn() as conn:
        df = pd.read_sql(sql, conn)
    
    # Convert timestamp columns
    if "Time_Stamp" in df.columns:
        df["Time_Stamp"] = pd.to_datetime(df["Time_Stamp"], errors="coerce")
    
    return df

# Load data
with st.spinner(f"Loading lot monitor data for {lot_number}..."):
    try:
        df = load_lot_monitor_data(lot_number)
        
        if df.empty:
            st.warning(f"⚠️ No data found for lot {lot_number}")
            st.stop()
        
        # Display metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Operations", len(df))
        col2.metric("Facility", df["Facility"].iloc[0] if "Facility" in df.columns else "N/A")
        col3.metric("Route", df["Route"].iloc[0] if "Route" in df.columns else "N/A")
        col4.metric("Total Actual CT", f"{df['Actual_Ct'].sum():.2f}" if "Actual_Ct" in df.columns else "N/A")
        
        # Display dataframe
        st.subheader("📋 Operation Details")
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            column_config={
                "Operation": st.column_config.TextColumn("Operation", width="small"),
                "Operation_Desc": st.column_config.TextColumn("Description", width="medium"),
                "Target_Ct": st.column_config.NumberColumn("Target CT", format="%.4f"),
                "Actual_Ct": st.column_config.NumberColumn("Actual CT", format="%.4f"),
                "Waittime": st.column_config.NumberColumn("Wait Time", format="%.4f"),
                "Time_Stamp": st.column_config.DatetimeColumn("Timestamp", format="DD/MM/YYYY HH:mm"),
                "Equipment": st.column_config.TextColumn("Equipment", width="small"),
            }
        )
        
        # Download button
        st.download_button(
            "⬇ Download CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name=f"lot_monitor_{lot_number}.csv",
            mime="text/csv",
        )
        
    except Exception as e:
        st.error(f"❌ Error loading data: {str(e)}")
        st.code(str(e))