import json
import config
import time
import math
from flask import Flask, request, jsonify, render_template
from binance.client import Client
from binance.enums import *

import telegram
from telegram.ext import Updater, CommandHandler, Dispatcher, MessageHandler, Filters
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

app = Flask(__name__)

############### Telegram BOT Setting ###############
updater = Updater(token=config.TELEGRAM_TOKEN, use_context=False)
bot = telegram.Bot(token=config.TELEGRAM_TOKEN)
chat_id = config.TELEGRAM_CHAT_ID

############### Binance Function ###############
client = Client(config.API_KEY, config.API_SECRET)
safeOrderAmount = 2000 #2000
initialCapital = 5800
startDate = "2022/07/16"
marginType = "ISOLATED"

def futures_order(side, quantity, symbol, leverage, order_type=ORDER_TYPE_MARKET):
	try:
		ts = time.time()
		client.futures_change_margin_type(symbol=symbol, marginType=marginType, timestamp=ts)

	except Exception as e:
		if str(e) == "APIError(code=-4046): No need to change margin type.":
			pass
		else:
			print("an exception occured - {}".format(e))
			bot.sendMessage(chat_id=chat_id, text=f"[Fail] Futures order\n-\n{side} {quantity} {symbol} {leverage}x\nAn exception occured - {e}")
			return False

	try:
		print(f"sending futures order {order_type} - {side} {quantity} {symbol}")
		client.futures_change_leverage(symbol=symbol, leverage=leverage, timestamp=ts)
		order = client.futures_create_order(symbol=symbol, side=side, type=order_type, quantity=quantity)

	except Exception as e:
		print("an exception occured - {}".format(e))
		bot.sendMessage(chat_id=chat_id, text=f"[Fail] Futures order\n-\n{side} {quantity} {symbol} {leverage}x\nAn exception occured - {e}")

		return False

	return order

def flat_future_order(symbol, precision, leverage, orderAmount):
	try:
		get_futures_order_response = get_futures_order()
		times = 0

		for i in range(len(get_futures_order_response["positions"])):
			if get_futures_order_response["positions"][i]["symbol"] == symbol:
				if float(get_futures_order_response["positions"][i]["positionAmt"]) != 0:
					totalQuantity = float(get_futures_order_response["positions"][i]["positionAmt"])
					entryPrice = round(float(get_futures_order_response["positions"][i]["entryPrice"]), 6)
					markPrice = round(float(get_futures_price(symbol)["markPrice"]), 6)

					# Closing positions in batches
					if totalQuantity > 0:
						side = "SELL"
					else:
						side = "BUY" 
					safeQuantity = safeOrderAmount / markPrice
					times = abs(int(totalQuantity / safeQuantity)) + 1
					quantity = math.floor(float(totalQuantity / times) * math.pow(10, precision)) / math.pow(10, precision)

				else:
					bot.sendMessage(chat_id=chat_id, text=f"The {symbol} position is already closed.")
					
					return True

	except Exception as e:
		bot.sendMessage(chat_id=chat_id, text=f"[Fail] The {symbol} position closed error.")

		return {
			"code": "error",
			"message": "The position closed error."
		}
	
	# execute    
	if times > 1:
		for t in range(times-1):
			order_response = futures_order(side, abs(quantity), symbol, leverage)
		order_response = futures_order(side, abs((totalQuantity - (quantity * (times-1)))), symbol, leverage)
	if times == 1:
		order_response = futures_order(side, abs(totalQuantity), symbol, leverage)

	if order_response:
		profit = round((float(markPrice) - float(entryPrice))
					   * float(totalQuantity), 2)
		percent = round((profit / (orderAmount / leverage)) * 100, 2)
		bot.sendMessage(chat_id=chat_id, text=f"[Success] Futures order\n-\n{side} ${orderAmount} {symbol} {leverage}x\n-\nProfit: ${str(profit)} ({str(percent)}%)")

	return order_response

def get_futures_order():
	try:
		order = client.futures_account()
		
	except Exception as e:
		print("an exception occured - {}".format(e))
		return False

	return order

def get_futures_price(symbol):
	try:
		price = client.futures_mark_price(symbol=symbol)

	except Exception as e:
		print("an exception occured - {}".format(e))
		return False

	return price

def get_futures_precision(symbol):
	try:
		symbol_info = client.get_symbol_info(symbol = symbol)
		step_size = 0.0
		for f in symbol_info['filters']:
			if f['filterType'] == 'LOT_SIZE':
				step_size = float(f['stepSize'])
		precision = int(round(-math.log(step_size, 10), 0))
		if precision >= 2:
			precision = precision - 2
		if precision == 1:
			precision = precision - 1 

	except Exception as e:
		print("an exception occured - {}".format(e))
		return False

	return precision

