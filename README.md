# 🚀 Quick Start & Local Execution

**Live Demo:** [https://moneytransfer.serpilas.com](https://moneytransfer.serpilas.com)

To run this application locally immediately:

```bash
# 1. Clone or navigate to the directory
cd virtualmoneytransfer

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start development server
python main.py
```

# Virtual Money Transfer

A local network virtual money transfer system designed to digitize banking for tabletop games like Monopoly. One device acts as the "Central Bank" and server host, while all other players join via their mobile browsers to manage their own balances.

## 🎮 How to Play

1. **Login:** Enter your name to join the session.
2. **Transferring Money:** Select a player from the list, enter the amount, and confirm the transfer.
3. **Banking:** The Host (or designated Bankers) can use the Admin Dashboard to:
   - **Mint/Burn:** Create or destroy money.
   - **Banker Mode:** Move money between any two players.
   - **Permissions:** Grant other players Banker status.
   - **Reset:** Clear all balances and transactions for a new game.

## ✨ Tech Stack

- **Backend:** Flask (Python) with Flask-SQLAlchemy for data management.
- **Database:** SQLite (`currency.db`) for persistent storage.
- **Host GUI:** Kivy-based interface for local server management (if run on desktop).
- **Frontend:** HTML5 and Tailwind CSS, served directly via Flask.
- **Network:** Binds to `0.0.0.0` (typically port 5000 or 8080) for LAN access.

## ⚡ Application Features

- **Virtual Economy:** Digital tracking of balances, replacing physical game money.
- **P2P Transfers:** Instant money movement between players via their own devices.
- **Central Bank Controls:** Tools for the "Banker" to mint, burn, or redirect funds.
- **Real-time Updates:** Balances and transaction histories update instantly.
- **SQLite Persistence:** All game data is saved locally, allowing you to resume games later.

## 🐳 Deployment & Containerization

This application is fully containerized. To build and run with Docker Compose:

```bash
# Build and run containers
docker compose up -d --build
```
The application will be exposed on host port `3014`.

## 🛡️ Serpilas Brand Mission

This application adheres to the core Serpilas mission pillars:
1. **Ad-Free Experience:** UI is entirely clean, clutter-free, and optimized purely for utility.
2. **Open Source:** Public, collaborative repository.
3. **Account-Free:** No signup or login required to play.
4. **Zero Data Monetization:** Local network privacy with zero tracking cookies or profiling.

---
*Created/vibe coded by Elric at Serpilas. Learn more about our mission at serpilas.com/.*

For questions or feedback, contact: elric.projects@gmail.com
