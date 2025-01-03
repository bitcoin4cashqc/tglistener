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
from checker import api

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
RETRY_LIMIT = int(os.getenv("RETRY_LIMIT"))  # Max retries for unverified contracts
RETRY_INTERVAL = int(os.getenv("RETRY_INTERVAL"))  # Retry source code
RETRY_INTERVAL_API = int(os.getenv("RETRY_INTERVAL_API"))  # Retry appis

MINIMUM_SCORE = int(os.getenv("MINIMUM_SCORE"))  
MAXIMUM_SIMILAR = int(os.getenv("MAXIMUM_SIMILAR")) 

RETRY_BLOCK_DELAY = int(os.getenv("RETRY_BLOCK_DELAY")) 



def normalize_data(data):
    """
    Recursively normalizes data, converting large integers to strings to avoid MongoDB OverflowError.
    """
    if isinstance(data, dict):
        return {key: normalize_data(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [normalize_data(item) for item in data]
    elif isinstance(data, int):
        # Convert integers that exceed MongoDB's 8-byte limit to strings
        if data > 2**63 - 1 or data < -(2**63):
            return str(data)
    return data

def formatToken(data):
    msg = f"{data['chain'].upper()}: ${data['details'].get('symbol', 'N/A')} {data['details'].get('name', 'N/A')}\n\n"

    if data['chain'].upper() == "ETH":
        hackerLink = f"[hackers.tools](https://hackers.tools/honeypot/ethereum/{data['address']})"
        honeypotLink = f"[Honeypot.is](https://honeypot.is/ethereum?address={data['address']})"
    if data['chain'].upper() == "BASE":
            
        hackerLink = f"[hackers.tools](https://hackers.tools/honeypot/base/{data['address']})"
        honeypotLink = f"[Honeypot.is](https://honeypot.is/base?address={data['address']})"

    if data["verified"]:
        if data['chain'].upper() == "ETH":
            msg += f"[Source Code](https://etherscan.io/address/{data['address']}#code)\n"
            hackerLink = f"[hackers.tools](https://hackers.tools/honeypot/ethereum/{data['address']})"
            honeypotLink = f"[Honeypot.is](https://honeypot.is/ethereum?address={data['address']})"
        if data['chain'].upper() == "BASE":
            msg += f"[Source Code](https://basescan.org/address/{data['address']}#code)\n"
            hackerLink = f"[hackers.tools](https://hackers.tools/honeypot/base/{data['address']})"
            honeypotLink = f"[Honeypot.is](https://honeypot.is/base?address={data['address']})"
    
    if data["hacker"]:
        msg += f"Liquidity {hackerLink}: {data["hacker"].get('liquidity', 'N/A')}\n"
        msg += f"Is Safe {hackerLink}: {data['hacker'].get('is_safe', 'N/A')}\n\n"

    if data["honeypot"]:
        msg += f"Liquidity {honeypotLink}: {data["honeypot"].get('pair', {}).get('liquidity', 'N/A')}\n"
        msg += f"Is Honeypot {honeypotLink}: {data['honeypot'].get('honeypot_result', 'N/A')}\n\n"

    if data["tokensniffer"]:
        score = data["tokensniffer"].get('score', '0')
        msg += f"TokenSniffer Score: {score}\n"
        similar_tokens = data["tokensniffer"].get('similar', [])
        similar_count = len(similar_tokens)
        msg += f"TokenSniffer Similar: {similar_count}\n\n"
        
        if score >= MINIMUM_SCORE and similar_count <= MAXIMUM_SIMILAR:
            return msg
        else:
            return None
    
    return msg

async def retry_unverified_contracts():
    while True:
        unverified_contracts = contracts_collection.find({"verified": False, "retries": {"$lt": RETRY_LIMIT}})
        for contract in unverified_contracts:
            chain = contract["chain"]
            contract_address = contract["address"]
            retries = contract.get("retries", 0)
            print(f"Retrying to check {contract_address} on {chain.upper()}, retry : {retries}/{RETRY_LIMIT} ")

            # Attempt to fetch the source code
            source_code = fetch_source_code(contract_address, chain)
            if source_code:
                # Update source code and mark as verified
                contracts_collection.update_one(
                    {"address": contract_address},
                    {"$set": {"verified": True, "source_code": source_code}}
                )

                
                api_checks = await api(chain, contract_address, TOKEN_SNIFFER_API, RETRY_INTERVAL_API, RETRY_LIMIT)

                if api_checks is not None:
                    
                   
                    
                    # Normalize the data to avoid MongoDB integer overflow
                    api_checks = normalize_data(api_checks)
                    
                    # Update contract details in the database
                    contracts_collection.update_one(
                        {"address": contract_address},
                        {
                            "$set": {
                                "hacker": api_checks["hacker"],
                                "honeypot": api_checks["honeypot"],
                                "tokensniffer": api_checks["tokensniffer"],
                            }
                        }
                    )

                    # Format and send the notification
                    existing_contract = contracts_collection.find_one({"address": contract_address})
                    if existing_contract:
                        details_message = formatToken(existing_contract)
                        if details_message is not None:
                            await send_notification(details_message)
            else:
                # Increment the retry counter if the source code is not yet available
                contracts_collection.update_one(
                    {"address": contract_address},
                    {"$inc": {"retries": 1}}
                )

        await asyncio.sleep(RETRY_INTERVAL)



async def send_notification(message):
    await bot.send_message(chat_id=1027097408, text=message,parse_mode="Markdown",disable_web_page_preview=True)


async def monitor_blocks(web3_instance, chain):
    latest_block = web3_instance.eth.block_number - 250
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
            await asyncio.sleep(RETRY_BLOCK_DELAY)
        await asyncio.sleep(1)
    return  # Exit the function immediately

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

            
            if source_code:
                contract_data["verified"] = True
                contract_data["source_code"] = source_code


            api_checks = await api(chain, contract_address, TOKEN_SNIFFER_API, RETRY_INTERVAL_API, RETRY_LIMIT)

            if api_checks is not None:
                api_checks = normalize_data(api_checks)
                contract_data["hacker"] = api_checks["hacker"]
                contract_data["honeypot"] = api_checks["honeypot"]
                contract_data["tokensniffer"] = api_checks["tokensniffer"]
                
               
                details_message = formatToken(contract_data)

                if details_message is not None:
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


async def start_monitoring(chain):
    if not monitoring[chain]:
        monitoring[chain] = True
        web3_instance = web3_eth if chain == "eth" else web3_base
        asyncio.create_task(monitor_blocks(web3_instance, chain))
        await send_notification(f"Monitoring started for {chain}")
    else:
        monitoring[chain] = False
        await send_notification(f"Monitoring stopped for {chain}")


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
        asyncio.create_task(retry_unverified_contracts())
        await dp.start_polling(bot)

    asyncio.run(main())