################ Trading Webhook ###############
@app.route('/')
def welcome():
	return render_template('index.html')

################ Futures Webhook ###############
@app.route('/webhook_futures', methods=['POST'])
def webhook_futures():
	order_response = False
	data = json.loads(request.data)

	# check passphrase
	if data['passphrase'] != config.WEBHOOK_PASSPHRASE:
		bot.sendMessage(chat_id=chat_id, text="[Invalid passphrase]\nSomeone tried to hack your trading system.")
		
		return {
			"code": "error",
			"message": "Invalid passphrase! Someone tried to hack your trading system."
		}

	# order info
	market_position = data['strategy']['market_position']
	prev_market_position = data['strategy']['prev_market_position']
	ticker = data['ticker'].split("USDT")[0] + "USDT"
	leverage = int(data['leverage'])
	margin = int(data['margin'])
	orderAmount = leverage * margin
	minutes = data['time'].split(":")[1]

	# if minutes in ["58", "59", "00", "01", "02"]:
	# precision and quantity
	precision = get_futures_precision(symbol = ticker)     
	quantity = float(round(orderAmount / data['strategy']['order_price'], precision))

	# order side
	# flat
	if market_position == "flat":
		order_response = flat_future_order(ticker, precision, leverage, orderAmount)
	
	# long
	if market_position == "long":
		if prev_market_position == "long":
			# check double order issue
			get_futures_order_response = get_futures_order()
			for i in range(len(get_futures_order_response["positions"])):
				if get_futures_order_response["positions"][i]["symbol"] == ticker:
					last_order_time = int(get_futures_order_response["positions"][i]['updateTime'])/1000
					if time.time() - last_order_time >= 300:
						side = "BUY"
						order_response = futures_order(side, quantity, ticker, leverage)

						if order_response:
							bot.sendMessage(chat_id=chat_id, text=f"[Success] Futures order\n-\n{side} ${orderAmount} {ticker} {leverage}x")

					else:
						bot.sendMessage(chat_id=chat_id, text=f"[Fail] An double order issue occurs in {ticker}.")

						return {
							"code": "error",
							"message": "Double order"
						}
		else:
			order_response = flat_future_order(ticker, precision, leverage, orderAmount)

			if order_response:
				side = "BUY"
				order_response = futures_order(side, quantity, ticker, leverage)

				if order_response:
					bot.sendMessage(chat_id=chat_id, text=f"[Success] Futures order\n-\n{side} ${orderAmount} {ticker} {leverage}x")

			else:
				pass
	
	# short
	if market_position == "short":
		if prev_market_position == "short":
			# check double order issue
			get_futures_order_response = get_futures_order()
			for i in range(len(get_futures_order_response["positions"])):
				if get_futures_order_response["positions"][i]["symbol"] == ticker:
					last_order_time = int(get_futures_order_response["positions"][i]['updateTime'])/1000
					if time.time() - last_order_time >= 300:
						side = "SELL"
						order_response = futures_order(side, quantity, ticker, leverage)

						if order_response:
							bot.sendMessage(chat_id=chat_id, text=f"[Success] Futures order\n-\n{side} ${orderAmount} {ticker} {leverage}x")

					else:
						bot.sendMessage(chat_id=chat_id, text=f"[Fail] An double order issue occurs in {ticker}.")

						return {
							"code": "error",
							"message": "Double order"
						}
		else:
			order_response = flat_future_order(ticker, precision, leverage, orderAmount)

			if order_response:
				side = "SELL"
				order_response = futures_order(side, quantity, ticker, leverage)

				if order_response:
					bot.sendMessage(chat_id=chat_id, text=f"[Success] Futures order\n-\n{side} ${orderAmount} {ticker} {leverage}x")

			else:
				pass

	# check order result
	if order_response:
		print("Order success")	
		return {
			"code": "success",
			"message": "order executed"
		}
	else:
		print("Order failed")
		return {
			"code": "error",
			"message": "order failed"
		}


