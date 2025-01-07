import os
import json
import requests
from web3 import Web3
from pymongo import MongoClient
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router
import aiohttp
from bs4 import BeautifulSoup
from checker import api

from dotenv import set_key, load_dotenv

import asyncio

load_dotenv()
env_path = ".env"

# Load environment variables
ALCHEMY_ETH_URL = os.getenv("ALCHEMY_ETH_URL")
ALCHEMY_BASE_URL = os.getenv("ALCHEMY_BASE_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
TOKEN_SNIFFER_API = os.getenv("TOKEN_SNIFFER_API")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

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

PENDING_TS = {"count": 0}



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
    # Chain-specific coloring for notifications
    chain_display = "🟦 BASE" if data['chain'].upper() == "BASE" else "🟩 ETH"

    msg = f"{chain_display}: ${data['details'].get('symbol', 'N/A')} {data['details'].get('name', 'N/A')}\n\n"

    # Add source code links if verified
    if data["verified"]:
        if data['chain'].upper() == "ETH":
            msg += f"[Source Code](https://etherscan.io/address/{data['address']}#code)\n"
        elif data['chain'].upper() == "BASE":
            msg += f"[Source Code](https://basescan.org/address/{data['address']}#code)\n"

    # TokenSniffer details
    if data["tokensniffer"]:
        score = data["tokensniffer"].get('score', 0)
        similar_tokens = data["tokensniffer"].get('similar', [])
        similar_count = len(similar_tokens)

        msg += f"Score: {score}\n"
        msg += f"Similar Tokens: {similar_count}\n"
        msg += f"Token Address: {data['address']}\n"

        # Liquidity from hacker or honeypot APIs
        liquidity = "N/A"
        if data.get("hacker"):
            liquidity = data["hacker"].get("liquidity", "N/A")
        elif data.get("honeypot"):
            liquidity = data["honeypot"].get("pair", {}).get("liquidity", "N/A")

        msg += f"Total liquidity: {liquidity}\n"

        # TokenSniffer link
        chain_id = 1 if data['chain'].upper() == "ETH" else 8453
        msg += f"[tokensniffer.com](https://tokensniffer.com/token/{chain_id}/{data['address']})\n"

        # Check if the token passes thresholds
        if score >= MINIMUM_SCORE and similar_count <= MAXIMUM_SIMILAR:
            return msg
        else:
            return None

    return None




async def send_notification(message):
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message,parse_mode="Markdown",disable_web_page_preview=True)


async def monitor_blocks(web3_instance, chain):
    latest_block = web3_instance.eth.block_number
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
            

            api_checks = await api(chain, contract_address, TOKEN_SNIFFER_API, ETHERSCAN_API_KEY, BASESCAN_API_KEY, PENDING_TS,  RETRY_INTERVAL_API, RETRY_LIMIT)

            if api_checks is not None:
                api_checks = normalize_data(api_checks)
                contract_data["verified"] = True
                contract_data["source_code"] = api_checks["source_code"]


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






async def start_monitoring(chain):
    if not monitoring[chain]:
        monitoring[chain] = True
        web3_instance = web3_eth if chain == "eth" else web3_base
        asyncio.create_task(monitor_blocks(web3_instance, chain))
        await send_notification(f"Monitoring started for {chain}")
    else:
        monitoring[chain] = False
        await send_notification(f"Monitoring stopped for {chain}")


@router.message(lambda message: message.text == "/start")
async def start(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Start Ethereum Monitoring" if not monitoring["eth"] else "Stop Ethereum Monitoring",
                    callback_data="monitor_eth"
                ),
                InlineKeyboardButton(
                    text="Start Base Monitoring" if not monitoring["base"] else "Stop Base Monitoring",
                    callback_data="monitor_base"
                )
            ],
            [InlineKeyboardButton(text="Check Status", callback_data="status")]
        ]
    )
    await message.reply("Welcome! Use the buttons below to manage monitoring.", reply_markup=keyboard)


@router.callback_query(lambda callback_query: callback_query.data in ["monitor_eth", "monitor_base"])
async def toggle_monitoring(callback_query: types.CallbackQuery):
    chain = "eth" if callback_query.data == "monitor_eth" else "base"
    await start_monitoring(chain)
    action = "started" if monitoring[chain] else "stopped"
    await callback_query.message.edit_text(
        f"Monitoring {action} for {chain.capitalize()}",
        reply_markup=await create_monitoring_keyboard()
    )


