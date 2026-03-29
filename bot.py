import asyncio
import websockets
import json
import os
import sys
import time
import random
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from google import genai
from google.genai import types, errors

# load environment variables
load_dotenv()

# get default websocket uri
URI = os.getenv("WS_URI", "ws://localhost:8765/?team=team2")

# allow cli arg override for local testing (e.g., `python bot.py team2`)
if len(sys.argv) > 1:
    override_team = sys.argv[1]
    URI = f"ws://localhost:8765/?team={override_team}"

# parse team id from the url params
parsed_url = urlparse(URI)
query_params = parse_qs(parsed_url.query)
MY_TEAM_ID = query_params.get("team", [None])[0] or query_params.get("token", ["UNKNOWN"])[0]

MAX_CHAT_MESSAGE_SIZE = 2900  
ALL_KEYS = [k.strip() for k in os.getenv("GOOGLE_API_KEYS", "").split(",") if k.strip()]

# global state tracking
opponent_profile = {
    "logical_fallacies": 0,
    "repetition": 0,
    "dodged_questions": 0
}

arena_state = {
    "is_paused": False,
    "current_turn": None,
    "topic": "",
    "my_stance": "PRO",
    "has_replied_this_turn": False,
    "awaiting_acknowledgment": False,
    "remaining_ms": 600000
}

my_opening_statement = ""
debate_history = []

FALLBACKS = [
    "The opponent has failed to provide empirical evidence for their claims. We must look at the facts instead.",
    "That assertion relies on a fundamental misunderstanding of the topic. The logical conclusion supports our stance.",
    "We reject that premise entirely. The data clearly points in the opposite direction."
]

# simple key manager to handle api rate limits and rotation
class KeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.cooldowns = {}
        
    def get_client(self):
        if not self.keys: return None, None
        now = time.time()
        available_keys = [k for k in self.keys if self.cooldowns.get(k, 0) < now]
        if not available_keys: return None, None
        chosen_key = random.choice(available_keys)
        return genai.Client(api_key=chosen_key), chosen_key
        
    def set_cooldown(self, key, seconds=65):
        self.cooldowns[key] = time.time() + seconds

manager = KeyManager(ALL_KEYS)

# formatting and parsing helpers
def clean_argument(text):
    text = text.replace('"', "'")
    if text.upper().startswith(f"{MY_TEAM_ID.upper()}:"):
        text = text[len(MY_TEAM_ID)+1:].strip()
        
    lines = []
    for line in text.split('\n'):
        if line.strip():
            lines.append(line.strip())
            
    return "\n\n".join(lines)

def determine_phase(rem_ms):
    mins = rem_ms / 60000
    if mins > 8: return "EARLY"
    if mins > 5: return "MID"
    if mins > 2: return "LATE"
    return "FINAL"

def analyze_opponent_message(text):
    t = text.lower()
    if any(word in t for word in ["strawman", "ad hominem", "fallacy", "bad faith"]):
        opponent_profile["logical_fallacies"] += 1
    if "what about" in t:
        opponent_profile["dodged_questions"] += 1

