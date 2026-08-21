import os
import sys
import shutil
import tempfile
import uuid
import subprocess
import threading
import webbrowser
import functools
import pandas as pd
from flask import Flask, render_template_string, request, jsonify, send_file

# Force unbuffered stdout so progress prints appear immediately in the terminal
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass
print = functools.partial(print, flush=True)  # type: ignore[assignment]

app = Flask(__name__)

# ============ KNIME CONFIGURATION ============
KNIME_EXE     = r"C:\Program Files\KNIME\KNIME Analytics Platform\knime.exe"
GUI_WORKSPACE = r"C:\Users\nazrinurnisa\knime-workspace"
WORKFLOW_NAME = "FCT_APC_MAPPING RUN  SEARCH 2LOT GUI"
OUTPUT_FILE   = r"C:\Users\nazrinurnisa\Desktop\COMBINE ALL.xlsx"
# Batch temp dir — NO spaces, short path safe
BATCH_TMP_DIR = r"C:\knime_batch"
# =============================================

JOBS = {}
JOBS_LOCK = threading.Lock()


def _set_job(job_id, **kwargs):
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {})
        JOBS[job_id].update(kwargs)


def _clear_workspace_locks(workspace_path):
    lock_locations = [
        os.path.join(workspace_path, ".metadata", ".lock"),
        os.path.join(workspace_path, ".metadata", "version.ini"),
        os.path.join(workspace_path, ".lock"),
    ]
    for lock in lock_locations:
        if os.path.exists(lock):
            try:
                os.remove(lock)
                print(f"[INFO] Removed lock: {lock}")
            except Exception as e:
                print(f"[WARN] Could not remove lock {lock}: {e}")


def _make_batch_ini():
    src_ini = os.path.join(os.path.dirname(KNIME_EXE), "knime.ini")
    custom_dir = os.path.join(os.path.expanduser("~"), ".knime-batch-launcher")
    os.makedirs(custom_dir, exist_ok=True)
    custom_ini = os.path.join(custom_dir, "knime-batch.ini")

    with open(src_ini, "r", encoding="utf-8") as f:
        lines = f.readlines()

    EXACT_REMOVE = {
        "-profileLocation",
        "-profileList",
        "https://api.knime.icp.infineon.com/execution/customization-profiles/contents",
        "33b999a1-8275-4aa3-bcb3-cfd3be2cd4ad",
    }
    CONTAINS_REMOVE = [
        "infineon.com",
        "customization-profiles",
        "api.knime.icp",
    ]

    out = []
    for line in lines:
        stripped = line.strip()
        if stripped in EXACT_REMOVE:
            print(f"[INI] Removed line: {stripped}")
            continue
        if any(p in stripped for p in CONTAINS_REMOVE):
            print(f"[INI] Removed line (pattern): {stripped}")
            continue
        out.append(line)

    batch_additions = [
        "\n# --- Batch mode additions ---\n",
        "-XX:+UseG1GC\n",
        "-XX:-OmitStackTraceInFastThrow\n",
        "-Dknime.database.timeout=120\n",
        "-Djava.awt.headless=true\n",
        # Prevent libcef.dll JVM crash in batch mode (KNIME JS / Interactive Views)
        "-Dchromium.swt.disable=true\n",
        "-Dorg.knime.ui.java.disabled=true\n",
        "-Dknime.disable.javascriptviews=true\n",
    ]
    out.extend(batch_additions)

    with open(custom_ini, "w", encoding="utf-8") as f:
        f.writelines(out)

    return custom_ini


def _prepare_batch_workspace(run_dir, idx):
    r"""
    Copy GUI workspace into C:\knime_batch\run_xxx\ws_001
    No spaces in path — prevents KNIME short-path issues.
    """
    batch_ws = os.path.join(run_dir, f"ws_{idx:03d}")
    print(f"[INFO] Copying workspace to: {batch_ws}")

    shutil.copytree(
        GUI_WORKSPACE,
        batch_ws,
        ignore=shutil.ignore_patterns("*.lock", "remote_blobs"),
        dirs_exist_ok=False,
    )
    _clear_workspace_locks(batch_ws)

    workflow_dir = os.path.join(batch_ws, WORKFLOW_NAME)
    if not os.path.isdir(workflow_dir):
        available = os.listdir(batch_ws)
        raise RuntimeError(
            f"Workflow folder not found: {workflow_dir}\n"
            f"Available folders: {available}"
        )

    print(f"[INFO] batch_ws    = {batch_ws}")
    print(f"[INFO] workflow_dir = {workflow_dir}")
    return batch_ws, workflow_dir