@app.route('/develop_test', methods=['POST']) 
def develop_test():
	data = json.loads(request.data)

	# order info
	side = data['strategy']['order_action'].upper()
	ticker = data['ticker'].split("USDT")[0] + "USDT"
	order_response = get_futures_order()
	get_futures_order_response = get_futures_order()

	for i in range(len(get_futures_order_response["assets"])):
		if get_futures_order_response["assets"][i]["asset"] == "USDT":
			walletBalance = round(float(get_futures_order_response["assets"][i]["walletBalance"]), 2)
			totalRevenue = round(float(walletBalance - initialCapital), 2)
			totalPercent = round(float((totalRevenue / initialCapital) * 100), 2)
			tmp_text = f"【 Performance - Strat From {startDate} 】\n-\nInitial Capital: ${str(initialCapital)}\nWallet Balance: ${str(walletBalance)}\nTotal Revenue: ${str(totalRevenue)} ({str(totalPercent)}%)\n"


	print(tmp_text)
	# check order result
	if order_response:
		return {
			"code": "success",
			"message": order_response
		}
	else:
		print("order failed")
		return {
			"code": "error",
			"message": "order failed"
		}

############### Telegram ###############
@app.route("/telegram_callback", methods=['POST'])
def webhook_handler():
	if request.method == "POST":
		update = telegram.Update.de_json(request.get_json(force=True), bot)
		# chat_id = update.message.chat.id
		# msg_id = update.message.message_id
		# text = update.message.text.encode('utf-8').decode()

		dispatcher.process_update(update)
	return 'ok', 200

