from decimal import Decimal
from typing import List

import structlog
from eth_typing import Address
from eth_utils import to_canonical_address
from web3 import Web3

from raiden_installer.account import Account
from raiden_installer.constants import (
    EXCHANGE_PRICE_MARGIN,
    GAS_LIMIT_MARGIN,
    NULL_ADDRESS,
    WEB3_TIMEOUT,
)
from raiden_installer.kyber.web3 import contracts as kyber_contracts, tokens as kyber_tokens
from raiden_installer.network import Network
from raiden_installer.tokens import Erc20Token, EthereumAmount, TokenAmount, TokenTicker, Wei
from raiden_installer.uniswap.web3 import contracts as uniswap_contracts
from raiden_installer.utils import estimate_gas, send_raw_transaction

log = structlog.get_logger()


class ExchangeError(Exception):
    pass


class Exchange:
    def __init__(self, w3: Web3):
        self.w3 = w3

    @property
    def chain_id(self):
        return self.w3.eth.chainId

    @property
    def network(self):
        return Network.get_by_chain_id(self.chain_id)

    @property
    def name(self):
        return self.__class__.__name__

    def get_current_rate(self, token_amount: TokenAmount) -> EthereumAmount:  # pragma: no cover
        raise NotImplementedError

    def calculate_transaction_costs(self, token_amount: TokenAmount, account: Account) -> dict:
        if not self.is_listing_token(token_amount.ticker):
            raise ExchangeError(
                f"Cannot calculate costs because {self.name} is not listing {token_amount.ticker}"
            )
        if token_amount.as_wei <= 0:
            raise ExchangeError(f"Cannot calculate costs for a swap of {token_amount.formatted}")

        log.debug("calculating exchange rate")
        exchange_rate = self.get_current_rate(token_amount)
        eth_sold = EthereumAmount(
            token_amount.value * exchange_rate.value * Decimal(EXCHANGE_PRICE_MARGIN)
        )

        log.debug("calculating gas price")
        gas_price = self._get_gas_price()

        transaction_params = {
            "from": account.address,
            "value": eth_sold.as_wei,
            "gasPrice": gas_price.as_wei,
        }
        log.debug("estimating gas")
        gas = self._estimate_gas(
            token_amount,
            account,
            transaction_params,
            exchange_rate=exchange_rate
        )

        block = self.w3.eth.getBlock(self.w3.eth.blockNumber)
        max_gas_limit = Wei(int(block["gasLimit"] * 0.9))
        gas_with_margin = Wei(int(gas * GAS_LIMIT_MARGIN))
        gas = min(gas_with_margin, max_gas_limit)
        gas_cost = EthereumAmount(Wei(gas * gas_price.as_wei))
        total = EthereumAmount(gas_cost.value + eth_sold.value)

        log.debug("transaction cost", gas_price=gas_price, gas=gas, eth=eth_sold)
        return {
            "gas_price": gas_price,
            "gas": gas,
            "eth_sold": eth_sold,
            "total": total,
            "exchange_rate": exchange_rate,
        }

    def buy_tokens(self, account: Account, token_amount: TokenAmount, transaction_costs=None):
        if not transaction_costs:
            try:
                transaction_costs = self.calculate_transaction_costs(token_amount, account)
            except ExchangeError as exc:
                raise ExchangeError("Failed to get transactions costs") from exc

        return self._buy_tokens(account, token_amount, transaction_costs)

    def _buy_tokens(self, account: Account, token_amount: TokenAmount, transaction_costs: dict):  # pragma: no cover
        raise NotImplementedError

    def is_listing_token(self, ticker: TokenTicker):  # pragma: no cover
        raise NotImplementedError

    def _get_gas_price(self):  # pragma: no cover
        raise NotImplementedError

    def _estimate_gas(
        self,
        token_amount: TokenAmount,
        account: Account,
        transaction_params: dict,
        **kw
    ):  # pragma: no cover
        raise NotImplementedError

    def _send_buy_transaction(self, account: Account, transaction_costs: dict, contract_function, *args):
        eth_to_sell = transaction_costs["eth_sold"]
        gas = transaction_costs["gas"]
        gas_price = transaction_costs["gas_price"]
        transaction_params = {
            "from": account.address,
            "value": eth_to_sell.as_wei,
            "gas": gas,
            "gas_price": gas_price.as_wei,
        }

        return send_raw_transaction(
            self.w3,
            account,
            contract_function,
            *args,
            **transaction_params,
        )

    @classmethod
    def get_by_name(cls, name):
        return {"kyber": Kyber, "uniswap": Uniswap}[name.lower()]


