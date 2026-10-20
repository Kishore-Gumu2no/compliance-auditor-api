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
# Overridable so the suite can be pointed at a server already running on
# another port (FAIRPAY_TEST_PORT=8001), instead of only the one it starts.
HOST = os.getenv("FAIRPAY_TEST_HOST", "127.0.0.1")
PORT = int(os.getenv("FAIRPAY_TEST_PORT", "8000"))

BASE_URL = f"http://{HOST}:{PORT}"
# Two endpoints, because reading and writing the stream are two roles: a
# dashboard subscribes to /stream, a platform feed sends to /stream/ingest.
SUBSCRIBE_URL = f"ws://{HOST}:{PORT}/stream"
INGEST_URL = f"ws://{HOST}:{PORT}/stream/ingest"


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
    """A tick sent to /stream/ingest must reach a dashboard on /stream.

    This is the end-to-end contract of the live earnings feed, and it is
    checked across the two endpoints rather than on one socket, because a
    producer is deliberately *not* a subscriber: it must never be sent the data
    it just published. A sender that received its own ticks is what filled the
    simulators' receive queues until the keepalive pong was starved and the
    library closed every connection as unresponsive, on a loop.

    "Zomato" is a real wire name from Backend/platforms.py. An unknown name is
    rejected at the boundary on purpose (see `_check_stream_rejects_unknown`),
    so a test cannot invent one.
    """
    print("\n--- Testing WebSocket Stream ---")
    try:
        async with websockets.connect(SUBSCRIBE_URL) as dashboard:
            async with websockets.connect(INGEST_URL) as feed:
                payload = {"platform": "Zomato", "earnings": 999}
                print(f"Sending payload: {payload}")
                await feed.send(json.dumps(payload))

                ack = json.loads(await asyncio.wait_for(feed.recv(), timeout=10.0))
                print(f"Ingest acknowledgement: {ack}")
                if ack.get("status") != "published":
                    print(f"❌ The feed was not acknowledged: {ack}")
                    return False
                if ack.get("delivered_to", 0) < 1:
                    print("❌ The tick was acknowledged but reached no dashboard.")
                    return False

                # The simulators are also publishing, so read until this tick
                # arrives rather than assuming it is the first frame.
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    frame = json.loads(
                        await asyncio.wait_for(dashboard.recv(), timeout=10.0)
                    )
                    print(f"Dashboard received: {frame}")
                    if frame.get("platform") == "Zomato" and frame.get("earnings") == 999:
                        print("✅ The tick reached the dashboard.")
                        return True
                print("❌ The dashboard never received the published tick.")
                return False
    except Exception as e:
        print(f"❌ WebSocket connection failed: {e}")
        return False


async def _check_stream_rejects_unknown_platform():
    """An unrecognised platform name must be refused, not forwarded.

    A name this server does not know would otherwise become a new key on the
    dashboard that nothing renders - the fault that once froze the Uber Eats
    card. The connection must stay open so one bad frame does not kill a feed.
    """
    print("\n--- Testing that an unknown platform is rejected ---")
    try:
        async with websockets.connect(INGEST_URL) as feed:
            await feed.send(json.dumps({"platform": "TestRunner", "earnings": 999}))
            reply = json.loads(await asyncio.wait_for(feed.recv(), timeout=10.0))
            print(f"Reply: {reply}")
            if reply.get("error") != "invalid_payload":
                print(f"❌ An unknown platform was not rejected: {reply}")
                return False

            # Still usable: prove it by publishing a valid tick on the same socket.
            await feed.send(json.dumps({"platform": "Swiggy", "earnings": 1}))
            ack = json.loads(await asyncio.wait_for(feed.recv(), timeout=10.0))
            if ack.get("status") != "published":
                print(f"❌ The connection did not survive a rejected frame: {ack}")
                return False
            print("✅ Rejected the unknown platform and kept the connection open.")
            return True
    except Exception as e:
        print(f"❌ WebSocket connection failed: {e}")
        return False

async def _server_is_up() -> bool:
    return await wait_for_port_async(PORT, host=HOST, timeout=1.0)


async def test_no_document_upload_endpoint():
    """Fails the suite if the website API starts accepting document uploads again."""
    if not await _server_is_up():
        pytest.skip(f"the backend is not running on {HOST}:{PORT}")
    assert await _check_no_document_upload_endpoint(), "the website API accepted a document upload"


async def test_websocket_stream():
    """Fails the suite if a published tick stops reaching the dashboard."""
    if not await _server_is_up():
        pytest.skip(f"the backend is not running on {HOST}:{PORT}")
    assert await _check_websocket_stream(), "a published tick did not reach a subscribed dashboard"


async def test_stream_rejects_unknown_platform():
    """Fails the suite if an unknown platform name is forwarded to dashboards."""
    if not await _server_is_up():
        pytest.skip(f"the backend is not running on {HOST}:{PORT}")
    assert await _check_stream_rejects_unknown_platform(), "an unknown platform was not rejected"


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
    
    results = {"No web upload": False, "WebSocket": False, "Unknown platform": False}

    try:
        # 2. Run Tests
        results["No web upload"] = await _check_no_document_upload_endpoint()
        results["WebSocket"] = await _check_websocket_stream()
        results["Unknown platform"] = await _check_stream_rejects_unknown_platform()
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
