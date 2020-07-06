import asyncio, websockets, json, hmac, hashlib, base64, requests

game_entryURL = "wss://live-f351468gBSWz.ws.gamesparks.net/ws/device/f351468gBSWz"

async def test():
    ws1 = await websockets.connect(game_entryURL)
    info = await ws1.recv()
    ws2 = await websockets.connect(json.loads(info)["connectUrl"])
    info2 = await ws2.recv() #
    outobj = {"@class": ".AuthenticatedConnectRequest", "hmac": base64.b64encode(hmac.new(b'a3insvuyMEertN6BV14ys1K05qcfaaoN', json.loads(info2)["nonce"].encode('utf-8'), hashlib.sha256).digest()).decode('utf-8'), "os": "uh"}
    await ws2.send(json.dumps(outobj))
    await ws2.recv()
    await ws2.send(json.dumps({"@class": ".AuthenticationRequest", "userName": "put in your username", "password": "put in your password", "scriptData": {"game_version": 9999, "client_version": 99999}, "requestId": "ok"}))
    print("logged in")
    for i in range(4):
        await ws2.recv()
    await ws2.send(json.dumps({"@class": ".LogEventRequest", "eventKey": "REFRESH_CHALLENGE_MODE", "requestId": ""}))
    async for message in ws2:
        print(message)

asyncio.get_event_loop().run_until_complete(test())
