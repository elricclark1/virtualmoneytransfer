# Virtual Money Transfer

A local network virtual money transfer system designed to digitize banking for tabletop games like Monopoly. One device acts as the "Central Bank" and server host, while all other players join via their mobile browsers to manage their own balances.

## 🚀 Usage & Installation

1. **Navigate to the project directory:**
   ```bash
   cd virtualmoneytransfer
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the server:**
   - **Windows:** Double-click `run_windows.bat`.
   - **Linux/Mac:** Run `./run_unix.sh`.
   - *Alternatively:* Run `python main.py` directly.

4. **Join the game:**
   - A Kivy GUI window will appear on the host machine showing a **QR Code** and an **IP Address**.
   - All players must be on the **same Wi-Fi network**.
   - Scan the QR code or enter the URL into your mobile browser to open your player dashboard.

## 🎮 How to Play

1. **Login:** Enter your name to join the session.
2. **Transferring Money:** Select a player from the list, enter the amount, and confirm the transfer. Balances are updated in real-time.
3. **Banking:** The Host (or designated Bankers) can use the Admin Dashboard to:
   - **Mint/Burn:** Create or destroy money.
   - **Banker Mode:** Move money between any two players.
   - **Permissions:** Grant other players Banker status.
   - **Reset:** Clear all balances and transactions for a new game.

## ✨ Features

- **Virtual Economy:** Digital tracking of balances, replacing physical game money.
- **P2P Transfers:** Instant money movement between players via their own devices.
- **Central Bank Controls:** Comprehensive tools for the "Banker" to mint, burn, or redirect funds.
- **Real-time Updates:** Balances and transaction histories update instantly.
- **Cross-Platform Host:** Run the server on Windows, Linux, Mac, or even Android.
- **SQLite Persistence:** All game data is saved locally, allowing you to resume games later.
- **Tailwind UI:** Responsive, modern mobile interface for all players.

## 🛠️ Technical Details

- **Backend:** Flask (Python) with Flask-SQLAlchemy for data management.
- **Database:** SQLite (`currency.db`) for persistent storage of balances and transactions.
- **Host GUI:** Kivy-based interface for server management and IP/QR display.
- **Frontend:** HTML5 and Tailwind CSS, served directly via Flask.
- **Network:** Binds to `0.0.0.0` (typically port 5000 or 8080) for LAN access.
- **Mobile Support:** Specifically designed to be buildable as an Android APK using Buildozer, allowing a phone to act as the server host.