class Kyber(Exchange):
    def __init__(self, w3: Web3):
        super().__init__(w3=w3)
        self.network_contract_proxy = kyber_contracts.get_network_contract_proxy(self.w3)

    def is_listing_token(self, ticker: TokenTicker):
        try:
            self.get_token_network_address(ticker)
            return True
        except ExchangeError:
            return False

    def get_token_network_address(self, ticker: TokenTicker) -> Address:
        try:
            token_network_address = to_canonical_address(
                kyber_tokens.get_token_network_address(self.chain_id, ticker)
            )
            return token_network_address
        except (KeyError, TypeError) as exc:
            raise ExchangeError(f"{self.name} is not listing {ticker}") from exc

    def get_current_rate(self, token_amount: TokenAmount) -> EthereumAmount:
        eth_address = self.get_token_network_address(TokenTicker("ETH"))
        token_network_address = self.get_token_network_address(token_amount.ticker)

        expected_rate, slippage_rate = self.network_contract_proxy.functions.getExpectedRate(
            token_network_address, eth_address, token_amount.as_wei
        ).call()

        if expected_rate == 0 or slippage_rate == 0:
            raise ExchangeError("Trade not possible at the moment due to lack of liquidity")

        return EthereumAmount(Wei(max(expected_rate, slippage_rate)))

    def _get_gas_price(self):
        web3_gas_price = self.w3.eth.generateGasPrice()
        kyber_max_gas_price = self.network_contract_proxy.functions.maxGasPrice().call()
        max_gas_price = min(web3_gas_price, kyber_max_gas_price)
        return EthereumAmount(Wei(max_gas_price))

    def _estimate_gas(
        self,
        token_amount: TokenAmount,
        account: Account,
        transaction_params: dict,
        **kw
    ):
        exchange_rate = kw.get("exchange_rate")
        if exchange_rate is None:
            raise ExchangeError(
                f"An exchange rate is needed to estimate gas for a swap on {self.name}"
            )

        eth_address = self.get_token_network_address(TokenTicker("ETH"))
        return estimate_gas(
            self.w3,
            account,
            self.network_contract_proxy.functions.trade,
            eth_address,
            transaction_params["value"],
            self.get_token_network_address(token_amount.ticker),
            account.address,
            token_amount.as_wei,
            exchange_rate.as_wei,
            account.address,
            **transaction_params,
        )

    def _buy_tokens(self, account: Account, token_amount: TokenAmount, transaction_costs: dict):
        exchange_rate = transaction_costs["exchange_rate"]
        eth_to_sell = transaction_costs["eth_sold"]
        eth_address = self.get_token_network_address(TokenTicker("ETH"))

        return self._send_buy_transaction(
            account,
            transaction_costs,
            self.network_contract_proxy.functions.trade,
            eth_address,
            eth_to_sell.as_wei,
            self.get_token_network_address(token_amount.ticker),
            account.address,
            token_amount.as_wei,
            exchange_rate.as_wei,
            account.address,
        )


class Uniswap(Exchange):
    # same address on all networks
    ROUTER02_ADDRESS = to_canonical_address("0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D")
    SUPPORTED_NETWORKS = ["mainnet", "ropsten", "rinkeby", "goerli", "kovan"]

    def __init__(self, w3: Web3):
        super().__init__(w3=w3)
        if not self.network.name in self.SUPPORTED_NETWORKS:
            raise ExchangeError(f"{self.name} does not support {self.network.name}")

        self.router_proxy = self.w3.eth.contract(
            abi=uniswap_contracts.UNISWAP_ROUTER02_ABI,
            address=self.ROUTER02_ADDRESS,
        )
        self.weth_address = to_canonical_address(self.router_proxy.functions.WETH().call())

    def _get_factory_proxy(self):
        factory_address = to_canonical_address(self.router_proxy.functions.factory().call())
        return self.w3.eth.contract(
            abi=uniswap_contracts.UNISWAP_FACTORY_ABI,
            address=factory_address,
        )

    def is_listing_token(self, token_ticker: TokenTicker):
        factory_proxy = self._get_factory_proxy()
        token = Erc20Token.find_by_ticker(token_ticker, self.network.name)
        pair_address = to_canonical_address(
            factory_proxy.functions.getPair(self.weth_address, token.address).call()
        )
        return pair_address != NULL_ADDRESS

    def _get_gas_price(self):
        return EthereumAmount(Wei(self.w3.eth.generateGasPrice()))

    def _estimate_gas(
        self,
        token_amount: TokenAmount,
        account: Account,
        transaction_params: dict,
        **kw
    ):
        latest_block = self.w3.eth.getBlock("latest")
        deadline = latest_block.timestamp + WEB3_TIMEOUT
        return estimate_gas(
            self.w3,
            account,
            self.router_proxy.functions.swapETHForExactTokens,
            token_amount.as_wei,
            [self.weth_address, token_amount.address],
            account.address,
            deadline,
            **transaction_params,
        )

    def _buy_tokens(self, account: Account, token_amount: TokenAmount, transaction_costs: dict):
        latest_block = self.w3.eth.getBlock("latest")
        deadline = latest_block.timestamp + WEB3_TIMEOUT

        return self._send_buy_transaction(
            account,
            transaction_costs,
            self.router_proxy.functions.swapETHForExactTokens,
            token_amount.as_wei,
            [self.weth_address, token_amount.address],
            account.address,
            deadline,
        )

    def get_current_rate(self, token_amount: TokenAmount) -> EthereumAmount:
        amounts_in = self.router_proxy.functions.getAmountsIn(
            token_amount.as_wei, [self.weth_address, token_amount.address]
        ).call()

        eth_to_sell = EthereumAmount(Wei(amounts_in[0]))
        return EthereumAmount(eth_to_sell.value / token_amount.value)
