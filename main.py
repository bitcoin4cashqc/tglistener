import os
import json
import requests
from web3 import Web3
from pymongo import MongoClient
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import Router
from dotenv import load_dotenv
import asyncio
import time

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
RETRY_LIMIT = 10
RETRY_INTERVAL = 60  # Retry every 60 seconds


async def retry_unverified_contracts():
    while True:
        unverified_contracts = contracts_collection.find({"verified": False})
        for contract in unverified_contracts:
            chain = contract["chain"]
            contract_address = contract["address"]
            source_code = fetch_source_code(contract_address, chain)
            if source_code:
                contracts_collection.update_one(
                    {"address": contract_address},
                    {"$set": {"verified": True, "source_code": source_code}}
                )
                await send_notification(f"Contract {contract_address} on {chain} has been verified.")
        await asyncio.sleep(RETRY_INTERVAL)


async def send_notification(message):
    await bot.send_message(chat_id=1027097408, text=message)


async def monitor_blocks(web3_instance, chain):
    latest_block = web3_instance.eth.block_number -500
    while monitoring[chain]:
        print(f"Monitoring block {latest_block} on {chain}")
        try:
            block = web3_instance.eth.get_block(latest_block, full_transactions=True)
            for tx in block.transactions:
                if tx.to is None:  # Contract deployment
                    #print(f"Analyzing  {tx['hash']} on {chain}")
                    asyncio.create_task(analyze_contract(tx['from'], tx['hash'], chain))
            latest_block += 1
        except Exception as e:
            print(f"Error fetching block {latest_block} on {chain}: {e}")
        
        #time.sleep(1)
        await asyncio.sleep(1)


async def analyze_contract(deployer, tx_hash, chain):
    web3_instance = web3_eth if chain == "eth" else web3_base
    receipt = web3_instance.eth.get_transaction_receipt(tx_hash)
    contract_address = receipt.contractAddress
    if contract_address:
        #print("contract_address",contract_address)
        is_erc20, details = check_erc20(contract_address, web3_instance)
        #print("is_erc20",is_erc20)
        #print("details",details)
        timestamp = web3_instance.eth.get_block(receipt.blockNumber).timestamp
        contract_data = {
            "address": contract_address,
            "deployer": deployer,
            "timestamp": timestamp,
            "verified": False,
            "details": None,
            "tokensniffer": None,
            "chain": chain
        }
        if is_erc20:
            contract_data["details"] = details
            source_code = fetch_source_code(contract_address, chain)
            contract_link = f"https://etherscan.io/token/{contract_address}#code" if chain == "eth" else f"https://basescan.org/token{contract_address}/#code"
            if source_code:
                contract_data["verified"] = True
                contract_data["source_code"] = source_code
                
                await send_notification(f"New VERIFIED ERC20 token detected on {chain}: {contract_address} {details} {contract_link} ")
            else:
                await send_notification(f"New UNVERIFIED ERC20 token detected on {chain}: {contract_address} {details} {contract_link}")
            contracts_collection.insert_one(contract_data)

def check_erc20(contract_address, web3_instance):
    try:
        # Read the ABI file
        with open('./IERC20.json', 'r') as abi_file:
            erc20_abi = json.load(abi_file)
        contract = web3_instance.eth.contract(address=contract_address, abi=erc20_abi)
        return True, {
            "name": contract.functions.name().call(),
            "symbol": contract.functions.symbol().call(),
            "decimals": contract.functions.decimals().call()
        }
    except Exception as e:
        print("erc20 exception: ",e)
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
        await send_notification(f"Monitoring is already active for {chain}")


@router.message(lambda message: message.text == "/start_monitor_eth")
async def start_monitor_eth(message: types.Message):
    await start_monitoring("eth")
    await message.reply("Ethereum monitoring started.")


@router.message(lambda message: message.text == "/start_monitor_base")
async def start_monitor_base(message: types.Message):
    await start_monitoring("base")
    await message.reply("Base monitoring started.")


@router.message(lambda message: message.text == "/start")
async def start(message: types.Message):
    await message.reply("Welcome! Use /start_monitor_eth or /start_monitor_base to start monitoring Ethereum or Base chain respectively.")


dp.include_router(router)

# Start bot
if __name__ == "__main__":
    async def main():
        #asyncio.create_task(retry_unverified_contracts())
        await dp.start_polling(bot)
        

    asyncio.run(main())
