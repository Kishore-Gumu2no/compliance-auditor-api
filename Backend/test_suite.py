import httpx
import asyncio
import websockets
import json
import os
import subprocess
import time
import sys

# --- CONFIGURATION ---
BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/stream"
# We'll use files from the Experimental_files folder for testing
PDF_TEST_FILE = "Experimental_files/dummy.pdf"
IMAGE_TEST_FILE = "Experimental_files/dummy.png"

async def test_audit_endpoint():
    print("\n--- Testing /audit/ Endpoint ---")
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Prepare unique files for upload to ensure unique hashes
        timestamp = int(time.time())
        temp_pdf = f"temp_test_{timestamp}.pdf"
        temp_img = f"temp_test_{timestamp}.png"
        
        files = []
        try:
            if os.path.exists(PDF_TEST_FILE):
                with open(PDF_TEST_FILE, "rb") as f:
                    content = f.read()
                # Append timestamp to content to change hash
                with open(temp_pdf, "wb") as f:
                    f.write(content + f"\nTimestamp: {timestamp}".encode())
                files.append(("pdfs", (temp_pdf, open(temp_pdf, "rb"), "application/pdf")))
                
            if os.path.exists(IMAGE_TEST_FILE):
                # For images, we just copy the file as adding text might break it, 
                # but the PDF change is enough to change the overall audit result hash if both are used.
                # Actually, let's just use the timestamp in the filename, 
                # though orchestrator.py uses file content for hashing (via pdfplumber/pytesseract).
                # To be sure, we'll just rely on the PDF content change.
                with open(IMAGE_TEST_FILE, "rb") as f:
                    content = f.read()
                with open(temp_img, "wb") as f:
                    f.write(content)
                files.append(("images", (temp_img, open(temp_img, "rb"), "image/png")))
            
            if not files:
                print("❌ Error: No test files found in Experimental_files/")
                return False

            print(f"Uploading {len(files)} files (unique run {timestamp})...")
            response = await client.post(f"{BASE_URL}/audit/", files=files)
            
            if response.status_code == 200:
                result = response.json()
                print("✅ Audit Success!")
                print(f"Status: {result.get('status')}")
                print(f"AI Result: {result.get('ai_analysis', {}).get('final_status', 'No status found')}")
                return True
            else:
                print(f"❌ Audit Failed with status {response.status_code}: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Connection error during Audit: {e}")
            return False
        finally:
            # Close file handles and cleanup
            for _, (_, f_obj, _) in files:
                f_obj.close()
            if os.path.exists(temp_pdf): os.remove(temp_pdf)
            if os.path.exists(temp_img): os.remove(temp_img)

async def test_websocket_stream():
    print("\n--- Testing WebSocket Stream ---")
    try:
        async with websockets.connect(WS_URL) as ws:
            payload = {"platform": "TestRunner", "earnings": 999}
            print(f"Sending payload: {payload}")
            await ws.send(json.dumps(payload))
            
            response = await ws.recv()
            print(f"✅ WebSocket Response: {response}")
            if "Logged earnings" in response:
                return True
            return False
    except Exception as e:
        print(f"❌ WebSocket connection failed: {e}")
        return False

async def wait_for_port_async(port, host="127.0.0.1", timeout=70.0):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            await asyncio.sleep(0.5)
    return False

async def main():
    print("🚀 Starting Backend Test Suite...")
    
    # 1. Start the server in the background and log output to server.log for debugging
    log_file = open("server.log", "w")
    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:server", "--host", "127.0.0.1", "--port", "8000"],
        stdout=log_file,
        stderr=log_file
    )
    
    print("⏳ Waiting for server to initialize (polling up to 15s)...")
    server_ready = await wait_for_port_async(8000)
    if not server_ready:
        print("⚠️ Warning: Server port 8000 did not become ready within timeout.")
        log_file.flush()
        if os.path.exists("server.log"):
            with open("server.log", "r") as f:
                print("\n=== Server Log Output ===")
                print(f.read())
                print("=========================\n")
    else:
        print("✅ Server is up and listening on port 8000!")
    
    results = {"Audit": False, "WebSocket": False}
    
    try:
        # 2. Run Tests
        results["Audit"] = await test_audit_endpoint()
        results["WebSocket"] = await test_websocket_stream()
    finally:
        # 3. Shutdown Server
        print("\n🛑 Shutting down test server...")
        server_process.terminate()
        server_process.wait()
        log_file.close()

    # Final Report
    print("\n" + "="*30)
    print("FINAL TEST REPORT")
    print("="*30)
    for test, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"{test:15}: {status}")
    print("="*30)

if __name__ == "__main__":
    asyncio.run(main())
