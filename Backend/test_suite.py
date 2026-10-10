import httpx
import pytest
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


async def _check_no_document_upload_endpoint():
    """Documents are submitted only through the Telegram bot, so the website
    API must not accept PDF or image uploads."""
    print("\n--- Checking that /audit/ no longer accepts documents ---")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            files = [("pdfs", ("probe.pdf", b"%PDF-1.4 probe", "application/pdf"))]
            response = await client.post(f"{BASE_URL}/audit/", files=files)
        except Exception as e:
            print(f"❌ Connection error: {e}")
            return False
    if response.status_code in (404, 405):
        print(f"✅ Upload rejected ({response.status_code}); submission is Telegram-only.")
        return True
    print(f"❌ Unexpected status {response.status_code}: {response.text[:200]}")
    return False

async def _check_websocket_stream():
    print("\n--- Testing WebSocket Stream ---")
    try:
        async with websockets.connect(WS_URL) as ws:
            payload = {"platform": "TestRunner", "earnings": 999}
            print(f"Sending payload: {payload}")
            await ws.send(json.dumps(payload))
            
            # The sender is also a connected client, so the broadcast of the
            # logged earnings arrives first and the confirmation meant for the
            # sender comes after it. Read on until the confirmation shows up.
            for _ in range(5):
                response = await asyncio.wait_for(ws.recv(), timeout=10.0)
                print(f"WebSocket message: {response}")
                if "Logged earnings" in response:
                    print("✅ The stream confirmed the earnings it was sent.")
                    return True
            print("❌ The stream never confirmed the earnings.")
            return False
    except Exception as e:
        print(f"❌ WebSocket connection failed: {e}")
        return False

async def _server_is_up() -> bool:
    return await wait_for_port_async(8000, timeout=1.0)


async def test_no_document_upload_endpoint():
    """Fails the suite if the website API starts accepting document uploads again."""
    if not await _server_is_up():
        pytest.skip("the backend is not running on 127.0.0.1:8000")
    assert await _check_no_document_upload_endpoint(), "the website API accepted a document upload"


async def test_websocket_stream():
    """Fails the suite if the live earnings stream stops answering."""
    if not await _server_is_up():
        pytest.skip("the backend is not running on 127.0.0.1:8000")
    assert await _check_websocket_stream(), "the WebSocket stream did not confirm the earnings it was sent"


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
    
    results = {"No web upload": False, "WebSocket": False}
    
    try:
        # 2. Run Tests
        results["No web upload"] = await _check_no_document_upload_endpoint()
        results["WebSocket"] = await _check_websocket_stream()
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