@router.callback_query(lambda callback_query: callback_query.data == "status")
async def show_status(callback_query: types.CallbackQuery):
    eth_status = "🟢 Active" if monitoring["eth"] else "🔴 Inactive"
    base_status = "🟢 Active" if monitoring["base"] else "🔴 Inactive"

    # Fetch TokenSniffer usage
    usage_url = "https://tokensniffer.com/api/v2/usage"
    headers = {"accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(usage_url, headers=headers) as response:
                if response.status == 200:
                    usage_data = await response.json()
                    remaining_tokens = usage_data.get("remaining_tokens", "N/A")
                else:
                    remaining_tokens = "N/A"
    except Exception as e:
        print(f"Error fetching TokenSniffer usage: {e}")
        remaining_tokens = "N/A"

    status_message = (
        f"Monitoring Status:\n\n"
        f"Ethereum: {eth_status}\n"
        f"Base: {base_status}\n\n"
        f"Pending Tokens: {PENDING_TS['count']}\n"
        f"TokenSniffer Remaining Tokens: {remaining_tokens}"
    )

    await callback_query.message.edit_text(status_message, reply_markup=await create_monitoring_keyboard())



async def create_monitoring_keyboard():
    """Creates a keyboard dynamically based on monitoring status."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Start Ethereum Monitoring" if not monitoring["eth"] else "Stop Ethereum Monitoring",
                    callback_data="monitor_eth"
                ),
                InlineKeyboardButton(
                    text="Start Base Monitoring" if not monitoring["base"] else "Stop Base Monitoring",
                    callback_data="monitor_base"
                )
            ],
            [InlineKeyboardButton(text="Check Status", callback_data="status")]
        ]
    )


@router.message(lambda message: message.text.startswith("/config"))
async def config_command(message: types.Message):
    command_parts = message.text.split()
    if len(command_parts) == 1:
        # List all configurations
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=key, callback_data=f"config_edit:{key}")]
                for key in [
                    "ALCHEMY_ETH_URL",
                    "ALCHEMY_BASE_URL",
                    "TELEGRAM_TOKEN",
                    "ETHERSCAN_API_KEY",
                    "BASESCAN_API_KEY",
                    "MONGO_URI",
                    "TOKEN_SNIFFER_API",
                    "RETRY_LIMIT",
                    "RETRY_INTERVAL",
                    "RETRY_INTERVAL_API",
                    "MINIMUM_SCORE",
                    "MAXIMUM_SIMILAR",
                    "RETRY_BLOCK_DELAY",
                ]
            ]
        )
        await message.reply("Configuration Settings:", reply_markup=keyboard)
    elif len(command_parts) == 2:
        # Show the current value and ask for a new value
        key = command_parts[1]
        try:
            current_value = globals()[key]
            await message.reply(f"Current value of {key}: {current_value}\nSend /config {key} <new_value> to update.")
        except KeyError:
            await message.reply(f"Configuration {key} not found.")
    elif len(command_parts) == 3:
        # Update the configuration
        key, value = command_parts[1], command_parts[2]
        try:
            if key in globals():
                # Cast to int if the variable is an integer type
                if isinstance(globals()[key], int):
                    value = int(value)
                # Update global variable
                globals()[key] = value
                # Persist to .env
                set_key(env_path, key, str(value))
                await message.reply(f"Configuration {key} updated to {value}.")
            else:
                await message.reply(f"Configuration {key} not found.")
        except ValueError:
            await message.reply(f"Invalid value for {key}. Expected an integer.")
        except KeyError:
            await message.reply(f"Configuration {key} not found.")


@router.callback_query(lambda callback_query: callback_query.data.startswith("config_edit:"))
async def config_edit_callback(callback_query: types.CallbackQuery):
    key = callback_query.data.split(":")[1]
    try:
        current_value = globals()[key]
        await callback_query.message.reply(
            f"Current value of {key}: {current_value}\nSend /config {key} <new_value> to update."
        )
    except KeyError:
        await callback_query.message.reply(f"Configuration {key} not found.")



dp.include_router(router)

if __name__ == "__main__":
    async def main():
        #asyncio.create_task(retry_unverified_contracts())
        await dp.start_polling(bot)

    asyncio.run(main())
