import os
import json
import requests
from web3 import Web3
from pymongo import MongoClient
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import Router
import aiohttp
from bs4 import BeautifulSoup

from dotenv import load_dotenv
import asyncio

load_dotenv()

# Load environment variables
ALCHEMY_ETH_URL = os.getenv("ALCHEMY_ETH_URL")
ALCHEMY_BASE_URL = os.getenv("ALCHEMY_BASE_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
TOKEN_SNIFFER_API = os.getenv("TOKEN_SNIFFER_API")

# Web3 and MongoDB setup
web3_eth = Web3(Web3.HTTPProvider(ALCHEMY_ETH_URL))
web3_base = Web3(Web3.HTTPProvider(ALCHEMY_BASE_URL))
client = MongoClient(MONGO_URI)
db = client['contract_monitor']
contracts_collection = db['contracts']

# Aiogram setup
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

# Monitoring settings
monitoring = {"eth": False, "base": False}
RETRY_LIMIT = 5  # Max retries for unverified contracts
RETRY_INTERVAL = 60  # Retry every 60 seconds

#Score: {data.get('score', 'N/A')}
#Similar Tokens: {token_sniffer_data.get('similar_tokens', 'N/A')}
#Total liquidity: {honeypot_data.get('liquidity', 'N/A')}

def formatToken(data):
    return f"""
{data['chain'].upper()}: ${data['details'].get('symbol', 'N/A')} {data['details'].get('name', 'N/A')}
Hacker: {data['hacker']}
TokenSniffer: {data['tokensniffer']}
Token Address: {data['address']}
            """

async def retry_unverified_contracts():
    while True:
        unverified_contracts = contracts_collection.find({"verified": False, "retries": {"$lt": RETRY_LIMIT}})
        for contract in unverified_contracts:
            chain = contract["chain"]
            contract_address = contract["address"]
            retries = contract.get("retries", 0)
            source_code = fetch_source_code(contract_address, chain)
            if source_code:
                contracts_collection.update_one(
                    {"address": contract_address},
                    {"$set": {"verified": True, "source_code": source_code}}
                )
                await send_notification(f"Contract {contract_address} on {chain} has been verified.")
            else:
                contracts_collection.update_one(
                    {"address": contract_address},
                    {"$inc": {"retries": 1}}
                )
        await asyncio.sleep(RETRY_INTERVAL)


async def send_notification(message):
    await bot.send_message(chat_id=1027097408, text=message, disable_web_page_preview=True)


async def monitor_blocks(web3_instance, chain):
    latest_block = web3_instance.eth.block_number - 500
    while monitoring[chain]:
        try:
            print(f"Fetching block {latest_block} on {chain}")
            block = web3_instance.eth.get_block(latest_block, full_transactions=True)
            for tx in block.transactions:
                if tx.to is None:  # Contract deployment
                    asyncio.create_task(analyze_contract(tx['from'], tx['hash'], chain))
            latest_block += 1
        except Exception as e:
            print(f"Error fetching block {latest_block} on {chain}: {e}")
        await asyncio.sleep(0.1)


async def analyze_contract(deployer, tx_hash, chain):
    web3_instance = web3_eth if chain == "eth" else web3_base
    receipt = web3_instance.eth.get_transaction_receipt(tx_hash)
    contract_address = receipt.contractAddress
    if contract_address:
        existing_contract = contracts_collection.find_one({"address": contract_address})
        if existing_contract:
            return  # Skip duplicates

        is_erc20, details = check_erc20(contract_address, web3_instance)
        timestamp = web3_instance.eth.get_block(receipt.blockNumber).timestamp
        contract_data = {
            "address": contract_address,
            "deployer": deployer,
            "timestamp": timestamp,
            "verified": False,
            "details": None,
            "hacker": None,
            "tokensniffer": None,
            "retries": 0,
            "chain": chain
        }
        if is_erc20:
            contract_data["details"] = details
            source_code = fetch_source_code(contract_address, chain)
            honeypot_data = await check_honeypot(contract_address)
            print("honeypot_data",honeypot_data)
            contract_data["hacker"] = honeypot_data

            if (honeypot_data.get('is_safe', False) is False):
                #token_sniffer_data = await check_token_sniffer(chain, contract_address)
                #print("token_sniffer_data",token_sniffer_data)
                #contract_data["tokensniffer"] = token_sniffer_data
                pass
            if source_code:
                contract_data["verified"] = True
                contract_data["source_code"] = source_code

            details_message = formatToken(contract_data)
            
            await send_notification(details_message)
            contracts_collection.insert_one(contract_data)


def check_erc20(contract_address, web3_instance):
    try:
        with open('./IERC20.json', 'r') as abi_file:
            erc20_abi = json.load(abi_file)
        contract = web3_instance.eth.contract(address=contract_address, abi=erc20_abi)
        return True, {
            "name": contract.functions.name().call(),
            "symbol": contract.functions.symbol().call(),
            "decimals": contract.functions.decimals().call()
        }
    except Exception as e:
        print("ERC20 exception: ", e)
        return False, None


def fetch_source_code(contract_address, chain):
    api_url = (
        f"https://api.etherscan.io/api?module=contract&action=getsourcecode&address={contract_address}&apikey={ETHERSCAN_API_KEY}"
        if chain == "eth" else
        f"https://api.basescan.org/api?module=contract&action=getsourcecode&address={contract_address}&apikey={BASESCAN_API_KEY}"
    )
    response = requests.get(api_url).json()
    if response["status"] == "1" and response["result"]:
        return response["result"][0]["SourceCode"]
    return None



async def check_honeypot(contract_address):
    try:
        url = f"https://hackers.tools/honeypot/ethereum?tokenAddr={contract_address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

                # Check safety status
                safety_status = soup.find("p", string="Looks safe for now")
                is_safe = safety_status is not None

                # Extract pair info
                pair_info = soup.find("p", string=lambda x: x and "pair on" in x.lower())
                pair = pair_info.text if pair_info else "N/A"


                # Extract liquidity by searching for <p> tags and inspecting their children
                liquidity_info = None
                for p_tag in soup.find_all("p"):
                    if "Liqudity:" in p_tag.text or "Liquidity:" in p_tag.text:
                        liquidity_info = p_tag
                        break

                # Extract liquidity value from the <span> within the found <p> tag
                if liquidity_info:
                    span = liquidity_info.find("span")
                    liquidity = span.text.strip() if span else "N/A"
                else:
                    liquidity = "N/A"


                # Extract "Can buy", "Can sell", "Can transfer" statuses
                actions = {}
                action_divs = soup.find_all("div", style=lambda x: x and "border-inline-start-color:#86efac" in x)
                for action_div in action_divs:
                    action_text = action_div.find("span").text
                    if "Can buy" in action_text:
                        actions["can_buy"] = action_text
                    elif "Can sell" in action_text:
                        actions["can_sell"] = action_text
                    elif "Can transfer" in action_text:
                        actions["can_transfer"] = action_text
                #negative
                action_divs = soup.find_all("div", style=lambda x: x and "border-inline-start-color:#fca5a5" in x)
                for action_div in action_divs:
                    action_text = action_div.find("span").text
                    if "Can buy" in action_text:
                        actions["can_buy"] = action_text
                    elif "Can sell" in action_text:
                        actions["can_sell"] = action_text
                    elif "Can transfer" in action_text:
                        actions["can_transfer"] = action_text

                # Combine all data into a JSON object
                data = {
                    "is_safe": is_safe,
                    "pair": pair,
                    "liquidity": liquidity,
                    **actions
                }

                return data
    except Exception as e:
        print(f"Honeypot checker error: {e}")
        return None



async def check_token_sniffer(chain, contract_address):
    chain_id = 1 if chain == "eth" else 8453
    url = f"https://tokensniffer.com/api/v2/tokens/{chain_id}/{contract_address}?apikey={TOKEN_SNIFFER_API}&include_metrics=true&include_tests=true&include_similar=true"
   
    headers = {"accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"TokenSniffer API error: {response.status}")
    except Exception as e:
        print(f"TokenSniffer request error: {e}")
    return None



async def start_monitoring(chain):
    if not monitoring[chain]:
        monitoring[chain] = True
        web3_instance = web3_eth if chain == "eth" else web3_base
        asyncio.create_task(monitor_blocks(web3_instance, chain))
        await send_notification(f"Monitoring started for {chain}")
    else:
        await send_notification(f"Monitoring is already active for {chain}")


@router.message(lambda message: message.text == "/start_monitor_eth")
async def start_monitor_eth(message: types.Message):
    await start_monitoring("eth")
    #await message.reply("Ethereum monitoring started.")


@router.message(lambda message: message.text == "/start_monitor_base")
async def start_monitor_base(message: types.Message):
    await start_monitoring("base")
    #await message.reply("Base monitoring started.")


@router.message(lambda message: message.text == "/start")
async def start(message: types.Message):
    await message.reply("Welcome! Use /start_monitor_eth or /start_monitor_base to start monitoring Ethereum or Base chain respectively.")


dp.include_router(router)

if __name__ == "__main__":
    async def main():
        #asyncio.create_task(retry_unverified_contracts())
        await dp.start_polling(bot)

    asyncio.run(main())
