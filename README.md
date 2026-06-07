# ClassFlow Watcher 🎓

Automatically tracks your Google Classroom assignments and exposes them via a REST API so you never miss a deadline.

## How it works

```
Google Classroom → main.py (watcher) → Gemini AI → PostgreSQL → api.py (Flask) → your frontend
```

- **main.py** polls Classroom every hour, detects new assignments, runs them through Gemini for classification + summary, stores in DB
- **api.py** is a REST API your frontend calls to get tasks and mark them done

---

## Setup

### 1. Clone and install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set up environment variables
```bash
cp .env.example .env
# Edit .env and fill in your values
```

### 3. Google OAuth credentials
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable **Google Classroom API**
3. Create OAuth 2.0 credentials (Desktop app type)
4. Download as `credentials.json` and place in this folder

### 4. Get a Gemini API key
1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create an API key
3. Add to `.env` as `GEMINI_API_KEY`

### 5. Set up PostgreSQL
Any PostgreSQL DB works. For local dev:
```bash
createdb classflow
# Then set DATABASE_URL=postgresql://localhost/classflow in .env
```

### 6. Run

**Start the watcher** (syncs Classroom every hour):
```bash
python main.py
```
First run will open a browser for Google login. After that it runs silently.

**Start the API server** (in a separate terminal):
```bash
python api.py
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/tasks` | Get all tasks |
| GET | `/tasks?subject=DSA` | Filter by subject |
| GET | `/tasks?completed=false` | Only pending tasks |
| GET | `/tasks?classification=CIE` | Filter by type |
| POST | `/tasks/<id>/complete` | Mark task done |
| POST | `/tasks/<id>/uncomplete` | Unmark task |
| DELETE | `/tasks/<id>` | Remove a task |
| GET | `/subjects` | List all subjects |
| GET | `/health` | Server health check |

**Authentication:** Pass your `MY_API_KEY` in the `X-API-KEY` header.

---

## Security Notes
- Never commit `credentials.json` or `token.json` to git — add them to `.gitignore`
- Never commit your `.env` file
# Gradewave
