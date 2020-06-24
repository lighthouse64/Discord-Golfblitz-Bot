import asyncio, websockets
import discord
import json
import datetime, time
import os, sys
import bot_globals
import traceback
import io
import statistics
import requests as httprequests
from collections import defaultdict

requests = json.loads(open(os.path.join(sys.path[0], "golfblitz-requests.json"), 'r').read())

async def sendGolfblitzWs(ws, response_function, args, message_object, function_key, request):
        reqId = request["requestId"] = str(time.time())
        await ws.send(json.dumps(request))
        bot_globals.pending_requests[reqId] = (response_function, message_object, function_key, args)

async def finishDiscordCommand(response, message, request_args):
    if "json" in request_args:
        attachment = discord.File(io.StringIO(response), filename="response.json")
        await message.channel.send("Resulting json file", file=attachment)
        return
    for i, page in enumerate(response):
        if page:
            if len(response) > 1:
                await message.channel.send("page {n} out of {total}".format(n=i+1, total=len(response)))
            await message.channel.send(page)
    print("discord response to", message.id, "\n" , response)

async def finishGolfblitzCommand(ws, response, message, request_args):
    reqsToSend = []
    messageobj = {"type": "chat"}
    groupId = message["teamId"]
    isTeamMessage = len(groupId) == 24
    baseReq = requests["send_team_chat_message"] if isTeamMessage else requests["send_friendly_chat_message"]
    if isTeamMessage:
        baseReq["teamId"] = message["teamId"]
    else:
        baseReq["match_id"] = message["teamId"]
    for i, page in enumerate(response):
        if page:
            if len(response) > 1:
                if isTeamMessage:
                    messageobj["msg"] = "page {n} out of {total}".format(n=i+1, total=len(response))
                    baseReq["message"] = json.dumps(messageobj)
                else:
                    baseReq["message"] = "page {n} out of {total}".format(n=i+1, total=len(response))
                reqsToSend.append(baseReq.copy())
            if isTeamMessage:
                messageobj["msg"] = page
                baseReq["message"] = json.dumps(messageobj)
            else:
                baseReq["message"] = page
            reqsToSend.append(baseReq.copy())
    for i in range(len(reqsToSend) - 1, -1, -1): # we need to send the stuff backwards because the latest messages appear on the top (this is the opposite of discord)
        await ws.send(json.dumps(reqsToSend[i]))
    print("golf blitz response to", "\n", response)

async def finishCommand(ws, responseJson, offlineData=False):
    response = ""
    reqId = responseJson["requestId"]
    requestInfo = bot_globals.pending_requests[reqId]
    del bot_globals.pending_requests[reqId]
    sendback_info = requestInfo[1]
    ws_request = requestInfo[2]
    request_args = requestInfo[3]
    if not offlineData:
        if ws_request in bot_globals.extraResponseCount:
            responseJson = [responseJson]
            for i in range(bot_globals.extraResponseCount[ws_request]):
                nResponse = await ws.recv()
                responseJson.append(json.loads(nResponse))
    else:
        responseJson = offlineData
    try:
        if "prev_function_data" in request_args:
            request_args["prev_function_data"].append(responseJson)
            responseJson = request_args["prev_function_data"]
        if "next_function" in request_args and type(request_args["next_function"]).__name__ == "function":
            request_args["prev_function_data"] = responseJson if type(responseJson) is list else [responseJson]
            nextFunction = request_args["next_function"]
            del request_args["next_function"]
            await nextFunction(ws, request_args, sendback_info)
            return
        if "json" in request_args:
            response = json.dumps(responseJson, ensure_ascii=False)
        else:
            response = await requestInfo[0](responseJson, request_args)
        await sendMessage(ws, response, sendback_info, request_args)
    except:
        await directlySendMessage(ws, "the command failed when processing the output\nDetails:\n" + traceback.format_exc(), sendback_info)
        #print("REQUEST INFORMATION", requestInfo)
        traceback.print_exc()

