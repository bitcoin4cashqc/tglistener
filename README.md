# Telegram Contract Monitor Bot

A bot to monitor new Ethereum and Base chain smart contracts, analyze their attributes, and provide token safety data using various APIs.

---

## Features

- Monitors Ethereum and Base chains for new contract deployments.
- Analyzes deployed contracts for ERC-20 compliance.
- Retrieves contract source code, token safety checks, and metadata.
- Uses APIs such as Honeypot.is, Hackers.tools, and TokenSniffer for analysis.
- Stores results in a MongoDB database.
- Sends formatted notifications to a Telegram bot.

---

## Requirements

- Python 3.9 or higher
- MongoDB installed and running
- Telegram Bot Token from [BotFather](https://t.me/BotFather)
- API keys for:
  - Etherscan
  - Basescan
  - TokenSniffer
  - (CHECK .ENV)

---

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/bitcoin4cashqc/tglistener.git
cd tglistener
```

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Install and Configure MongoDB

#### Windows
1. Download MongoDB from [MongoDB Official Site](https://www.mongodb.com/try/download/community).
2. Follow the installation steps, and make sure the MongoDB service is running.
3. Default connection URI: `mongodb://localhost:27017`.

#### Linux
1. Update the package database:
   ```bash
   sudo apt update
   ```
2. Install MongoDB:
   ```bash
   sudo apt install -y mongodb
   ```
3. Start MongoDB:
   ```bash
   sudo systemctl start mongodb
   ```
4. Verify installation:
   ```bash
   sudo systemctl status mongodb
   ```
   Default connection URI: `mongodb://localhost:27017`.

---

### 4. Create a `.env` File

Create a `.env` file in the root directory and fill in the following environment variables:

```env
TELEGRAM_TOKEN = ""
ALCHEMY_ETH_URL= "https://eth-mainnet.g.alchemy.com/v2/"
ALCHEMY_BASE_URL = "https://base-mainnet.g.alchemy.com/v2/"
ETHERSCAN_API_KEY = ""
BASESCAN_API_KEY = ""
TOKEN_SNIFFER_API = ""
MONGO_URI = "mongodb://localhost:27017/"
TELEGRAM_CHAT_ID = 1027408

RETRY_BLOCK_DELAY = 5
RETRY_LIMIT = 5
RETRY_INTERVAL = 300 
RETRY_INTERVAL_API = 30

MINIMUM_SCORE = 0
MAXIMUM_SIMILAR='10'

```

---

## Running the Bot

1. Start the bot:
   ```bash
   python main.py
   ```
2. Interact with the bot via Telegram using commands such as `/start` and `/config`

---

## Commands

- `/start`: Displays a welcome message.
- `/status`: Displays the status of chain monitoring.
- `/config`: Configure bot settings dynamically.

---

## Notes

- MongoDB is required to store contract data and analysis results.
- Ensure all APIs are functional and the `.env` file is correctly configured.

---

## Troubleshooting

- If the bot doesn't start, check the `.env` file for missing or incorrect configurations.
- Ensure MongoDB is running and accessible at the URI specified in the `.env` file.
- For API errors, verify the API keys and permissions.

---

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.

---

## License

This project is licensed under the MIT License.

