import asyncio
import discord
import json
import os, sys
import hmac, hashlib, base64
import websockets
import bot_globals
import time
import traceback
import io
import requests
from threading import Thread
from bs4 import BeautifulSoup
import zipfile
import re
import commandhandler #bot commands

confPath = os.path.join(sys.path[0], "configuration")
bot_globals.bot_config = config = json.loads(open(os.path.join(confPath, "main-configuration.json"), 'r').read())
defaultprefix = config["prefix"]
argprefix = "-"
bot = bot_globals.global_bot
default_ws = False
lastDownloadablesTimeCheck = 0
connecting = False
lastChatMessageId = False
timeRateLimit = 5
lastTimeSent = {}

#tasks
heartbeat_task = False

for root, dir, files in os.walk(confPath):
    for file in files:
        if file != "main-configuration.json" and file != "user_configs.json":
            confFile = open(os.path.join(confPath, file), "r")
            content = confFile.read()
            bot_globals.group_configs[file[:-5]] = json.loads(content) if content else {"fileLocation": os.path.join(confPath, file)}

def bgDownloadAssets():
    global config
    apkPage = BeautifulSoup(requests.get("https://apkpure.com/golf-blitz/com.noodlecake.ssg4/download").text, features="html.parser")
    apkLink = apkPage.find("a", id="download_link")['href']
    apkVersion = apkPage.find("span", attrs={"class": "file"}).text
    if not "apkVersion" in config:
        config["apkVersion"] = ""
    if config["apkVersion"] != apkVersion:
        print("downloading new apk for assets")
        apkPath = os.path.join(bot_globals.resources_path, "golfblitz.apk")
        with requests.get(apkLink, stream=True) as dl:
            with open(apkPath, "wb") as f:
                for chunk in dl.iter_content(chunk_size=16384):
                    f.write(chunk)
        with zipfile.ZipFile(apkPath, 'r') as to_unzip:
            to_unzip.extractall(bot_globals.resources_path)
        print("apk has been downloaded and extracted")
        config["apkVersion"] = apkVersion
        json.dump(config, open(os.path.join(confPath, "main-configuration.json"), 'w'))
        bot_globals.update_hats_and_golfers()

def bgDownloadExtraAssets(downloadablesJson):
    global config
    if not "extraAssetsVersion" in config:
        config["extraAssetsVersion"] = ""
    if config["extraAssetsVersion"] != downloadablesJson["lastModified"]:
        print("downloading extra assets")
        extraAssetsPath = os.path.join(bot_globals.resources_path, "extras.zip")
        with requests.get(downloadablesJson["url"], stream=True) as dl:
            with open(extraAssetsPath, "wb") as f:
                for chunk in dl.iter_content(chunk_size=16384):
                    f.write(chunk)
        with zipfile.ZipFile(extraAssetsPath, 'r') as to_unzip:
            to_unzip.extractall(bot_globals.extra_assets_path)
        print("extra assets have been downloaded and extracted")
        config["extraAssetsVersion"] = downloadablesJson["lastModified"]
        json.dump(config, open(os.path.join(confPath, "main-configuration.json"), 'w'))
        bot_globals.update_hats_and_golfers()

def sendMsgWaitTime(player): #rate limit commands
    global lastTimeSent
    if not player in lastTimeSent or time.time() - lastTimeSent[player] >= timeRateLimit:
        lastTimeSent[player] = time.time()
        return False
    return round(5 - (time.time() - lastTimeSent[player]), 2)


def argParser(raw_args):
    new_args = {}
    last_arg_indx = -1
    for i, arg in enumerate(raw_args):
        if arg.startswith(argprefix):
            if last_arg_indx > -1:
                new_args[raw_args[last_arg_indx][1:]] = " ".join(raw_args[last_arg_indx + 1:i])
            last_arg_indx = i
    if raw_args:
        new_args[raw_args[last_arg_indx][1:]] =  " ".join(raw_args[last_arg_indx + 1:])
    return new_args

async def sendCommand(ws, message, discord_message):
    #message = re.sub(r"^['\"“”’„]|['\"“”’„]$", "", message.lower())
    messagedetails = [re.sub(r"^['\"“”’„]|['\"“”’„]$", "", m) for m in message[1:].lower().split()]
    print(messagedetails)
    print("Command sent: ", message, file=sys.stderr)
    if messagedetails[0] in commandhandler.commands:
        try:
            await commandhandler.commands[messagedetails[0]](default_ws, argParser(messagedetails[1:]), discord_message)
        except:
            await commandhandler.directlySendMessage(ws, "The command failed while processing the input\nDetails\n" + traceback.format_exc(), discord_message)
            print(traceback.format_exc())