async def sendMessage(ws, message, message_object, request_args, arg_aliases={}):
    arg_aliases["page"] = "pages"
    #print(message)
    isGolfblitzMessage = type(message_object) is dict
    for arg in arg_aliases:
        if arg in request_args and not arg_aliases[arg] in request_args:
            request_args[arg_aliases[arg]] = request_args[arg]
    disableCodeFormat = "disable_code_format" in request_args or isGolfblitzMessage
    pagesToSend = message
    if not "json" in request_args:
        header = message[0] + "\n\n"
        message = message[1].split(bot_globals.safe_split_str)
        maxpagelen = (5000 if isGolfblitzMessage else (2000 if disableCodeFormat else 1991)) - len(header)
        pages = []
        currPage = ("" if disableCodeFormat else "```\n") + header
        for part in message:
            if len(part) > maxpagelen: #we have to do ugly page cuts to preserve order.
                cutoffIndex = maxpagelen - len(currPage)
                currPage += part[:cutoffIndex]
                pages.append(currPage if disableCodeFormat else currPage + "\n```")
                for i in range(cutoffIndex, len(part), maxpagelen): #make separate pages for each segment that is too long
                    currPage = ("" if disableCodeFormat else "```\n") + header + part[i:i+maxpagelen]
                    if len(currPage) == maxpagelen + len(header) + 4:
                        pages.append(currPage if disableCodeFormat else currPage + "\n```")
            elif len(part) + len(currPage) > maxpagelen:
                pages.append(currPage if disableCodeFormat else currPage + "\n```")
                currPage = ("" if disableCodeFormat else "```\n") + header
            currPage += part
        pages.append(currPage if disableCodeFormat else currPage + "\n```")
        pageArgs = request_args["pages"].split(",") if "pages" in request_args and request_args["pages"] else "1"
        pagesToSend = [False] * len(pages)
        for arg in pageArgs:
            if arg == "all":
                pagesToSend = pages
                break
            if "-" in arg: #deal with a range
                rangeVals = arg.split("-")
                try:
                    for i in range(int(rangeVals[0]) - 1, int(rangeVals[1])):
                        pagesToSend[i] = pages[i]
                except:
                    pass
            else:
                try:
                    index = int(arg) - 1
                    pagesToSend[index] = pages[index]
                except ValueError as e:
                    pass

    if isGolfblitzMessage:
        await finishGolfblitzCommand(ws, pagesToSend, message_object, request_args)
    else:
        await finishDiscordCommand(pagesToSend, message_object, request_args)

async def directlySendMessage(ws, message, message_object):
    if type(message_object) is dict:
        sendReq = requests["send_team_chat_message"]
        msgJson = {"msg": message, "type": "chat"}
        sendReq["message"] = json.dumps(msgJson)
        sendReq["teamId"] = message_object["teamId"]
        await ws.send(json.dumps(sendReq))
    else:
        await message_object.channel.send(message)

def has_permissions(message_object):
    if type(message_object) is dict:
        return True
    else:
        permissions = message_object.author.guild_permissions
        return permissions.manage_guild or permissions.manage_channels or bot_globals.bot_config["owner_discord_id"] == str(message_object.author.id)

def get_verification(id):
    if not id in bot_globals.user_configs:
        return False
    if not "externalId" in bot_globals.user_configs[id]:
        return False
    externalId = bot_globals.user_configs[id]["externalId"]
    if not externalId in bot_globals.user_configs:
        return False
    if not "externalId" in bot_globals.user_configs[externalId]:
        return False
    if id == bot_globals.user_configs[externalId]["externalId"]:
        return bot_globals.user_configs[externalId]["externalId"]
    else:
        print(id, bot_globals.user_configs[externalId]["externalId"])
        return False

def discordTable(elemList, changeDict={}, orderList=[], numbered=True, rowSegmentNum=50):
    header = ""
    for i in orderList:
        if i in changeDict:
            header += changeDict[i]
        else:
            header += i
        header += " "
    table = []
    for elem in elemList:
        line = "#" + str(len(table) + 1) + " " if numbered else ""
        for key in orderList:
            line += str(elem[key]) + " "
        table.append(line)
    if numbered:
        header = "# " + header
    tableStr = ""
    for i, row in enumerate(table):
        tableStr += row + "\n"
        if not (i+1) % rowSegmentNum:
            tableStr += bot_globals.safe_split_str
    return (header, tableStr)

