import asyncio
import websockets
import json
import os
import sys
import time
import textwrap
import random
from urllib.parse import urlparse, parse_qs
from google import genai
from google.genai import errors
from dotenv import load_dotenv

# ==========================================
# 1. CONFIGURATION & SCORING MATRIX
# ==========================================
load_dotenv()
API_KEYS = [k.strip() for k in (os.getenv("GOOGLE_API_KEYS") or "").split(",") if k.strip()]

MAX_ROUNDS = 25
MAX_TURNS = MAX_ROUNDS * 2 
TOPIC = "Are AI agents primarily tools for human augmentation, or do they risk replacing humans?"
TOPIC_DESCRIPTION = "Are AI agents primarily tools for human augmentation, or do they risk replacing humans?"

# Official Rulebook constraints
MAX_CHAT_MESSAGE_SIZE = 3000  # Updated to official 3,000 chars
MATCH_DURATION_SEC = 10 * 60  # 10 minutes total match time
TURN_TIMEOUT_SEC = 2 * 60     # 2 minutes per turn

CRITERIA = """
1. Persuasiveness (40%): Use of rhetoric, evidence, and narrative.
2. Logic (30%): Internal consistency and avoidance of logical fallacies. Penalize teams that argue against their assigned stance.
3. API Robustness (20%): Correct headers, formatting, and response speed.
4. Agility (10%): How well the agent adapts to the opponent's unique points.
"""

clients = {}
conversation_history = [] # Stores exact thread for 'previous-message' reconnects
debate_transcript = []    # Simple text transcript for Oracle
turn_count = 0
key_index = 0

stop_server_event = None

# Timer states
match_start_time = 0
turn_start_time = 0

match_state = {
    "team1": "TEAM A",
    "team2": "TEAM B",
    "topic": TOPIC,
    "description": TOPIC_DESCRIPTION,
    "round": "Round 1",
    "finishTime": 0,
    "pros": "team1", 
    "cons": "team2", 
    "turn": "team1",
    "status": "waiting",
    "remainingTime": MATCH_DURATION_SEC * 1000
}

# ==========================================
# 2. ORACLE & HELPERS
# ==========================================
def get_iso_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

def get_system_payload(msg_type, data_dict, sender="system"):
    """Standardizes EVERY outgoing message to match the official envelope (Section 6.1)"""
    return json.dumps({
        "type": msg_type,
        "from": sender,
        "timestamp": get_iso_timestamp(),
        "data": data_dict
    })

def get_error_payload(msg):
    return get_system_payload("error", {"message": msg})

def get_oracle_client():
    global key_index
    if not API_KEYS: return None
    key = API_KEYS[-(1 + (key_index % len(API_KEYS)))]
    key_index += 1
    return genai.Client(api_key=key)

async def evaluate_match():
    print(f"\n{'='*20} THE ORACLE IS DELIBERATING {'='*20}")
    full_text = "\n".join(debate_transcript)
    
    team1_stance = "PRO" if match_state["pros"] == "team1" else "CON"
    team2_stance = "PRO" if match_state["pros"] == "team2" else "CON"
    
    prompt = f"""
You are The Oracle (Judging Bot). Judge this debate match strictly based on the rubric.

ASSIGNMENTS:
Team 1 is assigned: {team1_stance}
Team 2 is assigned: {team2_stance}

{CRITERIA}

TRANSCRIPT:
{full_text}

Provide the final result exactly in this format:
Team 1 Score: [0-100]
Team 2 Score: [0-100]
WINNER: [Team Name]
Reason: [2-line justification]
"""
    for attempt in range(max(1, len(API_KEYS))):
        try:
            client = get_oracle_client()
            if not client: return "JUDGMENT FAILED: NO API KEYS"
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            return response.text
        except errors.APIError as e:
            if "429" in str(e):
                print(f"[!] Oracle key rate limited on attempt {attempt+1}, rotating...")
                continue
            return f"Oracle Error: {e}"

    return "JUDGMENT FAILED: API EXHAUSTED"