# core debate logic
async def execute_turn(websocket):
    global my_opening_statement
    
    arena_state["awaiting_acknowledgment"] = True
    turn_start_timer = time.time()
    
    await asyncio.sleep(4)
    
    if arena_state["is_paused"] or arena_state["current_turn"] != MY_TEAM_ID:
        print("[!] Turn interrupted or paused. Aborting generation.")
        arena_state["awaiting_acknowledgment"] = False
        arena_state["has_replied_this_turn"] = False
        return

    phase = determine_phase(arena_state["remaining_ms"])
    opp_summary = f"Opponent fallacies: {opponent_profile['logical_fallacies']}, Dodged questions: {opponent_profile['dodged_questions']}"
    
    opponent_last_point = "None"
    for msg in reversed(debate_history):
        if not msg.startswith(MY_TEAM_ID.upper()):
            opponent_last_point = msg
            break

    system_instruction = f"""You are an elite, professional debater. Topic: {arena_state['topic']}.
Your Stance: {arena_state['my_stance']} (CRITICAL: DO NOT argue the opponent's side!). Phase: {phase}.

RULES:
1. AGILITY: You MUST explicitly acknowledge and dismantle the opponent's last point before advancing your own argument.
2. LOGIC: Avoid logical fallacies. If the opponent uses one, explicitly name it. Ensure internal consistency.
3. CITATIONS & FACT-CHECKING: Expose the opponent's lies using real data. Every fact MUST have a URL appended like this: (Source: https://...)
4. LENGTH LIMIT: Keep it concise and devastating, strictly under 300 words.
5. FORMAT: Plain text only. No markdown, no bolding. Use single quotes. Break your argument into 2 or 3 short, devastating paragraphs.
6. PRE-BUNKING: Conclude your argument by predicting the opponent's most likely counter-attack and preemptively explain why it is logically flawed."""

    draft_prompt = f"""
Opponent Weaknesses: {opp_summary}

OPPONENT'S LAST POINT:
{opponent_last_point}

Recent History:
{chr(10).join(debate_history[-6:])}

Write a first draft of our next argument. Use Google Search to find citations.
"""

    dynamic_temp = 0.7 if phase in ["EARLY", "FINAL"] else 0.2
    
    # init variables for the timeout failsafe
    draft_text = ""
    final_argument = ""
    
    # main generation loop (85s timeout)
    while time.time() - turn_start_timer < 85:
        client, current_key = manager.get_client()
        if not client:
            await asyncio.sleep(2)
            continue
            
        try:
            # step 1: draft generation
            # only draft if we don't already have one saved from a previous loop
            if not draft_text:
                active_tools = [types.Tool(google_search=types.GoogleSearch())] if phase in ["EARLY", "MID"] else None
                
                print(f"\n[*] Phase 1: Generating Draft (Search: {'ON' if active_tools else 'OFF'})...")
                draft_response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=draft_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=dynamic_temp,
                            tools=active_tools
                        )
                    ), timeout=20
                )
                draft_text = draft_response.text.strip()

            # skip the polish phase if we are low on time
            if phase in ["LATE", "FINAL"]:
                print("[*] Crunch Time! Skipping Reflexion to ensure delivery.")
                final_argument = draft_text
                break

            # step 2: critique and polish the draft
            print("[*] Phase 2: Chain-of-Thought Reflexion (Critique & Polish)...")
            polish_prompt = f"""
            Act as both a ruthless debate judge and the final writer.
            Here is our initial draft: {draft_text}
            
            First, write a strict 2-sentence critique analyzing its weaknesses against the opponent's last point ({opponent_last_point}).
            Second, rewrite the draft to be absolutely perfect, incorporating your critique.
            
            CRITICAL FORMAT REQUIREMENT:
            You must separate your final rewritten argument using the exact phrase 'FINAL_ARGUMENT:'.
            Example:
            [Your Critique Here]
            FINAL_ARGUMENT:
            [Your Perfect Rewritten Argument Here]
            """
            
            final_response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=polish_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.3
                    )
                ), timeout=18
            )
            
            full_text = final_response.text.strip()
            
            # extract final result
            if "FINAL_ARGUMENT:" in full_text:
                final_argument = full_text.split("FINAL_ARGUMENT:")[-1].strip()
            else:
                final_argument = full_text # use full text as fallback
                
            print("[*] Reflexion Pipeline Complete!")
            break
            
        except errors.APIError as e:
            error_info = f"{str(e)}"
            if any(err in error_info for err in ["429", "RESOURCE_EXHAUSTED", "Quota", "quota"]):
                print("[!] Rate limit hit during pipeline. Rotating key...")
                manager.set_cooldown(current_key, seconds=65)
                await asyncio.sleep(0.5)
            else:
                print(f"[!] API Error: {error_info}")
            continue
            
        except asyncio.TimeoutError:
            print("[!] Step timed out. Rotating key and restarting pipeline...")
            manager.set_cooldown(current_key, seconds=10)
            continue
            
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                manager.set_cooldown(current_key, seconds=65)
            continue
            
    # fallback handling
    if not final_argument:
        if draft_text:
            print("[!] Pipeline ran out of time during Reflexion. Falling back to Layer 1 Draft.")
            final_argument = draft_text
        else:
            print("[!] Pipeline failed to generate even a draft. Resorting to hardcoded fallback.")
            final_argument = random.choice(FALLBACKS)
        
    final_argument = clean_argument(final_argument)
    
    if not my_opening_statement and phase == "EARLY":
        my_opening_statement = final_argument
        
    if len(final_argument) > MAX_CHAT_MESSAGE_SIZE:
        final_argument = final_argument[:MAX_CHAT_MESSAGE_SIZE - 3].rsplit('.', 1)[0] + '...'
        
    try:
        payload = {
            "type": "debate-message",
            "data": {
                "message": final_argument
            }
        }
        
        formatted_payload = json.dumps(payload, indent=2)
        print(f"\n[🚀 SENT PAYLOAD]:\n{formatted_payload}\n")
        
        await websocket.send(json.dumps(payload))
    except Exception as e:
        print(f"[!] Failed to send argument: {e}")
        arena_state["has_replied_this_turn"] = False
    finally:
        arena_state["awaiting_acknowledgment"] = False