def genRewardStr(i, rewards):
    rewardStr = "* win #" + str(i + 1) + " - "
    for reward in rewards:
        if type(rewards[reward]) is dict:
            rewards[reward] = rewards[reward].popitem()
        if reward == "bux":
            rewardStr += str(rewards[reward]) + " bux"
        elif reward == "card_pack":
            rewardStr += bot_globals.cardpacks[rewards[reward][1]] + " pack"
        elif reward == "emotes":
            emoteObj = rewards[reward][1]
            emoteObj = bot_globals.emotes[str(emoteObj["identifier"])]
            if "text" in emoteObj:
                rewardStr += emoteObj["text"]["en"] + " dialog emote"
            else:
                rewardStr += emoteObj["loc"]["en"] + " animated emote"
        elif reward == "golfers":
            golferObj = rewards[reward][1]
            golferObj = bot_globals.golfers[str(golferObj["identifier"])]
            rewardStr += golferObj["name"]["en"] + " golfer"
        elif reward == "hats":
            hatObj = rewards[reward][1]
            hatObj = bot_globals.hats[str(hatObj["identifier"])]
            rewardStr += hatObj["name"]["en"] + " hat"
        rewardStr += " & "
    return rewardStr

async def finishGetChallenge(response, args):
    print(response)
    challenge_data = response[1]["data"]
    current_event_data = challenge_data["current_event"]
    if not "get_challenge" in bot_globals.command_data:
        bot_globals.command_data["get_challenge"] = {}
    challenges_data = bot_globals.command_data["get_challenge"]
    if not challenge_data["current_event_id"].lower() in challenges_data: #store a record of challenges
        challenges_data[challenge_data["current_event_id"].lower()] = response
        json.dump(bot_globals.command_data, open(bot_globals.command_data_path, 'w'))
    header = challenge_data["current_event_id"] + "\n"
    if current_event_data["duration"]:
        header += "Event start time: " + time.asctime(time.gmtime(int(current_event_data["start_time"]/1000))) + " GMT\n"
        timedelta = (current_event_data["start_time"] + current_event_data["duration"])/1000 - time.time()
        timeleft = "the event finished"
        if timedelta >= 0:
            timeleft = datetime.timedelta(seconds = timedelta)
        header += "Event time left: " + str(timeleft)
    header = header.rstrip()
    outmsg = "Amateur Rewards:\n"
    for i, rewards in enumerate(current_event_data["tiers"]["amateur"]["prize"]):
        outmsg += genRewardStr(i, rewards)[:-3] + "\n"
    outmsg += "\nPro Rewards:\n"
    for i, rewards in enumerate(current_event_data["tiers"]["pro"]["prize"]):
        outmsg += genRewardStr(i, rewards)[:-3] + "\n"
    return (header, outmsg)

async def getChallenge(ws, args, message_object):
    baseReq = requests["get_current_challenge"].copy()
    baseReq["requestId"] = str(time.time())
    bot_globals.pending_requests[baseReq["requestId"]] = (finishGetChallenge, message_object, "get_current_challenge", args)
    if "event" in args:
        await finishCommand(ws, baseReq, offlineData=bot_globals.command_data["get_challenge"][args["event"].lower()])
    else:
        await ws.send(json.dumps(baseReq))

async def help(ws, args, message_object):
    if "command" in args:
        if args["command"] in bot_globals.command_help_page:
            await sendMessage(ws, bot_globals.command_help_page[args["command"]], message_object, args)
        else:
            await sendMessage(ws, bot_globals.error_messages["page_not_found"].split("\n"), message_object, args)
    else:
        await sendMessage(ws, (bot_globals.default_help_msg_head, bot_globals.default_help_msg), message_object, args)

async def info(ws, args, message_object):
    groupPrefix = bot_globals.bot_config["prefix"]
    groupId = ""
    if type(message_object) is dict:
        groupId = message_object["teamId"]
    else:
        groupId = str(message_object.guild.id)
    if groupId in bot_globals.group_configs and "prefix" in bot_globals.group_configs[groupId]:
        groupPrefix = bot_globals.group_configs[groupId]["prefix"]
    await sendMessage(ws, (bot_globals.info_msg_head, bot_globals.info_msg.format(prefix=groupPrefix)), message_object, args)

