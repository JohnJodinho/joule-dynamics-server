from fastapi.testclient import TestClient
from main import app
import json

client = TestClient(app)

print("--- Testing /health ---")
try:
    resp = client.get("/health")
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")
except Exception as e:
    print(f"Failed: {e}")

print("\n--- Testing /api/v1/real-estate/chat/starters ---")
try:
    resp = client.get("/api/v1/real-estate/chat/starters")
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")
except Exception as e:
    print(f"Failed: {e}")

messages = [
    "Hello there!",
    "What does the 7-day average mean?",
    "Can you export the market averages for Miami to a CSV file?"
]

print("\n--- Testing /api/v1/real-estate/chat ---")

# First, test isolated queries
for i, msg in enumerate(messages):
    print(f"\nTest {i+1}: '{msg}'")
    try:
        payload = {"message": msg, "session_id": f"isolated_session_{i}"}
        resp = client.post("/api/v1/real-estate/chat", json=payload)
        print(f"Status: {resp.status_code}")
        print(f"Response: {json.dumps(resp.json(), indent=2)}")
    except Exception as e:
        print(f"Failed: {e}")

# Next, test multi-turn conversation
print("\n--- Testing Multi-Turn Conversation & Memory ---")
multi_turn_msgs = [
    "What's the market average for Miami?",
    "Can you export that data into a CSV file for me?",
    "Are there any other markets you track? I'm not sure which one I want."
]
session_id = "multi_turn_test_session_1"

for i, msg in enumerate(multi_turn_msgs):
    print(f"\nTurn {i+1}: '{msg}'")
    try:
        payload = {"message": msg, "session_id": session_id}
        resp = client.post("/api/v1/real-estate/chat", json=payload)
        print(f"Status: {resp.status_code}")
        print(f"Response: {json.dumps(resp.json(), indent=2)}")
    except Exception as e:
        print(f"Failed: {e}")