async def trigger_match_end(reason_message="The match has ended!"):
    global match_state, stop_server_event
    
    if match_state["status"] == "ended": return

    match_state["status"] = "ended"
    await update_and_broadcast_state()
    await broadcast("match-finish", {"message": reason_message})

    verdict = await evaluate_match()
    print(f"\n{'='*20} FINAL VERDICT {'='*20}")
    print(verdict)
    print("="*55)

    await asyncio.sleep(2)
    stop_server_event.set()

# ==========================================
# 3. BACKGROUND REFEREE CLOCK
# ==========================================
async def active_timer_loop():
    global turn_start_time, turn_count, match_state
    
    while not stop_server_event.is_set():
        await asyncio.sleep(1)
        
        if match_state["status"] == "started":
            current_time = time.time()
            
            if current_time - match_start_time >= MATCH_DURATION_SEC:
                print("\n[!] MATCH TIME LIMIT EXCEEDED (10 Mins). Forcing match end.")
                await trigger_match_end("Global match time limit reached.")
                continue

            if current_time - turn_start_time > TURN_TIMEOUT_SEC:
                failing_team = match_state["turn"]
                print(f"\n[!] {failing_team.upper()} SILENT TIMEOUT EXCEEDED (2 Mins).")
                
                if failing_team in clients:
                    await clients[failing_team].send(get_error_payload(f"Turn timeout exceeded. {failing_team} disqualified for this round."))
                
                match_state["turn"] = "team2" if failing_team == "team1" else "team1"
                turn_start_time = time.time()
                turn_count += 1
                match_state["round"] = f"Round {(turn_count // 2) + 1}"
                
                if turn_count >= MAX_TURNS:
                    await trigger_match_end("Turn limits reached after timeout!")
                else:
                    await update_and_broadcast_state()

# ==========================================
# 4. NETWORKING
# ==========================================
async def broadcast(msg_type, data, sender_name=None):
    if not clients: return
    message = get_system_payload(msg_type, data, sender_name or "system")
    await asyncio.gather(*[c.send(message) for c in clients.values()], return_exceptions=True)

async def update_and_broadcast_state():
    elapsed = time.time() - match_start_time
    rem_time_ms = max(0, int((MATCH_DURATION_SEC - elapsed) * 1000))
    match_state["remainingTime"] = rem_time_ms
    await broadcast("match-state", match_state)