# main websocket event loop
async def run_tournament_bot():
    global debate_history
    print(f"[*] Starting Test Bot as {MY_TEAM_ID.upper()}...")
    print(f"[*] Target URI: {URI}")
    
    while True:
        try:
            async with websockets.connect(URI) as websocket:
                print("[*] Connected to Arena!")
                
                async for message in websocket:
                    data = json.loads(message)
                    msg_type = data.get("type")
                    payload = data.get("data", {})
                    
                    if msg_type == "error":
                        print(f"[SERVER ERROR] {payload.get('message')}")
                        
                    elif msg_type == "info" and payload.get("message") == "acknowledged":
                        pass
                        
                    elif msg_type == "match-finish":
                        print("[*] Match finished! The Oracle is calculating scores...")
                        return
                        
                    elif msg_type == "match-paused":
                        arena_state["is_paused"] = True
                        print("[!] Match Paused by Admin.")
                        
                    elif msg_type == "match-resumed":
                        arena_state["is_paused"] = False
                        print("[!] Match Resumed!")
                        
                    elif msg_type == "previous-message":
                        debate_history.clear()
                        for conv in payload.get("conversations", []):
                            sender = str(conv.get('team', 'SERVER')).upper()
                            debate_history.append(f"{sender}: {conv.get('message', '')}")
                        
                        if debate_history and debate_history[-1].startswith(MY_TEAM_ID.upper()):
                            arena_state["has_replied_this_turn"] = True
                            
                    elif msg_type == "debate-message":
                        sender = str(payload.get("from", "SERVER")).upper()
                        msg_text = payload.get("message", "")
                        debate_history.append(f"{sender}: {msg_text}")
                        
                        if sender != MY_TEAM_ID.upper():
                            analyze_opponent_message(msg_text)
                            arena_state["has_replied_this_turn"] = False
                            
                    elif msg_type == "match-state":
                        arena_state["topic"] = payload.get("topic", "Unknown")
                        arena_state["remaining_ms"] = payload.get("remainingTime", 600000)
                        arena_state["my_stance"] = "PRO" if payload.get("pros") == MY_TEAM_ID else "CON"
                        arena_state["current_turn"] = payload.get("turn")
                        
                        status = payload.get("status")
                        if status != "started":
                            arena_state["is_paused"] = True
                        else:
                            arena_state["is_paused"] = False

                        if arena_state["current_turn"] != MY_TEAM_ID:
                            arena_state["has_replied_this_turn"] = False
                            arena_state["awaiting_acknowledgment"] = False
                        
                        if (status == "started" and arena_state["current_turn"] == MY_TEAM_ID
                            and not arena_state["is_paused"] and not arena_state["has_replied_this_turn"]
                            and not arena_state["awaiting_acknowledgment"]):
                            
                            arena_state["has_replied_this_turn"] = True
                            asyncio.create_task(execute_turn(websocket))
                            
        except Exception as e:
            print(f"[!] Connection lost or socket closed: {e}. Reconnecting in 2 seconds...")
            await asyncio.sleep(2)

if __name__ == "__main__":
    # check for valid api keys before starting
    if not ALL_KEYS:
        print("\n[FATAL ERROR] No GOOGLE_API_KEYS found in your .env file!")
        print("Please ensure your environment variables are set correctly.")
        exit(1)
        
    try:
        asyncio.run(run_tournament_bot())
    except KeyboardInterrupt:
        print("\n[*] Bot manually shut down. Good luck!")