async def onGolfblitzMessage(ws, msgJson):
    print("Golf blitz message", msgJson)
    msgdetails = False
    if "extCode" in msgJson:
        messages = msgJson["data"]["messages"]
        currMsg = messages[0]
        msgdetails = {"msg": currMsg["message"]}
        msgJson["fromId"] = currMsg["player_id"]
        msgJson["who"] = currMsg["display_name"]
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["message"].startswith("match_id:") and messages[i]["player_id"] == bot_globals.golfblitz_bot_details["userId"]:
                msgJson["teamId"] = messages[i]["message"][9:]
    else:
        msgdetails = json.loads(msgJson["message"])
    if msgJson["fromId"] == bot_globals.golfblitz_bot_details["userId"]: #don't respond to messages from the bot
        return
    teamid = msgJson["teamId"]
    if "msg" in msgdetails:
        local_prefix = defaultprefix
        if teamid in bot_globals.group_configs and "prefix" in bot_globals.group_configs[teamid]:
            local_prefix = bot_globals.group_configs[teamid]["prefix"]
        msgcontent = msgdetails["msg"]
        if msgcontent.startswith(local_prefix):
            waitTime = sendMsgWaitTime(msgJson["fromId"])
            if not waitTime:
                await sendCommand(ws, msgcontent, msgJson)
            else:
                error = bot_globals.error_messages["commands_too_quick"].split("\n")
                error[1] = error[1].format(waitTime)
                await commandhandler.sendMessage(ws, error, msgJson, {})
        elif teamid in bot_globals.group_configs:
            if "linkedGroups" in bot_globals.group_configs[teamid]:
                for groupId, channelId in bot_globals.group_configs[teamid]["linkedGroups"]:
                    if len(str(groupId)) == 18: # send to discord
                        textChannel = bot.get_channel(channelId)
                        channelMsgs = await textChannel.history(limit=1).flatten()
                        if not channelMsgs:
                            await textChannel.send("first", delete_after=0)
                            channelMsgs  = await textChannel.history(limit=1).flatten()
                        channelMsg = channelMsgs[0]
                        await commandhandler.sendMessage(ws, ("**" + msgJson["who"] + "**:", msgcontent), channelMsg, {"disable_code_format": True})
    return

async def recv_all(ws):
    messages = []
    while True:
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=0.9)
            msgJson = json.loads(message)
            if "extCode" in msgJson and msgJson["extCode"] == "PLAYER_DATA_UPDATE":
                bot_globals.golfblitz_bot_details = msgJson["data"]
                print(msgJson["data"])
            messages.append(msgJson)
        except asyncio.TimeoutError:
            break
    return messages