async def finishGetLeaderboard(response, args):
    if "error" in response:
        return ("There was an error with the leaderboard request:", json.dumps(response))
    leaderboardData = response["data"]
    isTeamData = False
    for elem in leaderboardData:
        if "teamName" in elem: #remove the country indicator from the team name.  it's already in the leaderboard, so no need to show it twice
            elem["teamName"] = elem["teamName"][4:]
            isTeamData = True
        else:
            break
    tableData = False
    try:
        tableData = discordTable(leaderboardData, changeDict={"LAST-SCORE" if isTeamData else "SCORE": "trophies", "rank": "#", "LAST-COUNTRY": "COUNTRY"}, orderList= ["rank", "teamName", "LAST-COUNTRY", "LAST-SCORE"] if isTeamData else ["rank", "userName", "COUNTRY", "SCORE"], numbered=False)
    except IndexError:
        return ("Error: empty leaderboard", "There was no data to display.")
    return ("Season {seasonNum} leaderboard\n{tableHeader}".format(seasonNum=leaderboardData[0]["LAST-SEASON" if isTeamData else "SEASON"], tableHeader=tableData[0]), tableData[1])

async def getLeaderboard(ws, args, message_object):
    if "teams" in args:
        args["team"] = args["teams"]
    baseReq = requests["get_leaderboard"].copy()
    isTeam = False
    shortCodeParts = []
    if "count" in args:
        baseReq["entryCount"] = min(10000, int(args["count"]))
    #build the short code
    if "team" in args:
        shortCodeParts.append("TEAM_TROPHIES_LEADERBOARD")
        isTeam = True
    else:
        shortCodeParts.append("INDIVIDUAL_TROPHIES")

    if "country" in args and args["country"]:
        shortCodeParts[0] += "_BY_COUNTRY"
        if isTeam:
            shortCodeParts.append("LAST-COUNTRY")
        else:
            shortCodeParts.append("COUNTRY")
        shortCodeParts.append(args["country"])

    if isTeam:
        shortCodeParts.append("LAST-SEASON")
    else:
        shortCodeParts.append("SEASON")

    if "season" in args:
        shortCodeParts.append(args["season"])
    else:
        shortCodeParts.append(bot_globals.curr_season)
    shortCodeParts[-1] = str(shortCodeParts[-1])

    baseReq["leaderboardShortCode"] = ".".join(shortCodeParts)
    if "offset" in args:
        baseReq["offset"] = args["offset"]
    await sendGolfblitzWs(ws, finishGetLeaderboard if not "stats" in args else finishGetLeaderboardStats, args, message_object, "get_leaderboard", baseReq)

async def finishGetLeaderboardStats(response, args):
    header = "Leaderboard Statistics"
    entries = response["data"]
    trophyList = []
    countries = {}
    for entry in entries:
        trophyList.append(entry["SCORE"])
        if entry["country"] in countries:
            countries[entry["country"]] += 1
        else:
            countries[entry["country"]] = 1
    countryList = sorted(countries, key=lambda country: countries[country], reverse=True)
    for i in range(len(countryList)):
        countryList[i] = {"country": countryList[i], "n": countries[countryList[i]]}
    body = "average trophies: " + str(statistics.mean(trophyList)) + "\n"
    body += "trophy standard deviation: " + str(statistics.stdev(trophyList)) + "\n"
    body += "median trophies: " + str(statistics.median(trophyList))+ "\n"
    body += "representation by country:\n" + "\n".join(discordTable(countryList, orderList=["country", "n"]))
    return (header, body)

async def getLeaderboardStats(ws, args, message_object):
    args["stats"] = True
    await getLeaderboard(ws, args, message_object)

async def linkChat(ws, args, message_object):
    isGolfblitzMessage = type(message_object) is dict
    userId = message_object["fromId"] if isGolfblitzMessage else str(message_object.author.id)
    if not get_verification(userId):
        sendMessage(ws, ("You need to have a verified account for this command", ""), message_object, args)
        return
    currGroupId = message_object["teamId"] if isGolfblitzMessage else message_object.guild.id
    linkGroupId = args["group_id"]
    textChannelId = "channel_id" in args
    if isGolfblitzMessage:
        textChannelId = args["channel_id"] if textChannelId else bot_globals.global_bot.get_guild(int(linkGroupId)).text_channels[0].id
    else:
        textChannelId = args["channel_id"] if textChannelId else message_object.guild.text_channels[0].id
    textChannelId = int(textChannelId)
    if len(linkGroupId) != 18 and isGolfblitzMessage or len(linkGroupId) != 24 and not isGolfblitzMessage:
        sendMessage(ws, ("Invalid group id", ""), message_object, args)
        return
    if not linkGroupId in bot_globals.group_configs:
        bot_globals.group_configs[linkGroupId] = {}
    linkGroupConfig = bot_globals.group_configs[linkGroupId]
    if not "linkedGroups" in linkGroupConfig:
        linkGroupConfig["linkedGroups"] = [[currGroupId, textChannelId]]
    else:
        linkGroupConfig["linkedGroups"].append([currGroupId, textChannelId])
    json.dump(bot_globals.group_configs, open(bot_globals.group_configs_path, 'w'))

