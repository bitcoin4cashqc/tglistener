import aiohttp
from bs4 import BeautifulSoup 
import asyncio


async def api(chain, contract_address, TOKEN_SNIFFER_API, retry_interval=10, max_retries=5):
    retries = 0

    while retries < max_retries:
        # Perform Hacker API check
        hacker_data = await check_hacker(chain, contract_address)
        print(hacker_data)
        # If Hacker API indicates honeypot or data is unavailable
        if hacker_data and not hacker_data.get("is_safe", True) and hacker_data.get("liquidity", "N/A") != "N/A":
            print(f"Hacker API detected honeypot for {contract_address}. No further checks.")
            
            
            return None  # Exit early if honeypot detected

        # Perform Honeypot.is API check
        honey_data = await check_honeypot_is(chain, contract_address)
        print(honey_data)
        # If Honeypot.is API indicates honeypot
        if honey_data and honey_data.get("honeypot_result", True):  # True means it's a honeypot
            print(f"Honeypot.is API detected honeypot for {contract_address}. No further checks.")
            
            return None  # Exit early if honeypot detected

        # If both APIs return data and no honeypot is detected, break the retry loop
        if hacker_data is not None and honey_data is not None:
            print(f"Free APIs returned data for {contract_address}. Proceeding to TokenSniffer.")
            break

        # Retry if data is not available yet
        retries += 1
        print(f"Retrying free APIs for {contract_address}. Attempt {retries}/{max_retries}.")
        await asyncio.sleep(retry_interval)

    # If retries are exhausted and still no data, log and exit
    if retries == max_retries:
        print(f"Max retries reached for {contract_address}. No valid data from free APIs.")
        return None

    # If no honeypot is detected, proceed with TokenSniffer API
    token_sniffer_data = await check_token_sniffer(chain, contract_address, TOKEN_SNIFFER_API)
    print(token_sniffer_data)
    # Combine the results from all APIs
    result = {
        "hacker": hacker_data,
        "honeypot": honey_data,
        "tokensniffer": token_sniffer_data,
    }

    return result


async def check_hacker(chain, contract_address):
    chain_id = "ethereum" if chain == "eth" else "base"
    try:
        url = f"https://hackers.tools/honeypot/{chain_id}/{contract_address}"
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
        print(f"Hacker checker error: {e}")
        return None
    

async def check_honeypot_is(chain, contract_address):
    chain_id = 1 if chain == "eth" else 8453
    url = f"https://api.honeypot.is/v2/IsHoneypot?address={contract_address}&chainID={chain_id}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()

                    # Extract relevant details
                    token_info = data.get("token", {})
                    with_token_info = data.get("withToken", {})
                    summary = data.get("summary", {})
                    simulation = data.get("simulationResult", {})
                    honeypot_result = data.get("honeypotResult", {}).get("isHoneypot", None)
                    contract_code = data.get("contractCode", {})
                    pair = data.get("pair", {})

                    # Build output JSON
                    result = {
                        "token": {
                            "name": token_info.get("name", "N/A"),
                            "symbol": token_info.get("symbol", "N/A"),
                            "decimals": token_info.get("decimals", "N/A"),
                            "address": token_info.get("address", "N/A"),
                            "totalHolders": token_info.get("totalHolders", "N/A"),
                        },
                        "with_token": {
                            "name": with_token_info.get("name", "N/A"),
                            "symbol": with_token_info.get("symbol", "N/A"),
                            "decimals": with_token_info.get("decimals", "N/A"),
                            "address": with_token_info.get("address", "N/A"),
                            "totalHolders": with_token_info.get("totalHolders", "N/A"),
                        },
                        "summary": {
                            "risk": summary.get("risk", "N/A"),
                            "risk_level": summary.get("riskLevel", "N/A"),
                        },
                        "simulation": {
                            "buy_tax": simulation.get("buyTax", "N/A"),
                            "sell_tax": simulation.get("sellTax", "N/A"),
                            "transfer_tax": simulation.get("transferTax", "N/A"),
                            "buy_gas": simulation.get("buyGas", "N/A"),
                            "sell_gas": simulation.get("sellGas", "N/A"),
                        },
                        "honeypot_result": honeypot_result,
                        "contract_code": {
                            "open_source": contract_code.get("openSource", False),
                            "root_open_source": contract_code.get("rootOpenSource", False),
                            "is_proxy": contract_code.get("isProxy", False),
                            "has_proxy_calls": contract_code.get("hasProxyCalls", False),
                        },
                        "pair": {
                            "name": pair.get("pair", {}).get("name", "N/A"),
                            "address": pair.get("pair", {}).get("address", "N/A"),
                            "type": pair.get("pair", {}).get("type", "N/A"),
                            "reserves0": pair.get("reserves0", "N/A"),
                            "reserves1": pair.get("reserves1", "N/A"),
                            "liquidity": pair.get("liquidity", "N/A"),
                        },
                    }

                    return result

                else:
                    print(f"Error: Received status code {response.status}")
                    return None

    except Exception as e:
        print(f"Honeypot.is checker error: {e}")
        return None

async def check_token_sniffer(chain, contract_address,TOKEN_SNIFFER_API):
    chain_id = 1 if chain == "eth" else 8453
    url = f"https://tokensniffer.com/api/v2/tokens/{chain_id}/{contract_address}?apikey={TOKEN_SNIFFER_API}&include_metrics=true&include_tests=true&include_similar=true&block_until_ready=true"
   
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