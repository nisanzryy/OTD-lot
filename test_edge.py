# test_edge.py
import sys
print("Step 1: Python is running", flush=True)

import os
import tempfile
print("Step 2: os and tempfile imported", flush=True)

try:
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.service import Service as EdgeService
    print("Step 3: Selenium imported OK", flush=True)
except Exception as e:
    print(f"Step 3 FAILED: {e}", flush=True)
    sys.exit(1)

try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    print("Step 4: webdriver_manager imported OK", flush=True)
except Exception as e:
    print(f"Step 4 FAILED: {e}", flush=True)
    sys.exit(1)

import time
print("Step 5: All imports done", flush=True)

# ── Hardcode the driver path we already know works ──
DRIVER_PATH = r"C:\Users\nazrinurnisa\.wdm\drivers\edgedriver\win64\147.0.3912.98\msedgedriver.exe"
EDGE_PATH   = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

print(f"Step 6: Driver path = {DRIVER_PATH}", flush=True)
print(f"Step 7: Edge path   = {EDGE_PATH}",   flush=True)

# Check files exist
print(f"Step 8: Driver exists? {os.path.exists(DRIVER_PATH)}", flush=True)
print(f"Step 9: Edge exists?   {os.path.exists(EDGE_PATH)}",   flush=True)

edge_options = EdgeOptions()
print("Step 10: EdgeOptions created", flush=True)

# ── NO headless — browser VISIBLE ──
edge_options.add_argument("--no-sandbox")
edge_options.add_argument("--disable-dev-shm-usage")
edge_options.add_argument("--disable-gpu")
edge_options.add_argument("--no-first-run")
edge_options.add_argument("--no-default-browser-check")
edge_options.add_argument("--disable-extensions")

# ── Point to correct Edge binary ──
edge_options.binary_location = EDGE_PATH

# ── Use temp profile ──
temp_dir = tempfile.mkdtemp(prefix="edge_test_")
print(f"Step 11: Temp dir = {temp_dir}", flush=True)
edge_options.add_argument(f"--user-data-dir={temp_dir}")

print("Step 12: Starting EdgeDriver service...", flush=True)

try:
    service = EdgeService(executable_path=DRIVER_PATH)
    print("Step 13: Service created OK", flush=True)

    print("Step 14: Opening Edge browser...", flush=True)
    driver = webdriver.Edge(service=service, options=edge_options)
    print("✅ Step 15: Edge opened successfully!", flush=True)

    print(f"📄 Title: {driver.title}", flush=True)

    print("🌐 Step 16: Navigating to OTD...", flush=True)
    driver.get("https://otd.icp.infineon.com/content")

    print("⏳ Step 17: Waiting 15 seconds...", flush=True)
    time.sleep(15)

    print(f"📄 Page title : {driver.title}", flush=True)
    print(f"🔗 Current URL: {driver.current_url}", flush=True)

    driver.quit()
    print("✅ Test PASSED! Edge works!", flush=True)

except Exception as e:
    print(f"❌ FAILED at browser launch: {e}", flush=True)
    import traceback
    traceback.print_exc()

finally:
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    print("🧹 Cleanup done", flush=True)