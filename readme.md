# Gemini Debate Tournament Bot

An asynchronous, websocket-driven debate bot powered by the Google Gemini API. This bot connects to a live debate arena, tracks match states, dynamically researches topics using Google Search, and generates structured counter-arguments in real-time.

## Features

* **Real-Time Websocket Integration:** Automatically handles match states, pauses, turn tracking, and message history parsing.
* **Smart Stance Adherence:** Automatically detects its assigned stance (PRO/CON) from the server payload and strictly argues for its side.
* **Live Fact-Checking:** Utilizes the Gemini `GoogleSearch` tool to cite real-world statistics and pre-bunk opponent claims.
* **Bulletproof API Key Manager:** Implements a strict round-robin key rotation system. If one key hits a rate limit (429) or quota error, the bot seamlessly rotates to the next available key without dropping the turn.
* **Failsafe Mechanisms:** Includes a 100-second timeout shield and hardcoded fallback arguments to ensure a response is always sent to the server.
* **Black Box Logging:** Automatically saves all match transcripts to a local text file for post-match analysis.

## Prerequisites

* Python 3.9+
* A valid Google Gemini API Key (Multiple keys are highly recommended to prevent rate-limiting during fast-paced rounds).

## Installation

1. Clone the repository:
   ```bash
   git clone <https://github.com/sy17012002/Agent_slam>
  