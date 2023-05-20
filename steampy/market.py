import urllib.parse
import json

from decimal import Decimal
from requests import Session
from steampy.confirmation import ConfirmationExecutor
from steampy.exceptions import ApiException, TooManyRequests, LoginRequired
from steampy.models import Currency, SteamUrl, GameOptions
from steampy.utils import text_between, get_listing_id_to_assets_address_from_html, get_market_listings_from_html, \
    merge_items_with_descriptions_from_listing, get_market_sell_listings_from_api


def login_required(func):
    def func_wrapper(self, *args, **kwargs):
        if not self.was_login_executed:
            raise LoginRequired('Use login method first on SteamClient')
        else:
            return func(self, *args, **kwargs)

    return func_wrapper


class SteamMarket:
    def __init__(self, session: Session):
        self._session = session
        self._steam_guard = None
        self._session_id = None
        self.was_login_executed = False

    def _set_login_executed(self, steamguard: dict, session_id: str):
        self._steam_guard = steamguard
        self._session_id = session_id
        self.was_login_executed = True

    def fetch_price(self, item_hash_name: str, game: GameOptions, currency: str = Currency.USD) -> dict:
        url = SteamUrl.COMMUNITY_URL + '/market/priceoverview/'
        params = {'country': 'PL',
                  'currency': currency.value,
                  'appid': game.app_id,
                  'market_hash_name': item_hash_name}
        response = self._session.get(url, params=params)
        if response.status_code == 429:
            raise TooManyRequests("You can fetch maximum 20 prices in 60s period")
        return response.json()

    @login_required
    def fetch_price_history(self, item_hash_name: str, game: GameOptions) -> dict:
        url = SteamUrl.COMMUNITY_URL + '/market/pricehistory/'
        params = {'country': 'PL',
                  'appid': game.app_id,
                  'market_hash_name': item_hash_name}
        response = self._session.get(url, params=params)
        if response.status_code == 429:
            raise TooManyRequests("You can fetch maximum 20 prices in 60s period")
        return response.json()

    @login_required
    def get_my_market_listings(self) -> dict:
        response = self._session.get("%s/market" % SteamUrl.COMMUNITY_URL)
        if response.status_code != 200:
            raise ApiException("There was a problem getting the listings. http code: %s" % response.status_code)
        assets_descriptions = json.loads(text_between(response.text, "var g_rgAssets = ", ";\r\n"))
        listing_id_to_assets_address = get_listing_id_to_assets_address_from_html(response.text)
        listings = get_market_listings_from_html(response.text)
        listings = merge_items_with_descriptions_from_listing(listings, listing_id_to_assets_address,
                                                              assets_descriptions)
        if '<span id="tabContentsMyActiveMarketListings_end">' in response.text:
            n_showing = int(text_between(response.text, '<span id="tabContentsMyActiveMarketListings_end">', '</span>'))
            n_total = int(text_between(response.text, '<span id="tabContentsMyActiveMarketListings_total">', '</span>').replace(',',''))
            if n_showing < n_total < 1000:
                url = "%s/market/mylistings/render/?query=&start=%s&count=%s" % (SteamUrl.COMMUNITY_URL, n_showing, -1)
                response = self._session.get(url)
                if response.status_code != 200:
                    raise ApiException("There was a problem getting the listings. http code: %s" % response.status_code)
                jresp = response.json()
                listing_id_to_assets_address = get_listing_id_to_assets_address_from_html(jresp.get("hovers"))
                listings_2 = get_market_sell_listings_from_api(jresp.get("results_html"))
                listings_2 = merge_items_with_descriptions_from_listing(listings_2, listing_id_to_assets_address,
                                                                        jresp.get("assets"))
                listings["sell_listings"] = {**listings["sell_listings"], **listings_2["sell_listings"]}
            else:
                for i in range(0, n_total, 100):
                    url = "%s/market/mylistings/?query=&start=%s&count=%s" % (SteamUrl.COMMUNITY_URL, n_showing + i, 100)
                    response = self._session.get(url)
                    if response.status_code != 200:
                        raise ApiException("There was a problem getting the listings. http code: %s" % response.status_code)
                    jresp = response.json()
                    listing_id_to_assets_address = get_listing_id_to_assets_address_from_html(jresp.get("hovers"))
                    listings_2 = get_market_sell_listings_from_api(jresp.get("results_html"))
                    listings_2 = merge_items_with_descriptions_from_listing(listings_2, listing_id_to_assets_address,
                                                                            jresp.get("assets"))
                    listings["sell_listings"] = {**listings["sell_listings"], **listings_2["sell_listings"]}
        return listings

    @login_required
    def create_sell_order(self, assetid: str, game: GameOptions, money_to_receive: str) -> dict:
        data = {
            "assetid": assetid,
            "sessionid": self._session_id,
            "contextid": game.context_id,
            "appid": game.app_id,
            "amount": 1,
            "price": money_to_receive
        }
        headers = {'Referer': "%s/profiles/%s/inventory" % (SteamUrl.COMMUNITY_URL, self._steam_guard['steamid'])}
        response = self._session.post(SteamUrl.COMMUNITY_URL + "/market/sellitem/", data, headers=headers).json()
        if response.get("needs_mobile_confirmation"):
            return self._confirm_sell_listing(assetid)
        return response

    @login_required
    def create_buy_order(self, market_name: str, price_single_item: str, quantity: int, game: GameOptions,
                         currency: Currency = Currency.USD) -> dict:
        data = {
            "sessionid": self._session_id,
            "currency": currency.value,
            "appid": game.app_id,
            "market_hash_name": market_name,
            "price_total": str(Decimal(price_single_item) * Decimal(quantity)),
            "quantity": quantity
        }
        headers = {'Referer': "%s/market/listings/%s/%s" % (SteamUrl.COMMUNITY_URL, game.app_id, 
                                                            urllib.parse.quote(market_name))}
        response = self._session.post(SteamUrl.COMMUNITY_URL + "/market/createbuyorder/", data,
                                      headers=headers).json()
        if response.get("success") != 1:
            raise ApiException("There was a problem creating the order. Are you using the right currency? success: %s"
                               % response.get("success"))
        return response

    @login_required
    def buy_item(self, market_name: str, market_id: str, price: int, fee: int, game: GameOptions,
                 currency: Currency = Currency.USD) -> dict:
        data = {
            "sessionid": self._session_id,
            "currency": currency.value,
            "subtotal" : price - fee,
            "fee" : fee,
            "total" : price,
            "quantity": '1'
        }
        headers = {'Referer': "%s/market/listings/%s/%s" % (SteamUrl.COMMUNITY_URL, game.app_id,
                                                            urllib.parse.quote(market_name))}
        response = self._session.post(SteamUrl.COMMUNITY_URL + "/market/buylisting/" + market_id, data,
                                      headers=headers).json()
        try:
            if response["wallet_info"]["success"] != 1:
                raise ApiException("There was a problem buying this item. Are you using the right currency? success: %s"
                                   % response['wallet_info']['success'])
        except:
            raise ApiException("There was a problem buying this item. Message: %s"
                               % response.get("message"))
        return response

    @login_required
    def cancel_sell_order(self, sell_listing_id: str) -> None:
        data = {"sessionid": self._session_id}
        headers = {'Referer': SteamUrl.COMMUNITY_URL + "/market/"}
        url = "%s/market/removelisting/%s" % (SteamUrl.COMMUNITY_URL, sell_listing_id)
        response = self._session.post(url, data=data, headers=headers)
        if response.status_code != 200:
            raise ApiException("There was a problem removing the listing. http code: %s" % response.status_code)

    @login_required
    def cancel_buy_order(self, buy_order_id) -> dict:
        data = {
            "sessionid": self._session_id,
            "buy_orderid": buy_order_id
        }
        headers = {"Referer": SteamUrl.COMMUNITY_URL + "/market"}
        response = self._session.post(SteamUrl.COMMUNITY_URL + "/market/cancelbuyorder/", data, headers=headers).json()
        if response.get("success") != 1:
            raise ApiException("There was a problem canceling the order. success: %s" % response.get("success"))
        return response

    def _confirm_sell_listing(self, asset_id: str) -> dict:
        con_executor = ConfirmationExecutor(self._steam_guard['identity_secret'], self._steam_guard['steamid'],
                                            self._session)
        return con_executor.confirm_sell_listing(asset_id)