def _run_one_lot(lot, batch_workspace, workflow_dir):
    """Run KNIME for a single lot. Returns (ok, error_message)."""
    if os.path.exists(OUTPUT_FILE):
        try:
            os.remove(OUTPUT_FILE)
        except Exception as e:
            return False, f"Could not delete previous output: {e}"

    try:
        custom_ini = _make_batch_ini()
    except Exception as e:
        return False, f"Could not create batch knime.ini: {e}"

    DENODO_USER     = "nazrinurnisa"
    DENODO_PASSWORD = "Protonsaga979_"

    cmd = [
        KNIME_EXE,
        "--launcher.ini", custom_ini,
        "-nosplash",
        "-consoleLog",
        "-data", batch_workspace,           # ✅ separate arg — no = sign
        "-application", "org.knime.product.KNIME_BATCH_APPLICATION",
        # ✅ -workflowDir MUST be the "-workflowDir=PATH" form — KNIME's
        # BatchExecutor does not accept it as two separate args and will
        # error with: "Couldn't parse -workflowDir argument: -workflowDir"
        f"-workflowDir={workflow_dir}",
        f"-workflow.variable=search_lot,{lot},String",
        # ✅ KNIME BatchExecutor uses "-credential" (singular) with SEMICOLON
        # separators. The previous "-workflow.credentials=..." raised:
        #   IllegalOptionException: Unknown option '-workflow.credentials'
        f"-credential=credentials;{DENODO_USER};{DENODO_PASSWORD}",
    ]

    log_path = os.path.join(os.path.dirname(OUTPUT_FILE), "knime_last_run.log")

    print(f"\n[DEBUG] CMD:")
    for p in cmd:
        print(f"  {p}")
    print()

    import time
    start_ts = time.time()
    # Write KNIME stdout/stderr DIRECTLY to a file (no PIPE) to avoid
    # Windows knime.exe -> javaw.exe pipe deadlocks where the child never
    # flushes and Popen.stdout.read() hangs forever.
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as logf:
            logf.write(f"=== CMD ===\n{chr(10).join(cmd)}\n\n=== OUTPUT ===\n")
            logf.flush()
            proc = subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            print(f"[KNIME] PID={proc.pid} started. Live log: {log_path}")
            try:
                proc.wait(timeout=600)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
                return False, (
                    f"KNIME timed out after 10 minutes for lot {lot}. "
                    f"Tail of log: {log_path}"
                )
    except FileNotFoundError:
        return False, f"KNIME not found at: {KNIME_EXE}"
    except Exception as e:
        return False, f"KNIME launch error: {e}"

    elapsed = time.time() - start_ts
    try:
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n\n=== EXIT CODE ===\n{proc.returncode}\n=== ELAPSED ===\n{elapsed:.1f}s\n")
    except Exception:
        pass

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            combined_output = f.read()
    except Exception:
        combined_output = ""

    print(f"[DEBUG] Exit code : {proc.returncode}  (elapsed {elapsed:.1f}s)")
    print(f"[DEBUG] Log tail  :\n{combined_output[-1500:]}")

    if "IllegalOptionException" in combined_output or \
       "customization-profiles" in combined_output:
        return False, (
            "KNIME blocked by Infineon profile flags or invalid CLI option. "
            f"Full log: {log_path}"
        )

    if ("another process has locked a portion of the file" in combined_output
            or "ConfigurationAreaChecker" in combined_output):
        return False, (
            "KNIME Analytics Platform GUI is still open and holding an "
            "Eclipse configuration lock. Close KNIME completely and retry. "
            f"Full log: {log_path}"
        )

    if "EXCEPTION_ACCESS_VIOLATION" in combined_output:
        return False, (
            "JVM crashed. Close KNIME GUI completely, then retry. "
            f"Full log: {log_path}"
        )

    if "DB Connection no longer available" in combined_output:
        return False, (
            "DENODO DB connection failed in batch mode. "
            f"Full log: {log_path}"
        )

    if "created an empty data table" in combined_output \
            and not os.path.exists(OUTPUT_FILE):
        return False, (
            f"Lot '{lot}' returned no data. "
            "Verify the lot ID exists in DENODO. "
            f"Full log: {log_path}"
        )

    if proc.returncode != 0:
        return False, (
            f"KNIME failed (code {proc.returncode}). "
            f"{combined_output[:600]} "
            f"Full log: {log_path}"
        )

    if not os.path.exists(OUTPUT_FILE):
        return False, (
            "Workflow finished but output file not produced. "
            f"KNIME tail: {combined_output[-800:]!r}. "
            f"Full log: {log_path}"
        )

    return True, ""


