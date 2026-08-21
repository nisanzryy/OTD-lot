# token_fetcher.py
import json
import time
import datetime
import os
import tempfile
import shutil
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService

try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
except ImportError:
    EdgeChromiumDriverManager = None

TOKEN_FILE  = "otd_token.json"
TOKEN_EXPIRY_MINUTES = 18
# Driver is auto-resolved to match the installed Edge version (see _resolve_driver_path).
EDGE_PATH   = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def _resolve_driver_path() -> str:
    """Return a path to an msedgedriver matching the installed Edge.

    Uses webdriver-manager when available; otherwise returns "" so Selenium
    Manager (built into Selenium 4.10+) downloads/locates the driver itself.
    """
    if EdgeChromiumDriverManager is not None:
        try:
            path = EdgeChromiumDriverManager().install()
            print(f"🧩 Edge driver resolved: {path}", flush=True)
            return path
        except Exception as e:
            print(f"⚠️ webdriver-manager failed ({e}); falling back to Selenium Manager", flush=True)
    return ""

# ============================================================
def save_token(token: str):
    data = {
        "token":    token,
        "saved_at": datetime.datetime.now().isoformat()
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)
    print(f"✅ Token saved at {data['saved_at']}")

def load_saved_token():
    """Returns (token, is_valid)"""
    if not os.path.exists(TOKEN_FILE):
        return "", False
    try:
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)
        token    = data.get("token", "")
        saved_at = datetime.datetime.fromisoformat(data["saved_at"])
        elapsed  = (datetime.datetime.now() - saved_at).total_seconds() / 60
        is_valid = elapsed < TOKEN_EXPIRY_MINUTES and bool(token.strip())
        print(f"📋 Token age: {elapsed:.1f} min — {'✅ Valid' if is_valid else '❌ Expired'}")
        return token, is_valid
    except Exception as e:
        print(f"❌ Error loading token: {e}")
        return "", False

# ============================================================
def fetch_token_via_edge() -> str:
    print("🚀 Starting Edge to fetch token...", flush=True)

    edge_options = EdgeOptions()

    # ── NO headless — runs invisible but stable ──
    edge_options.add_argument("--no-sandbox")
    edge_options.add_argument("--disable-dev-shm-usage")
    edge_options.add_argument("--disable-gpu")
    edge_options.add_argument("--window-size=1920,1080")
    edge_options.add_argument("--no-first-run")
    edge_options.add_argument("--no-default-browser-check")
    edge_options.add_argument("--disable-extensions")
    edge_options.add_argument("--disable-popup-blocking")
    edge_options.add_argument("--ignore-certificate-errors")

    # ── Point to correct Edge binary ──
    edge_options.binary_location = EDGE_PATH

    # ── Enable network performance logging ──
    edge_options.set_capability(
        "ms:loggingPrefs", {"performance": "ALL"}
    )

    # ── Use temp profile ──
    temp_dir = tempfile.mkdtemp(prefix="edge_otd_")
    edge_options.add_argument(f"--user-data-dir={temp_dir}")
    print(f"📁 Temp profile: {temp_dir}", flush=True)

    token  = None
    driver = None

    try:
        driver_path = _resolve_driver_path()
        service = EdgeService(executable_path=driver_path) if driver_path else EdgeService()
        driver  = webdriver.Edge(service=service, options=edge_options)
        print("✅ Edge started!", flush=True)

        # ── Step 1: Visit OTD (SSO auto-login) ──
        print("🌐 Opening OTD website...", flush=True)
        driver.get("https://otd.icp.infineon.com/content")
        print("⏳ Waiting for SSO + page load (15s)...", flush=True)
        time.sleep(15)
        print(f"📄 Title: {driver.title}", flush=True)
        print(f"🔗 URL  : {driver.current_url}", flush=True)

        # ── Step 2: Inject interceptor ──
        print("💉 Injecting token interceptor...", flush=True)
        _inject_interceptor(driver)

        # ── Step 3: Refresh to trigger API calls ──
        print("🔄 Refreshing page...", flush=True)
        driver.refresh()
        time.sleep(12)

        # ── Step 4: Check JS capture ──
        token = driver.execute_script("return window._capturedToken;")
        if token:
            print(f"✅ Token captured via JS! Length: {len(token)}", flush=True)
        else:
            print("⚠️ JS did not capture token yet...", flush=True)

        # ── Step 5: Try network logs ──
        if not token:
            print("🔍 Scanning network logs...", flush=True)
            token = _scan_network_logs(driver)

        # ── Step 6: Try order-tracking page ──
        if not token:
            print("🔄 Trying order-tracking page...", flush=True)
            _inject_interceptor(driver)
            driver.get("https://otd.icp.infineon.com/content#/order-tracking")
            time.sleep(12)
            print(f"📄 Title: {driver.title}", flush=True)

            token = driver.execute_script("return window._capturedToken;")
            if token:
                print(f"✅ Token on order-tracking page!", flush=True)
            else:
                token = _scan_network_logs(driver)

        # ── Step 7: Check localStorage ──
        if not token:
            print("🗄️ Checking localStorage...", flush=True)
            token = _check_storage(driver)

        # ── Step 8: Try cookies ──
        if not token:
            print("🍪 Checking cookies...", flush=True)
            token = _check_cookies(driver)

        if token:
            print(f"✅ Final token length: {len(token)}", flush=True)
        else:
            print("❌ Could not capture token", flush=True)
            # Save screenshot for debugging
            try:
                driver.save_screenshot("debug_screenshot.png")
                print("📸 Screenshot saved: debug_screenshot.png", flush=True)
            except Exception:
                pass

    except Exception as e:
        print(f"❌ Error: {e}", flush=True)
        import traceback
        traceback.print_exc()

    finally:
        if driver:
            try:
                driver.quit()
                print("🔒 Browser closed", flush=True)
            except Exception:
                pass
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("🧹 Cleanup done", flush=True)

    return token or ""