async def handle_client(websocket):
    global turn_count, debate_transcript, conversation_history, match_start_time, turn_start_time
    team_name = "UNKNOWN"
    is_authenticated = False # <-- ADDED FIX

    try:
        path = websocket.request.path
        query = parse_qs(urlparse(path).query)
        team_name = query.get("team", [None])[0] or query.get("token", [None])[0]

        if team_name not in ["team1", "team2"]:
            await websocket.close(1008, "Invalid or missing team token in URL")
            return # Silent rejection, ignores finally broadcast

        if team_name in clients:
            try: await clients[team_name].close()
            except: pass

        clients[team_name] = websocket
        is_authenticated = True # <-- ADDED FIX
        print(f"[CONNECTED] {team_name}")

        # 1. Welcome & Presence
        await websocket.send(get_system_payload("welcome", {"message": f"Welcome {team_name} to AgentSlam!"}))
        await broadcast("user-joined", {"message": f"{team_name} joined the match."})

        # 2. Match Start Trigger
        if len(clients) == 2 and match_state["status"] == "waiting":
            match_state["pros"] = "team1" if random.choice([True, False]) else "team2"
            match_state["cons"] = "team2" if match_state["pros"] == "team1" else "team1"
            
            match_state["status"] = "started"
            match_start_time = time.time()
            turn_start_time = time.time()
            
            # Set absolute finish time in ms (Section 6.4)
            finish_time_ms = int((match_start_time + MATCH_DURATION_SEC) * 1000)
            match_state["finishTime"] = finish_time_ms
            
            print(f"\n[MATCH START] Topic: {TOPIC}")
            print(f"[STANCES] PRO: {match_state['pros'].upper()} | CON: {match_state['cons'].upper()}")
            
            await update_and_broadcast_state()
            await broadcast("match-update", {
                "message": f"The match has started! Let the slam begin! It's {match_state['turn']}'s turn.",
                "finishTime": finish_time_ms
            })

        # 3. Reconnection Handling (Section 8)
        elif match_state["status"] == "started":
            await websocket.send(get_system_payload("match-state", match_state))
            if conversation_history:
                await websocket.send(get_system_payload("previous-message", {
                    "message": "Match is already live! Here are the previous conversations.",
                    "conversations": conversation_history
                }))

        # 4. Message Loop
        async for message in websocket:
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send(get_error_payload("Invalid message format."))
                continue

            # Relaxed Envelope Check: Outgoing payload from bots only strictly requires type and data.
            if "type" not in payload or "data" not in payload:
                await websocket.send(get_error_payload("Invalid message format. Send JSON: { 'type': '...', 'data': { ... } }"))
                continue

            msg_type = payload.get("type")

            # Sandbox Handling
            if msg_type == "sandbox-message":
                content = payload.get("data", {}).get("message", "Test message")
                await websocket.send(get_system_payload("sandbox-message", {"message": content}))
                continue

            # Debate Logic
            if msg_type == "debate-message":
                if match_state["status"] != "started":
                    await websocket.send(get_error_payload("Cannot send debate messages when match is not live."))
                    continue

                if team_name != match_state["turn"]:
                    await websocket.send(get_error_payload("It's not your turn! Please wait for your turn."))
                    continue

                content = payload.get("data", {}).get("message", "")

                if len(content) > MAX_CHAT_MESSAGE_SIZE:
                    await websocket.send(get_error_payload(f"Message exceeds maximum allowed size of {MAX_CHAT_MESSAGE_SIZE} bytes. Please shorten your message."))
                    continue

                # Record message into memory
                stance = "PRO" if team_name == match_state["pros"] else "CON"
                debate_transcript.append(f"{team_name} ({stance}): {content}")
                conversation_history.append({
                    "team": team_name,
                    "message": content,
                    "timestamp": get_iso_timestamp()
                })

                # Broadcast
                await websocket.send(get_system_payload("info", {"message": "acknowledged"}))
                await broadcast("debate-message", {"message": content}, sender_name=team_name)

                print(f"\n[{team_name.upper()} | {stance}] (Time taken: {int(time.time() - turn_start_time)}s)")
                print(textwrap.fill(content, width=80, initial_indent="  | ", subsequent_indent="  | "))

                # Advance Round & Turn
                turn_count += 1
                match_state["round"] = f"Round {(turn_count // 2) + 1}"

                if turn_count >= MAX_TURNS:
                    await trigger_match_end("The match has ended!")
                    return

                match_state["turn"] = "team2" if match_state["turn"] == "team1" else "team1"
                turn_start_time = time.time()
                await update_and_broadcast_state()

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        # <-- ADDED FIX: Only announce disconnects for verified teams.
        if is_authenticated:
            # Ensure we only delete the socket if it hasn't been replaced by a rapid reconnect
            if clients.get(team_name) == websocket:
                del clients[team_name]
            print(f"[DISCONNECTED] {team_name}")
            await broadcast("user-left", {"message": f"{team_name} has left the match."})

# ==========================================
# 5. MAIN
# ==========================================
async def main():
    global stop_server_event
    stop_server_event = asyncio.Event()

    print(f"--- AGENT SLAM ARENA: PRODUCTION MIRROR ---")
    print(f"ws://localhost:8765/?team=team1 or ?team=team2")
    
    asyncio.create_task(active_timer_loop())
    async with websockets.serve(handle_client, "localhost", 8765):
        await stop_server_event.wait()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: sys.exit(0)
