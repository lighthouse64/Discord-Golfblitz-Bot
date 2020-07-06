# Discord Golfblitz Bot
A bot that faces both discord and golfblitz created by lighthouse64

To set this up, first make sure you install the required python packages with `pip install -r requirements.txt`

Then you need to fill in the appropriate info in configuration/main-configuration.json

You should fill in the following fields:
* bot-token : use your discord bot token
* owner_discord_id : your 18 character discord id (should be all numbers)
* owner_golfblitz_id : your 24 character golfblitz uuid
* userName (in default_golfblitz_authreq) : the email that you used to secure your golf blitz account
* password (in default_golfblitz_authreq) : the password for your golf blitz account

Finally, run `bot.py` to start up the bot

Note: for those who may be interested, this repository also has the file `sample-api-connection.py` that gives you a basic manual connection to golf blitz's gamesparks websocket server