def telegram_callback(bot, update):
	try:
		operation = str(bot.message.text.split(" ")[0]).upper()
		ticker = str(bot.message.text.split(" ")[1]).upper()
	except Exception as e:
		pass

	if operation == "GET":
		try:
			get_futures_order_response = get_futures_order()
			has_ticker = False
			position_text = "Futures order\n-\n"
			totalUnrealizedProfit = 0

			for i in range(len(get_futures_order_response["positions"])):
				if float(get_futures_order_response["positions"][i]["positionAmt"]) != 0:
					has_ticker = True
					ticker = get_futures_order_response["positions"][i]["symbol"]
					entryPrice = round(float(get_futures_order_response["positions"][i]["entryPrice"]), 6)
					markPrice = round(float(get_futures_price(symbol = ticker)["markPrice"]), 6)
					totalQuantity = float(get_futures_order_response["positions"][i]["positionAmt"])
					margin = float(get_futures_order_response["positions"][i]["positionInitialMargin"])
					unrealizedProfit = round(float(get_futures_order_response["positions"][i]["unrealizedProfit"]), 2)
					percent = round((unrealizedProfit / margin) * 100, 2)
					leverage = int(get_futures_order_response["positions"][i]["leverage"])
					totalUnrealizedProfit += unrealizedProfit

					if totalQuantity > 0:
						side = "Long"
					else:
						side = "Short"

					tmp_text = f"Symbol: {ticker}\nSide: {side}\nMargin: {margin}\nLeverage: {leverage}x\nEntry Price: {str(entryPrice)}\nMark Price: {str(markPrice)}\n-\nUnrealized Profit: ${str(unrealizedProfit)} ({str(percent)}%)\n\n==========\n\n"
					position_text = position_text + tmp_text

			if has_ticker == False:
				bot.message.reply_text(text="No position.")
			else:
				position_text = position_text + "Total Unrealized Profit: $" + str(round(totalUnrealizedProfit, 2))
				bot.message.reply_text(text=position_text)
					
		except Exception as e:
			print("Fail to get futures orders.")
			bot.message.reply_text(text="Fail to get futures orders.")

	elif operation == "CLOSE":
		if ticker == "ALL":
			try:
				get_futures_order_response = get_futures_order()
				has_position = False
				times = 0
				position_text = "[Success] Futures order\n-\n"
				totalProfit = 0

				for i in range(len(get_futures_order_response["positions"])):
					if float(get_futures_order_response["positions"][i]["positionAmt"]) != 0:
						has_position = True
						ticker = get_futures_order_response["positions"][i]["symbol"]
						totalQuantity = float(get_futures_order_response["positions"][i]["positionAmt"])
						margin = float(get_futures_order_response["positions"][i]["positionInitialMargin"])
						entryPrice = round(float(get_futures_order_response["positions"][i]["entryPrice"]), 6)
						markPrice = round(float(get_futures_price(symbol = ticker)["markPrice"]), 6)
						leverage = int(get_futures_order_response["positions"][i]["leverage"])

						# Closing positions in batches
						precision = get_futures_precision(symbol = ticker)
						safeQuantity = safeOrderAmount / markPrice
						times = abs(int(totalQuantity / safeQuantity)) + 1
						quantity = math.floor(float(totalQuantity / times) * math.pow(10, precision)) / math.pow(10, precision)
	
						if totalQuantity > 0:
							side = "SELL"
						else:
							side = "BUY"

						if times > 1:
							for t in range(times-1):
								order_response = futures_order(side, abs(quantity), ticker, leverage)
							order_response = futures_order(side, abs((totalQuantity - (quantity * (times-1)))), ticker, leverage)
						else:
							order_response = futures_order(side, abs(totalQuantity), ticker, leverage)


						if order_response:
							profit = round(float(get_futures_order_response["positions"][i]["unrealizedProfit"]), 2)
							percent = round((profit / margin) * 100, 2)
							totalProfit += profit

							tmp_text = f"Closed {ticker} {leverage}x\nMargin: {margin}\nEntry Price: {str(entryPrice)}\nExit Price: {str(markPrice)}\n-\nProfit: ${str(profit)} ({str(percent)}%)\n\n==========\n\n"
							position_text = position_text + tmp_text

				if has_position == False:
					bot.message.reply_text(text="All futures order has been closed.")
				else:
					position_text = position_text + "Total Profit: $" + str(round(totalProfit, 2))
					bot.message.reply_text(text=position_text)

			except Exception as e:
				bot.message.reply_text(text=f"All futures closed failed.")

		else:
			try:
				get_futures_order_response = get_futures_order()
				ticker = ticker + "USDT"
				has_ticker = False

				for i in range(len(get_futures_order_response["positions"])):
					if get_futures_order_response["positions"][i]["symbol"] == ticker:
						has_ticker = True
						if float(get_futures_order_response["positions"][i]["positionAmt"]) != 0:
							totalQuantity = float(get_futures_order_response["positions"][i]["positionAmt"])
							margin = float(get_futures_order_response["positions"][i]["positionInitialMargin"])
							entryPrice = round(float(get_futures_order_response["positions"][i]["entryPrice"]), 6)
							markPrice = round(float(get_futures_price(symbol = ticker)["markPrice"]), 6)
							leverage = int(get_futures_order_response["positions"][i]["leverage"])
							
							# Closing positions in batches
							precision = get_futures_precision(symbol = ticker)
							safeQuantity = safeOrderAmount / markPrice
							times = abs(int(totalQuantity / safeQuantity)) + 1
							quantity = math.floor(float(totalQuantity / times) * math.pow(10, precision)) / math.pow(10, precision)

							if totalQuantity > 0:
								side = "SELL"
							else:
								side = "BUY"

							if times > 1:
								for t in range(times-1):
									order_response = futures_order(side, abs(quantity), ticker, leverage)
								order_response = futures_order(side, abs((totalQuantity - (quantity * (times-1)))), ticker, leverage)
							else:
								order_response = futures_order(side, abs(totalQuantity), ticker, leverage)

							if order_response:
								profit = round(float(get_futures_order_response["positions"][i]["unrealizedProfit"]), 2)
								percent = round((profit / margin) * 100, 2)   
								bot.message.reply_text(text=f"[Success] Futures order\n-\nClosed {ticker} {leverage}x\nMargin: {margin}\nEntry Price: {str(entryPrice)}\nExit Price: {str(markPrice)}\n-\nProfit: ${str(profit)} ({str(percent)}%)")                           
						else:
							bot.message.reply_text(text=f"The {ticker} position is already closed.")

				if has_ticker == False:
					bot.message.reply_text(text=f"{ticker} is not in symbol list.")

			except Exception as e:
				bot.message.reply_text(text="Futures closed failed.")

	elif operation == "PROFIT":
		get_futures_order_response = get_futures_order()

		for i in range(len(get_futures_order_response["assets"])):
			if get_futures_order_response["assets"][i]["asset"] == "USDT":
				walletBalance = round(float(get_futures_order_response["assets"][i]["walletBalance"]), 2)
				totalRevenue = round(float(walletBalance - initialCapital), 2)
				totalPercent = round(float((totalRevenue / initialCapital) * 100), 2)
				tmp_text = f"【 Performance - Strat From {startDate} 】\n-\nInitial Capital: ${str(initialCapital)}\nWallet Balance: ${str(walletBalance)}\nTotal Revenue: ${str(totalRevenue)} ({str(totalPercent)}%)\n"

		bot.message.reply_text(text=tmp_text)

	else:
		bot.message.reply_text(text="Please enter the correct command format.\n\nInput: [operation] [symbol]\nex.\nclose ETH\nclose all\nget")

	return 'ok', 200


# Add handler for handling message, there are many kinds of message. For this handler, it particular handle text message.
dispatcher = Dispatcher(bot, None)
dispatcher.add_handler(MessageHandler(Filters.text, telegram_callback))