async def listChallenges(ws, args, message_object):
    events = []
    for eventName in bot_globals.command_data["get_challenge"]:
        eventData = bot_globals.command_data["get_challenge"][eventName][1]["data"]["current_event"]
        events.append((eventData["start_time"]/1000, eventName))
    events.sort(reverse=True)
    outStr = "Challenge events:\n"
    for event in events:
        outStr += "* " + event[1] + " event started at " + (time.asctime(time.gmtime(int(event[0]))) + " GMT" if event[0] else "no specific time") + "\n"
    await directlySendMessage(ws, outStr, message_object)

async def ping(ws, args, message_object):
    start = time.time()
    pong = await ws.ping()
    await pong
    await sendMessage(ws, ("pong!", "discord latency: {discord_latency} ms\ngolfblitz latency {golfblitz_latency} ms".format(discord_latency=1000*bot_globals.global_bot.latency, golfblitz_latency=1000*(time.time() - start))), message_object, args)

async def finishGetExtraPlayerInfo(response, args):
    smallPlayerData = response[0]["scriptData"]["data"]
    playerId = smallPlayerData["player_id"] #Note: this attribute does not actually exist by default.  a previous part of the code should have created it
    head = smallPlayerData["display_name"] + " " + str(int(smallPlayerData["trophies"]))
    body = "basic player details:\n"
    body += "team: " + smallPlayerData["team_name"][4:] + ("(id: " + smallPlayerData["team_id"] + ")" if smallPlayerData["team_id"] else "none")+"\n"
    body += "last logged in {0} ago\n".format(datetime.timedelta(seconds=time.time() - smallPlayerData["last_login"]/1000))
    body += "hat: " + bot_globals.hats[str(smallPlayerData["hat"])]["name"]["en"] + ", golfer: " + bot_globals.golfers[str(smallPlayerData["golfer"])]["name"]["en"] + "\n"
    body += "\nplayer attributes:\nlevel: {level}\npower: {power}, speed: {speed}, accuracy: {accuracy}, cooldown: {cooldown}\n".format(level=smallPlayerData["level"], power=smallPlayerData["attr"]["attr_pwr"], speed=smallPlayerData["attr"]["attr_speed"], accuracy=smallPlayerData["attr"]["attr_acc"], cooldown=smallPlayerData["attr"]["attr_cool"])
    stats = smallPlayerData["stats"]
    body += "\nplayer stats:\nswishes: {swishes}\nnumber of games played: {gamesplayed}\nwin rate: {winrate}%\nhighest trophies: {highscore}\nbest season rank: {bestrank}\n".format(swishes=stats["swishes"], gamesplayed=stats["gamesplayed"], winrate=100*stats["wins"]/stats["gamesplayed"] if stats["gamesplayed"] else 0, highscore=stats["highesttrophies"], bestrank=stats["highestseasonrank"])
    body += bot_globals.safe_split_str
    if not smallPlayerData["team_id"]:
        if playerId:
            head += " (id: " + playerId + ")"
        return (head, body)
    teamMembers = response[1]["teams"][0]["members"]
    bigPlayerData = False
    for member in teamMembers:
        if member["id"] == playerId or not playerId and member["displayName"] == smallPlayerData["display_name"] and member["scriptData"]["last_login"] == smallPlayerData["last_login"]:
            bigPlayerData = member
            break
    if not bigPlayerData: #do this in case the player somehow left the team in the small time fragment that existed between the chain of commands
        if playerId:
            head += " (id: " + playerId + ")"
        return (head, body)
    head += " (id: " + bigPlayerData["id"] + ")"
    body += "\nextra player details:\nplayer status: " + ("online" if bigPlayerData["online"] else "offline") + "\n"
    bigPlayerData = bigPlayerData["scriptData"] #there is a bit of stuff outside of the scriptData, but we probably won't need it
    corePlayerData = bigPlayerData["data"]
    body += "friend code: " + bigPlayerData["invite_code"] + "\n"
    body += "xp: " + str(corePlayerData["xp"]) + "\n"
    sellTime = bigPlayerData["token_time"]/1000 - time.time()
    body += "player can sell cards {0}\n".format("in " + str(datetime.timedelta(seconds = sellTime)) if sellTime > 0 else "now")
    packSlots = [bigPlayerData["slot1"], bigPlayerData["slot2"], bigPlayerData["slot3"], bigPlayerData["slot4"]]
    body += "packs slots: "
    for i, pack in enumerate(packSlots):
        packStr = "{n} - {packType}".format(n=i+1, packType=bot_globals.cardpacks[pack["type"]] if pack["type"] != -1 else "empty")
        if pack["unlocking"]:
            timechg = pack["available_time"]/1000 - time.time()
            packStr += " (currently being unlocked, available in {timestr})".format(timestr=datetime.timedelta(seconds = timechg)) if timechg > 0 else "(ready to open)"
        body += packStr + (", " if i < len(packSlots) - 1 else "")
    body += "\n"
    starpack = bigPlayerData["pinpack"]
    body += "star pack: "
    if starpack["available_time"]/1000 > time.time():
        body += "available in {timedelta}\n".format(timedelta=datetime.timedelta(seconds = starpack["available_time"]/1000 - time.time()))
    else:
        body += "{n} / 10 stars\n".format(n=starpack["pin_count"])
    body += bot_globals.safe_split_str
    body += "\npowerups:\n"
    powerups = corePlayerData["cards"]
    for id in sorted(powerups.keys(), key=lambda k: int(k)):
        if id != "0":
            powerup = powerups[id]
            if powerup["level"] < 12:
                body += "level {lvl} {powerup}: accuracy {attr_acc}, speed {attr_speed}, power {attr_pwr}\n".format(powerup=bot_globals.powerups[id]["name"]["en"], lvl=powerup["level"], attr_acc=powerup["attr_acc"], attr_speed=powerup["attr_speed"], attr_pwr=powerup["attr_pwr"])
            else:
                body += "level {lvl} {powerup}\n".format(powerup=bot_globals.powerups[id]["name"]["en"], lvl=powerup["level"])
    body += bot_globals.safe_split_str
    body += "\nnotable hats: \n"
    for id in corePlayerData["hats"]:
        if id in bot_globals.hats and bot_globals.hats[id]["rarity"] >= 4:
            body += bot_globals.hats[id]["name"]["en"] + "\n"
        elif not id in bot_globals.hats:
            body += "UNKNOWN HAT with id " + str(id) + "\n"
    body += "\nnotable golfers: \n"
    for id in corePlayerData["golfers"]:
        if id in bot_globals.golfers and bot_globals.golfers[id]["rarity"] >= 4:
            body += bot_globals.golfers[id]["name"]["en"] + "\n"
        elif not id in bot_globals.golfers:
            body += "UNKNOWN GOLFER with id " + str(id) + "\n"
    if "emotes" in corePlayerData:
        body += "\nemotes: \n"
        for id in corePlayerData["emotes"]:
            emoteStr = ""
            emoteObj = bot_globals.emotes[id]
            if "text" in emoteObj:
                emoteStr = emoteObj["text"]["en"] + " dialog emote"
            else:
                emoteStr = emoteObj["loc"]["en"] + " animated emote"
            body += emoteStr + "\n" + bot_globals.safe_split_str
        body += "total number of emotes: {n}\n".format(n=len(corePlayerData["emotes"])) + bot_globals.safe_split_str
    body += "\ndaily deals:\n"
    for deal in bigPlayerData["daily_deals"]:
        if deal == "time":
            body += "time until the deals reset: " + str(datetime.timedelta(seconds=time.time() - bigPlayerData["daily_deals"][deal]/1000)) + "\n"
        else:
            deal = bigPlayerData["daily_deals"][deal]
            body += "{item} {type} x{num} for {cost} gems\n".format(item=bot_globals.golfers[deal["identifier"]]["name"]["en"] if deal["type"] == "golfer" else bot_globals.hats[deal["identifier"]]["name"]["en"], type=deal["type"], num=deal["count"], cost=deal["cost"])
    return (head, body)

