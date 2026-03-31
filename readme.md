 ## 🏆Gemini Debate Bot

This repository contains the complete infrastructure for running an automated, real-time AI debate tournament. It includes the centralized WebSocket server (`arena.py`) that manages the match state, and a highly competitive, self-correcting AI debate client (`finalbot.py`).

📁**Components**

* **arena.py(The Server):** A WebSocket server that hosts the debate. It manages the match clock, assigns turns, broadcasts messages to clients, and handles the overall state of the debate (paused, resumed, finished).
* **finalbot.py(The Bot):** A highly competitive AI debater powered by Google's Gemini 2.5 Flash. It features an advanced "Reflexion" pipeline, dynamic API key rotation, live opponent profiling, and conditional web search.

✨**Features**

* **Chain-of-Thought Reflexion Pipeline:** The bot doesn't just generate a response; it drafts an argument, simulates a ruthless "Oracle Judge" to critique its own logic, and rewrites the final polish in a single, highly-optimized API call.
* **Smart Key Manager (Rate Limit Shield):** Capable of handling multiple Google API keys. It tracks individual key cooldowns and gracefully rotates keys if it encounters `429 Too Many Requests` or `Quota` errors.
* **Conditional Web Search:** Integrates Google Search grounding to cite real-world URLs during the "EARLY" and "MID" phases of the match, automatically disabling it in the "LATE" phase to save time.
* **Crunch-Time Bypass:** Dynamically monitors the match clock. If time is running out, it bypasses the critique phase to guarantee a response is delivered before the timeout.
* **Active Opponent Profiling:** Scans the opponent's messages for logical fallacies (e.g., "strawman", "ad hominem") and dodged questions ("what about"), feeding this data back into the prompt to exploit weaknesses.
* **Robust State Management:** Handles WebSocket disconnects, match pauses, and server errors seamlessly without losing the debate history.
  
🛠️**Prerequisites**

* Python 3.8+
* A valid Google Gemini API Key (Multiple keys are highly recommended to prevent rate-limiting during fast-paced rounds).

### Required Libraries
Install the necessary Python packages using pip:
`bash
pip install websockets google-genai python-dotenv`


## 🚀 Setup & Configuration
* **Create a .env file** in the same directory as your scripts.
* **Add your configuration** to the .env file. The bot will not start without this guardrail.

**Code snippet**
```
#Add one or multiple API keys separated by commas (no spaces)
GOOGLE_API_KEYS=AIzaSyYourKeyOne...,AIzaSyYourKeyTwo...

#Set the default WebSocket URI for the Arena
WS_URI=ws://localhost:8765/?team=team1
```


## 🎮 How to Run a Match

To simulate a full debate, you need to start the Arena server first, and then connect your bots to it.

**Step 1: Start the Arena Server**

Open a terminal and run the server script. This will start hosting the WebSocket connection (typically on localhost:8765).

```
Bash
python arena.py
```

**Step 2: Connect the Bots**

Open new, separate terminal windows for each bot you want to connect.

**Bot 1 (Team 1):**
```
Bash
python finalbot.py team1
```

**Bot 2 (Team 2):**
```
Bash
python finalbot.py team2
```

**Note**: Passing team1 or team2 via the command line overrides the default team in the .env file, allowing you to run multiple instances of the same script against each other.
