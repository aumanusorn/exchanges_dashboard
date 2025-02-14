import datetime
import logging
import threading
import time

from unicorn_binance_rest_api import BinanceRestApiManager
from unicorn_binance_websocket_api import BinanceWebSocketApiManager

from scraper_root.scraper.data_classes import AssetBalance, Position, ScraperConfig, Tick, Balance, \
    Income, Order
from scraper_root.scraper.persistence.repository import Repository

logger = logging.getLogger()


class BinanceFutures:
    def __init__(self, config: ScraperConfig, repository: Repository, exchange: str = "binance.com-futures"):
        print('Binance initialized')
        self.config = config
        self.api_key = self.config.api_key
        self.secret = self.config.api_secret
        self.repository = repository
        self.ws_manager = BinanceWebSocketApiManager(exchange=exchange, throw_exception_if_unrepairable=True,
                                                     warn_on_update=False)

        self.rest_manager = BinanceRestApiManager(
            self.api_key, api_secret=self.secret)

    def start(self):
        print('Starting binance futures scraper')

        # userdata_thread = threading.Thread(name=f'userdata_thread', target=self.process_userdata, daemon=True)
        # userdata_thread.start()

        for symbol in self.config.symbols:
            symbol_trade_thread = threading.Thread(
                name=f'trade_thread_{symbol}', target=self.process_trades, args=(symbol,), daemon=True)
            symbol_trade_thread.start()

        sync_balance_thread = threading.Thread(
            name=f'sync_balance_thread', target=self.sync_account, daemon=True)
        sync_balance_thread.start()

        sync_trades_thread = threading.Thread(
            name=f'sync_trades_thread', target=self.sync_trades, daemon=True)
        sync_trades_thread.start()

        sync_orders_thread = threading.Thread(
            name=f'sync_orders_thread', target=self.sync_open_orders, daemon=True)
        sync_orders_thread.start()

    def sync_trades(self):
        while True:
            try:
                counter = 0
                newest_trade_reached = False
                while newest_trade_reached is False and counter < 3:
                    counter += 1
                    newest_income = self.repository.get_newest_income()
                    if newest_income is None:
                        # Binance started in September 2017, so no trade can be before that
                        newest_timestamp = int(datetime.datetime.fromisoformat('2017-09-01 00:00:00').timestamp() * 1000)
                    else:
                        newest_datetime = newest_income.time
                        newest_timestamp = int(newest_datetime.timestamp() * 1000)
                        logger.warning(f'Synced newer trades since {newest_datetime}')

                    exchange_incomes = self.rest_manager.futures_income_history(**{'limit': 1000, 'startTime': newest_timestamp + 1})
                    logger.info(f"Length of newer trades fetched from {newest_timestamp}: {len(exchange_incomes)}")
                    incomes = []
                    for exchange_income in exchange_incomes:
                        income = Income(symbol=exchange_income['symbol'],
                                        asset=exchange_income['asset'],
                                        type=exchange_income['incomeType'],
                                        income=float(
                                            exchange_income['income']),
                                        timestamp=exchange_income['time'],
                                        transaction_id=exchange_income['tranId'])
                        incomes.append(income)
                    self.repository.process_incomes(incomes)
                    if len(exchange_incomes) < 1:
                        newest_trade_reached = True

                logger.warning('Synced trades')
            except Exception as e:
                logger.error(f'Failed to process balance: {e}')

            time.sleep(60)

    def sync_account(self):
        while True:
            try:
                account = self.rest_manager.futures_account()
                asset_balances = [AssetBalance(asset=asset['asset'],
                                               balance=float(
                                                   asset['walletBalance']),
                                               unrealizedProfit=float(
                                                   asset['unrealizedProfit'])
                                               ) for asset in account['assets']]
                balance = Balance(totalBalance=account['totalWalletBalance'],
                                  totalUnrealizedProfit=account['totalUnrealizedProfit'],
                                  assets=asset_balances)
                self.repository.process_balances(balance)

                positions = [Position(symbol=position['symbol'],
                                      entry_price=float(
                                          position['entryPrice']),
                                      position_size=float(
                                          position['positionAmt']),
                                      side=position['positionSide'],
                                      unrealizedProfit=float(
                                          position['unrealizedProfit'])
                                      ) for position in account['positions'] if position['positionSide'] != 'BOTH']
                self.repository.process_positions(positions)
                logger.warning('Synced account')
            except Exception as e:
                logger.error(f'Failed to process balance: {e}')

            time.sleep(20)

    def sync_open_orders(self):
        while True:
            orders = {}
            try:
                for symbol in self.config.symbols:
                    open_orders = self.rest_manager.futures_get_open_orders(
                        **{'symbol': symbol})
                    orders[symbol] = []
                    for open_order in open_orders:
                        order = Order()
                        order.symbol = open_order['symbol']
                        order.price = float(open_order['price'])
                        order.quantity = float(open_order['origQty'])
                        order.side = open_order['side']
                        order.position_side = open_order['positionSide']
                        order.type = open_order['type']
                        orders[symbol].append(order)
                self.repository.process_orders(orders)
            except Exception as e:
                logger.error(f'Failed to process open orders: {e}')
            logger.warning('Synced orders')

            time.sleep(20)

    # def process_userdata(self):
    #     self.ws_manager.create_stream(channels="arr",
    #                                   markets="!userData",
    #                                   stream_buffer_name="userdata",
    #                                   api_key=self.api_key,
    #                                   api_secret=self.secret,
    #                                   output="UnicornFy")
    #     while True:
    #         if self.ws_manager.is_manager_stopping():
    #             logger.debug('Stopping userdata-stream processing...')
    #             break
    #         event = self.ws_manager.pop_stream_data_from_stream_buffer(stream_buffer_name="userdata")
    #         if event is False:
    #             time.sleep(0.01)# The pop_stream_data_from_stream_buffer is non-blocking, so need to sleep to prevent eating up CPU
    #         else:
    #             logger.debug(f'Userdata event: {event}')
    #             try:
    #                 if event["event_type"] == "ACCOUNT_UPDATE":
    #                     queue_item = {}
    #                     positions = SymbolPositions(symbol='')
    #                     if "balances" in event:
    #                         queue_item['balances'] = []
    #                         for i in event["balances"]:
    #                             asset_balance = AssetBalance(asset=i['asset'], balance=float(i['wallet_balance']))
    #                             queue_item['balances'].append(asset_balance)
    #                     if "positions" in event:
    #                         for i in event["positions"]:
    #                             new_position = Position(i["symbol"], float(i["entry_price"]),
    #                                                     float(i["position_amount"]), float(i["upnl"]))
    #                             positions.symbol = i['symbol']
    #                             if i["position_side"] == "LONG":
    #                                 positions.long = new_position
    #                             elif i["position_side"] == "SHORT":
    #                                 positions.short = new_position
    #                             else:
    #                                 logger.debug(f'Ignoring unsupported position side BOTH {new_position}')
    #                         if positions.symbol == '':
    #                             logger.warning(f'Symbol on account update not recognized in event {event}')
    #                             continue
    #                         else:
    #                             queue_item['positions'] = positions
    #                     if 'positions' in queue_item and 'balances' in queue_item:
    #                         logger.info('PROCESSED ACCOUNT_UPDATE EVENT')
    #                         # self.userdata_queues[positions.symbol].put(queue_item)
    #                     else:
    #                         logger.warning(f'Balance or positions not filled for account_update in queue_item {queue_item}')
    #                 elif event['event_type'] == 'ORDER_TRADE_UPDATE':
    #                     if event["order_price_type"] == "MARKET":
    #                         order = MarketOrder(id=int(event["order_id"]),
    #                                             symbol=event["symbol"],
    #                                             quantity=float(event["order_quantity"]),
    #                                             side=event["side"],
    #                                             position_side=event["position_side"],
    #                                             status=OrderStatus[event["current_order_status"]],
    #                                             price=float(event["order_avg_price"]))
    #                     elif event["order_price_type"] == "LIMIT":
    #                         order = LimitOrder(id=int(event["order_id"]),
    #                                            symbol=event["symbol"],
    #                                            quantity=float(event["order_quantity"]),
    #                                            side=event["side"],
    #                                            position_side=event["position_side"],
    #                                            status=OrderStatus(event["current_order_status"]),
    #                                            price=float(event["order_price"]))
    #                     elif event["order_price_type"] == "STOP":
    #                         order = StopLossOrder(id=int(event["order_id"]),
    #                                               symbol=event["symbol"],
    #                                               quantity=float(event["order_quantity"]),
    #                                               side=event["side"],
    #                                               position_side=event["position_side"],
    #                                               status=OrderStatus[event["current_order_status"]],
    #                                               price=float(event["order_price"]),
    #                                               stop_price=float(event["order_stop_price"]))
    #                     elif event["order_price_type"] == "TAKE_PROFIT":
    #                         order = TakeProfitOrder(id=int(event["order_id"]),
    #                                                 symbol=event["symbol"],
    #                                                 quantity=float(event["order_quantity"]),
    #                                                 side=event["side"],
    #                                                 position_side=event["position_side"],
    #                                                 status=OrderStatus[event["current_order_status"]],
    #                                                 price=float(event["order_price"]),
    #                                                 stop_price=float(event["order_stop_price"]))
    #                     elif event["order_price_type"] == "STOP_MARKET":
    #                         order = StopLossMarketOrder(id=int(event["order_id"]),
    #                                                     symbol=event["symbol"],
    #                                                     quantity=float(event["order_quantity"]),
    #                                                     side=event["side"],
    #                                                     position_side=event["position_side"],
    #                                                     status=OrderStatus[event["current_order_status"]],
    #                                                     price=float(event["order_avg_price"]),
    #                                                     stop_price=float(event["order_stop_price"]))
    #                     elif event["order_price_type"] == "TAKE_PROFIT_MARKET":
    #                         order = TakeProfitMarketOrder(id=int(event["order_id"]),
    #                                                       symbol=event["symbol"],
    #                                                       quantity=float(event["order_quantity"]),
    #                                                       side=event["side"],
    #                                                       position_side=event["position_side"],
    #                                                       status=OrderStatus[event["current_order_status"]],
    #                                                       price=float(event["order_avg_price"]),
    #                                                       stop_price=float(event["order_stop_price"]))
    #                     # ToDo: Double-check if the values and fields are correct
    #                     elif event["order_price_type"] == "TRAILING_STOP_MARKET":
    #                         order = TrailingStopLossOrder(id=int(event["order_id"]),
    #                                                       symbol=event["symbol"],
    #                                                       quantity=float(event["order_quantity"]),
    #                                                       side=event["side"],
    #                                                       position_side=event["position_side"],
    #                                                       status=OrderStatus[event["current_order_status"]],
    #                                                       price=float(event["order_price"]))
    #                     # ToDo: Double-check if any other field is required
    #                     elif event["order_price_type"] == "LIQUIDATION":
    #                         order = LiquidationOrder(id=int(event["order_id"]),
    #                                                  symbol=event["symbol"],
    #                                                  quantity=float(event["order_quantity"]),
    #                                                  side=event["side"],
    #                                                  position_side=event["position_side"],
    #                                                  status=OrderStatus[event["current_order_status"]])
    #                     else:
    #                         logger.error("Order is None: " + event)
    #                         continue
    #
    #                     if order.status == OrderStatus.PARTIALLY_FILLED:
    #                         order.quantity = order.quantity - float(event["last_executed_quantity"])
    #                     logger.info("PROCESSED ORDER UPDATE")
    #                     # self.order_updates_queue.put(order)
    #                 else:
    #                     logger.debug(f'Not processing event {event}')
    #             except Exception as e:
    #                 logger.error(f'Failed to process userdata event: {e}')

    def process_trades(self, symbol: str):
        # stream buffer is set to length 1, because we're only interested in the most recent tick
        self.ws_manager.create_stream(channels=['aggTrade'],
                                      markets=symbol,
                                      stream_buffer_name=f"trades_{symbol}",
                                      output="UnicornFy",
                                      stream_buffer_maxlen=1)
        logger.info(f"Trade stream started")
        while True:
            if self.ws_manager.is_manager_stopping():
                logger.debug('Stopping trade-stream processing...')
                break
            event = self.ws_manager.pop_stream_data_from_stream_buffer(
                stream_buffer_name=f"trades_{symbol}")
            if event and 'event_type' in event and event['event_type'] == 'aggTrade':
                logger.debug(event)
                tick = Tick(symbol=event['symbol'],
                            price=float(event['price']),
                            qty=float(event['quantity']),
                            timestamp=int(event['trade_time']))
                logger.debug(f"Processed tick for {tick.symbol}")
                self.repository.process_tick(tick)
            # Price update every 5 seconds is fast enough
            time.sleep(5)
        logger.warning('Stopped trade-stream processing')
