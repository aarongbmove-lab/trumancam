import asyncio
import re
from telethon import TelegramClient, events
from telethon import errors
from aiohttp import web, BasicAuth
import aiohttp_cors

# Replace these with your credentials from my.telegram.org
api_id = 33285927
api_hash = '0da84e101b84a536892bc5f4d918e1f8'
bot1_username = '@ProSearchM5Bot' # The first bot you want to control
bot2_username = '@TVSeriesSearchBot' # The second bot you want to control

# API Credentials
API_USERNAME = 'cinestream'
API_PASSWORD = 'privateapi'

client = TelegramClient(
    'my_session', 
    api_id, 
    api_hash,
    device_model='Desktop PC',
    system_version='Windows 11',
    app_version='1.10.0'
)

def parse_size_to_mb(text):
    # Look for a number (with optional decimals) followed by GB, MB, or KB
    match = re.search(r'(\d+(?:\.\d+)?)\s*(GB|MB|KB)', text, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        unit = match.group(2).upper()
        if unit == 'GB':
            return val * 1024
        elif unit == 'KB':
            return val / 1024
        return val # Treat as MB
    return float('inf') # If no size is found, treat as infinite so it isn't prioritized

async def query_bot(bot_username, text, max_pages=3):
    # client.conversation makes it easy to send a message and wait for the direct reply
    print(f"[{bot_username}] Sending query: '{text}'...")
    messages = []
    try:
        async with client.conversation(bot_username) as conv:
            await conv.send_message(text)
            response = await conv.get_response(timeout=10)
            print(f"[{bot_username}] Received response (Page 1).")
            if response:
                messages.append(response)
                
            for page in range(2, max_pages + 1):
                next_button = None
                next_row, next_col = -1, -1
                
                if response and response.buttons:
                    for r_idx, row in enumerate(response.buttons):
                        for c_idx, button in enumerate(row):
                            btn_text = button.text.lower()
                            # Check for pagination keywords, ignoring buttons that look like file results (contain sizes)
                            if any(x in btn_text for x in ['next', 'load more', '➡️', '>>']):
                                if not re.search(r'\d+(?:\.\d+)?\s*(gb|mb|kb)', btn_text, re.IGNORECASE):
                                    next_button = button
                                    next_row = r_idx
                                    next_col = c_idx
                                    break
                        if next_button: break
                
                if next_button:
                    print(f"[{bot_username}] Clicking pagination button to get Page {page}...")
                    await response.click(next_row, next_col)
                    
                    # Give the bot a moment to process the click and update the message
                    await asyncio.sleep(2)
                    
                    # Check if the bot sent a new message
                    latest_messages = await client.get_messages(bot_username, limit=1)
                    if latest_messages and latest_messages[0].id > response.id:
                        response = latest_messages[0]
                    else:
                        # Otherwise, fetch the updated state of the current message
                        response = await client.get_messages(bot_username, ids=response.id)
                        
                    print(f"[{bot_username}] Received response (Page {page}).")
                    if response:
                        messages.append(response)
                else:
                    print(f"[{bot_username}] No more pagination buttons found.")
                    break
                    
            return messages
    except asyncio.TimeoutError:
        print(f"[{bot_username}] Timeout waiting for response.")
        return messages
    except Exception as e:
        print(f"[{bot_username}] Error during query: {e}")
        return messages

async def handle_request(request):
    # Check for Basic Authentication
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return web.json_response({'error': 'Unauthorized'}, status=401, headers={'WWW-Authenticate': 'Basic realm="Bot API"'})
        
    try:
        auth = BasicAuth.decode(auth_header)
        if auth.login != API_USERNAME or auth.password != API_PASSWORD:
            return web.json_response({'error': 'Forbidden: Invalid credentials'}, status=403)
    except ValueError:
        return web.json_response({'error': 'Bad Request: Invalid auth format'}, status=400)

    # Receive data from the external app (expects JSON like {"text": "your command"})
    data = await request.json()
    input_text = data.get('text')
    resolution = data.get('resolution')
    fallback_res_1 = data.get('fallback_1', '720p')
    fallback_res_2 = data.get('fallback_2', '480p')
    
    if not input_text or not resolution:
        return web.json_response({'error': 'Missing text or resolution'}, status=400)
        
    print(f"Received input from external app: {input_text} (Target: {resolution})")
    
    print("Starting concurrent bot queries...")
    # Query all bots concurrently to save time
    try:
        resp1, resp2 = await asyncio.gather(
            query_bot(bot1_username, input_text),
            query_bot(bot2_username, input_text)
        )
        
        print("Bot queries complete. Searching through results...")
        best_msg = None
        best_row, best_col = -1, -1
        lowest_size_mb = float('inf')
        
        fallback_1_best_msg = None
        fallback_1_best_row, fallback_1_best_col = -1, -1
        fallback_1_lowest_size_mb = float('inf')
        
        fallback_2_best_msg = None
        fallback_2_best_row, fallback_2_best_col = -1, -1
        fallback_2_lowest_size_mb = float('inf')
        
        any_best_msg = None
        any_best_row, any_best_col = -1, -1
        any_lowest_size_mb = float('inf')
        request_parts = input_text.lower().split()
        
        all_msgs = []
        if resp1: all_msgs.extend(resp1)
        if resp2: all_msgs.extend(resp2)
        
        # Search through all bot responses and find the button with the lowest file size
        for msg in all_msgs:
            if msg and msg.buttons:
                button_count = 0
                for r_idx, row in enumerate(msg.buttons):
                    for c_idx, button in enumerate(row):
                        if button_count >= 20:
                            break
                            
                        button_count += 1
                        button_text_lower = button.text.lower()
                        
                        if '.srt' in button_text_lower:
                            continue
                        
                        # Exclude any file that has a file extension other than .mp4 or .mkv
                        if re.search(r'\.(avi|wmv|flv|webm|mov|m4v|ts|m2ts|vob|rmvb|mpg|mpeg|3gp|divx|xvid|zip|rar|7z|tar|gz|bz2|xz|iso|bin|img|nrg|srt|sub|txt|pdf|mp3|flac|wav|aac|ogg|m4a)(?:\s|$|\[|\]|\(|\)|-)', button_text_lower):
                            continue
                        
                        if all(part in button_text_lower for part in request_parts):
                            size_mb = parse_size_to_mb(button.text)
                            
                            if size_mb < 10:
                                continue
                            
                            # Keep track of the lowest size overall, regardless of resolution
                            if any_best_msg is None or size_mb < any_lowest_size_mb:
                                any_lowest_size_mb = size_mb
                                any_best_msg = msg
                                any_best_row = r_idx
                                any_best_col = c_idx
                                
                            if resolution.lower() in button_text_lower:
                                print(f"Found matching primary option: '{button.text}' ({size_mb} MB)")
                                
                                # Update if it's the first match or if it's smaller than the current best
                                if best_msg is None or size_mb < lowest_size_mb:
                                    print(f"-> New best primary option selected: {size_mb} MB")
                                    lowest_size_mb = size_mb
                                    best_msg = msg
                                    best_row = r_idx
                                    best_col = c_idx
                                    
                            elif fallback_res_1.lower() in button_text_lower:
                                print(f"Found matching 1st fallback option: '{button.text}' ({size_mb} MB)")
                                if fallback_1_best_msg is None or size_mb < fallback_1_lowest_size_mb:
                                    print(f"-> New best 1st fallback option selected: {size_mb} MB")
                                    fallback_1_lowest_size_mb = size_mb
                                    fallback_1_best_msg = msg
                                    fallback_1_best_row = r_idx
                                    fallback_1_best_col = c_idx
                                    
                            elif fallback_res_2.lower() in button_text_lower:
                                print(f"Found matching 2nd fallback option: '{button.text}' ({size_mb} MB)")
                                if fallback_2_best_msg is None or size_mb < fallback_2_lowest_size_mb:
                                    print(f"-> New best 2nd fallback option selected: {size_mb} MB")
                                    fallback_2_lowest_size_mb = size_mb
                                    fallback_2_best_msg = msg
                                    fallback_2_best_row = r_idx
                                    fallback_2_best_col = c_idx
        
                    if button_count >= 20:
                        break
                        
        final_resolution = resolution
        # Use fallbacks if the requested resolution wasn't found
        if not best_msg:
            if fallback_1_best_msg:
                print(f"Primary resolution '{resolution}' not found. Falling back to '{fallback_res_1}'.")
                best_msg = fallback_1_best_msg
                best_row = fallback_1_best_row
                best_col = fallback_1_best_col
                final_resolution = fallback_res_1
            elif fallback_2_best_msg:
                print(f"Primary resolution '{resolution}' and '{fallback_res_1}' not found. Falling back to '{fallback_res_2}'.")
                best_msg = fallback_2_best_msg
                best_row = fallback_2_best_row
                best_col = fallback_2_best_col
                final_resolution = fallback_res_2
            elif any_best_msg:
                print("Requested resolutions not found. Falling back to the best available option overall.")
                best_msg = any_best_msg
                best_row = any_best_row
                best_col = any_best_col
                btn_text = best_msg.buttons[best_row][best_col].text.lower()
                res_match = re.search(r'(\d{3,4}p|4k)', btn_text)
                final_resolution = res_match.group(1) if res_match else "unknown"
            
        if not best_msg:
            print(f"No suitable options found for '{input_text}'.")
            return web.json_response({'error': f'No suitable options found for {input_text}'}, status=404)
        
        print(f"Clicking best option (Row {best_row}, Col {best_col})...")   
        # Click the button and wait for the bot to send the media file
        async with client.conversation(best_msg.chat_id) as conv:
            try:
                await best_msg.click(best_row, best_col)
            except (errors.DataInvalidError, errors.MessageIdInvalidError):
                print("Button data is invalid (likely due to pagination). Re-querying bot to click the button...")
                
                # We need to re-query the bot with the original text and click the same button
                await conv.send_message(input_text)
                response = await conv.get_response(timeout=10)
                
                # Now we need to paginate until we find the exact button text again
                found = False
                target_text = best_msg.buttons[best_row][best_col].text
                
                for page in range(1, 4):
                    if response and response.buttons:
                        for r_idx, row in enumerate(response.buttons):
                            for c_idx, button in enumerate(row):
                                if button.text == target_text:
                                    print(f"Found target button again on Page {page}! Clicking...")
                                    await response.click(r_idx, c_idx)
                                    found = True
                                    break
                            if found: break
                    
                    if found:
                        break
                        
                    # Find and click next button if not found
                    next_button = None
                    next_row, next_col = -1, -1
                    if response and response.buttons:
                        for r_idx, row in enumerate(response.buttons):
                            for c_idx, button in enumerate(row):
                                btn_text = button.text.lower()
                                if any(x in btn_text for x in ['next', 'load more', '➡️', '>>']):
                                    if not re.search(r'\d+(?:\.\d+)?\s*(gb|mb|kb)', btn_text, re.IGNORECASE):
                                        next_button = button
                                        next_row = r_idx
                                        next_col = c_idx
                                        break
                            if next_button: break
                    
                    if next_button:
                        await response.click(next_row, next_col)
                        await asyncio.sleep(2)
                        latest_messages = await client.get_messages(best_msg.chat_id, limit=1)
                        if latest_messages and latest_messages[0].id > response.id:
                            response = latest_messages[0]
                        else:
                            response = await client.get_messages(best_msg.chat_id, ids=response.id)
                    else:
                        break
                        
                if not found:
                    print("Failed to find the button again after re-querying.")
                    return web.json_response({'error': 'Failed to re-locate the button after pagination'}, status=500)

            try:
                print("Waiting for media file to be sent...")
                file_msg = await conv.wait_event(events.NewMessage(incoming=True, chats=best_msg.chat_id), timeout=15)
                print("Media file received!")
            except asyncio.TimeoutError:
                print("Bot timed out sending the media file.")
                return web.json_response({'error': 'Bot timed out sending the media file'}, status=504)

        print("Forwarding media to @FileToLinkiBot...")    
        # Forward the media to @FileToLinkiBot and fetch the resulting URL
        async with client.conversation('@File2VidBot') as conv:
            await file_msg.forward_to('@File2VidBot')
            
            final_url = ""
            print("Waiting for URL generation...")
            # Loop up to 3 times in case the bot sends a "processing" message first
            for i in range(3):
                try:
                    link_msg = await conv.wait_event(events.NewMessage(incoming=True, chats='@File2VidBot'), timeout=10)
                    final_text = link_msg.text
                    
                    match = re.search(r'(https?://[^\s]*clck\.ru[^\s]*)', final_text)
                    if match:
                        final_url = match.group(1).rstrip('*')
                        print(f"Generated URL: {final_url}")
                        break # Found the URL, stop waiting
                    else:
                        print(f"Received interim status (Attempt {i+1}): {final_text.strip()}")
                except asyncio.TimeoutError:
                    print("Timeout waiting for link generation.")
                    break

        print("Request finished successfully.")            
        return web.json_response({'url': final_url, 'resolution': final_resolution})
    except Exception as e:
        print(f"Error processing request: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def main():
    # Set up an HTTP API server on port 8080
    app = web.Application()
    
    # Configure default CORS settings to allow all origins ("*")
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
    })
    
    # Add the route and apply CORS to it
    resource = cors.add(app.router.add_resource("/query"))
    cors.add(resource.add_route("POST", handle_request))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8088)
    await site.start()
    
    print("Server running on http://0.0.0.0:8088")
    print("Send a POST request to http://<your_ip_address>:8080/query with JSON: {\"text\": \"your message\", \"resolution\": \"1080p\"}")
    
    # Keep the client and server running
    await client.run_until_disconnected()

with client:
    client.loop.run_until_complete(main())