async def getExtraPlayerInfo(ws, args, message_object):
    teamId = args["prev_function_data"][0]["scriptData"]["data"]["team_id"]
    args["prev_function_data"][0]["scriptData"]["data"]["player_id"] = args["id"] #we will need the player id later
    if teamId:
        baseReq = requests["get_team_data"].copy()
        baseReq["teamId"] = teamId
        await sendGolfblitzWs(ws, finishGetExtraPlayerInfo, args, message_object, "none", baseReq)
    else:
        message = await finishGetExtraPlayerInfo(args["prev_function_data"], args)
        await sendMessage(ws, message, message_object, args)
    return

async def getPlayerInfo(ws, args, message_object):
    playerId = False
    if "id" in args and args["id"]:
        playerId = args["id"]
    if not playerId:
        if type(message_object) is dict:
            playerId = message_object["fromId"]
        if "prev_function_data" in args:
            playerId = args["prev_function_data"][0]["data"][0]["userId"]
            args["prev_function_data"].pop() #this info will not be necessary later on
        elif "rank" in args:
            args["count"] = 1
            args["offset"] = int(args["rank"]) - 1
            args["next_function"] = getPlayerInfo
            await getLeaderboard(ws, args, message_object)
            return
        elif "code" in args:
            data = json.loads(httprequests.post("https://f351468gbswz.live.gamesparks.net/rs/gb_api/S2cypG37waV7wXE2cpSS4lKSRlzzgBZz/LogEventRequest", headers={"content-type": "application/json"}, data=json.dumps({"@class": ".LogEventRequest", "eventKey": "GB_API_PLAYER_INFO", "playerId": "5ccf4984235bac98e46dec48", "requestId": "", "friendcode": args["code"].lower()})).text)
            args["prev_function_data"] = [data]
            args["id"] = False # we are going to have to handle this soon
            await getExtraPlayerInfo(ws, args, message_object)
            return
        else:
            if not playerId:
                authorId = str(message_object.author.id)
                if authorId in bot_globals.user_configs and "externalId" in bot_globals.user_configs[authorId]:
                    playerId = bot_globals.user_configs[authorId]["externalId"]
                else:
                    errorMsg = bot_globals.error_messages["no_associated_player_id"].split("\n")
                    await sendMessage(ws, errorMsg, message_object, args)
                    return
    baseReq = requests["get_player_info"].copy()
    args["id"] = baseReq["player_id"] = playerId #make sure that the id argument is set because it will be needed later
    args["next_function"] = getExtraPlayerInfo
    reqId = baseReq["requestId"] = str(time.time())
    await ws.send(json.dumps(baseReq))
    bot_globals.pending_requests[reqId] = ("THIS SHOULD NOT BE HAPPENING", message_object, "playerinfo", args) #this function shouldn't trigger any "response function"
    return

