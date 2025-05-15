# Frontdesk AI Supervisor - Human-in-the-Loop System

## Overview  
This project implements a human-in-the-loop system for an AI receptionist that:  
- Handles customer inquiries for a salon business  
- Escalates unknown questions to human supervisors  
- Maintains a knowledge base of learned answers  
- Provides a supervisor dashboard for managing requests  

## System Components  
1. **AI Agent**: Built with LiveKit, handles customer calls and escalates unknown questions  
2. **Supervisor Dashboard**: Flask web interface for managing help requests  
3. **Knowledge Base**: Stores learned Q&A pairs for future reference  
4. **Notification System**: Alerts supervisors of new help requests  

## Prerequisites  
- Python 3.8+  
- LiveKit account ([free tier](https://livekit.io/))  
- OpenAI API key  
- Deepgram API key 
- LiveKit CLI (for generating access tokens)   

## Installation  

### 1. Install LiveKit CLI  
For MAC
```bash
brew update && brew install livekit-cli 
```
For Windows
```bash
winget install LiveKit.LiveKitCLI
```

### 2. Clone the repository

```bash
git clone https://github.com/yourusername/frontdesk_ai_supervisor.git
cd frontdesk_ai_supervisor
```

### 3. Set up Python environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure environment

Create .env file:
```ini
# Flask Configuration
SECRET_KEY=your-secret-key
DEBUG=True
FLASK_APP=app.py
FLASK_DEBUG=1

# LiveKit Configuration
LIVEKIT_URL=your-livekit-url
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret

# AI Configuration
OPENAI_API_KEY=your-openai-key
DEEPGRAM_API_KEY=your-deepgram-key

# Timeout Settings
REQUEST_TIMEOUT_MINUTES=30
```

## Running the System

### 1. Start Supervisor Dashboard

##### A. Delete if theres any existing db file for local server only (For a start fresh)

```bash
rm instance/supervisor.db
```

##### B. Initiate the Database

```bash
flask init-db  
```

##### C. Start the Flask app

```bash
flask run
```

### 2. Start AI Agent (In another terminal)

```bash
 python -m modules.agent dev
```

### 3. Generate Access Token (In another terminal)

```bash
livekit-cli create-token \
  --api-key "YOUR_API_KEY" \
  --api-secret "YOUR_API_SECRET" \
  --join \
  --room "customer-123" \
  --identity "test-customer" \
  --valid-for 24h
```

## System Flow

### Customer Interaction

![Customer_flow](https://github.com/user-attachments/assets/485b52da-ed3f-4357-89b2-06f428539967)

### Supervisor Workflow
  1. **View Pending Requests:** /pending
  2. **Resolve a Request:** Submit answer via form
  3. **View Knowledge Base:** /knowledge
  4. **View Unresolved Requests:** /unresolved


## Key Features

* **Request Lifecycle Management:** Requests transition between pending, resolved, and unresolved states

* **Automatic Timeout Handling:** Pending requests time out after 30 minutes (configurable)

* **Knowledge Base Learning:** Answers are automatically added to the knowledge base

* **Persistent Callbacks:** Maintains request-session mapping across agent restarts

* **Memory-Database Sync:** In-memory storage syncs with SQLite database


## Design Decisions

1. **Dual Storage System:** Uses both in-memory storage and SQLite database for flexibility

2. **Modular Architecture:** Separates agent, help requests, and knowledge base logic

3. **Timeout Handling**: Automatic marking of unresolved requests after timeout period

4. **Semantic Search Matching:** Knowledge base queries use multiple matching strategies

5. **Webhook Support:** Allows for integration with external notification systems


## Next Steps (Phase 2)

Potential improvements:

1. Live call transfer capability

2. Enhanced knowledge base search

3. Supervisor availability status

4. Request prioritization

5. Analytics dashboard




























