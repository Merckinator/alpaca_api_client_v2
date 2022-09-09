import os
from datetime import datetime, timedelta
import time

import requests
import schedule

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestBarRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import AssetClass, AssetExchange, AssetStatus, GetAssetsRequest, MarketOrderRequest, OrderSide, OrderType, TimeInForce


def sendNotification(message):
    """Send the string 'message' to my Discord channel and logs it via standard output."""
    ID = os.environ.get('DISCORD_ID')
    TOKEN = os.environ.get('DISCORD_TOKEN')
    URL = f'https://discordapp.com/api/webhooks/{ID}/{TOKEN}'
    payload = {'content': message,
               'username': 'alpaca-algorithmic-trader',
               'avatar_url': 'https://d1qb2nb5cznatu.cloudfront.net/startups/i/638844-fc9b06d417a209c9e53f71809af92091-medium_jpg.jpg?buster=1516408319'}
    print(message)
    return requests.post(URL, payload)


def is_target_asset(asset):
    """Used to filter a list of assets to those of interest."""
    return (asset.easy_to_borrow
            and asset.marginable
            and asset.shortable
            and asset.tradable)


def can_still_afford(symbols, bankroll):
    """Returns a new list of symbols whose prices are less than bankroll."""
    still_buyable = []
    for symbol in symbols:
        bar_request = StockLatestBarRequest(symbol_or_symbols=symbol)
        bar = data_client.get_stock_latest_bar(bar_request)
        print('In can_still_afford, the latest_bar was: ' + str(bar), flush=True)

        if float(bankroll) > bar.close:
            still_buyable.append(symbol)

    return still_buyable


def get_cheap_symbols(assets, low, high):
    """
    Finds the symbols in the list of assets that are priced
    between the low and high parameters.

    Parameters:
    low  (int): the bottom of the price range.
    high (int): the top of the price range.

    Returns:
    [str]: a list of the stock symbols whose price
        falls within the given price range.
    """
    print(f'looking through {len(assets)} assets...')
    cheap_assets = []
    bar_request = StockLatestBarRequest(symbol_or_symbols=[x.symbol for x in assets])
    bars = data_client.get_stock_latest_bar(bar_request)

    for index, (symbol, bar) in enumerate(bars.items()):
        if bar.close > low and bar.close < high:
            cheap_assets.append(symbol)
        if index % 100 == 0:
            print('100 assets queried...')

    return cheap_assets


def get_SMAs(items):
    """Calculates the short (50-day) and long (200-day) simple moving average of a symbol."""
    short_period, long_period = 50, 200
    shorter, longer = [], []
    for index, _ in enumerate(items):
        # print(index)
        if index + short_period <= len(items):
            shorter.append(
                sum(items[index: (index + short_period - 1)]) / short_period)
        if index + long_period <= len(items):
            longer.append(
                sum(items[index: (index + long_period - 1)]) / long_period)
    return (shorter, longer)


def get_actionable_assets(cheap_assets):
    """
    Takes a list of symbols as strings and determines which are death/golden crossing.

    Parameter:
    [str]: The list of asset symbols to search through.

    Returns:
    ([str], [str]): A pair of lists of strings; (golden crossing, death crossing).
    """
    buyable_assets, sellable_assets = [], []
    bars_request = StockBarsRequest(
        symbol_or_symbols=cheap_assets,
        start=datetime.now() - timedelta(days=210),
        limit=210,
        timeframe=TimeFrame.Day
    )
    bars = data_client.get_stock_bars(bars_request)

    for symbol, bars in bars.items():
        close_prices = [bar.close for bar in bars]
        close_prices.reverse()  # reversing makes it new to old
        # print(close_prices)
        if len(close_prices) > 200:
            short_sma, long_sma = get_SMAs(close_prices)
            # a 'buy-able' asset's short_sma should have recently rose above its long_sma
            # check all cheap assets for 'buy-able'
            if short_sma[0] > long_sma[0] and short_sma[1] < long_sma[1]:
                buyable_assets.append(symbol)
            elif short_sma[0] < long_sma[0] and short_sma[1] > long_sma[1]:
                sellable_assets.append(symbol)

    return (buyable_assets, sellable_assets)


def main():
    try:
        start_time = datetime.now()
        global data_client
        data_client = StockHistoricalDataClient(
            os.environ.get('APCA_API_KEY_ID'),
            os.environ.get('APCA_API_SECRET_KEY')
        )
        trading_client = TradingClient(
            os.environ.get('APCA_API_KEY_ID'),
            os.environ.get('APCA_API_SECRET_KEY')
        )

        # Check if the market is open, if it isn't then don't do anything else
        market = trading_client.get_clock()
        sendNotification(f'Is the market open? {market.is_open}!')

        if market.is_open:
            # Check if I own stocks and if I should sell them
            positions = TradingClient.get_all_positions()
            positions_symbols = [position.symbol for position in positions]
            buyable_symbols, sellable_symbols = get_actionable_assets(positions_symbols)
            for position in positions:
                if position.symbol in sellable_symbols:
                    order_request = MarketOrderRequest(
                        symbol=position.symbol,
                        qty=position.qty,
                        side=OrderSide.SELL,
                        type=OrderType.MARKET,
                        time_in_force=TimeInForce.DAY
                    )
                    order = trading_client.submit_order(order_request)
                    sendNotification('A market SELL order was placed for: ' + order.symbol)

            # Check if I have cash to buy stocks with
            account = trading_client.get_account()
            if float(account.cash) > 5:
                nasdaq_asset_request = GetAssetsRequest(
                    status=AssetStatus.ACTIVE,
                    asset_class=AssetClass.US_EQUITY,
                    exchange=AssetExchange.NASDAQ
                )
                nyse_asset_request = GetAssetsRequest(
                    status=AssetStatus.ACTIVE,
                    asset_class=AssetClass.US_EQUITY,
                    exchange=AssetExchange.NYSE
                )
                assets = trading_client.get_all_assets(nasdaq_asset_request) + trading_client.get_all_assets(nyse_asset_request)

                target_assets = list(filter(is_target_asset, assets))
                cheap_symbols = get_cheap_symbols(target_assets, 2, int(float(account.cash)))
                buyable_symbols, sellable_symbols = get_actionable_assets(cheap_symbols)
                sendNotification(str(buyable_symbols))

                while float(account.cash) > 5 and len(buyable_symbols) > 0:
                    order_request = MarketOrderRequest(
                        symbol=position.symbol,
                        qty=1,
                        side=OrderSide.BUY,
                        type=OrderType.MARKET,
                        time_in_force=TimeInForce.DAY
                    )
                    order = trading_client.submit_order(order_request)
                    sendNotification('A market BUY order was placed for: ' + order.symbol)
                    # 1 minute; letting orders settle and cash update
                    time.sleep(60)
                    account = trading_client.get_account()
                    buyable_symbols = can_still_afford(buyable_symbols, account.cash)

        sendNotification(f'This script took {datetime.now() - start_time}')
    except Exception as e:
        sendNotification('@everyone\nERROR: ' + str(e))


if __name__ == '__main__':
    schedule.every().day.at("11:30").do(main)

    while True:
        schedule.run_pending()
        time.sleep(1)