async def finishGetTeamInfo(response, args):
    if type(response) is list:
        response = response[0]
    teamMetadata = response["scriptData"]
    teamData = response["teams"][0]
    header = "{name} {trophies} (id: {id})".format(name=teamData["teamName"][4:], trophies=int(teamMetadata["teamcurrenttrophies"]), id=teamData["teamId"])
    body = teamMetadata["desc"] + "owner: " + teamData["owner"]["displayName"] + "\n"
    body += "location: " + teamMetadata["teamlocation"] + "\n"
    body += "required trophies: " + str(teamMetadata["teamrequiredtrophies"]) + "\n"
    if "teamCards" in teamMetadata:
        body += "team cardpool:\n"
        for cardtype in teamMetadata["teamCards"]:
            n = 1
            cards = teamMetadata["teamCards"][cardtype]
            total = 0
            body += cardtype + "(s):\n"
            for card in sorted(cards, key = lambda c: cards[c]["count"], reverse=True):
                if cards[card]["count"]:
                    nameRef = bot_globals.golfers if cardtype == "golfer" else bot_globals.hats
                    body += "* " + nameRef[card]["name"]["en"] + ": " + str(cards[card]["count"]) + "\n"
                    total += cards[card]["count"]
            body += "total " + cardtype + " cards: " + str(total) + "\n"
    if "cardpool" in args:
        return (header, body)
    body += "members:\n"
    for member in teamData["members"]:
        #print("STUFF", member["scriptData"], "\n")
        body += "{name} {trophies} friend code: {code}, id: {id}\n".format(name=member["displayName"], trophies=int(member["scriptData"]["data"]["trophies"]), code=member["scriptData"]["invite_code"] if "invite_code" in member["scriptData"] else "none", id=member["id"]) + bot_globals.safe_split_str

    return (header, body)