# ============================================================
def _inject_interceptor(driver):
    """Inject JS to intercept fetch/XHR and capture Bearer token"""
    script = """
    window._capturedToken = null;

    // ── Intercept fetch() ──
    if (!window._fetchPatched) {
        window._fetchPatched = true;
        const origFetch = window.fetch;
        window.fetch = function(...args) {
            try {
                const options = args[1] || {};
                const headers = options.headers || {};
                let auth = null;
                if (headers instanceof Headers) {
                    auth = headers.get('Authorization');
                } else if (typeof headers === 'object') {
                    auth = headers['Authorization']
                        || headers['authorization']
                        || null;
                }
                if (auth && auth.startsWith('Bearer ')) {
                    window._capturedToken = auth;
                    console.log('✅ fetch token captured');
                }
            } catch(e) {}
            return origFetch.apply(this, args);
        };
    }

    // ── Intercept XMLHttpRequest ──
    if (!window._xhrPatched) {
        window._xhrPatched = true;
        const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
        XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
            if (name && name.toLowerCase() === 'authorization'
                && value && value.startsWith('Bearer ')) {
                window._capturedToken = value;
                console.log('✅ XHR token captured');
            }
            return origSetHeader.apply(this, arguments);
        };
    }
    console.log('✅ Interceptor injected');
    """
    driver.execute_script(script)


def _scan_network_logs(driver) -> str:
    """Scan Edge performance logs for Bearer token"""
    try:
        logs = driver.get_log("performance")
        print(f"📊 {len(logs)} network log entries", flush=True)
        for log in logs:
            try:
                msg = json.loads(log["message"])["message"]
                if msg.get("method") == "Network.requestWillBeSent":
                    headers = (msg.get("params", {})
                                  .get("request", {})
                                  .get("headers", {}))
                    auth = headers.get("Authorization", "")
                    if auth.startswith("Bearer "):
                        print("✅ Token in network logs!", flush=True)
                        return auth
            except Exception:
                continue
    except Exception as e:
        print(f"⚠️ Network log error: {e}", flush=True)
    return ""


def _check_storage(driver) -> str:
    """Check localStorage and sessionStorage for token"""
    try:
        script = """
        let token = null;
        const stores = [];
        try { stores.push(localStorage); }   catch(e) {}
        try { stores.push(sessionStorage); } catch(e) {}

        for (const store of stores) {
            try {
                for (let i = 0; i < store.length; i++) {
                    const key = store.key(i);
                    const val = store.getItem(key);
                    if (!val) continue;

                    // Direct Bearer token
                    if (val.startsWith('Bearer ')) {
                        token = val; break;
                    }
                    // JWT token stored as JSON
                    if (val.includes('eyJ')) {
                        try {
                            const p = JSON.parse(val);
                            const t = p.access_token
                                   || p.token
                                   || p.id_token
                                   || p.bearerToken
                                   || null;
                            if (t) { token = 'Bearer ' + t; break; }
                        } catch(e) {}
                        // Raw JWT
                        if (val.startsWith('eyJ')) {
                            token = 'Bearer ' + val; break;
                        }
                    }
                }
            } catch(e) {}
            if (token) break;
        }
        return token;
        """
        result = driver.execute_script(script)
        if result:
            print(f"✅ Token in storage! Length: {len(result)}", flush=True)
        return result or ""
    except Exception as e:
        print(f"⚠️ Storage error: {e}", flush=True)
        return ""


def _check_cookies(driver) -> str:
    """Check cookies for auth token"""
    try:
        cookies = driver.get_cookies()
        print(f"🍪 Found {len(cookies)} cookies", flush=True)
        for cookie in cookies:
            name = cookie.get("name", "").lower()
            val  = cookie.get("value", "")
            if any(k in name for k in ["token", "auth", "bearer", "access"]):
                print(f"🍪 Auth cookie found: {name}", flush=True)
                if val.startswith("Bearer "):
                    return val
                if val.startswith("eyJ"):
                    return f"Bearer {val}"
    except Exception as e:
        print(f"⚠️ Cookie error: {e}", flush=True)
    return ""


# ============================================================
def get_valid_token() -> str:
    """Main function — call this in Streamlit app"""
    token, is_valid = load_saved_token()
    if is_valid:
        print("✅ Using cached token", flush=True)
        return token

    print("🔄 Fetching new token via Edge...", flush=True)
    new_token = fetch_token_via_edge()

    if new_token:
        save_token(new_token)
        print("✅ Token saved!", flush=True)
        return new_token
    else:
        print("❌ Failed to fetch token", flush=True)
        return ""


# ============================================================
if __name__ == "__main__":
    print("=" * 50, flush=True)
    print("🧪 OTD Token Fetcher Test", flush=True)
    print("=" * 50, flush=True)
    token = get_valid_token()
    if token:
        print(f"\n✅ SUCCESS!", flush=True)
        print(f"Preview: {token[:80]}...", flush=True)
        print(f"Length : {len(token)} chars", flush=True)
    else:
        print("\n❌ FAILED", flush=True)
        print("Check debug_screenshot.png if it was created", flush=True)