async def get_new_session():
    portal_ws = await websockets.connect(config["golfblitz_entryURL"], max_size=None)
    response = await portal_ws.recv()
    print("connecting to the main websocket")
    main_ws = await websockets.connect(json.loads(response)["connectUrl"], max_size=None)
    entryInfo = await main_ws.recv()
    handshake_obj = config["golfblitz_handshakeframe"]
    handshake_obj["hmac"] = base64.b64encode(hmac.new(config["golfblitz_hmac_key"].encode("utf-8"), json.loads(entryInfo)["nonce"].encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
    await main_ws.send(json.dumps(handshake_obj))
    return main_ws

async def login(ws, request):
    global season
    await ws.send(json.dumps(request))
    #await recv_all(default_ws) #receive the filler messages
    #await ws.send(json.dumps(commandhandler.requests["get_current_season"]))
    #result = await ws.recv()
    #bot_globals.curr_season = json.loads(result)["scriptData"]["current_season"]["seasonnumber"] #update the season number
    #print(bot_globals.curr_season)

async def gbconnect():
    global default_ws, heartbeat_task
    while True:
        if heartbeat_task:
            heartbeat_task.cancel()
            #responses_task.cancel()
            print("tasks have been cancelled for the restart")
        default_ws = await get_new_session()
        print("bot is logging in")
        await login(default_ws, config["default_golfblitz_authreq"]) #log in as the default golf blitz bot
        loop = asyncio.get_event_loop()
        heartbeat_task = loop.create_task(keepalive(default_ws))
        try:
            await getResponses(default_ws)
        except:
            traceback.print_exc()
        print("connection established")

async def keepalive(ws):
    global lastDownloadablesTimeCheck, default_ws
    while True:
        if time.time() - lastDownloadablesTimeCheck > 9600:
            print("checking for new assets")
            Thread(target=bgDownloadAssets).start()
            await ws.send(json.dumps(commandhandler.requests["get_extra_assets"]))
            lastDownloadablesTimeCheck = time.time()
            await commandhandler.getChallenge(ws, {"noreply": True}, {})
        try:
            await ws.ping()
            await ws.send(json.dumps(commandhandler.requests["get_current_season"]))
            print("keep alive request sent")
            await bot.change_presence(status=discord.Status.online, activity=discord.Game("Golf Blitz for {0} servers".format(len(bot.guilds))))
            await asyncio.sleep(45)
        except:
            print("bot had an issue, restart 1")
            break
            #os.execl(sys.executable, 'python', __file__, *sys.argv[1:])

async def getResponses(ws):
    while True:
        try:
            response = await ws.recv()
            responseJson = json.loads(response)
            if "requestId" in responseJson and responseJson["requestId"] in bot_globals.pending_requests:
                await commandhandler.finishCommand(ws, responseJson)
            elif "requestId" in responseJson and responseJson["requestId"] == "keepalive":
                print(responseJson)
                bot_globals.curr_season = responseJson["scriptData"]["current_season"]["seasonnumber"]
                print("keepalive request was received")
            elif responseJson["@class"] == ".SessionTerminatedMessage":
                print("session terminated, restart 2")
                break
            elif responseJson["@class"] == ".GetDownloadableResponse":
                Thread(target=bgDownloadExtraAssets, args=(responseJson,)).start()
            elif responseJson["@class"] == ".TeamChatMessage":
                await onGolfblitzMessage(ws, responseJson)
            elif "extCode" in responseJson:
                if responseJson["extCode"] == "PLAYER_DATA_UPDATE":
                    bot_globals.golfblitz_bot_details = responseJson["data"]
                elif responseJson["extCode"] == "PLAYER_INVITED_TO_FRIEND_MATCH": #accept the invite and join
                    baseReq = commandhandler.requests["join_friendly_lobby"].copy()
                    baseReq["match_id"] = responseJson["data"]["match_id"]
                    await ws.send(json.dumps(baseReq))
                    baseReq = commandhandler.requests["send_friendly_chat_message"].copy()
                    baseReq["match_id"] = responseJson["data"]["match_id"]
                    baseReq["message"] = "match_id:"+responseJson["data"]["match_id"]
                    await ws.send(json.dumps(baseReq))
                elif responseJson["extCode"] == "PLAYER_FRIENDS_UPDATE":
                    for req in responseJson["data"]["incoming_requests"]:
                        baseReq = commandhandler.requests["accept_friend_request"].copy()
                        baseReq["request_id"] = req["request_id"]
                        await ws.send(json.dumps(baseReq))
                elif responseJson["extCode"] == "FRIENDLY_MATCH_CHAT_CHANGED":
                    await onGolfblitzMessage(ws, responseJson)
            else:
                print("unknown response", responseJson)
        except:
            traceback.print_exc()
            print("bot had an issue, restart 2")
            break

            #os.execl(sys.executable, 'python', __file__, *sys.argv[1:])

@bot.event
async def on_ready():
    global default_ws
    print('bot has started up')
    await gbconnect()

@bot.event
async def on_message(message):
    #print(message.guild)
    guildid = str(message.guild.id) if message.guild else False
    if message.author.id == bot.user.id: #ignore messages from the bot
        return
    local_prefix = defaultprefix
    if guildid in bot_globals.group_configs and "prefix" in bot_globals.group_configs[guildid]:
        local_prefix = bot_globals.group_configs[guildid]["prefix"]
    if guildid in bot_globals.group_configs and not message.content.startswith(local_prefix):
        if "linkedGroups" in bot_globals.group_configs[guildid]:
            for groupId, channelId in bot_globals.group_configs[guildid]["linkedGroups"]:
                if channelId == message.channel.id:
                    await commandhandler.sendMessage(default_ws, (message.author.name + ":", message.content), {"teamId": groupId}, {"noformat": True})
    if message.content.startswith(local_prefix):
        print("command sent!", message.content, message.id)
        waitTime = sendMsgWaitTime(str(message.author.id))
        if not waitTime:
            await sendCommand(default_ws, message.content, message)
        else:
            error = bot_globals.error_messages["commands_too_quick"].split("\n")
            error[1] = error[1].format(waitTime)
            await commandhandler.sendMessage(default_ws, error, message, {})

bot.run(config["bot_token"])