async def getTeamInfo(ws, args, message_object):
    teamId = args["id"] if "id" in args else False
    if not teamId:
        if type(message_object) is dict:
            teamId = message_object["fromId"]
        if "prev_function_data" in args:
            currData = args["prev_function_data"][0]
            if currData["@class"] == ".LeaderboardDataResponse":
                teamId = currData["data"][0]["teamId"]
            else:
                currData = currData["scriptData"]
                if "teams" in currData:
                    teamId = currData["teams"][-1]["teamId"]
                else:
                    teamId = currData["data"]["team_id"]
            args["prev_function_data"].pop()
        elif "name" in args: #we need to search for the team first
            args["next_function"] = getTeamInfo
            baseReq = requests["get_teams"].copy()
            baseReq["NAME"] = args["name"]
            await sendGolfblitzWs(ws, False, args, message_object, "teaminfo", baseReq)
            return
        elif "rank" in args:
            args["team"] = ""
            args["count"] = 1
            args["offset"] = int(args["rank"]) - 1
            args["next_function"] = getTeamInfo
            await getLeaderboard(ws, args, message_object)
            return
        else:
            authorId = str(message_object.author.id)
            if authorId in bot_globals.user_configs and "externalId" in bot_globals.user_configs[authorId]:
                args["next_function"] = getTeamInfo
                baseReq = requests["get_player_info"].copy()
                baseReq["player_id"] = bot_globals.user_configs[authorId]["externalId"]
                await sendGolfblitzWs(ws, False, args, message_object, "teaminfo", baseReq)
                return
            else:
                errorMsg = bot_globals.error_messages["no_associated_player_id"].split("\n")
                await sendMessage(ws, errorMsg, message_object, args)
                return
    baseReq = requests["get_team_data"].copy()
    baseReq["teamId"] = teamId
    await sendGolfblitzWs(ws, finishGetTeamInfo, args, message_object, "teaminfo", baseReq)
    return

async def setPrefix(ws, args, message_object):
    isGolfblitzMessage =  type(message_object) is dict
    if not has_permissions(message_object):
        errorMsg = bot_globals.error_messages["insufficient_permissions"].split("\n")
        errorMsg[1] += "You need to have at least the pro rank to perform this action" if isGolfblitzMessage else " (You need manage channels or manage server)"
        await sendMessage(ws, errorMsg, message_object, args)
        return
    groupId = False
    if isGolfblitzMessage:
        groupId = message_object["teamId"]
    else:
        groupId = str(message_object.guild.id)

    if not groupId in bot_globals.group_configs:
        bot_globals.group_configs[groupId] = {}
    bot_globals.group_configs[groupId]["prefix"] = args["prefix"] if args["prefix"] != "default" else "~"
    json.dump(bot_globals.group_configs, open(bot_globals.group_configs_path, 'w'))
    await sendMessage(ws, ("", "the prefix is now: " + args["prefix"]), message_object, args)

async def verifyAccount(ws, args, message_object):
    id = False
    externalId = args["id"]
    needsVerificationMsg = "Your account is not verified yet.  "
    if type(message_object) is dict:
        if len(externalId) != 18:
            await directlySendMessage(ws, "Invalid discord id of " + externalId, message_object)
            return
        id = message_object["fromId"]
        needsVerificationMsg += "You need to verify it on discord now."
        externalId = externalId
    else:
        if len(externalId) != 24:
            await directlySendMessage(ws, "Invalid golf blitz id", message_object)
            return
        id = str(message_object.author.id)
        needsVerificationMsg += "You need to verify it on golf blitz now."
    if not id in bot_globals.user_configs:
        bot_globals.user_configs[id] = {"externalId": externalId}
    else:
        bot_globals.user_configs[id]["externalId"] = externalId
    if get_verification(id):
        await directlySendMessage(ws, "Your account is verified now!", message_object)
    else:
        await directlySendMessage(ws, needsVerificationMsg, message_object)
    json.dump(bot_globals.user_configs, open(bot_globals.user_configs_path, 'w'))

commands = {"getchallenge": getChallenge, "info": info, "help": help, "leaderboard": getLeaderboard, "leaderboardstats": getLeaderboardStats, "linkchat": linkChat, "listchallenges": listChallenges, "ping": ping, "playerinfo": getPlayerInfo, "ranks": getLeaderboard, "setprefix": setPrefix, "teaminfo": getTeamInfo, "verifyaccount": verifyAccount}