def _merge_excels(per_lot_files, final_path):
    sheet_buckets = {}
    for src in per_lot_files:
        try:
            xls = pd.read_excel(src, sheet_name=None, dtype=str)
        except Exception:
            continue
        for sheet, df in xls.items():
            sheet_buckets.setdefault(sheet, []).append(df)
    if not sheet_buckets:
        raise RuntimeError("No data could be read from per-lot Excel files.")
    with pd.ExcelWriter(final_path, engine="openpyxl") as writer:
        for sheet, frames in sheet_buckets.items():
            combined = pd.concat(frames, ignore_index=True)
            safe_name = sheet[:31] if sheet else "Sheet1"
            combined.to_excel(writer, sheet_name=safe_name, index=False)


def _run_knime(job_id, lot_string):
    try:
        lots = [l.strip() for l in lot_string.split(",") if l.strip()]
        if not lots:
            _set_job(job_id, status="done", ok=False, message="No lots provided.")
            return

        _set_job(job_id, status="running",
                 message=f"Preparing batch run for {len(lots)} lot(s)...")

        if os.path.exists(OUTPUT_FILE):
            try:
                os.remove(OUTPUT_FILE)
            except Exception as e:
                _set_job(job_id, status="done", ok=False,
                         message=f"Could not delete old output: {e}")
                return

        # ✅ Clear original workspace locks
        _clear_workspace_locks(GUI_WORKSPACE)

        # ✅ Use C:\knime_batch — NO spaces in path
        os.makedirs(BATCH_TMP_DIR, exist_ok=True)

        # Clean previous runs
        for item in os.listdir(BATCH_TMP_DIR):
            item_path = os.path.join(BATCH_TMP_DIR, item)
            try:
                shutil.rmtree(item_path, ignore_errors=True)
            except Exception:
                pass

        run_dir = tempfile.mkdtemp(prefix="run_", dir=BATCH_TMP_DIR)
        print(f"[INFO] run_dir = {run_dir}")

        per_lot_files = []

        try:
            for idx, lot in enumerate(lots, start=1):

                _set_job(job_id, status="running",
                         message=f"Copying workspace for lot {idx}/{len(lots)}: {lot}...")

                try:
                    batch_workspace, workflow_dir = _prepare_batch_workspace(
                        run_dir, idx)
                except Exception as e:
                    _set_job(job_id, status="done", ok=False,
                             message=f"Workspace copy failed: {e}")
                    return

                _set_job(job_id, status="running",
                         message=f"Running KNIME for lot {idx}/{len(lots)}: {lot}")

                ok, err = _run_one_lot(lot, batch_workspace, workflow_dir)

                if not ok:
                    _set_job(job_id, status="done", ok=False,
                             message=f"Failed on lot {lot}: {err}")
                    return

                copy_path = os.path.join(run_dir, f"lot_{idx:03d}_{lot}.xlsx")
                shutil.copy2(OUTPUT_FILE, copy_path)
                per_lot_files.append(copy_path)

            _set_job(job_id, status="running",
                     message=f"Merging {len(per_lot_files)} files...")
            _merge_excels(per_lot_files, OUTPUT_FILE)
            _set_job(job_id, status="done", ok=True,
                     message=f"Done! {len(lots)} lot(s) processed and merged.")

        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    except Exception as e:
        _set_job(job_id, status="done", ok=False,
                 message=f"Unexpected error: {e}")


HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>LOT Query Tool</title>
<style>
  * { box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', Arial, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh; margin: 0;
    display: flex; align-items: center; justify-content: center; padding: 20px;
  }
  .card {
    background: #fff; border-radius: 16px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
    padding: 40px; max-width: 560px; width: 100%;
  }
  h1 { margin: 0 0 6px 0; color: #2c3e50; font-size: 28px; }
  .subtitle { color: #7f8c8d; font-size: 14px; margin-bottom: 24px; }
  hr { border: none; border-top: 1px solid #ecf0f1; margin: 18px 0; }
  label { display: block; font-weight: 600; color: #2c3e50; margin-bottom: 6px; }
  .hint { font-size: 12px; color: #95a5a6; margin-bottom: 8px; }
  input[type=text] {
    width: 100%; padding: 12px 14px;
    border: 1px solid #dcdfe3; border-radius: 8px;
    font-size: 14px; outline: none; transition: border-color .2s;
  }
  input[type=text]:focus { border-color: #667eea; }
  .buttons { display: flex; gap: 10px; margin-top: 18px; }
  button {
    flex: 1; padding: 12px 18px; border: none; border-radius: 8px;
    font-size: 14px; font-weight: 600; cursor: pointer;
    transition: opacity .2s, transform .1s;
  }
  button:hover { opacity: .9; }
  button:active { transform: scale(.98); }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .btn-primary { background: #27ae60; color: #fff; }
  .btn-secondary { background: #e74c3c; color: #fff; }
  #status {
    margin-top: 20px; padding: 12px; border-radius: 8px;
    text-align: center; font-size: 14px; min-height: 20px; display: none;
  }
  .status-info    { background: #fef5e7; color: #d68910; display: block !important; }
  .status-success { background: #e8f8f0; color: #27ae60; display: block !important; }
  .status-error   { background: #fdecea; color: #c0392b; display: block !important; }
  .footer { text-align: center; color: #95a5a6; font-size: 12px; margin-top: 22px; }
  .spinner {
    display: inline-block; width: 14px; height: 14px;
    border: 2px solid #d68910; border-top-color: transparent;
    border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle;
    margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
  <div class="card">
    <h1>🔍 LOT Query Tool</h1>
    <div class="subtitle">APC + FCT + Python Combined Report</div>
    <hr>
    <label for="lots">Enter Lot IDs (comma separated)</label>
    <div class="hint">Example: VA614483,VA620896,VA614484</div>
    <input type="text" id="lots" placeholder="e.g. VA614483,VA620896,VA614484" autofocus>
    <div class="buttons">
      <button class="btn-primary"   id="generateBtn" onclick="generate()">▶ GENERATE EXCEL</button>
      <button class="btn-secondary" onclick="document.getElementById('lots').value=''">🗑 CLEAR</button>
    </div>
    <div id="status"></div>
    <div class="footer">📁 Output: Desktop → COMBINE ALL.xlsx</div>
  </div>
<script>
let pollTimer = null;
const sBox = () => document.getElementById('status');
const btn  = () => document.getElementById('generateBtn');
function showInfo(msg, withSpinner = false) {
  const s = sBox();
  s.className = 'status-info';
  s.innerHTML = (withSpinner ? '<span class="spinner"></span>' : '') + msg;
}
function showSuccess(msg) { sBox().className = 'status-success'; sBox().textContent = '✅ ' + msg; }
function showError(msg)   { sBox().className = 'status-error';   sBox().textContent = '❌ ' + msg; }
async function generate() {
  const input = document.getElementById('lots').value.trim();
  if (!input) { showError('Please enter at least one Lot ID!'); return; }
  btn().disabled = true;
  showInfo('Submitting job...', true);
  let jobId;
  try {
    const res = await fetch('/start', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ lots: input })
    });
    const data = await res.json();
    if (!data.ok) { showError(data.message); btn().disabled = false; return; }
    jobId = data.job_id;
  } catch (e) {
    showError('Could not start job: ' + e.message);
    btn().disabled = false;
    return;
  }
  showInfo('Running KNIME workflow... please wait.', true);
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => poll(jobId), 3000);
}
async function poll(jobId) {
  try {
    const res = await fetch('/status/' + jobId);
    const data = await res.json();
    if (data.status === 'running' || data.status === 'queued') {
      showInfo(data.message || 'Still running...', true);
      return;
    }
    clearInterval(pollTimer); pollTimer = null;
    btn().disabled = false;
    if (data.ok) {
      showSuccess(data.message + ' — downloading Excel...');
      window.location = '/download';
    } else {
      showError(data.message);
    }
  } catch (e) {
    showInfo('Reconnecting... ' + e.message, true);
  }
}
document.getElementById('lots').addEventListener('keydown', e => {
  if (e.key === 'Enter') generate();
});
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/start", methods=["POST"])
def start_job():
    data = request.get_json(silent=True) or {}
    lot_input = (data.get("lots") or "").strip()
    if not lot_input:
        return jsonify(ok=False, message="No Lot IDs provided.")
    lots = [l.strip() for l in lot_input.split(",") if l.strip()]
    lot_string = ",".join(lots)
    job_id = uuid.uuid4().hex
    _set_job(job_id, status="queued", ok=None, message="Queued")
    t = threading.Thread(target=_run_knime, args=(job_id, lot_string), daemon=True)
    t.start()
    return jsonify(ok=True, job_id=job_id)


@app.route("/status/<job_id>")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify(status="unknown", ok=False, message="Job not found")
    return jsonify(
        status=job.get("status", "unknown"),
        ok=job.get("ok"),
        message=job.get("message", ""),
    )


@app.route("/download")
def download():
    if not os.path.exists(OUTPUT_FILE):
        return "Output file not found.", 404
    return send_file(OUTPUT_FILE, as_attachment=True)


def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000/")


if __name__ == "__main__":
    threading.Timer(1.0, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)